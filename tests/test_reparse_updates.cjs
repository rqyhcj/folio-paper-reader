/* Synthetic reader state only: no stored papers, browser, credentials or API calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const clone = (value) => JSON.parse(JSON.stringify(value));
let writes = 0;
const nodes = new Map();
function element() {
  return new Proxy({
    dataset: {}, style: {}, children: [], hidden: false,
    classList: { toggle() { writes++; } },
    setAttribute() { writes++; }, removeAttribute() { writes++; },
    replaceChildren(...children) { writes++; this.children = children; },
  }, { set(target, key, value) {
    if (['textContent', 'innerHTML', 'hidden', 'title', 'href', 'target', 'disabled', 'value'].includes(key)) writes++;
    target[key] = value;
    return true;
  } });
}
const get = (id) => {
  if (!nodes.has(id)) nodes.set(id, element());
  return nodes.get(id);
};
const scroll = get('reader-scroll');
scroll.scrollTop = 1600;
scroll.getBoundingClientRect = () => ({ top: 100 });
let scrollCalls = [];
scroll.scrollTo = ({ top, behavior }) => { scrollCalls.push({ top, behavior }); scroll.scrollTop = top; };
function block(id, page, contentY) {
  const result = element();
  result.dataset = { paragraphId: id, page: String(page) };
  result.getBoundingClientRect = () => ({ top: 100 + contentY - scroll.scrollTop });
  return result;
}

const oldParagraphs = [
  { id: 'old-before', page: 2, bbox: [60, 700, 280, 740], text: 'Earlier prose.', contentY: 1000 },
  { id: 'old-current', page: 3, bbox: [60, 300, 280, 340], text: 'Current prose.', contentY: 1620 },
  { id: 'old-after', page: 3, bbox: [60, 450, 280, 490], text: 'Later prose.', contentY: 2000 },
];
const state = {
  paper: null, papers: [], blocks: new Map(), figures: new Map(),
  readingBlocks: [], outlineItems: [], outlineTargets: [], activeOutline: null,
  bilingual: true, readMode: 'bilingual', originalPage: 1,
  search: 'Current', hits: [], hitIndex: 0,
};
const base = {
  id: 'synthetic-paper', title: 'A generic paper', filename: 'sample.pdf', source_format: 'pdf',
  parse_version: 5, parse_status: 'updating', created_at: '2026-01-01', page_count: 5,
  paragraphs: oldParagraphs, paragraph_count: oldParagraphs.length, translated_count: 1,
  figures: [{ id: 'old-figure' }], outline: [{ paragraph_id: 'old-current', title: 'Generic section' }],
  progress: { total: 3, done: 1, failed: 1 }, status: 'paused', figure_status: 'completed',
};
const calls = {};
const animationFrames = [];
const sandbox = {
  state, $: get, icon: (name) => `<i>${name}</i>`,
  document: { querySelectorAll: () => [] },
  dateLabel: () => '2026.01.01',
  localStorage: { setItem() { calls.storage = (calls.storage || 0) + 1; } },
  showView() {}, switchSidebarTab() { calls.sidebar = (calls.sidebar || 0) + 1; },
  schedulePoll() {}, updateSummary() {},
  requestAnimationFrame(callback) { animationFrames.push(callback); },
  updateReadingPosition() { calls.position = (calls.position || 0) + 1; },
  clearMathObservations() {
    calls.clear = (calls.clear || 0) + 1;
    assert.ok(state.blocks.has('old-current'), 'release math observers while old blocks still exist');
  },
  renderParagraphs() {
    calls.paragraphs = (calls.paragraphs || 0) + 1;
    assert.equal(state.blocks.size, 0, 'reparse clears old paragraph IDs before rebuilding');
    for (const p of state.paper.paragraphs) state.blocks.set(p.id, block(p.id, p.page, p.contentY));
    state.readingBlocks = [...state.blocks.values()];
  },
  renderFigures() {
    calls.figures = (calls.figures || 0) + 1;
    assert.equal(state.figures.size, 0, 'reparse clears old artwork IDs before rebuilding');
    for (const f of state.paper.figures) state.figures.set(f.id, element());
  },
  renderOutline() {
    calls.outline = (calls.outline || 0) + 1;
    assert.equal(state.outlineItems.length, 0, 'stale outline nodes are released');
    assert.equal(state.outlineTargets.length, 0);
    assert.equal(state.activeOutline, null);
    state.outlineItems = state.paper.outline.map((item) => ({ ...item }));
  },
  renderTranslationStatus() { calls.status = (calls.status || 0) + 1; },
  renderFigureStatus() {},
  setReadingMode() { throw new Error('an in-place parser update must preserve the reading mode'); },
};
vm.createContext(sandbox);
vm.runInContext([
  section('function isDemo(', 'function dateLabel('),
  section('function captureReparsePosition(', 'function translatedText('),
  section('function lastPassedElement(', 'function setReadingText('),
].join('\n'), sandbox);

// Establish display signatures without treating the current paper as newly opened.
sandbox.renderParagraphs = () => {};
sandbox.renderFigures = () => {};
sandbox.renderOutline = () => {};
state.paper = clone(base);
sandbox.displayPaper(clone(base));
animationFrames.length = 0;
for (const p of oldParagraphs) state.blocks.set(p.id, block(p.id, p.page, p.contentY));
state.readingBlocks = [...state.blocks.values()];
state.figures.set('old-figure', element());
state.outlineItems = [{ paragraph_id: 'old-current' }];
state.outlineTargets = [{ block: state.blocks.get('old-current') }];
state.activeOutline = state.outlineItems[0];
state.outlineSignature = 'old outline';
get('document-search').value = 'Current';
get('search-bar').hidden = false;

// Replace only renderer stubs, preserving the real display/update logic.
for (const [name, operation] of Object.entries({
  renderParagraphs() {
    calls.paragraphs = (calls.paragraphs || 0) + 1;
    assert.equal(state.blocks.size, 0);
    for (const p of state.paper.paragraphs) state.blocks.set(p.id, block(p.id, p.page, p.contentY));
    state.readingBlocks = [...state.blocks.values()];
  },
  renderFigures() {
    calls.figures = (calls.figures || 0) + 1;
    assert.equal(state.figures.size, 0);
    for (const f of state.paper.figures) state.figures.set(f.id, element());
  },
  renderOutline() {
    calls.outline = (calls.outline || 0) + 1;
    assert.equal(state.outlineItems.length, 0);
    assert.equal(state.outlineTargets.length, 0);
    assert.equal(state.activeOutline, null);
    assert.equal(state.outlineSignature, '');
    state.outlineItems = state.paper.outline.map((item) => ({ ...item }));
  },
})) sandbox[name] = operation;

const updated = { ...clone(base), parse_version: 6, parse_status: 'completed',
  paragraphs: [
    { id: 'new-left', page: 3, bbox: [60, 302, 280, 345], text: 'Current prose.', contentY: 2500 },
    { id: 'new-right', page: 3, bbox: [320, 300, 550, 340], text: 'Other column.', contentY: 2800 },
  ], figures: [{ id: 'new-figure' }], outline: [{ paragraph_id: 'new-left', title: 'Generic section' }],
};
for (const key of Object.keys(calls)) delete calls[key];
sandbox.displayPaper(updated);
assert.equal(calls.clear, 1);
assert.equal(calls.paragraphs, 1);
assert.equal(calls.figures, 1);
assert.equal(calls.outline, 1);
assert.equal(calls.storage, undefined);
assert.equal(calls.sidebar, undefined);
assert.deepEqual([...state.blocks.keys()], ['new-left', 'new-right']);
assert.deepEqual([...state.figures.keys()], ['new-figure']);
assert.equal(state.search, 'Current');
assert.equal(get('document-search').value, 'Current');
assert.equal(get('search-bar').hidden, false);
assert.equal(scroll.scrollTop, 1600, 'the reader is not sent to the top while rebuilding');
while (animationFrames.length) animationFrames.shift()();
assert.deepEqual(scrollCalls, [{ top: 2480, behavior: 'instant' }],
  'new source coordinates select the same page and column, preserving the visible offset');

writes = 0;
for (const key of Object.keys(calls)) delete calls[key];
sandbox.displayPaper(clone(updated));
assert.equal(writes, 0, 'unchanged polling after the reparse still performs zero reader DOM writes');
assert.deepEqual(calls, {});
assert.equal(scrollCalls.length, 1);

const parsingAgain = { ...clone(updated), parse_status: 'pending' };
sandbox.displayPaper(parsingAgain);
assert.equal(calls.status, 1, 'a parse status change invalidates translation controls without reflowing the article');
assert.equal(calls.paragraphs, undefined);
assert.equal(calls.figures, undefined);

// Exercise the real banner and retry controls, including an independently busy retry.
vm.runInContext(section('function renderTranslationStatus()', 'function renderFigureStatus()'), sandbox);
const retry = element(); retry.disabled = false;
const inFlightRetry = element(); inFlightRetry.disabled = true;
sandbox.document.querySelectorAll = () => [retry, inFlightRetry];
state.paper = { ...clone(base), parse_status: 'updating' };
sandbox.renderTranslationStatus();
assert.equal(get('translation-banner').hidden, false);
assert.match(get('translation-detail').textContent, /已保存译文保留/);
assert.equal(get('translate-button').disabled, true);
assert.equal(get('retry-failed').hidden, true);
assert.equal(retry.disabled, true);
state.paper.parse_status = 'pending';
state.paper.status = 'translating';
sandbox.renderTranslationStatus();
assert.equal(get('translate-button').disabled, false, 'pending reparse must leave an active translation pause button usable');
assert.match(get('translate-button').innerHTML, /暂停翻译/);
state.paper.parse_status = 'completed';
state.paper.status = 'paused';
sandbox.renderTranslationStatus();
assert.equal(retry.disabled, false, 'only retries blocked by parsing become available again');
assert.equal(inFlightRetry.disabled, true, 'a parsing update does not re-enable an independently in-flight request');
assert.equal(get('translate-button').disabled, false);
state.paper.parse_status = 'error';
state.paper.parse_error = '<script>untrusted diagnostic</script>';
sandbox.renderTranslationStatus();
assert.equal(get('translation-banner').hidden, false);
assert.equal(get('translation-detail').title, state.paper.parse_error);
assert.match(get('translation-detail').textContent, /可继续阅读或打开原版核对/);

// Polling follows local parsing even when translation and image extraction are idle.
const timers = [];
sandbox.clearTimeout = () => {};
sandbox.setTimeout = (callback, delay) => { timers.push({ callback, delay }); return timers.length; };
vm.runInContext(section('function schedulePoll()', 'async function translatePaper('), sandbox);
for (const parseStatus of ['pending', 'updating']) {
  state.paper = { ...clone(base), parse_status: parseStatus, status: 'paused' };
  const before = timers.length;
  sandbox.schedulePoll();
  assert.equal(timers.length, before + 1);
}
for (const parseStatus of ['completed', 'error']) {
  state.paper = { ...clone(base), parse_status: parseStatus, status: 'paused' };
  const before = timers.length;
  sandbox.schedulePoll();
  assert.equal(timers.length, before);
}

// Guards block both bulk and paragraph retries while allowing the pause operation.
vm.runInContext(section('async function translatePaper(', 'async function deletePaper('), sandbox);
const requests = [];
sandbox.api = async (url, options) => {
  requests.push({ url, options });
  return { ...state.paper, status: 'paused' };
};
sandbox.isModelConfigured = () => { throw new Error('blocked actions should not open model configuration'); };
sandbox.openSettings = () => { throw new Error('blocked actions should not open settings'); };
sandbox.settingsMessage = () => {};
sandbox.toast = () => {};
sandbox.displayPaper = (paper) => { state.paper = paper; };
(async () => {
  state.paper = { ...clone(base), parse_status: 'updating', status: 'paused' };
  await sandbox.translatePaper();
  await sandbox.translatePaper(true);
  await sandbox.retryParagraph('old-current', retry);
  assert.equal(requests.length, 0);
  state.paper.parse_status = 'pending';
  state.paper.status = 'translating';
  await sandbox.translatePaper();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/papers/synthetic-paper/pause');
  assert.equal(requests[0].options.method, 'POST');
  console.log('PASS: local parser updates preserve reading position and saved translations, replace stale IDs, poll without unchanged DOM writes, and gate translation controls while allowing pause.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
