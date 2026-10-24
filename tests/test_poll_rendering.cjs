/* No browser, stored papers, model calls or network access are used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static/app.js'), 'utf8');
let mutations = 0;
function element(tag = 'div', className = '', text = '') {
  const value = { tag, className, textContent: text, children: [], dataset: {}, attributes: {}, hidden: false,
    classList: { toggle() { mutations++; } }, addEventListener() {},
    setAttribute(key, next) { mutations++; this.attributes[key] = next; },
    removeAttribute(key) { mutations++; delete this.attributes[key]; },
    append(...items) { for (const item of items) this.insert(item, this.children.length); },
    insert(item, index) {
      if (item.parentElement) {
        const previous = item.parentElement;
        const priorIndex = previous.children.indexOf(item);
        previous.children.splice(priorIndex, 1);
        if (previous === this && priorIndex < index) index--;
      }
      mutations++;
      this.children.splice(index, 0, item);
      item.parentElement = this;
    },
    replaceChildren(...items) {
      mutations++;
      for (const item of this.children) item.parentElement = null;
      this.children = [];
      this.append(...items);
    },
    before(item) { this.parentElement.insert(item, this.parentElement.children.indexOf(this)); },
    after(item) { this.parentElement.insert(item, this.parentElement.children.indexOf(this) + 1); },
    remove() {
      if (!this.parentElement) return;
      mutations++;
      this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
      this.parentElement = null;
    },
    get previousElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) - 1] || null; },
    get nextElementSibling() { return this.parentElement?.children[this.parentElement.children.indexOf(this) + 1] || null; },
  };
  return new Proxy(value, { set(target, key, next) {
    if (['textContent', 'innerHTML', 'hidden', 'title', 'target', 'href', 'disabled', 'value'].includes(key)) mutations++;
    target[key] = next;
    return true;
  } });
}

const nodes = new Map();
const get = (id) => {
  if (!nodes.has(id)) nodes.set(id, element());
  return nodes.get(id);
};
const calls = {};
const state = { paper: null, papers: [], readMode: 'bilingual', bilingual: true, search: '', originalPage: 1 };
const sandbox = {
  state, $: get, node: element, icon: () => '',
  document: { createDocumentFragment: () => element('fragment') },
  localStorage: { setItem() { calls.storage = (calls.storage || 0) + 1; }, removeItem() {} },
  dateLabel: () => '2026.10.24', showView() { calls.view = (calls.view || 0) + 1; },
  schedulePoll() {}, updateReadingPosition() {}, requestAnimationFrame() {},
  clearMathObservations() { calls.clearMath = (calls.clearMath || 0) + 1; },
  clearTimeout() {}, closeSidebar() {}, switchSidebarTab() {},
};
for (const name of ['renderParagraphs', 'renderFigures', 'renderOutline', 'renderTranslationStatus', 'renderFigureStatus']) {
  sandbox[name] = () => { calls[name] = (calls[name] || 0) + 1; };
}
sandbox.setReadingMode = (mode) => {
  calls.mode = (calls.mode || 0) + 1;
  state.readMode = mode;
  state.bilingual = mode === 'bilingual';
  sandbox.renderTranslationStatus();
};
vm.createContext(sandbox);
vm.runInContext([
  source.slice(source.indexOf('function isDemo('), source.indexOf('function dateLabel(')),
  source.slice(source.indexOf('function renderLibrary('), source.indexOf('function showView(')),
  source.slice(source.indexOf('function displayPaper('), source.indexOf('function translatedText(')),
].join('\n'), sandbox);
const clone = (value) => JSON.parse(JSON.stringify(value));
const paper = {
  id: 'large-paper', title: 'A large readable paper', filename: 'large.pdf', page_count: 31,
  created_at: '2026-10-24', paragraphs: Array.from({ length: 949 }, (_, index) => ({
    id: `p-${index + 1}`, text: `Paragraph ${index + 1}`, page: Math.floor(index / 31) + 1,
    kind: index % 50 === 0 ? 'heading' : 'paragraph', translation: null,
  })), paragraph_count: 949, translated_count: 0,
  progress: { total: 949, done: 0, failed: 0 }, status: 'translating',
  figures: [], figure_status: 'extracting', figure_progress: { done: 1, total: 31 },
  translation_history: [{ paragraphs: [{ translation: 'An old private translation' }] }],
};
const reset = () => { for (const key of Object.keys(calls)) delete calls[key]; mutations = 0; };
sandbox.displayPaper(clone(paper));
assert.equal(calls.renderParagraphs, 1);
assert.equal(calls.mode, 1);
assert.equal(state.papers[0].translation_history, undefined, 'the library retains summaries, not full translation histories');
assert.equal(state.papers[0].paragraphs, undefined);

reset();
sandbox.displayPaper(clone(paper));
assert.equal(mutations, 0, 'a fresh network object with identical contents must not rewrite reader or library DOM');
assert.deepEqual(calls, {}, 'identical polls do not rebuild paragraphs, figures, outline, modes or status');

const artwork = clone(paper);
artwork.figure_progress.done = 2;
reset();
sandbox.displayPaper(artwork);
assert.equal(calls.renderFigureStatus, 1);
assert.equal(calls.renderParagraphs, undefined, 'artwork counters alone do not redraw article content');
assert.equal(calls.mode, undefined);
assert.equal(mutations, 0);

const translated = clone(artwork);
translated.paragraphs[100].translation = 'A newly translated paragraph';
translated.progress.done = translated.translated_count = 1;
reset();
sandbox.displayPaper(translated);
assert.equal(calls.renderParagraphs, 1);
assert.equal(calls.renderTranslationStatus, 1);
assert.equal(calls.mode, undefined, 'new translations preserve the current reading mode instead of resetting mode DOM');
assert.equal(calls.storage, undefined, 'polling does not rewrite the last-opened paper preference');
assert.equal(get('paper-translated').textContent, '已翻译 1 段');

reset();
sandbox.displayPaper(clone(translated));
assert.equal(mutations, 0);
assert.deepEqual(calls, {});

const changedOutline = clone(translated);
changedOutline.outline = [{ title: 'Abstract', paragraph_id: 'p-1', page: 1 }];
reset();
sandbox.displayPaper(changedOutline);
assert.equal(calls.renderOutline, 1, 'outline changes can arrive independently of paragraph text');
assert.equal(calls.renderParagraphs, undefined);
assert.equal(calls.renderFigures, undefined);
assert.equal(mutations, 0);

// Application lifecycle cleanup runs before replacing old paper content, even
// when the next paper contains no formulas and creates no new observations.
const oldContent = element();
get('paragraphs').append(oldContent);
state.blocks = new Map([['old', oldContent]]);
state.figures = new Map();
sandbox.clearMathObservations = () => {
  calls.clearMath = (calls.clearMath || 0) + 1;
  assert.equal(get('paragraphs').children[0], oldContent, 'observations are cleared before article DOM replacement');
  assert.equal(state.blocks.size, 1, 'cleanup still has access to the previous paper references');
};
reset();
sandbox.displayPaper(clone(changedOutline), true);
assert.equal(calls.clearMath, 1, 'opening another paper releases old math observations');
assert.equal(state.blocks.size, 0);
assert.equal(get('paragraphs').children.length, 0);

get('paragraphs').append(oldContent);
state.blocks.set('old', oldContent);
vm.runInContext(source.slice(source.indexOf('function showWelcome()'), source.indexOf('function loading(')), sandbox);
reset();
sandbox.showWelcome();
assert.equal(calls.clearMath, 1, 'returning to the welcome screen releases observations');
assert.equal(get('paragraphs').children.length, 0, 'the welcome screen does not retain the old article DOM');
assert.equal(state.paper, null);

// Pruning also runs after an empty/plain article refresh, independently of
// whether any renderMath call was needed during that refresh.
state.paper = { paragraphs: [] };
state.readingBlocks = [];
state.search = '';
sandbox.getFigureTextIds = () => new Set();
sandbox.pruneMathObservations = () => { calls.pruneMath = (calls.pruneMath || 0) + 1; };
vm.runInContext(source.slice(source.indexOf('function renderParagraphs('), source.indexOf('function renderEquationSource(')), sandbox);
reset();
sandbox.renderParagraphs();
assert.equal(calls.pruneMath, 1, 'plain-text and empty updates prune detached math targets');

// Figures sharing the same before-anchor must remain in order without being
// detached/reinserted every time a translation update asks for figure refresh.
const container = get('paragraphs');
container.replaceChildren();
const first = element(); first.dataset.paragraphId = 'p-1';
const second = element(); second.dataset.paragraphId = 'p-2';
container.append(first, second);
state.blocks = new Map([['p-1', first], ['p-2', second]]);
state.figures = new Map();
state.paper = {
  id: 'illustrated', source_format: 'pdf', paragraphs: [
    { id: 'p-1', text: 'Body before figures', page: 1 }, { id: 'p-2', text: 'Body after figures', page: 1 },
  ], figures: [
    { id: 'f-1', page: 1, before_paragraph_id: 'p-2' },
    { id: 'f-2', page: 1, before_paragraph_id: 'p-2' },
    { id: 'f-3', page: 1, after_paragraph_id: 'p-2' },
  ],
};
sandbox.figureURL = () => '';
vm.runInContext(source.slice(source.indexOf('function renderFigures()'), source.indexOf('function getFigureTextIds()')), sandbox);
sandbox.renderFigures();
const order = () => container.children.map((item) => item.dataset.paragraphId || item.dataset.figureId);
assert.deepEqual(order(), ['p-1', 'f-1', 'f-2', 'p-2', 'f-3']);
mutations = 0;
sandbox.renderFigures();
assert.equal(mutations, 0, 'unchanged same-anchor figures must not be removed and inserted in each poll');
assert.deepEqual(order(), ['p-1', 'f-1', 'f-2', 'p-2', 'f-3']);
state.paper.figures.splice(1, 1);
sandbox.renderFigures();
assert.deepEqual(order(), ['p-1', 'f-1', 'p-2', 'f-3']);
mutations = 0;
sandbox.renderFigures();
assert.equal(mutations, 0, 'the remaining figures settle without repeated moves after removal');
console.log('PASS: identical polls make zero reader/library DOM writes; translation and artwork changes update their own regions; shared-anchor figures remain stable.');
