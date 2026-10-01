# Idle CPU investigation: NTHUCC on Linux

Investigated on 2026-10-01. The user reported loud laptop fans despite no active interaction. Fan RPM was not exposed, so this investigation measured processes, CPU time, and temperatures, not sound or fan speed.

## Environment

| Component | Observed |
|---|---|
| OS | Ubuntu 26.04.1 LTS |
| Desktop | GNOME, Wayland session |
| CPU | Intel Core i7-12700H, 20 logical CPUs |
| GPU | NVIDIA RTX 3070 Laptop GPU, hybrid graphics |
| Application | NTHUCC, AppImage, Tauri 2.11.1 identifiers in binary |
| App's loaded WebKitGTK | 2.50.4, bundled in the AppImage |
| System WebKitGTK package | 2.52.6; the running app uses its bundled copy |
| App's GTK backend | `GDK_BACKEND=x11`, set by its bundled launch hook |

The initial CPU package reading was 66°C and ACPI thermal sensors reported 69°C. Memory was not under pressure: approximately 47 GiB available, effectively no swap use, and no sustained CPU or I/O pressure. NVIDIA reported 0% GPU utilization and approximately 16 W; its contribution to fan behavior was not isolated.

## Reversible intervention

The app remained running, with its proxy engine and configuration intact. GDB was used to evaluate temporary styling in the existing main and tray WebViews. Measurements used differences in `/proc/PID/stat` CPU ticks over wall-clock intervals, not the lifetime CPU percentage printed by `ps`.

| Intervention | Renderer CPU, % of one core | Sample |
|---|---:|---:|
| Original UI, initial measurement | 98.7 | 15 seconds |
| Original UI, repeat | 98.7 | 20 seconds |
| Remove CSS `filter` and `backdrop-filter` only | 97.6 | 8 seconds |
| Pause CSS animations only | 59.1 | 8 seconds |
| Disable CSS animation/transition, cancel existing Web Animations, pause SVG/SMIL | 9.6 | 8 seconds |
| Remove the diagnostic CSS override | 97.0 | 8 seconds |
| Apply the packaged NTHU Quiet tool | 10.5 | 15 seconds |
| Restore using the packaged tool | 97.9 | 10 seconds |
| Reapply using the packaged tool | 10.0 | 15 seconds |

The final reapply also measured the native app at 1.6% and another WebKit process at 0.0% to the sample's precision. The frontend acknowledged zero running Web Animations, with SVG roots paused in both local documents. Applying the tool again was idempotent and `status` confirmed that the override remained active. The animation-off test retained the original filters; a separate DOM check still found 14 filtered/backdrop-filtered elements in the main document.

This supports a causal link between the UI's continuous motion and its high renderer CPU use. It does **not** establish that a particular aurora layer alone causes all of the load, that XWayland is the root cause, or that the NVIDIA GPU is responsible. Pausing CSS and completely disabling animation are different interventions; the residual load in the pause test was not individually attributed.

## Native profile

An eight-second `perf` CPU-clock profile at 99 Hz captured 773 samples, with no lost samples:

- 89.65% of sampled renderer CPU time was in the bundled `libwebkit2gtk-4.1.so.0`.
- 7.24% was in the kernel, including page allocation/fault paths.
- 2.59% was in libc, including `memset`.

Several hottest instructions clustered around library offsets `0x3b5df5a`, `0x3b5e110`, and `0x3b657b3`. Disassembly of the nearby `0x3b65xxx` code references `Source/ThirdParty/skia/src/core/SkBlurEngine.cpp`, including a sigma-range assertion. This ties a prominent hot region to Skia blur processing. The library is stripped, and matching external debug symbols could not be retrieved, so an exact symbolized call tree is unavailable. The profile does not justify attributing every WebKit sample to blur.

The proxy engine was far below the renderer's CPU consumption in these samples. Changing UI motion affected renderer load without stopping the proxy engine.

## Recovered frontend behavior

The binary's embedded assets were recovered by interpreting ELF relocations and decompressing its Brotli-compressed Tauri assets. The recovered bundles are not included in this repository.

1. **Aurora:** the component creates 3–5 large gradient layers, each with `blur(14px)` and an infinite animation. Its keyframes change opacity, translation, and vertical scale over approximately 11–24 seconds. A slow animation can still request new frames continuously.
2. **Starfield:** the main UI requests 70 SVG circles. Each has an indefinite opacity/twinkle animation; some stars also have drop-shadow filters.
3. **Sidebar:** a translucent sidebar applies `backdropFilter: blur(12px)` above the decorative background.
4. **Globe:** the globe has indefinite glow-breathing and SVG-content drift/roll animations. The main sidebar and tray header explicitly request a live globe. Turning off the main globe's rotation setting therefore does not disable all motion.
5. **SVG map:** the frontend includes indefinite SVG radius/opacity animations. CSS animation rules alone do not stop SMIL; the workaround also calls `pauseAnimations()` on SVG roots.
6. **Reduced motion:** the stylesheet applies `animation:none` to `[class*=terra-]` and `.tfade` under `prefers-reduced-motion: reduce`. Many animated nodes receive animation through inline styles and have neither selector. The setting therefore misses those nodes. This is a concrete selector-coverage defect.
7. **Visibility:** the app already listens for `visibilitychange` and applies a `.terra[data-hidden] *` animation-pause rule. In the investigated state, the main document reported `hidden:false`, while the tray document reported `hidden:true`. This does not demonstrate a broken minimized-window handler; it shows that visible-but-unattended motion still runs. The CSS pause rule also does not cover SVG/SMIL animation.

Frontend polling includes visibility-aware timers, so it would be inaccurate to claim that the app ignores visibility everywhere. No `requestAnimationFrame` loop was found in the recovered shipped JS bundles. That does not rule out engine-driven CSS/SMIL animation.

## Recommended upstream changes

1. **Offer a real static mode.** A single option should disable aurora, star twinkle, meteors, globe glow/drift/roll, SVG pulses, and transitions across the main UI and tray. Consider making static motion the Linux default until idle CPU is validated.
2. **Honor reduced motion comprehensively.** Target the entire application subtree and pseudo-elements rather than animation-name-like class names. Gate SVG `<animate>` elements in component logic as well. Verify the preference using the actual Linux/WebKit runtime.
3. **Treat visibility and attention separately.** A visible window need not animate all day. Combine the existing visibility handling with explicit minimize/tray lifecycle events and a documented policy for unfocused windows. Stop SMIL as well as CSS motion.
4. **Avoid animated filtered layers where CPU rasterization dominates.** Pre-render soft decorative gradients or use static assets, reduce their area, and remove or simplify backdrop blur over moving layers. Do not assume slow motion is cheap or that every filter is GPU-accelerated on every Linux configuration.
5. **Test the shipped AppImage.** Its bundled WebKit and forced X11 backend differ from the host's normal Wayland/system-WebKit stack. Benchmark that actual distribution on hybrid Intel/NVIDIA hardware. Updating WebKit or changing backend may help, but neither is proven to fix this issue by this investigation.
6. **Add an idle-performance acceptance check.** Compare motion on/off with the window visible, unfocused, minimized, and in the tray; compare connected/disconnected states. Use CPU ticks and package power where available. A static idle UI should not continuously occupy a core, and ordinary proxy traffic should not require decorative redraws.

## Reproduce and compare

```bash
python3 nthu_quiet.py restore --sample 15
python3 nthu_quiet.py apply --sample 15
python3 nthu_quiet.py status
```

Repeat with the same screen, connection state, window size, and power mode. After each change, allow the machine several minutes to cool before comparing fan noise; CPU reduction is immediate, thermal/fan response is delayed. Temperatures and fan response were not re-measured as a controlled outcome in this investigation.

Public reference: [WebKitGTK 2.50 rendering changes](https://webkitgtk.org/2025/11/26/webkitgtk-2.50.html) describe the Skia rendering backend and mixed CPU/GPU rendering modes. This contextualizes the profile but is not evidence that any particular mode or GPU was active in the app.

## Suggested message to the maintainers

> We found a reproducible idle CPU problem in the Linux NTHUCC AppImage on an Intel i7-12700H / NVIDIA RTX 3070 laptop. The WebKit renderer consumes about a full core with decorative motion enabled. A reversible animation override reduces it to about 10% of a core; restoring motion brings it back to about 97%. The profile has prominent Skia blur work. The current reduced-motion rule misses inline-styled animated nodes, and CSS-only pausing does not cover SVG animation. Please provide a complete static mode, honor reduced motion throughout the main and tray UI, and benchmark the shipped AppImage at idle. The workaround, reproduction steps, and limitations are in this repository.

This message is provided for the user to share. No maintainers were contacted by the tool or during publication.
