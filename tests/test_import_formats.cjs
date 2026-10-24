/* Run with: node tests/test_import_formats.cjs. No files imported or APIs called. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const app = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const elements = new Map();
const get = (id) => {
  if (!elements.has(id)) elements.set(id, {
    hidden: false, disabled: false, textContent: '', value: '', attributes: {},
    classList: { toggle() {} }, replaceChildren() {},
    setAttribute(key, value) { this.attributes[key] = String(value); },
    removeAttribute(key) { delete this.attributes[key]; if (key === 'target') delete this.target; },
  });
  return elements.get(id);
};
const state = { paper: null, readMode: 'pdf', bilingual: false, search: '', originalPage: 1, originalPaperId: null };
const observed = { originalRenders: 0, summaries: [], toasts: [], requests: [] };
class FormDataStub { constructor() { this.values = []; } append(...value) { this.values.push(value); } }
const sandbox = {
  state, $: get, observed, FormData: FormDataStub,
  localStorage: { setItem() {} }, dateLabel: () => '2026.10.24', showView() {},
  renderParagraphs() {}, renderFigures() {}, renderOutline() {}, renderTranslationStatus() {}, renderFigureStatus() {},
  renderOriginalPages() { observed.originalRenders++; return false; }, setOriginalPage() {}, updateReadingPosition() {},
  updateSummary(paper) { observed.summaries.push(paper.id); }, schedulePoll() {}, requestAnimationFrame() {},
  clearTimeout() {}, closeSidebar() {}, closeSearch() {},
  toast(...value) { observed.toasts.push(value); },
  async api(...value) { observed.requests.push(value); return { id: 'imported', filename: value[1].body.values[0][1].name, source_format: 'docx', paragraphs: [] }; },
};
vm.createContext(sandbox);
vm.runInContext([
  app.slice(app.indexOf('function isDemo('), app.indexOf('function dateLabel(')),
  app.slice(app.indexOf('function displayPaper('), app.indexOf('function translatedText(')),
  app.slice(app.indexOf('function setReadingMode('), app.indexOf('function renderOriginalPages(')),
  app.slice(app.indexOf('async function importPaper('), app.indexOf('async function loadDemo(')),
].join('\n'), sandbox);

for (const extension of ['pdf', 'DOCX', 'md', 'markdown', 'txt', 'html', 'htm']) {
  assert.equal(sandbox.importFileError({ name: `study.${extension}`, size: 100 }), '', extension);
  assert.ok(html.match(/<input[^>]+id="pdf-input"[^>]+accept="([^"]+)"/)[1].includes(`.${extension.toLowerCase()}`));
}
assert.match(sandbox.importFileError({ name: 'study.exe', size: 1 }), /支持/);
assert.match(sandbox.importFileError({ name: 'study.pdf.exe', size: 1 }), /支持/);
assert.match(sandbox.importFileError({ name: 'study.docx', size: 50 * 1024 * 1024 + 1 }), /50 MB/);
assert.equal(sandbox.sourceFormat({ filename: 'legacy.pdf' }), 'pdf');
assert.equal(sandbox.sourceFormat({ filename: 'constructor' }), 'pdf');
assert.equal(sandbox.sourceFormat({ filename: 'unexpected.extension' }), 'pdf');
assert.equal(sandbox.sourceFormat({ filename: 'notes.MD' }), 'markdown');
assert.equal(sandbox.sourceFormat({ source_format: 'html', filename: 'misleading.pdf' }), 'html');

for (const format of ['docx', 'markdown', 'txt', 'html']) {
  state.readMode = 'pdf';
  sandbox.displayPaper({ id: format, source_format: format, filename: `notes.${format}`, page_count: 1, paragraphs: [], warnings: ['外链图片未自动下载。'] });
  assert.equal(state.readMode, 'bilingual', `${format} must leave the PDF-only reader`);
  assert.equal(get('view-pdf').hidden, true);
  assert.equal(get('open-original').title, '下载源文件');
  assert.equal(get('open-original').attributes.download, `notes.${format}`);
  assert.equal(get('open-original').target, undefined);
  assert.equal(get('paper-pages').textContent.includes('页'), false);
  assert.equal(get('paper-notice').textContent, '外链图片未自动下载。');
  assert.equal(get('paper-notice').hidden, false);
  sandbox.setReadingMode('pdf');
  assert.equal(state.readMode, 'bilingual', 'direct requests cannot enter unsupported PDF preview');
}
assert.equal(observed.originalRenders, 0);
state.readMode = 'pdf';
sandbox.displayPaper({ id: 'legacy', filename: 'legacy.pdf', page_count: 31, paragraphs: [] });
assert.equal(state.readMode, 'pdf');
assert.equal(get('view-pdf').hidden, false);
assert.equal(get('open-original').title, '查看原始 PDF');
assert.equal(get('open-original').target, '_blank');
assert.equal(get('open-original').attributes.download, undefined);
assert.equal(get('paper-pages').textContent, '31 页');
assert.equal(observed.originalRenders, 1);
sandbox.displayPaper({ id: 'demo', is_demo: true, paragraphs: [] });
assert.equal(get('open-original').hidden, true);
assert.equal(get('view-pdf').disabled, true);
assert.equal(state.readMode, 'bilingual');

(async () => {
  await sandbox.importPaper({ name: 'study.exe', size: 1 });
  await sandbox.importPaper({ name: 'study.docx', size: 50 * 1024 * 1024 + 1 });
  assert.equal(observed.requests.length, 0, 'invalid imports never reach the API');
  // Keep actual import routing, but stub the full reader after the format-specific UI checks above.
  sandbox.displayPaper = (paper) => { observed.imported = paper; };
  state.loadToken = 0;
  await sandbox.importPaper({ name: 'study.DOCX', size: 100 });
  assert.equal(observed.requests.length, 1);
  assert.equal(observed.requests[0][0], '/papers');
  assert.equal(observed.requests[0][1].body.values[0][1].name, 'study.DOCX');
  assert.equal(observed.imported.source_format, 'docx');
  assert.equal(state.importing, false);
  assert.equal(get('import-status').hidden, true);
  console.log('PASS: multi-format validation, upload routing, safe source downloads, metadata, warnings, PDF-mode fallback and legacy PDF/demo behavior.');
})().catch((error) => { console.error(error); process.exitCode = 1; });
