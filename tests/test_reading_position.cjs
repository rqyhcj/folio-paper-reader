/* Run with: node tests/test_reading_position.cjs. No browser or model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const geometry = source.slice(source.indexOf('function cacheReadingTargets()'), source.indexOf('function setFontSize('));
const originalScroll = source.slice(source.indexOf('function updateOriginalPageFromScroll()'), source.indexOf('function openSearch()'));
const events = [];
let reads = 0;
let writes = 0;
let headingWrites = 0;
let pageControlUpdates = 0;

function element(text = '') {
  let value = text;
  const classes = new Set();
  return {
    dataset: {}, hidden: false,
    get textContent() { return value; },
    set textContent(next) { value = next; writes++; events.push('write'); },
    classList: {
      add(name) { classes.add(name); headingWrites++; events.push('write'); },
      remove(name) { classes.delete(name); headingWrites++; events.push('write'); },
      contains(name) { return classes.has(name); },
    },
    querySelector() { return { textContent: text }; },
    getBoundingClientRect() { reads++; events.push('read'); return { top: this.top || 0 }; },
  };
}

const region = element();
region.scrollTop = 0;
region.scrollHeight = 401000;
region.clientHeight = 800;
region.top = 50;
const pdfRegion = element();
pdfRegion.top = 50;
pdfRegion.children = [];
const nodes = {
  'reader-view': element(), 'reader-scroll': region, 'read-progress': element(),
  'reading-location': element(), 'original-pdf-pages': pdfRegion, 'original-page': element(),
};
const state = {
  paper: { page_count: 167, source_format: 'pdf' }, readMode: 'bilingual', originalPage: 1,
  blocks: new Map(), readingBlocks: [], outlineItems: [], outlineTargets: [], activeOutline: null,
};
const context = vm.createContext({
  state, $: (id) => nodes[id], document: { activeElement: null },
  isPdfPaper: (paper) => paper.source_format === 'pdf',
  syncOriginalPageControls: () => { pageControlUpdates++; },
});
vm.runInContext(geometry + originalScroll, context);
const run = (code) => vm.runInContext(code, context);
const resetMetrics = () => { reads = writes = headingWrites = 0; events.length = 0; };

// A long, formula-heavy paper still needs only log2(N) live layout probes.
// Elements remain live rather than storing stale positions after a translation,
// font change, image load, or opening retained translation details.
let heightShift = 0;
for (let index = 0; index < 5000; index++) {
  const block = element();
  block.dataset.paragraphId = `p-${index}`;
  block.dataset.page = String(Math.floor(index / 30) + 1);
  block.getBoundingClientRect = () => {
    reads++; events.push('read');
    return { top: 300 + index * 80 + (index >= 2000 ? heightShift : 0) - region.scrollTop };
  };
  state.blocks.set(block.dataset.paragraphId, block);
  if (index % 40 === 0) {
    const heading = element(`Section ${index}`);
    heading.dataset.paragraphId = block.dataset.paragraphId;
    heading.dataset.page = block.dataset.page;
    state.outlineItems.push(heading);
  }
}
run('cacheReadingTargets()');
assert.equal(state.readingBlocks.length, 5000);

function expectedIndex() {
  let current = -1;
  for (let index = 0; index < 5000; index++) {
    const top = 300 + index * 80 + (index >= 2000 ? heightShift : 0) - region.scrollTop;
    if (top <= 150) current = index;
    else break;
  }
  return current;
}

for (const position of [0, 149, 150, 151, 8567, 160300, 398800, 400900, 12000]) {
  region.scrollTop = position;
  resetMetrics();
  run('updateReadingPosition()');
  const index = expectedIndex();
  const page = index < 0 ? 1 : Math.floor(index / 30) + 1;
  const section = index < 0 ? 0 : Math.floor(index / 40) * 40;
  assert.equal(nodes['reading-location'].textContent, `Section ${section} · 第 ${page} 页`);
  assert.ok(reads <= 22, `5,000 paragraphs used ${reads} layout probes`);
  const firstWrite = events.indexOf('write');
  assert.ok(firstWrite < 0 || !events.slice(firstWrite).includes('read'), 'read geometry before changing layout');
  resetMetrics();
  run('updateReadingPosition()');
  assert.equal(writes, 0, 'scroll inside the same progress/location does not rewrite footer');
  assert.equal(headingWrites, 0, 'unchanged heading does not rewrite every outline class');
}

// Live positions change without rebuilding the element cache.
region.scrollTop = 180300;
heightShift = 8000;
run('updateReadingPosition()');
assert.equal(nodes['reading-location'].textContent, 'Section 2120 · 第 72 页');
heightShift = 0;
run('updateReadingPosition()');
assert.equal(nodes['reading-location'].textContent, 'Section 2240 · 第 76 页');

// Cache outline targets in actual DOM reading order, suppress missing figure
// label targets, and replace a heading element after a fresh outline render.
state.outlineItems.reverse();
const missing = element('Figure label');
missing.dataset.paragraphId = 'hidden-figure-label';
state.outlineItems.push(missing);
run('cacheReadingTargets()');
assert.equal(state.outlineTargets.length, 125);
assert.equal(state.outlineTargets[0].item.dataset.paragraphId, 'p-0');
region.scrollTop = 180300;
run('updateReadingPosition()');
assert.equal(nodes['reading-location'].textContent, 'Section 2240 · 第 76 页');
const oldHeading = state.activeOutline;
const replacement = element('Section 2240 updated');
replacement.dataset = { ...oldHeading.dataset };
state.outlineItems[state.outlineItems.indexOf(oldHeading)] = replacement;
run('cacheReadingTargets(); updateReadingPosition()');
assert.equal(oldHeading.classList.contains('active'), false);
assert.equal(replacement.classList.contains('active'), true);

// Non-PDF imports use headings without claiming fixed PDF page numbers.
state.paper.source_format = 'docx';
run('updateReadingPosition()');
assert.equal(nodes['reading-location'].textContent, 'Section 2240 updated');
state.outlineItems = [];
run('cacheReadingTargets(); updateReadingPosition()');
assert.equal(nodes['reading-location'].textContent, '正在阅读');

// Empty papers and scroll positions before the first block remain well-defined.
state.blocks.clear();
run('cacheReadingTargets(); updateReadingPosition()');
assert.equal(state.readingBlocks.length, 0);
assert.equal(run('lastPassedElement([], 100)'), null);
nodes['reader-view'].hidden = true;
resetMetrics();
run('updateReadingPosition()');
assert.equal(reads, 0);
nodes['reader-view'].hidden = false;

// Original PDF page tracking is logarithmic too; controls update only when the
// active page changes and never overwrite a page field that the user is editing.
state.paper.source_format = 'pdf';
state.readMode = 'pdf';
for (let page = 1; page <= 1000; page++) {
  const figure = element();
  figure.dataset.page = String(page);
  figure.top = page * 50 - 12000;
  pdfRegion.children.push(figure);
}
resetMetrics();
run('updateOriginalPageFromScroll()');
assert.equal(state.originalPage, 242);
assert.ok(reads <= 12);
assert.equal(pageControlUpdates, 1);
resetMetrics();
run('updateOriginalPageFromScroll()');
assert.equal(pageControlUpdates, 1);
assert.equal(writes, 0);
pdfRegion.children[242].top = 100;
context.document.activeElement = nodes['original-page'];
run('updateOriginalPageFromScroll()');
assert.equal(state.originalPage, 243);
assert.equal(pageControlUpdates, 1);

console.log('PASS: 5,000-paragraph scrolling uses at most 22 layout probes, reads before writes, preserves dynamic heights, outline/page tracking, and editable PDF controls.');
