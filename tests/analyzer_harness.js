// Runs the Lap Analyzer's own script (suite/web/analyzer/index.html) in Node, with a
// do-nothing stand-in for the page, so its analysis functions can be tested on files.
// Usage: node analyzer_harness.js <index.html> <test.js>
// The test script runs in the same context: it can call ibt(), sessionStats(), ...
// and must print one JSON line with its results.
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const any = () => new Proxy(function () {}, {
  get: (t, k) => k === Symbol.toPrimitive ? () => '' : k === 'length' ? 0 : k === Symbol.iterator ? function* () {} :
    (k === 'value' || k === 'textContent' || k === 'innerHTML') ? '' : any(),
  set: () => true, apply: () => any(), construct: () => any(),
});
const store = {};
const ctx = {
  console, TextDecoder, Uint8Array, Int32Array, Float64Array, Float32Array, DataView, ArrayBuffer, Math, JSON, Date,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0, requestAnimationFrame: () => 0,
  localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = String(v) }, removeItem: k => { delete store[k] } },
  document: any(), location: { pathname: '/analyzer/', search: '', host: '127.0.0.1', protocol: 'http:' },
  navigator: any(), devicePixelRatio: 1, matchMedia: () => ({ matches: false, addEventListener() {} }),
  WebSocket: function () { return any() }, fetch: () => new Promise(() => {}), addEventListener() {}, getComputedStyle: () => any(),
  ResizeObserver: function () { return { observe() {} } }, performance: { now: () => 0 },
};
ctx.readBytes = p => { const b = fs.readFileSync(p); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) };
ctx.ARGS = process.argv.slice(4);
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
// The page's script declares with const/let at top level; run it and the test in ONE
// script so the test sees those bindings.
const test = fs.readFileSync(process.argv[3], 'utf8');
// The test runs in its own block, so its names cannot clash with the page's.
vm.runInContext(scripts.join('\n;\n') + '\n;\n{\n' + test + '\n}\n', ctx, { filename: 'analyzer' });
