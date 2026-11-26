// The page tests wait for the page, never for a fixed time: a busy computer makes a run slower, never different.
//   const { arm, settle } = require('./test_settle'); arm(browser);   // every new context counts its page's work
//   await settle(page);                                                // then: no request in flight, no animation running,
//                                                                      // no timer of its own due within 5 s, every picture
//                                                                      // loaded, the page unchanged for 300 ms
// A page that never settles (20 s) is not an error here: the check that follows says what it did not see.
const WATCH = `(() => {
  window.__kitLast = performance.now(); window.__kitInflight = 0;
  const seen = () => { window.__kitLast = performance.now(); };
  new MutationObserver(seen).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  const f = window.fetch;
  if (f) window.fetch = function () { window.__kitInflight++; return f.apply(this, arguments).finally(() => { window.__kitInflight--; seen(); }); };
  // a timer the page set for itself within 5 s (a save after typing, a fold that ends) is work still to come; longer ones (polls) are not
  window.__kitTimers = new Set();
  const st = window.setTimeout, ct = window.clearTimeout;
  window.setTimeout = function (fn, ms) {
    // the page's timer exactly as it asked (its extra arguments, a string body), only counted while it is due
    if (typeof fn !== 'function' || !(Number(ms) > 0 && Number(ms) <= 5000)) return st.apply(window, arguments);
    const args = Array.prototype.slice.call(arguments, 2);
    const id = st.call(window, function () { window.__kitTimers.delete(id); return fn.apply(window, args); }, ms);
    window.__kitTimers.add(id);
    return id;
  };
  window.clearTimeout = function (id) { window.__kitTimers.delete(id); return ct.call(window, id); };
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () { window.__kitInflight++; this.addEventListener('loadend', () => { window.__kitInflight--; seen(); }, { once: true }); return send.apply(this, arguments); };
})();`;

const HANG_MS = 300000;

function arm(browser) {
  const make = browser.newContext.bind(browser);
  // Playwright's own 30 s limits (open a page, find an element) become hang guards: 5 minutes, never a speed check
  browser.newContext = async (...a) => { const ctx = await make(...a); ctx.setDefaultTimeout(HANG_MS); ctx.setDefaultNavigationTimeout(HANG_MS); await ctx.addInitScript(WATCH); return ctx; };
  return browser;
}

// true once the page is quiet for `q` ms (the same rule for Playwright and for the kit's own driver, kit/claude/page_driver.js)
function QUIET_FN(q) {
  if (document.readyState !== 'complete' || window.__kitLast === undefined || window.__kitInflight > 0 || window.__kitTimers.size) return false;
  const running = (document.getAnimations ? document.getAnimations() : []).filter((a) => a.playState === 'running'
    && (a.effect && a.effect.getTiming ? a.effect.getTiming().iterations !== Infinity : true));
  if (running.length) return false;
  if ([...document.images].some((i) => i.loading !== 'lazy' && !i.complete)) return false;
  return performance.now() - window.__kitLast >= q;
}
const QUIET = '(' + QUIET_FN.toString() + ')';   // its source, for the kit's own driver (DevTools evaluates it, no page eval)


async function settle(p, quietMs = 300, timeout = 20000) {
  // a function, never a string: a string is eval'd in the page, and the board's own CSP refuses eval (a page error of the test's making)
  await p.waitForFunction(QUIET_FN, quietMs, { timeout, polling: 50 }).catch(() => {});
}

module.exports = { arm, settle, WATCH, QUIET, HANG_MS };
