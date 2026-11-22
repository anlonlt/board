// Pull renderBody and its helpers out of the page and run them in node with a fake state.
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const a = src.indexOf('  function esc(s)'), b = src.indexOf('  window._kitRenderBody = renderBody;');
const code = src.slice(a, b);
const store = {};
global.sessionStorage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
let clickHandler = null;
global.document = { addEventListener: (t, f) => { clickHandler = f; } };
global.window = {};
const state = { special: { _prefs: { sections: { 'numbers': { score: -2 }, 'design': { score: 1 } } } }, isOwner: true };
const writes = [];
const db = { doc: p => ({ set: d => { writes.push([p, d]); return Promise.resolve(); } }) };
const str = v => (v == null ? '' : String(v));
const f = new Function('state', 'db', 'str', code + '; return {renderBody, secRecord, secKey};');
const m = f(state, db, str);
const body = [
  'For you: the parser is ready.',
  '',
  '## Verdict',
  'It reads 48 of 50 files.',
  '### Numbers',
  '| a | b |', '|---|---|', '| 1 | 2 |',
  '## What I checked',
  '- reran it',
  '## Design {fold}',
  'long text',
  '## Plan {fold}',
  'x',
  '# Title',
  'after',
].join('\n');
const html = m.renderBody(body);
const out = [];
const re = /<details class="md-sec" data-sec="([^"]*)" data-inst="[^"]*"( open)?>/g;
let x; while ((x = re.exec(html))) out.push(x[1] + ':' + (x[2] ? 'open' : 'folded'));
console.log(out.join(' | '));
console.log('lead before first section:', html.indexOf('For you') < html.indexOf('<details'));
console.log('balanced:', (html.match(/<details/g) || []).length === (html.match(/<\/details>/g) || []).length);
console.log('h1 outside sections:', /<\/details><h1>Title<\/h1><p>after<\/p>$/.test(html));
// two folds of "Verdict" by the owner make it folded everywhere
m.secRecord('verdict', 'i1', false); m.secRecord('verdict', 'i2', false);
console.log('learned verdict score:', state.special._prefs.sections.verdict.score, 'writes:', writes.length, writes[0][0]);
console.log('verdict now:', /data-sec="verdict" data-inst="[^"]*">/.test(m.renderBody('## Verdict\nnew post')) ? 'folded' : 'open');
state.isOwner = false; m.secRecord('plan', 'i3', true);
console.log('viewer does not teach:', !state.special._prefs.sections.plan);
