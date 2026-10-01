const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'quiet.js'), 'utf8');

function fixture() {
  class Animation {
    constructor(state = 'running') { this.playState = state; this.currentTime = 123; }
    cancel() { this.playState = 'idle'; this.currentTime = null; }
    play() { this.playState = 'running'; }
    pause() { this.playState = 'paused'; }
  }
  class CSSAnimation extends Animation {}
  class CSSTransition extends Animation {}
  class SVG {
    constructor(paused = false) { this.paused = paused; this.isConnected = true; }
    matches(s) { return s === 'svg'; }
    querySelectorAll() { return []; }
    pauseAnimations() { this.paused = true; }
    unpauseAnimations() { this.paused = false; }
    animationsPaused() { return this.paused; }
  }
  const svg = new SVG();
  const initiallyPaused = new SVG(true);
  const animations = [new CSSAnimation(), new CSSTransition(), new Animation(), new Animation('paused')];
  const styles = [];
  const document = {
    head: { appendChild(s) { s.isConnected = true; if (!styles.includes(s)) styles.push(s); } },
    documentElement: {},
    createElement() { return {isConnected: false, remove() { this.isConnected = false; }}; },
    querySelectorAll() { return [svg, initiallyPaused]; },
    getAnimations() { return animations.filter(a => a.playState !== 'idle'); }
  };
  let observer;
  class MutationObserver {
    constructor(callback) { this.callback = callback; observer = this; }
    observe() { this.connected = true; }
    disconnect() { this.connected = false; }
  }
  const context = vm.createContext({window: {}, document, CSSAnimation, CSSTransition, MutationObserver});
  return {context, svg, initiallyPaused, animations, styles, SVG,
    run: () => vm.runInContext(source, context), get observer() { return observer; }};
}

test('motion suppression is idempotent and restore preserves previous SVG/WA state', () => {
  const f = fixture();
  f.run();
  const state = f.context.window.__NTHU_QUIET__;
  assert.equal(state.status().runningAnimations, 0);
  assert.equal(f.svg.paused, true);
  assert.match(f.styles[0].textContent, /animation: none !important/);
  assert.match(f.styles[0].textContent, /\*::before/);
  f.run();
  assert.equal(f.context.window.__NTHU_QUIET__, state);
  assert.equal(f.styles.length, 1);
  state.restore();
  assert.equal(f.styles[0].isConnected, false);
  assert.equal(f.svg.paused, false);
  assert.equal(f.initiallyPaused.paused, true);
  assert.equal(f.animations[2].playState, 'running');
  assert.equal(f.animations[2].currentTime, 123);
  assert.equal(f.animations[3].playState, 'paused');
  assert.equal(f.observer.connected, false);
  assert.equal(f.context.window.__NTHU_QUIET__, undefined);
});

test('navigation freezes added SVGs, repairs removed CSS, and releases detached SVGs', () => {
  const f = fixture();
  f.run();
  const next = new f.SVG();
  f.svg.isConnected = false;
  f.styles[0].remove();
  f.observer.callback([{addedNodes: [next]}]);
  assert.equal(next.paused, true);
  assert.equal(f.styles[0].isConnected, true);
  f.context.window.__NTHU_QUIET__.restore();
  assert.equal(next.paused, false);
  assert.equal(f.svg.paused, true); // detached roots are no longer retained
});
