/* NTHU Quiet: reversible, document-local motion suppression. MIT licensed. */
(() => {
  "use strict";
  if (window.__NTHU_QUIET__) return window.__NTHU_QUIET__.status();
  const style = document.createElement("style");
  style.id = "nthu-quiet-motion";
  style.textContent = `*, *::before, *::after {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
  }`;
  const svgStates = new Map();
  const scriptAnimations = [];
  for (const animation of document.getAnimations()) {
    // CSS animations are recreated naturally when the override is removed.
    if (!(typeof CSSAnimation !== "undefined" && animation instanceof CSSAnimation) &&
        !(typeof CSSTransition !== "undefined" && animation instanceof CSSTransition)) {
      scriptAnimations.push({animation, state: animation.playState, time: animation.currentTime});
    }
    animation.cancel();
  }
  function freezeSvg(root) {
    const svgs = [];
    if (root.matches?.("svg")) svgs.push(root);
    if (root.querySelectorAll) svgs.push(...root.querySelectorAll("svg"));
    for (const svg of svgs) {
      if (!svg.pauseAnimations || svgStates.has(svg)) continue;
      svgStates.set(svg, svg.animationsPaused());
      svg.pauseAnimations();
    }
  }
  document.head.appendChild(style);
  freezeSvg(document);
  let active = true;
  const observer = new MutationObserver(records => {
    if (!active) return;
    if (!style.isConnected) document.head.appendChild(style);
    for (const record of records) {
      for (const node of record.addedNodes) freezeSvg(node);
    }
    // Drop detached SVGs rather than retaining every visited page forever.
    for (const svg of svgStates.keys()) if (!svg.isConnected) svgStates.delete(svg);
  });
  observer.observe(document.documentElement, {childList: true, subtree: true});
  window.__NTHU_QUIET__ = {
    version: 1,
    status() {
      return {active, stylePresent: style.isConnected,
        runningAnimations: document.getAnimations().filter(a => a.playState === "running").length,
        pausedSvgRoots: [...svgStates.keys()].filter(s => s.isConnected && s.animationsPaused()).length};
    },
    restore() {
      active = false;
      observer.disconnect();
      style.remove();
      for (const [svg, wasPaused] of svgStates) {
        if (svg.isConnected && !wasPaused) svg.unpauseAnimations();
      }
      for (const {animation, state, time} of scriptAnimations) {
        if (state === "running" || state === "paused") {
          animation.currentTime = time;
          if (state === "running") animation.play(); else animation.pause();
        }
      }
      svgStates.clear();
      delete window.__NTHU_QUIET__;
      return {active: false};
    }
  };
  return window.__NTHU_QUIET__.status();
})();
