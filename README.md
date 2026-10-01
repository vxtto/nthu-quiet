# NTHU Quiet

Disable decorative motion in a **running NTHUCC Linux app**, without rebuilding it or changing its proxy settings.

On the investigated laptop, NTHUCC's WebKit renderer continuously consumed almost one CPU core while the user was not interacting with the app. This tool reduced renderer CPU from **about 98% to 10.5% of one core**. Restoring the original animations returned it to **97%**. These are short measurements on one machine, not a universal benchmark or a measurement of fan speed.

## Use

Start NTHUCC normally, then run:

```bash
git clone https://github.com/vxtto/nthu-quiet.git
cd nthu-quiet
python3 nthu_quiet.py apply --sample 15
```

Requirements: **Linux x86_64**, Python 3.9+, GDB, `nm` from binutils, and permission to attach to your own NTHUCC process. On Ubuntu:

```bash
sudo apt install python3 gdb binutils
```

The normal command uses `sudo` for debugger attachment and a narrowly scoped process-memory scan, because Ubuntu's default ptrace policy prevents an unrelated same-user process from attaching. Run the Python entry point as your normal desktop user: AppImage FUSE mounts can deny root reads. It prompts for sudo authentication if needed. It does **not** change `ptrace_scope`, disable WebKit's sandbox, enable a debug server, or patch any installed binary.

```bash
python3 nthu_quiet.py status
python3 nthu_quiet.py restore --sample 15
python3 nthu_quiet.py apply --pid 12345   # when multiple NTHUCC processes exist
python3 nthu_quiet.py apply --no-sudo    # only if your ptrace policy already permits it
```

Successful output includes `active: true`, `stylePresent: true`, and `runningAnimations: 0` for the local main and tray frontends. CPU percentages refer to **one logical CPU**, not total machine capacity.

The override remains through ordinary in-app navigation. **Run it again after an app restart or full frontend reload.** Applying twice is supported. `restore` removes the override and resumes SVG motion that was previously running. Restarting the app also removes the injection.

## What it changes

- Overrides CSS animations and transitions, including inline styles and pseudo-elements.
- Stops current Web Animations and pauses SVG/SMIL animations.
- Watches for SVGs added by in-app navigation and pauses those too.
- Leaves colors, gradients, filters, blur, layout, and proxy configuration intact. Some animated decoration will become a static frame; controls update immediately.

The tool resolves exported WebKit/GObject symbols from the libraries actually loaded by NTHUCC, finds WebView instances, and evaluates `quiet.js` only in `tauri://localhost` documents. No PID, address, or ASLR offset is hard-coded. A temporary copy of the app executable lets privileged GDB locate libc when the original AppImage mount is inaccessible to root; it is deleted afterward.

Verification briefly uses the frontend's title as a status handshake, then restores its exact original value. Debugger attachment briefly pauses the app. This is an experimental workaround for the tested Tauri/WebKitGTK 4.1 build, not a supported NTHUCC extension interface. An incompatible future build may require changes. The tool refuses unsupported platforms, missing symbols, ambiguous instance discovery, and non-local WebViews; do not disable operating-system protections to force it to run.

Only use it on your own app session. It reads process memory to identify object headers; it does not save or publish memory dumps, cookies, subscription data, or session credentials. The repository contains original workaround code and a sanitized diagnostic report, not the vendor binary or recovered frontend bundles.

## For the NTHU developers

Please read [the investigation and recommended fixes](docs/investigation.md). The actionable finding is **continuous decorative animation causing expensive CPU rasterization**, with blur work prominent in the profile. Removing filters alone did not solve the observed load; disabling motion did.

The app already exposes motion settings, but some decoration is always animated and the reduced-motion stylesheet misses many inline-styled elements. An always-open proxy client should have an inexpensive static mode, honor reduced motion across the whole interface, and suspend motion when minimized or not being viewed.

## Tests

```bash
python3 -m unittest discover -s tests -v
node --test tests/quiet.test.cjs
```

See the report for the live apply/restore measurements. Tests do not attach to an app or require sudo.

## License

MIT. Unofficial project; no affiliation with NTHU or nthux.com.
