#!/usr/bin/env python3
"""Inject reversible motion suppression into a running Linux NTHUCC app."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
PROBE = "NTHU_QUIET_PROBE:"
SESSION_EXECUTABLE = None


class QuietError(RuntimeError):
    pass


def run(args, **kwargs):
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **kwargs)
    if result.returncode:
        raise QuietError(f"{args[0]} failed: {(result.stderr or result.stdout).strip()[-1600:]}")
    return result.stdout


def privileged(args, use_sudo):
    return (["sudo", "--"] if use_sudo else []) + args


def mappings(pid):
    result = []
    for line in Path(f"/proc/{pid}/maps").read_text().splitlines():
        parts = line.split(maxsplit=5)
        start, end = (int(x, 16) for x in parts[0].split("-"))
        result.append((start, end, parts[1], int(parts[2], 16),
                       parts[5] if len(parts) == 6 else ""))
    return result


def library(pid, fragment):
    for start, _, _, offset, path in mappings(pid):
        if fragment in Path(path).name and offset == 0:
            if path.endswith(" (deleted)"):
                raise QuietError("A required library was replaced; restart NTHUCC first.")
            return start, path
    raise QuietError(f"Cannot find {fragment} in process {pid}.")


def symbol(base, path, name):
    for line in run(["nm", "-D", "--defined-only", path], timeout=20).splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[2].split("@")[0] == name:
            return base + int(fields[0], 16)
    raise QuietError(f"Required symbol {name} is unavailable in {Path(path).name}.")


def gdb(pid, commands, use_sudo):
    # The AppImage's FUSE mount may deny root reads. Use a temporary copy of
    # the executable so GDB can locate libc's malloc for string arguments.
    source = ("set pagination off\nset confirm off\nset architecture i386:x86-64\n"
              f"file {json.dumps(SESSION_EXECUTABLE)}\nattach {pid}\n" + "\n".join(commands) + "\ndetach\nquit\n")
    with tempfile.TemporaryDirectory(prefix="nthu-quiet-") as tmp:
        script = Path(tmp) / "commands.gdb"
        script.write_text(source)
        # A same-user GDB can read this; sudo GDB can also read ordinary tmpfs.
        output = run(privileged(["gdb", "--quiet", "--nx", "--batch", "-x", str(script)], use_sudo),
                     timeout=20)
    return output


def marker(output, name):
    match = re.search(rf"^{re.escape(name)}=(.*)$", output, re.MULTILINE)
    if not match:
        raise QuietError(f"Debugger did not return {name}; injection cannot be verified.")
    return match.group(1)


def scan_bytes(data, start, needle):
    """Match GObject headers, rejecting incidental class pointers."""
    positions = []
    at = 0
    while True:
        at = data.find(needle, at)
        if at < 0:
            return positions
        if (start + at) % 8 == 0 and at + 16 <= len(data):
            references = struct.unpack_from("<I", data, at + 8)[0]
            if 0 < references < 4096:
                positions.append(start + at)
        at += 1


def scan_instances(pid, class_pointer):
    # Read only anonymous writable allocations. Emit matching addresses, never
    # memory contents, cookies, configuration, or proxy/session credentials.
    needle = struct.pack("<Q", class_pointer)
    found = set()
    total = 0
    with open(f"/proc/{pid}/mem", "rb", buffering=0) as memory:
        for start, end, perms, _, path in mappings(pid):
            if perms != "rw-p" or path not in ("", "[heap]"):
                continue
            if end - start > 256 * 1024 * 1024:
                continue
            position = start
            while position < end:
                length = min(8 * 1024 * 1024, end - position)
                total += length
                if total > 1024 * 1024 * 1024:
                    raise QuietError("Memory scan exceeded its bound; unsupported allocation layout.")
                try:
                    memory.seek(position)
                    data = memory.read(length)
                except OSError:
                    break
                found.update(scan_bytes(data, position, needle))
                if length <= 16:
                    break
                position += length - 16
    return sorted(found)


def resolve_views(pid, use_sudo):
    base, path = library(pid, "libwebkit2gtk-4.1.so")
    obj_base, obj_path = library(pid, "libgobject-2.0.so")
    get_type = symbol(base, path, "webkit_web_view_get_type")
    peek = symbol(obj_base, obj_path, "g_type_class_peek")
    output = gdb(pid, [
        f"set $qt = ((unsigned long (*)()){get_type:#x})()",
        f"set $qc = ((void* (*)(unsigned long)){peek:#x})($qt)",
        'printf "CLASS=%p\\n", $qc'], use_sudo)
    cls = int(marker(output, "CLASS"), 16)
    if not cls:
        raise QuietError("NTHUCC has no initialized WebKitWebView class.")
    if use_sudo:
        text = run(privileged([sys.executable, "-I", str(Path(__file__).resolve()),
                               "--internal-scan", str(pid), hex(cls)], True), timeout=20)
        candidates = json.loads(text)
    else:
        candidates = scan_instances(pid, cls)
    if not candidates or len(candidates) > 16:
        raise QuietError("No supported WebView instances found, or allocation layout is ambiguous.")
    uri = symbol(base, path, "webkit_web_view_get_uri")
    commands = []
    for candidate in candidates:
        commands.append(f'printf "URI_{candidate:x}=%s\\n", '
                        f'((char* (*)(void*)){uri:#x})((void*){candidate:#x})')
    output = gdb(pid, commands, use_sudo)
    views = []
    for candidate in candidates:
        value = marker(output, f"URI_{candidate:x}")
        # Restrict injection to the application's own local frontend.
        if value == "tauri://localhost" or value.startswith("tauri://localhost/"):
            views.append((candidate, value))
    if not views:
        raise QuietError("No local tauri://localhost frontend found; refusing to inject.")
    return views, symbol(base, path, "webkit_web_view_run_javascript"), symbol(base, path, "webkit_web_view_get_title")


def evaluate(pid, views, address, source, use_sudo):
    script = json.dumps(source, ensure_ascii=True)
    commands = [f"call ((void (*)(void*,char*,void*,void*,void*)){address:#x})"
                f"((void*){view:#x}, {script}, (void*)0, (void*)0, (void*)0)"
                for view, _ in views]
    gdb(pid, commands, use_sudo)


def probe(pid, views, evaluate_address, title_address, use_sudo):
    # A short-lived title handshake avoids installing native callbacks or
    # opening a remote-debugging port. Restore the exact original title.
    source = """(() => {
      window.__NTHU_QUIET_PROBE_TITLE__ ??= document.title;
      const value = window.__NTHU_QUIET__?.status() ?? {active:false};
      document.title = 'NTHU_QUIET_PROBE:' + JSON.stringify(value);
    })();"""
    try:
        evaluate(pid, views, evaluate_address, source, use_sudo)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            time.sleep(0.25)
            commands = [f'printf "TITLE_{view:x}=%s\\n", '
                        f'((char* (*)(void*)){title_address:#x})((void*){view:#x})'
                        for view, _ in views]
            output = gdb(pid, commands, use_sudo)
            values = [marker(output, f"TITLE_{view:x}") for view, _ in views]
            if all(value.startswith(PROBE) for value in values):
                return [json.loads(value[len(PROBE):]) for value in values]
        raise QuietError("Frontend did not acknowledge the probe within 8 seconds.")
    finally:
        evaluate(pid, views, evaluate_address,
                 "if(window.__NTHU_QUIET_PROBE_TITLE__!==undefined){"
                 "document.title=window.__NTHU_QUIET_PROBE_TITLE__;"
                 "delete window.__NTHU_QUIET_PROBE_TITLE__;}", use_sudo)


def processes():
    matches = []
    for path in Path("/proc").glob("[0-9]*"):
        try:
            if (path / "comm").read_text().strip() == "NTHUCC":
                matches.append(int(path.name))
        except (OSError, ValueError):
            continue
    return matches


def ticks(pid):
    text = Path(f"/proc/{pid}/stat").read_text()
    fields = text[text.rfind(")") + 2:].split()
    return int(fields[11]) + int(fields[12])


def cpu_sample(parent, seconds):
    pids = [parent]
    for path in Path("/proc").glob("[0-9]*"):
        try:
            text = (path / "stat").read_text()
            fields = text[text.rfind(")") + 2:].split()
            if int(fields[1]) == parent and "WebKitWebProces" in text:
                pids.append(int(path.name))
        except (OSError, ValueError):
            pass
    before = {pid: ticks(pid) for pid in pids}
    start = time.monotonic()
    time.sleep(seconds)
    elapsed = time.monotonic() - start
    result = {}
    for pid, value in before.items():
        try:
            result[pid] = round((ticks(pid) - value) / os.sysconf("SC_CLK_TCK") / elapsed * 100, 1)
        except OSError:
            pass
    return result


def main():
    global SESSION_EXECUTABLE
    if len(sys.argv) == 4 and sys.argv[1] == "--internal-scan":
        print(json.dumps(scan_instances(int(sys.argv[2]), int(sys.argv[3], 16))))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "restore", "status"), nargs="?", default="apply")
    parser.add_argument("--pid", type=int, help="NTHUCC PID if more than one instance exists")
    parser.add_argument("--no-sudo", action="store_true", help="attach without sudo when ptrace permissions allow")
    parser.add_argument("--sample", type=float, metavar="SECONDS", default=0,
                        help="measure NTHUCC and renderer CPU after the action (percent of one core)")
    args = parser.parse_args()
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise QuietError("This version supports Linux x86_64 only.")
    if not 0 <= args.sample <= 60:
        raise QuietError("--sample must be between 0 and 60 seconds.")
    for command in ("gdb", "nm"):
        if not shutil.which(command):
            raise QuietError(f"Install the required command: {command}.")
    use_sudo = os.geteuid() != 0 and not args.no_sudo
    if use_sudo:
        if not shutil.which("sudo"):
            raise QuietError("sudo is unavailable; use --no-sudo if ptrace permissions allow.")
        ready = subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        if ready.returncode:
            subprocess.run(["sudo", "-v"], check=True)
    pids = processes()
    if args.pid is not None:
        if args.pid not in pids:
            raise QuietError("--pid must identify a running NTHUCC process.")
        pid = args.pid
    elif len(pids) == 1:
        pid = pids[0]
    else:
        raise QuietError(f"Expected one NTHUCC process; found {pids}. Start the app or specify --pid.")
    if os.geteuid() != 0 and Path(f"/proc/{pid}").stat().st_uid != os.getuid():
        raise QuietError("Refusing to attach to another user's app session.")
    with tempfile.TemporaryDirectory(prefix="nthu-quiet-exe-") as tmp:
        SESSION_EXECUTABLE = str(Path(tmp) / "NTHUCC")
        shutil.copyfile(f"/proc/{pid}/exe", SESSION_EXECUTABLE)
        perform_action(pid, args, use_sudo)


def perform_action(pid, args, use_sudo):
    views, evaluate_address, title_address = resolve_views(pid, use_sudo)
    if args.action == "apply":
        source = (HERE / "quiet.js").read_text()
        evaluate(pid, views, evaluate_address, source, use_sudo)
    elif args.action == "restore":
        evaluate(pid, views, evaluate_address, "window.__NTHU_QUIET__?.restore();", use_sudo)
    states = probe(pid, views, evaluate_address, title_address, use_sudo)
    for (_, uri), state in zip(views, states):
        print(f"{uri}: {json.dumps(state, sort_keys=True)}")
    if args.action == "apply" and not all(state.get("active") and state.get("stylePresent") for state in states):
        raise QuietError("The frontend did not confirm the motion override.")
    if args.action == "restore" and any(state.get("active") for state in states):
        raise QuietError("The frontend still reports an active override.")
    if args.sample:
        time.sleep(2)
        print("CPU (% of one core): " + json.dumps(cpu_sample(pid, args.sample), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (QuietError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"nthu-quiet: {error}", file=sys.stderr)
        sys.exit(1)
