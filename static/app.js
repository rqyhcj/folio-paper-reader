import { initAppearance } from './appearance.js';
import { initLayout } from './layout.js';
import { normalizeMathText, renderMath, splitMathText, clearMathObservations, pruneMathObservations } from './math.js?v=viewport-scroll-7';

const $ = (id) => document.getElementById(id);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const state = {
  papers: [], paper: null, settings: null, providers: [], poll: null, loading: false, importing: false,
  loadToken: 0, keyDirty: false, search: '', hits: [], hitIndex: -1,
  fontSize: Number(localStorage.getItem('folio.fontSize')) || 18,
  bilingual: true, readMode: 'bilingual', originalPage: 1, originalPaperId: null, originalScrollFrame: null,
  blocks: new Map(), figures: new Map(), outlineSignature: '', scrollFrame: null,
  readingBlocks: [], outlineItems: [], outlineTargets: [], activeOutline: null,
  aiDialogPaperId: null, aiRequestPending: false,
  chatPaperId: null, chatMessages: [], chatPending: false,
};

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function toast(message, error = false, action = null) {
  const el = node('div', `toast${error ? ' error' : ''}`, message);
  if (action) {
    const button = node('button', 'toast-action', action.label);
    button.addEventListener('click', () => { action.run(); el.remove(); });
    el.append(button);
  }
  $('toasts').append(el);
  setTimeout(() => el.remove(), action ? 9000 : error ? 6500 : 4000);
}

async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(`/api${path}`, { ...options, headers });
  } catch {
    throw new Error('暂时无法连接本地服务，请确认程序仍在运行。');
  }
  const raw = await response.text();
  let result;
  try { result = raw ? JSON.parse(raw) : {}; } catch { result = {}; }
  if (!response.ok) {
    const detail = result.detail || result.error || result.message;
    const message = typeof detail === 'string' ? detail : (Array.isArray(detail) ? detail.map((e) => e.msg).join('；') : null);
    throw new Error(message || `请求失败（${response.status}），请稍后重试。`);
  }
  return result;
}

function isDemo(paper) {
  return paper?.is_demo || /(?:^demo|示例文稿|folio.demo)/i.test(`${paper?.id || ''} ${paper?.filename || ''}`);
}

function sourceFormat(paper) {
  const supplied = String(paper?.source_format || '').toLowerCase();
  if (['pdf', 'docx', 'markdown', 'txt', 'html'].includes(supplied)) return supplied;
  const extension = String(paper?.filename || '').toLowerCase().split('.').pop();
  return ({ __proto__: null, docx: 'docx', md: 'markdown', markdown: 'markdown', txt: 'txt', html: 'html', htm: 'html' })[extension] || 'pdf';
}

function isPdfPaper(paper) { return sourceFormat(paper) === 'pdf'; }

function documentMeta(paper) {
  if (isPdfPaper(paper)) return `${paper?.page_count || 0} 页`;
  return ({ docx: 'DOCX 文档', markdown: 'Markdown 文档', txt: 'TXT 文本', html: 'HTML 文档' })[sourceFormat(paper)];
}

function importFileError(file) {
  if (!/\.(?:pdf|docx|md|markdown|txt|html|htm)$/i.test(file?.name || '')) return '支持 PDF、DOCX、Markdown、TXT 和 HTML 文件，请选择其中一种格式。';
  if (file.size > 50 * 1024 * 1024) return '文件不能超过 50 MB，请选择较小的文件。';
  return '';
}

function dateLabel(date) {
  const parsed = new Date(date || Date.now());
  return Number.isNaN(parsed.getTime()) ? '' : parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).replaceAll('/', '.');
}

function closeSidebar() {
  $('sidebar').classList.remove('open');
  $('sidebar-backdrop').hidden = true;
}

function switchSidebarTab(tab) {
  const library = tab === 'library';
  $('tab-library').classList.toggle('active', library);
  $('tab-outline').classList.toggle('active', !library);
  $('tab-library').setAttribute('aria-selected', String(library));
  $('tab-outline').setAttribute('aria-selected', String(!library));
  $('library-panel').hidden = !library;
  $('outline-panel').hidden = library;
}

function renderLibrary() {
  const signature = JSON.stringify([state.paper?.id, state.papers.map((paper) => [
    paper.id, paper.title, paper.filename, documentMeta(paper), paper.translated_count,
    paper.progress?.total, paper.paragraph_count, paper.status,
  ])]);
  if (state.librarySignature === signature) return;
  state.librarySignature = signature;
  $('library-count').textContent = state.papers.length;
  $('library-empty').hidden = state.papers.length > 0;
  const fragment = document.createDocumentFragment();
  for (const paper of state.papers) {
    const item = node('div', `library-item${paper.id === state.paper?.id ? ' active' : ''}`);
    item.tabIndex = 0;
    item.setAttribute('role', 'button');
    item.setAttribute('aria-label', `阅读 ${paper.title || paper.filename}`);
    item.innerHTML = icon('file');
    const content = node('div', 'library-item-content');
    content.append(node('div', 'library-item-title', paper.title || paper.filename || '未命名论文'));
    const meta = node('div', 'library-item-meta');
    meta.append(node('span', '', documentMeta(paper)));
    const done = paper.translated_count || 0;
    const total = paper.progress?.total ?? paper.paragraph_count ?? 0;
    const label = paper.status === 'completed' || done >= total && total > 0 ? '已完成' : done > 0 ? `已译 ${Math.min(100, Math.round(done / Math.max(1, total) * 100))}%` : '待翻译';
    meta.append(node('span', '', label));
    content.append(meta);
    item.append(content);
    const remove = node('button', 'delete-paper');
    remove.innerHTML = icon('trash');
    remove.title = '移除论文';
    remove.setAttribute('aria-label', `移除 ${paper.title || paper.filename}`);
    remove.addEventListener('click', (e) => { e.stopPropagation(); deletePaper(paper); });
    item.append(remove);
    item.addEventListener('click', () => loadPaper(paper.id));
    item.addEventListener('keydown', (e) => {
      if (e.target === item && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); loadPaper(paper.id); }
    });
    fragment.append(item);
  }
  $('paper-list').replaceChildren(fragment);
}

function updateSummary(paper) {
  // Keep the library small: full translation history and artwork belong only
  // to the open paper, not to a second retained copy in every library item.
  const summary = Object.fromEntries([
    'id', 'title', 'filename', 'source_format', 'page_count', 'paragraph_count',
    'translated_count', 'progress', 'created_at', 'status', 'is_demo',
  ].map((key) => [key, paper[key]]));
  const existing = state.papers.findIndex((p) => p.id === paper.id);
  if (existing >= 0) state.papers[existing] = summary;
  else state.papers.unshift(summary);
  renderLibrary();
}

function showView(name) {
  $('welcome-view').hidden = name !== 'welcome';
  $('reader-view').hidden = name !== 'reader';
  $('loading-view').hidden = name !== 'loading';
}

function showWelcome() {
  clearMathObservations();
  clearTimeout(state.poll);
  state.loadToken++;
  state.paper = null;
  state.blocks.clear();
  state.figures.clear();
  state.outlineSignature = '';
  state.readingBlocks = [];
  state.outlineItems = [];
  state.outlineTargets = [];
  state.activeOutline = null;
  state.loading = false;
  $('breadcrumb-title').textContent = '开始探索';
  $('paragraphs').replaceChildren();
  $('outline-list').replaceChildren();
  $('outline-count').textContent = '';
  $('outline-empty').hidden = false;
  showView('welcome');
  $('original-pdf-pages').replaceChildren();
  state.originalPaperId = null;
  switchSidebarTab('library');
  renderLibrary();
  localStorage.removeItem('folio.lastPaper');
  closeSidebar();
}

function loading(title, detail) {
  state.loading = true;
  $('loading-title').textContent = title;
  $('loading-detail').textContent = detail;
  showView('loading');
}

async function loadPaper(id) {
  if (state.loading) return;
  if (state.paper?.id === id) { showView('reader'); switchSidebarTab('outline'); closeSidebar(); return; }
  clearTimeout(state.poll);
  const token = ++state.loadToken;
  loading('正在打开论文', '调出原文与译文，接着上一次的思考。');
  closeSidebar();
  try {
    const paper = await api(`/papers/${encodeURIComponent(id)}`);
    if (token !== state.loadToken) return;
    displayPaper(paper, true);
  } catch (error) {
    if (token !== state.loadToken) return;
    state.loading = false;
    showView(state.paper ? 'reader' : 'welcome');
    toast(error.message, true);
    schedulePoll();
  }
}

async function importPaper(file) {
  if (!file || state.importing) return;
  const invalid = importFileError(file);
  if (invalid) { toast(invalid, true); return; }
  // Import is independent of navigation: keep the current article usable and polling.
  const token = ++state.loadToken;
  // A previously requested article must not replace the newly imported one later.
  if (state.loading) {
    state.loading = false;
    showView(state.paper ? 'reader' : 'welcome');
    schedulePoll();
  }
  state.importing = true;
  for (const id of ['sidebar-import', 'welcome-import']) $(id).disabled = true;
  $('drop-zone').setAttribute('aria-disabled', 'true');
  $('import-status').hidden = false;
  $('import-status-text').textContent = `正在导入「${file.name}」· ${/\.pdf$/i.test(file.name) ? '优先提取正文，插图随后在后台整理' : '正在提取正文、标题与内嵌插图'}`;
  closeSidebar();
  const form = new FormData();
  form.append('file', file);
  try {
    const paper = await api('/papers', { method: 'POST', body: form });
    updateSummary(paper);
    if (token === state.loadToken) {
      displayPaper(paper, true);
      toast('正文已就绪，可以先读。点击「全文翻译」后，译文会逐段显示。');
    } else {
      toast(`「${paper.title || file.name}」已加入文献库。`, false, { label: '打开论文', run: () => loadPaper(paper.id) });
    }
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.importing = false;
    $('import-status').hidden = true;
    for (const id of ['sidebar-import', 'welcome-import']) $(id).disabled = false;
    $('drop-zone').setAttribute('aria-disabled', 'false');
    $('pdf-input').value = '';
  }
}

async function loadDemo() {
  if (state.loading) return;
  const token = ++state.loadToken;
  loading('为你准备一段阅读时光', '正在打开示例文稿，体验双语阅读与目录导航。');
  try {
    const paper = await api('/demo');
    if (token !== state.loadToken) return;
    paper.is_demo = true;
    displayPaper(paper, true);
    toast('这是一篇用于体验排版的示例文稿。');
  } catch (error) {
    state.loading = false;
    showView(state.paper ? 'reader' : 'welcome');
    toast(error.message, true);
  }
}

function captureReparsePosition() {
  if (state.readMode === 'pdf') return null;
  const scroll = $('reader-scroll');
  const top = scroll.getBoundingClientRect().top;
  const blocks = state.readingBlocks?.length ? state.readingBlocks : [...(state.blocks?.values() || [])];
  const block = lastPassedElement(blocks, top + 100) || blocks[0];
  const paragraph = state.paper?.paragraphs?.find((item) => String(item.id) === block?.dataset.paragraphId);
  return { page: paragraph?.page || Number(block?.dataset.page) || 1, bbox: paragraph?.bbox,
    offset: block ? block.getBoundingClientRect().top - top : 0, scrollTop: scroll.scrollTop };
}

function restoreReparsePosition(position) {
  if (!position || state.readMode === 'pdf') return;
  const candidates = (state.paper?.paragraphs || []).filter((item) => Number(item.page || 1) === Number(position.page));
  const target = candidates.reduce((closest, paragraph) => {
    if (!state.blocks.has(String(paragraph.id))) return closest;
    const score = (item) => {
      if (!position.bbox || !item?.bbox) return item ? 0 : Infinity;
      const [x0, y0, x1] = item.bbox;
      const [oldX0, oldY0, oldX1] = position.bbox;
      return Math.abs((x0 + x1 - oldX0 - oldX1) / 2) * 2 + Math.abs(y0 - oldY0);
    };
    return score(paragraph) < score(closest) ? paragraph : closest;
  }, null);
  const scroll = $('reader-scroll');
  const block = target && state.blocks.get(String(target.id));
  const top = block ? scroll.scrollTop + block.getBoundingClientRect().top
    - scroll.getBoundingClientRect().top - position.offset : position.scrollTop;
  // Do not animate through a document whose paragraph IDs have just changed.
  scroll.scrollTo({ top: Math.max(0, top), behavior: 'instant' });
  updateReadingPosition();
}

function displayPaper(paper, fresh = false) {
  const newDocument = state.paper?.id !== paper.id;
  const wasDemo = state.paper?.id === paper.id && state.paper?.is_demo;
  const reparsed = !fresh && !newDocument && (Number(paper.parse_version || 0) > Number(state.paper?.parse_version || 0)
    || Number(paper.ai_layout_revision || 0) !== Number(state.paper?.ai_layout_revision || 0));
  const readingPosition = reparsed ? captureReparsePosition() : null;
  state.paper = paper;
  if (newDocument && typeof closePaperChat === "function") closePaperChat(true);
  if (wasDemo) state.paper.is_demo = true;
  const signatures = {
    metadata: JSON.stringify([paper.id, paper.title, paper.filename, paper.created_at,
      paper.page_count, paper.paragraph_count, paper.source_format, paper.is_demo, paper.warnings, paper.parse_status, paper.parse_error]),
    paragraphs: JSON.stringify([paper.id, paper.parse_version, paper.ai_layout_revision, paper.paragraphs, state.search]),
    figures: JSON.stringify([paper.id, paper.figures, paper.figures_error]),
    outline: JSON.stringify([paper.id, paper.outline]),
    translation: JSON.stringify([paper.id, paper.status, paper.error, paper.progress,
      paper.paragraph_count, paper.translated_count, state.bilingual, paper.parse_status, paper.parse_error,
      paper.ai_layout_status, paper.ai_layout_progress, paper.ai_layout_error, paper.ai_layout_warnings]),
    artwork: JSON.stringify([paper.id, paper.figure_status, paper.figure_progress, paper.page_count]),
  };
  const previous = state.displaySignatures || {};
  const changed = (key) => fresh || newDocument || reparsed || signatures[key] !== previous[key];
  state.loading = false;
  if (fresh || newDocument || $('reader-view').hidden) showView('reader');
  if (changed('metadata')) {
    $('breadcrumb-title').textContent = paper.title || paper.filename;
    $('paper-title').textContent = paper.title || paper.filename;
    $('paper-date').textContent = dateLabel(paper.created_at);
    $('paper-type').textContent = isDemo(paper) ? 'FOLIO SAMPLE · 示例文稿' : 'YOUR RESEARCH, IN FOCUS';
    $('paper-pages').textContent = documentMeta(paper);
    $('paper-paragraphs').textContent = `${paper.paragraph_count || paper.paragraphs?.length || 0} 段`;
    $('open-original').href = `/api/papers/${encodeURIComponent(paper.id)}/original`;
    $('open-original').hidden = !!isDemo(paper);
    $('open-original').title = isPdfPaper(paper) ? '查看原始 PDF' : '下载源文件';
    $('open-original').setAttribute('aria-label', $('open-original').title);
    if (isPdfPaper(paper)) {
      $('open-original').removeAttribute('download');
      $('open-original').target = '_blank';
    } else {
      $('open-original').setAttribute('download', paper.filename || '源文件');
      $('open-original').removeAttribute('target');
    }
    $('original-pdf-fallback').href = $('open-original').href;
    $('view-pdf').hidden = !isPdfPaper(paper);
    $('view-pdf').disabled = !!isDemo(paper);
    $('view-pdf').title = isDemo(paper) ? '示例文稿没有原始 PDF，请导入自己的论文体验原版阅读' : '保留论文原有双栏、图片及公式的位置';
    if (newDocument) {
      state.originalPage = 1;
      $('original-pdf-pages').replaceChildren();
      state.originalPaperId = null;
    }
    const notice = isDemo(paper) ? '示例文稿 · 本文为演示阅读排版而创作，内容与译文仅用于体验，不是真实发表的论文。你可以导入自己的论文或文档，开启正式阅读。' : (paper.warnings || []).join(' ');
    $('paper-notice').hidden = !notice;
    $('paper-notice').textContent = notice;
  }
  if (changed('translation')) {
    $('paper-translated').textContent = `已翻译 ${paper.translated_count || 0} 段`;
    $('paper-translated').hidden = !(paper.translated_count > 0);
  }
  if (fresh || reparsed) {
    clearMathObservations();
    state.blocks.clear();
    state.figures.clear();
    state.outlineSignature = '';
    state.readingBlocks = [];
    state.outlineItems = [];
    state.outlineTargets = [];
    state.activeOutline = null;
    state.hits = [];
    state.hitIndex = -1;
    $('paragraphs').replaceChildren();
    if (fresh) {
      state.search = '';
      $('document-search').value = '';
      $('search-bar').hidden = true;
      $('reader-scroll').scrollTop = 0;
      switchSidebarTab('outline');
    }
  }
  if (changed('paragraphs') || changed('figures')) {
    renderParagraphs();
    renderFigures();
  }
  if (changed('paragraphs') || changed('figures') || changed('outline')) renderOutline();
  const mode = state.readMode === 'pdf' && (isDemo(paper) || !isPdfPaper(paper)) ? 'bilingual' : state.readMode;
  if (fresh || newDocument || mode !== state.readMode) setReadingMode(mode);
  else if (changed('translation')) renderTranslationStatus();
  if (changed('artwork')) renderFigureStatus();
  updateSummary(paper);
  if (fresh || newDocument) localStorage.setItem('folio.lastPaper', paper.id);
  state.displaySignatures = signatures;
  schedulePoll();
  if (reparsed && readingPosition) requestAnimationFrame(() => {
    if (state.paper?.id === paper.id && state.paper?.parse_version === paper.parse_version
        && state.paper?.ai_layout_revision === paper.ai_layout_revision) restoreReparsePosition(readingPosition);
  });
  else if (fresh || changed('paragraphs') || changed('figures')) requestAnimationFrame(updateReadingPosition);
}

function translatedText(paragraph) {
  return typeof paragraph.translation === 'string' ? paragraph.translation : '';
}

function paragraphText(element, text) {
  text = normalizeMathText(text);
  const signature = JSON.stringify([text, state.search]);
  if (element.dataset.textSignature === signature) return;
  element.dataset.textSignature = signature;
  delete element.dataset.mathSearchHit;
  if (!state.search) { element.textContent = text; renderMath(element); return; }
  const query = state.search.toLocaleLowerCase();
  element.replaceChildren();
  for (const part of splitMathText(text)) {
    const lower = part.text.toLocaleLowerCase();
    if (part.math) {
      element.append(document.createTextNode(part.text));
      if (lower.includes(query)) element.dataset.mathSearchHit = 'true';
      continue;
    }
    let start = 0;
    let next = lower.indexOf(query, start);
    while (next !== -1) {
      element.append(document.createTextNode(part.text.slice(start, next)));
      element.append(node('mark', 'search-hit', part.text.slice(next, next + state.search.length)));
      start = next + state.search.length;
      next = lower.indexOf(query, start);
    }
    element.append(document.createTextNode(part.text.slice(start)));
  }
  renderMath(element);
}

function translatedParagraphText(element, text, paragraph) {
  const formulas = Array.isArray(paragraph.inline_math) ? paragraph.inline_math : [];
  if (!formulas.length) {
    delete element.dataset.inlineSignature;
    paragraphText(element, text.replace(/⟦FOLIO_MATH_\d+⟧/g, '〔原式缺失，请查看原 PDF〕'));
    return;
  }
  const signature = JSON.stringify([state.paper.id, state.paper.parse_version, text, state.search, formulas]);
  if (element.dataset.inlineSignature === signature) return;
  element.dataset.inlineSignature = signature;
  delete element.dataset.textSignature;
  delete element.dataset.mathSearchHit;
  element.replaceChildren();
  const byToken = new Map(formulas.map((formula) => [formula.placeholder, formula]));
  let offset = 0;
  const appendText = (value) => {
    if (!value) return;
    const span = node('span');
    paragraphText(span, value);
    if (span.dataset.mathSearchHit) element.dataset.mathSearchHit = 'true';
    element.append(span);
  };
  for (const match of text.matchAll(/⟦FOLIO_MATH_\d+⟧/g)) {
    appendText(text.slice(offset, match.index));
    const formula = byToken.get(match[0]);
    if (!formula) appendText('〔原式缺失，请查看原 PDF〕');
    else if (typeof formula.latex === 'string' && formula.latex) {
      appendText(`\\(${formula.latex}\\)`);
    } else {
      const group = node('span', 'inline-source-math');
      group.setAttribute('aria-label', '待恢复为 LaTeX 的原文数学公式');
      const sourceText = String(formula.source_text || '〔待识别公式〕').trim();
      group.append(node('span', 'inline-extracted-math', sourceText));
      const sourcePage = formula.source?.[0]?.page || paragraph.page || 1;
      const link = node('a', 'inline-source-link', '核对原 PDF ↗');
      link.href = `/api/papers/${encodeURIComponent(state.paper.id)}/original#page=${encodeURIComponent(sourcePage)}`;
      link.target = '_blank'; link.rel = 'noopener'; link.title = '在原始 PDF 中核对公式';
      group.append(link);
      element.append(group);
    }
    offset = match.index + match[0].length;
  }
  appendText(text.slice(offset));
}

function structuredParagraphText(element, paragraph, translated = false) {
  const algorithm = paragraph.kind === 'algorithm' && Array.isArray(paragraph.algorithm_lines);
  const table = paragraph.kind === 'table' && Array.isArray(paragraph.table_rows);
  if (!algorithm && !table) return false;
  const signature = JSON.stringify([paragraph.kind, paragraph.algorithm_lines, paragraph.table_rows,
    paragraph.table_header, paragraph.table_header_rows, paragraph.ai_reconstructed, paragraph.needs_visual_review, translated, state.search]);
  if (element.dataset.structureSignature === signature) return true;
  element.dataset.structureSignature = signature;
  delete element.dataset.textSignature;
  delete element.dataset.inlineSignature;
  delete element.dataset.equationSignature;
  delete element.dataset.mathSearchHit;
  element.replaceChildren();
  const label = node('div', 'structure-label', `${algorithm ? '算法' : '表格'}${translated ? ' · 中文' : ''}`);
  if (!translated && paragraph.ai_reconstructed) label.append(node('span', 'structure-ai-note', 'AI 识别 · 可对照原版核查'));
  else if (!translated && paragraph.needs_visual_review) label.append(node('span', 'structure-ai-note', '部分公式待核对 · 可用 AI 视觉识别补全'));
  element.append(label);
  const appendContent = (target, text) => {
    paragraphText(target, String(text ?? ''));
    if (target.dataset.mathSearchHit) element.dataset.mathSearchHit = 'true';
  };
  if (algorithm) {
    const listing = node('div', 'algorithm-listing');
    listing.setAttribute('role', 'group');
    listing.setAttribute('aria-label', '算法步骤与缩进');
    for (const entry of paragraph.algorithm_lines) {
      const row = node('div', 'algorithm-line');
      const value = typeof entry === 'string' ? entry : entry?.text;
      const indent = Math.min(16, Math.max(0, Number(entry?.indent) || 0));
      row.style.paddingInlineStart = `${indent * 1.4}em`;
      appendContent(row, value);
      listing.append(row);
    }
    element.append(listing);
  } else {
    const viewport = node('div', 'table-scroll');
    viewport.tabIndex = 0;
    viewport.setAttribute('role', 'region');
    viewport.setAttribute('aria-label', '论文表格，可横向滚动');
    const grid = node('table', 'paper-data-table');
    const body = node('tbody');
    const headerRows = Math.max(0, Math.min(paragraph.table_rows.length,
      Math.floor(Number(paragraph.table_header_rows) || (paragraph.table_header ? 1 : 0))));
    for (const [index, cells] of paragraph.table_rows.entries()) {
      if (!Array.isArray(cells)) continue;
      const row = node('tr');
      for (const value of cells) {
        const cell = node(index < headerRows ? 'th' : 'td');
        if (index < headerRows) cell.setAttribute('scope', 'col');
        appendContent(cell, value);
        row.append(cell);
      }
      body.append(row);
    }
    grid.append(body); viewport.append(grid); element.append(viewport);
  }
  return true;
}

function renderParagraphs(force = false) {
  const paragraphs = state.paper?.paragraphs || [];
  const figureTextIds = getFigureTextIds();
  let searchChanged = false;
  let targetsChanged = false;
  for (const paragraph of paragraphs) {
    const id = String(paragraph.id);
    // The figure already contains these labels at their original coordinates.
    // Keep source text and translations in storage; suppress only confirmed duplicates here.
    if (figureTextIds.has(id)) {
      const duplicate = state.blocks.get(id);
      if (duplicate) { duplicate.remove(); state.blocks.delete(id); searchChanged = true; targetsChanged = true; }
      continue;
    }
    let block = state.blocks.get(id);
    if (!block) {
      block = node('section', `paragraph-block${paragraph.kind === 'heading' ? ' is-heading' : ''}`);
      block.id = `paragraph-${id}`;
      block.dataset.paragraphId = id;
      block.dataset.level = paragraph.level || 1;
      block.dataset.page = paragraph.page || 1;
      block.append(node(paragraph.kind === 'heading' ? 'h2' : 'div', 'paragraph-original'));
      block.append(node('div', 'paragraph-translation'));
      state.blocks.set(id, block);
      targetsChanged = true;
      $('paragraphs').append(block);
    }
    const text = paragraph.text || '';
    const translation = translatedText(paragraph);
    const retainedParts = !translation && Array.isArray(paragraph.previous_translation_parts)
      ? paragraph.previous_translation_parts.filter((part) => typeof part.translation === 'string' && part.translation.trim()) : [];
    const review = paragraph.translation_review_reason;
    const signature = JSON.stringify([text, paragraph.translation_text, translation, paragraph.error, state.search, paragraph.kind,
      paragraph.algorithm_lines, paragraph.table_rows, paragraph.translated_algorithm_lines, paragraph.translated_table_rows,
      paragraph.table_header, paragraph.table_header_rows, paragraph.ai_reconstructed, paragraph.needs_visual_review,
      paragraph.math_status, paragraph.equation_image, paragraph.equation_source, paragraph.inline_math, review, retainedParts]);
    if (!force && block.dataset.signature === signature) continue;
    block.dataset.signature = signature;
    block.classList.toggle('is-algorithm', paragraph.kind === 'algorithm');
    block.classList.toggle('is-table', paragraph.kind === 'table');
    const original = block.querySelector('.paragraph-original');
    const structured = structuredParagraphText(original, paragraph);
    if (!structured) delete original.dataset.structureSignature;
    const sourceEquation = !structured && paragraph.math_status === 'source-image';
    original.classList.toggle('has-equation-source', !!sourceEquation);
    if (sourceEquation) renderEquationSource(original, paragraph);
    else if (!structured) {
      delete original.dataset.structureSignature;
      delete original.dataset.equationSignature;
      if (paragraph.math_status === 'source-inline' && typeof paragraph.translation_text === 'string') {
        translatedParagraphText(original, paragraph.translation_text, paragraph);
      } else {
        delete original.dataset.inlineSignature;
        paragraphText(original, text);
      }
      if (paragraph.math_status === 'latex' && paragraph.equation_source && !original.querySelector('.equation-original-link')) {
        const source = node('a', 'equation-original-link', paragraph.ai_reconstructed ? 'AI 识别 · 对照原式 ↗' : '查看原式 ↗');
        source.href = `/api/papers/${encodeURIComponent(state.paper.id)}/original#page=${encodeURIComponent(paragraph.equation_source?.page || paragraph.page || 1)}`;
        source.target = '_blank';
        source.rel = 'noopener';
        original.append(source);
      }
    }
    const target = block.querySelector('.paragraph-translation');
    target.className = 'paragraph-translation';
    target.hidden = !translation && !paragraph.error && !retainedParts.length && !review;
    block.classList.toggle('has-translation', !!translation || retainedParts.length > 0 || !!review);
    block.classList.toggle('has-translation-error', !!paragraph.error && !translation);
    if (translation) {
      const structuredTranslation = { ...paragraph, algorithm_lines: paragraph.translated_algorithm_lines,
        table_rows: paragraph.translated_table_rows };
      if (!structuredParagraphText(target, structuredTranslation, true)) {
        delete target.dataset.structureSignature;
        translatedParagraphText(target, translation, paragraph);
      }
    }
    else if (retainedParts.length || review) {
      delete target.dataset.structureSignature;
      delete target.dataset.textSignature;
      delete target.dataset.mathSearchHit;
      target.classList.add('partial');
      delete target.dataset.inlineSignature;
      target.replaceChildren(node('small', 'retained-translation-note', '原文已重新整理，请重新翻译完整段落。旧译文已保留。'));
      const details = node('details', 'retained-translation-details');
      details.append(node('summary', '', `查看旧译文片段（${retainedParts.length}）`));
      for (const part of retainedParts) {
        const fragment = node('div', 'retained-translation-part');
        paragraphText(fragment, part.translation);
        details.append(fragment);
      }
      if (retainedParts.length) target.append(details);
      const retry = node('button', 'retained-translation-retry', '重新翻译此段');
      retry.title = paragraph.error || '根据完整原文生成译文，已保存的旧译文保留在历史中';
      retry.addEventListener('click', () => retryParagraph(paragraph.id, retry));
      target.append(retry);
    }
    else if (paragraph.error) {
      delete target.dataset.structureSignature;
      delete target.dataset.textSignature;
      delete target.dataset.mathSearchHit;
      delete target.dataset.inlineSignature;
      target.classList.add('failed');
      target.replaceChildren();
      target.append(node('span', '', '这一段暂未译出，可重试继续。'));
      const retry = node('button', '');
      retry.innerHTML = `${icon('retry')}重试`;
      retry.title = paragraph.error;
      retry.addEventListener('click', () => retryParagraph(paragraph.id, retry));
      target.append(retry);
    } else {
      delete target.dataset.structureSignature;
      delete target.dataset.textSignature;
      delete target.dataset.mathSearchHit;
      delete target.dataset.inlineSignature;
      // Empty translations take no space. A single instruction lives in the article header.
      target.replaceChildren();
    }
    searchChanged = true;
  }
  if (targetsChanged || state.readingBlocks.length !== state.blocks.size) cacheReadingTargets();
  if (state.search && searchChanged) updateSearchHits(false);
  pruneMathObservations();
}

function renderEquationSource(element, paragraph) {
  const signature = JSON.stringify([state.paper.id, state.paper.parse_version, paragraph.id, paragraph.text, paragraph.equation_source, paragraph.ambiguous_inline_math]);
  if (element.dataset.equationSignature === signature) return;
  element.dataset.equationSignature = signature;
  delete element.dataset.inlineSignature;
  delete element.dataset.textSignature;
  delete element.dataset.mathSearchHit;
  element.replaceChildren();
  const extracted = node('div', 'equation-extracted-text');
  paragraphText(extracted, paragraph.text || '〔公式文字待识别〕');
  const note = node('small', 'equation-source-note', '已保留可选择的抽取文字；可用「AI 整理」恢复为标准 LaTeX。');
  const link = node('a', 'equation-original-link', '核对原 PDF ↗');
  link.href = `/api/papers/${encodeURIComponent(state.paper.id)}/original#page=${encodeURIComponent(paragraph.equation_source?.page || paragraph.page || 1)}`;
  link.target = '_blank';
  link.rel = 'noopener';
  note.append(link);
  element.append(extracted, note);
}

function imageRetryButton(image, url) {
  const button = node('button', 'image-retry', '重试加载');
  button.type = 'button';
  button.hidden = true;
  button.addEventListener('click', () => {
    button.disabled = true;
    button.textContent = '重新加载中…';
    image.hidden = false;
    image.src = `${url}${url.includes('?') ? '&' : '?'}retry=${Date.now()}`;
  });
  image.addEventListener('error', () => {
    button.hidden = false;
    button.disabled = false;
    button.textContent = '重试加载';
  });
  image.addEventListener('load', () => {
    button.hidden = true;
    button.disabled = false;
    button.textContent = '重试加载';
  });
  return button;
}

function figureURL(value) {
  if (typeof value !== 'string' || !value) return '';
  try {
    const url = new URL(value, window.location.origin);
    const prefix = `/api/papers/${encodeURIComponent(state.paper.id)}/`;
    return url.origin === window.location.origin && url.pathname.startsWith(prefix) ? url.href : '';
  } catch { return ''; }
}

function renderFigures() {
  const figures = Array.isArray(state.paper?.figures) ? state.paper.figures : [];
  const active = new Set();
  const placements = new Map();
  const captionKeys = new Set((state.paper?.paragraphs || []).map((paragraph) =>
    `${Number(paragraph.page || 1)}:${(paragraph.text || '').replace(/\s+/g, ' ').trim()}`));
  for (const [index, figure] of figures.entries()) {
    const id = String(figure.id || `figure-${figure.page || 1}-${index}`);
    active.add(id);
    const caption = figure.caption || (isPdfPaper(state.paper) ? `原文插图 · 第 ${figure.page || 1} 页` : '原文插图');
    const url = figureURL(figure.url);
    const captionPresent = captionKeys.has(`${Number(figure.page || 1)}:${caption.replace(/\s+/g, ' ').trim()}`);
    const signature = JSON.stringify([caption, url, figure.width, figure.height, captionPresent]);
    let block = state.figures.get(id);
    if (!block) {
      block = node('figure', 'paper-figure');
      block.dataset.figureId = id;
      state.figures.set(id, block);
    }
    if (block.dataset.signature !== signature) {
      block.dataset.signature = signature;
      block.replaceChildren();
      const fallback = node('p', 'figure-error', isPdfPaper(state.paper) ? '这张插图暂时无法显示，可点击工具栏的「查看原始 PDF」阅读。' : '这张插图暂时无法显示，可下载源文件查看。');
      fallback.hidden = !!url;
      if (url) {
        const link = node('a', 'figure-image-link');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener';
        link.title = '在新窗口查看原图';
        link.setAttribute('aria-label', `${caption}，在新窗口查看原图`);
        const image = document.createElement('img');
        image.loading = 'lazy';
        image.decoding = 'async';
        image.alt = caption;
        if (Number(figure.width) > 0 && Number(figure.height) > 0) {
          image.width = Math.min(10000, Number(figure.width));
          image.height = Math.min(10000, Number(figure.height));
        }
        image.addEventListener('error', () => { link.hidden = true; fallback.hidden = false; });
        image.src = url;
        link.append(image, node('span', 'figure-open-label', '查看原图 ↗'));
        block.append(link);
      }
      const captionNode = node('figcaption', '', caption);
      captionNode.hidden = captionPresent;
      block.append(fallback, captionNode);
    }
    const beforeAnchor = figure.before_paragraph_id == null ? null : state.blocks.get(String(figure.before_paragraph_id));
    let anchor = beforeAnchor || state.blocks.get(String(figure.after_paragraph_id));
    if (!anchor) {
      const preceding = (state.paper.paragraphs || []).filter((paragraph) => Number(paragraph.page || 1) <= Number(figure.page || 1));
      anchor = state.blocks.get(String(preceding[preceding.length - 1]?.id));
    }
    const anchorId = `${beforeAnchor ? 'before' : 'after'}:${anchor?.dataset.paragraphId || 'end'}`;
    if (!placements.has(anchorId)) placements.set(anchorId, { beforeAnchor, anchor, blocks: [] });
    placements.get(anchorId).blocks.push(block);
  }
  for (const { beforeAnchor, anchor, blocks } of placements.values()) {
    if (beforeAnchor) {
      // Place a shared before-anchor group from its tail. Forward placement
      // would move its first figure after the others and move them back on
      // every poll, even when nothing in the paper had changed.
      let next = beforeAnchor;
      for (let index = blocks.length - 1; index >= 0; index--) {
        const block = blocks[index];
        if (block.nextElementSibling !== next) next.before(block);
        next = block;
      }
    } else {
      let previous = anchor;
      for (const block of blocks) {
        if (previous) {
          if (block.previousElementSibling !== previous) previous.after(block);
        } else if (block.parentElement !== $('paragraphs')) $('paragraphs').append(block);
        previous = block;
      }
    }
  }
  for (const [id, block] of state.figures) {
    if (!active.has(id)) { block.remove(); state.figures.delete(id); }
  }
  let notice = $('figure-load-notice');
  if (state.paper?.figures_error) {
    if (!notice) { notice = node('p', 'figure-error'); notice.id = 'figure-load-notice'; }
    const message = isPdfPaper(state.paper) ? '部分插图未能加载，正文仍可阅读。请打开原始 PDF 查看完整图表。' : '部分插图未能加载，正文仍可阅读。可下载源文件查看完整图表。';
    if (notice.textContent !== message) notice.textContent = message;
    if (notice.parentElement !== $('paragraphs') || notice.nextElementSibling) $('paragraphs').append(notice);
  } else notice?.remove();
}

function getFigureTextIds() {
  return new Set((state.paper?.figures || []).flatMap((figure) => Array.isArray(figure.text_paragraph_ids) ? figure.text_paragraph_ids.map(String) : []));
}

function getOutline() {
  const labels = getFigureTextIds();
  const outline = state.paper?.outline?.length ? state.paper.outline
    : (state.paper?.paragraphs || []).filter((p) => p.kind === 'heading').map((p) => ({ id: p.id, title: p.text, level: p.level || 1, page: p.page, paragraph_id: p.id }));
  return outline.filter((section) => !labels.has(String(section.paragraph_id ?? section.id)));
}

function renderOutline() {
  const outline = getOutline();
  const headingTranslations = new Map((state.paper?.paragraphs || []).filter((p) => p.kind === 'heading').map((p) => [String(p.id), translatedText(p)]));
  const signature = JSON.stringify([outline, [...headingTranslations]]);
  if (signature === state.outlineSignature) return;
  state.outlineSignature = signature;
  $('outline-count').textContent = outline.length ? `${outline.length} 节` : '';
  $('outline-empty').hidden = !!outline.length;
  $('outline-empty').textContent = state.paper ? '这篇论文没有识别到章节标题，你仍可以滚动阅读或搜索内容。' : '导入一篇论文，开始有条理地阅读。';
  const fragment = document.createDocumentFragment();
  for (const section of outline) {
    const item = node('button', 'outline-item');
    item.dataset.level = Math.min(3, Number(section.level) || 1);
    item.dataset.paragraphId = String(section.paragraph_id ?? section.id);
    item.dataset.page = Number(section.page) || 1;
    const title = node('span', 'outline-title');
    title.append(node('span', 'outline-english', section.title));
    const translation = headingTranslations.get(item.dataset.paragraphId);
    if (translation && translation !== section.title) title.append(node('span', 'outline-chinese', translation));
    item.append(title);
    if (isPdfPaper(state.paper)) item.append(node('span', 'outline-page', String(section.page || '')));
    item.title = section.title;
    item.addEventListener('click', () => {
      if (state.readMode === 'pdf') setOriginalPage(item.dataset.page);
      else {
        const target = state.blocks.get(item.dataset.paragraphId);
        target?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      closeSidebar();
    });
    fragment.append(item);
  }
  $('outline-list').replaceChildren(fragment);
  state.outlineItems = [...$('outline-list').querySelectorAll('.outline-item')];
  cacheReadingTargets();
  requestAnimationFrame(updateReadingPosition);
}

function renderTranslationStatus() {
  const paper = state.paper;
  if (!paper) return;
  const paragraphs = paper.paragraphs || [];
  const total = paper.progress?.total ?? paper.paragraph_count ?? paragraphs.length;
  const done = paper.progress?.done ?? paper.translated_count ?? paragraphs.filter((p) => !!p.translation).length;
  const failed = paper.progress?.failed ?? paragraphs.filter((p) => !!p.error && !p.translation).length;
  const noTextToTranslate = total === 0 && paragraphs.length > 0;
  const updating = ['updating', 'pending'].includes(paper.parse_status);
  const aiRunning = paper.ai_layout_status === 'running';
  const layoutBusy = updating || aiRunning;
  const updateFailed = paper.parse_status === 'error';
  const running = paper.status === 'translating';
  const paused = paper.status === 'paused';
  const completed = paper.status === 'completed' || done >= total && total > 0;
  const banner = $('translation-banner');
  banner.hidden = !updating && !updateFailed && !running && !paused && !failed && !paper.error && !done;
  banner.classList.toggle('running', running || updating);
  banner.classList.toggle('has-error', updateFailed || !!failed || paper.status === 'error');
  $('translation-status').textContent = updating && running ? '排版更新等待翻译暂停' : updating ? '正在整理行号与公式' : updateFailed ? '本地排版更新暂未完成' : running ? '翻译进行中' : completed ? '双语文稿已就绪' : paused ? '翻译已暂停' : failed || paper.error ? '部分内容需要重试' : '已保留当前译文';
  $('translation-detail').textContent = updating && running ? '可暂停翻译后更新排版 · 已保存译文保留，可继续阅读' : updating ? '本地更新中 · 已保存译文保留，可继续阅读' : updateFailed ? '已保留原文与全部译文，可继续阅读或打开原版核对' : `${done} / ${total} 段${failed ? ` · ${failed} 段未成功` : running ? ' · 可随时阅读已完成的部分' : ''}`;
  $('translation-detail').title = paper.parse_error || paper.error || '';
  $('translation-progress').style.width = `${Math.min(100, total ? done / total * 100 : 0)}%`;
  $('retry-failed').hidden = !failed || running || layoutBusy;
  const button = $('translate-button');
  button.innerHTML = `${icon(running ? 'pause' : completed || noTextToTranslate ? 'check' : 'spark')}<span>${running ? '暂停翻译' : aiRunning ? 'AI 整理中' : updating ? '更新排版中' : noTextToTranslate ? '无需翻译' : completed ? '翻译完成' : paused || done ? '继续翻译' : '全文翻译'}</span>`;
  button.disabled = layoutBusy ? !running : noTextToTranslate || completed && !failed;
  button.title = running ? '暂停后会保留已完成的译文' : layoutBusy ? '整理完成后即可继续翻译，已保存译文会保留' : noTextToTranslate ? '当前文稿没有需要翻译的正文段落' : completed ? '所有正文段落均已完成翻译' : '用当前配置的模型逐段翻译';
  $('reader-help').hidden = layoutBusy || noTextToTranslate || running || done > 0 || !!paper.error || !!failed || !state.bilingual;
  for (const retry of document.querySelectorAll('#paragraphs .paragraph-translation button')) {
    if (layoutBusy && !retry.disabled) {
      retry.dataset.parseBlocked = 'true';
      retry.disabled = true;
    } else if (!layoutBusy && retry.dataset.parseBlocked) {
      delete retry.dataset.parseBlocked;
      retry.disabled = false;
    }
  }
  const aiAction = $('ai-layout-button');
  aiAction.hidden = !isPdfPaper(paper) || isDemo(paper);
  aiAction.disabled = layoutBusy || running;
  aiAction.title = running ? '请先暂停翻译，再整理段落、算法和表格' : layoutBusy ? '正在整理，已完成部分可继续阅读' : '让当前 API 辅助整理段落，或识别公式、算法与表格';
  const aiStatus = paper.ai_layout_status;
  const aiError = aiStatus === 'error';
  const aiPaused = aiStatus === 'paused';
  const aiWarnings = Array.isArray(paper.ai_layout_warnings) ? paper.ai_layout_warnings : [];
  const aiCompletedWithWarnings = aiStatus === 'completed' && aiWarnings.length > 0;
  const aiBanner = $('ai-layout-banner');
  aiBanner.hidden = !aiRunning && !aiError && !aiPaused && !aiCompletedWithWarnings;
  aiBanner.classList.toggle('has-error', aiError);
  aiBanner.classList.toggle('running', aiRunning);
  const progress = paper.ai_layout_progress || {};
  const page = Math.max(1, Number(progress.page) || 1);
  const totalPages = Math.max(0, Number(progress.total) || 0);
  const finishedPages = Math.min(totalPages, Math.max(0, Number(progress.done) || 0));
  $('ai-layout-status').textContent = aiRunning ? `AI 正在整理第 ${page} 页` : aiPaused ? 'AI 整理已暂停' : aiError ? 'AI 整理暂未完成' : aiCompletedWithWarnings ? 'AI 整理已完成' : '';
  $('ai-layout-detail').textContent = aiError ? (paper.ai_layout_error || '当前页识别未通过，已完成部分已保留，可重新选择范围重试。')
    : aiCompletedWithWarnings ? `${aiWarnings.length} 项未可靠识别的内容已局部保留原文；可对这些页面再次使用视觉识别。`
      : `${finishedPages} / ${totalPages} 页 · ${aiPaused ? '已完成部分已保存，可重新选择剩余范围' : '可继续阅读已完成部分'}`;
  $('ai-layout-detail').title = aiWarnings.join('\n');
  $('ai-layout-pause').hidden = !aiRunning;
  $('ai-layout-pause').disabled = !!state.aiRequestPending;
}

function renderFigureStatus() {
  const paper = state.paper;
  const status = paper?.figure_status;
  const badge = $('paper-figures');
  badge.hidden = !status || status === 'completed';
  if (badge.hidden) return;
  const progress = paper.figure_progress;
  const pages = progress?.total ? ` ${Math.min(progress.done || 0, progress.total)} / ${progress.total} 页` : '';
  badge.textContent = status === 'extracting' || status === 'pending' ? `插图后台整理中${pages}` : status === 'paused' ? '插图等待继续整理' : '部分插图未提取';
  badge.title = '正文和翻译可正常阅读；原版 PDF 随时可查看完整图片与公式。';
}

function updateAiLayoutDescription() {
  const vision = $('ai-mode-vision').checked;
  const all = $('ai-layout-scope').value === 'all';
  const page = Math.max(1, Number($('ai-layout-page').value) || 1);
  $('ai-layout-page').disabled = all || state.aiRequestPending;
  const scope = all ? `整篇论文（${state.paper?.page_count || 0} 页）` : `第 ${page} 页`;
  $('ai-layout-privacy').textContent = `点击开始后，${scope}的${vision ? '页面图像和文字' : '提取文字'}将发送到当前配置的 API，按服务商规则计费。${vision ? '请使用支持图片输入的模型；识别后的公式、算法和表格可对照原版核查。' : '文本整理用于调整段落边界；复杂公式和图表可选择视觉识别。'}`;
  $('ai-layout-submit').textContent = $('ai-layout-translate').checked ? '开始整理并翻译' : '开始整理';
}

function openAiLayout() {
  const paper = state.paper;
  if (!paper || !isPdfPaper(paper) || isDemo(paper)) return;
  if (paper.ai_layout_status === 'running' || ['updating', 'pending'].includes(paper.parse_status)) return;
  if (paper.status === 'translating') { toast('请先暂停翻译，再整理段落、算法和表格。'); return; }
  if (!isModelConfigured()) {
    openSettings();
    settingsMessage('请先配置用于整理和翻译的 API，再点击「AI 整理」。');
    return;
  }
  state.aiDialogPaperId = paper.id;
  const position = state.readMode === 'pdf' ? state.originalPage : captureReparsePosition()?.page;
  const page = Math.max(1, Math.min(Number(paper.page_count) || 1, Number(position) || 1));
  $('ai-layout-document').textContent = paper.title || paper.filename || '当前论文';
  const provider = state.providers?.find((item) => item.id === state.settings?.provider)?.name || '当前 API 服务';
  $('ai-layout-service').textContent = `${provider} · ${state.settings?.model || ''}`;
  $('ai-layout-model').value = '';
  $('ai-layout-model').placeholder = state.settings?.model || '使用当前配置的模型';
  $('ai-layout-scope').value = 'page';
  $('ai-layout-page').value = String(page);
  $('ai-layout-page').max = String(Math.max(1, Number(paper.page_count) || 1));
  $('ai-mode-text').checked = false;
  $('ai-mode-vision').checked = true;
  $('ai-layout-translate').checked = true;
  $('ai-layout-message').hidden = true;
  setAiLayoutBusy(false);
  updateAiLayoutDescription();
  $('ai-layout-dialog').showModal();
}

function setAiLayoutBusy(busy) {
  state.aiRequestPending = busy;
  for (const id of ['ai-mode-text', 'ai-mode-vision', 'ai-layout-scope', 'ai-layout-model',
    'ai-layout-translate', 'ai-layout-submit', 'ai-layout-close', 'ai-layout-cancel']) $(id).disabled = busy;
  $('ai-layout-page').disabled = busy || $('ai-layout-scope').value === 'all';
}

async function startAiLayout(event) {
  event.preventDefault();
  if (state.aiRequestPending || !$('ai-layout-form').reportValidity()) return;
  const id = state.aiDialogPaperId;
  if (!id || state.paper?.id !== id) { $('ai-layout-dialog').close(); return; }
  if (state.paper.status === 'translating' || state.paper.ai_layout_status === 'running'
      || ['updating', 'pending'].includes(state.paper.parse_status)) return;
  const payload = {
    scope: $('ai-layout-scope').value === 'all' ? 'all' : 'page',
    page: Math.max(1, Math.min(Number(state.paper.page_count) || 1, Number($('ai-layout-page').value) || 1)),
    vision: $('ai-mode-vision').checked,
    translate_after: $('ai-layout-translate').checked,
  };
  const model = $('ai-layout-model').value.trim();
  if (model) payload.model = model;
  setAiLayoutBusy(true);
  $('ai-layout-message').hidden = true;
  try {
    const result = await api(`/papers/${encodeURIComponent(id)}/analyze`, { method: 'POST', body: JSON.stringify(payload) });
    $('ai-layout-dialog').close();
    if (state.paper?.id !== id) return;
    displayPaper(result.paragraphs ? result : await api(`/papers/${encodeURIComponent(id)}`));
    toast('AI 整理已开始，已完成的页面会逐页更新。');
  } catch (error) {
    $('ai-layout-message').textContent = error.message;
    $('ai-layout-message').hidden = false;
  } finally {
    setAiLayoutBusy(false);
    if (state.paper?.id === id) renderTranslationStatus();
  }
}

async function pauseAiLayout() {
  if (state.aiRequestPending || state.paper?.ai_layout_status !== 'running') return;
  const id = state.paper.id;
  state.aiRequestPending = true;
  $('ai-layout-pause').disabled = true;
  $('ai-layout-pause').textContent = '正在暂停…';
  try {
    const result = await api(`/papers/${encodeURIComponent(id)}/analyze/pause`, { method: 'POST', body: '{}' });
    if (state.paper?.id !== id) return;
    displayPaper(result.paragraphs ? result : await api(`/papers/${encodeURIComponent(id)}`));
    toast('AI 整理已暂停，已完成部分会保留。');
  } catch (error) { toast(error.message, true); }
  finally {
    state.aiRequestPending = false;
    $('ai-layout-pause').textContent = '暂停整理';
    if (state.paper?.id === id) renderTranslationStatus();
  }
}

function closePaperChat(reset = false) {
  $('paper-chat-panel').hidden = true;
  $('paper-chat-backdrop').hidden = true;
  $('paper-chat-button').setAttribute('aria-expanded', 'false');
  if (reset) {
    state.chatPaperId = null;
    state.chatMessages = [];
    state.chatPending = false;
    if ($('paper-chat-input')) $('paper-chat-input').value = '';
    renderPaperChat();
  }
}

function appendChatInline(target, value) {
  value = String(value || '');
  const pattern = /(\*\*|__)(.+?)\1|`([^`]+)`/g;
  let offset = 0;
  const appendText = (text) => {
    if (!text) return;
    const span = node('span');
    target.append(span);
    paragraphText(span, text);
  };
  for (const match of value.matchAll(pattern)) {
    appendText(value.slice(offset, match.index));
    const child = node(match[3] !== undefined ? 'code' : 'strong');
    target.append(child);
    paragraphText(child, match[3] !== undefined ? match[3] : match[2]);
    offset = match.index + match[0].length;
  }
  appendText(value.slice(offset));
}

function renderChatMarkdown(element, value) {
  element.replaceChildren();
  const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
  let paragraph = [];
  let list = null;
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const block = node('p');
    element.append(block);
    appendChatInline(block, paragraph.join(' ').trim());
    paragraph = [];
  };
  const closeList = () => { list = null; };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushParagraph(); closeList(); continue; }
    const heading = /^(#{1,4})\s+(.+)$/.exec(line);
    const bullet = /^[-*+]\s+(.+)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.+)$/.exec(line);
    const quote = /^>\s?(.+)$/.exec(line);
    if (heading) {
      flushParagraph(); closeList();
      const block = node(`h${Math.min(4, heading[1].length + 2)}`);
      element.append(block); appendChatInline(block, heading[2]);
    } else if (bullet || numbered) {
      flushParagraph();
      const tag = bullet ? 'ul' : 'ol';
      if (!list || list.nodeName?.toLowerCase() !== tag) {
        list = node(tag); element.append(list);
      }
      const item = node('li'); list.append(item); appendChatInline(item, (bullet || numbered)[1]);
    } else if (quote) {
      flushParagraph(); closeList();
      const block = node('blockquote'); element.append(block); appendChatInline(block, quote[1]);
    } else {
      closeList(); paragraph.push(line);
    }
  }
  flushParagraph();
}

function renderPaperChat() {
  const container = $('paper-chat-messages');
  if (!container) return;
  container.replaceChildren();
  if (!state.chatMessages.length && !state.chatPending) {
    const empty = node('div', 'paper-chat-empty');
    empty.innerHTML = `${icon('chat')}<strong>从论文出发，问得具体一点。</strong><span>例如：作者为什么要引入低秩分支？</span>`;
    container.append(empty);
  }
  for (const message of state.chatMessages) {
    const bubble = node('div', `paper-chat-message ${message.role}${message.error ? ' error' : ''}`);
    container.append(bubble);
    if (message.role === 'assistant' && !message.error) renderChatMarkdown(bubble, message.content);
    else paragraphText(bubble, message.content);
  }
  if (state.chatPending) container.append(node('div', 'paper-chat-message assistant pending', '正在阅读相关段落并组织回答…'));
  $('paper-chat-input').disabled = state.chatPending;
  $('paper-chat-send').disabled = state.chatPending;
  requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}

function openPaperChat() {
  if (!state.paper) return;
  if (!isModelConfigured()) {
    openSettings();
    settingsMessage('请先连接一个模型，再使用论文问答。');
    return;
  }
  if (state.chatPaperId !== state.paper.id) {
    state.chatPaperId = state.paper.id;
    state.chatMessages = [];
  }
  $('paper-chat-panel').hidden = false;
  $('paper-chat-backdrop').hidden = false;
  $('paper-chat-button').setAttribute('aria-expanded', 'true');
  renderPaperChat();
  $('paper-chat-input').focus();
}

async function askPaper(question) {
  question = String(question || '').trim();
  if (!question || state.chatPending || !state.paper) return;
  const paperId = state.paper.id;
  if (state.chatPaperId !== paperId) {
    state.chatPaperId = paperId;
    state.chatMessages = [];
  }
  const history = state.chatMessages.slice(-8).map(({ role, content }) => ({ role, content }));
  state.chatMessages.push({ role: 'user', content: question });
  state.chatPending = true;
  $('paper-chat-input').value = '';
  renderPaperChat();
  const position = state.readMode === 'pdf' ? state.originalPage : captureReparsePosition()?.page;
  try {
    const result = await api(`/papers/${encodeURIComponent(paperId)}/chat`, {
      method: 'POST', body: JSON.stringify({ question, page: Math.max(1, Number(position) || 1), history }),
    });
    if (state.chatPaperId === paperId) state.chatMessages.push({ role: 'assistant', content: result.answer });
  } catch (error) {
    if (state.chatPaperId === paperId) state.chatMessages.push({ role: 'assistant', content: error.message, error: true });
  } finally {
    if (state.chatPaperId === paperId) {
      state.chatPending = false;
      renderPaperChat();
      $('paper-chat-input').focus();
    }
  }
}

function schedulePoll() {
  clearTimeout(state.poll);
  if (state.paper?.status !== 'translating' && !['extracting', 'pending'].includes(state.paper?.figure_status)
      && !['updating', 'pending'].includes(state.paper?.parse_status)
      && state.paper?.ai_layout_status !== 'running') return;
  const id = state.paper.id;
  state.poll = setTimeout(async () => {
    try {
      const paper = await api(`/papers/${encodeURIComponent(id)}`);
      if (state.paper?.id !== id || state.loading) return;
      const previous = state.paper.status;
      const previousLayout = state.paper.ai_layout_status;
      displayPaper(paper);
      if (previous === 'translating' && paper.status === 'completed') toast('全文翻译完成，安心读下去。');
      if (previous === 'translating' && paper.status === 'error') toast(paper.error || '翻译暂时遇到问题，已保留完成的译文。', true);
      if (previousLayout === 'running' && paper.ai_layout_status === 'completed') toast('AI 整理完成，段落、公式与版面已更新。');
    } catch (error) {
      if (state.paper?.id !== id) return;
      toast(error.message, true);
      state.poll = setTimeout(() => schedulePoll(), 4500);
    }
  }, 1500);
}

async function translatePaper(retry = false) {
  if (!state.paper) return;
  const id = state.paper.id;
  const running = state.paper.status === 'translating' && !retry;
  if ((['updating', 'pending'].includes(state.paper.parse_status) || state.paper.ai_layout_status === 'running') && !running) return;
  if (!running && !isModelConfigured()) {
    openSettings();
    settingsMessage('请先选择服务商，自行填写模型和连接信息并保存，然后开始翻译。');
    return;
  }
  $('translate-button').disabled = true;
  try {
    const result = await api(`/papers/${encodeURIComponent(id)}/${running ? 'pause' : 'translate'}`, {
      method: 'POST', body: JSON.stringify(running ? {} : { retry_failed: retry }),
    });
    if (state.paper?.id !== id) return;
    const paper = result.paragraphs ? result : await api(`/papers/${encodeURIComponent(id)}`);
    displayPaper(paper);
    toast(running ? '翻译已暂停，已完成的译文会保留。' : '翻译已开始，你可以一边等待，一边阅读。');
  } catch (error) {
    toast(error.message, true);
    renderTranslationStatus();
  }
}

async function retryParagraph(id, button) {
  if (['updating', 'pending'].includes(state.paper?.parse_status) || state.paper?.ai_layout_status === 'running') return;
  if (!isModelConfigured()) { openSettings(); return; }
  const paperId = state.paper.id;
  button.disabled = true;
  button.textContent = '重试中…';
  try {
    const result = await api(`/papers/${encodeURIComponent(paperId)}/paragraphs/${encodeURIComponent(id)}/translate`, { method: 'POST', body: '{}' });
    if (state.paper?.id !== paperId) return;
    displayPaper(result.paragraphs ? result : await api(`/papers/${encodeURIComponent(paperId)}`));
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
    button.innerHTML = `${icon('retry')}重试`;
  }
}

async function deletePaper(paper) {
  const dialog = $('confirm-dialog');
  $('confirm-description').textContent = `「${paper.title || paper.filename}」及已保存的译文将被移除，电脑中的源文件不受影响。`;
  dialog.returnValue = 'cancel';
  dialog.showModal();
  const confirmed = await new Promise((resolve) => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }));
  if (!confirmed) return;
  try {
    await api(`/papers/${encodeURIComponent(paper.id)}`, { method: 'DELETE' });
    state.papers = state.papers.filter((p) => p.id !== paper.id);
    if (state.paper?.id === paper.id) showWelcome();
    renderLibrary();
    toast('已从文献库中移除。');
  } catch (error) { toast(error.message, true); }
}

// Keep only element references, not pixel offsets: translations, font changes,
// and lazily loaded figures can all change a paragraph's height while reading.
function cacheReadingTargets() {
  state.readingBlocks = [...state.blocks.values()];
  const order = new Map(state.readingBlocks.map((block, index) => [block, index]));
  state.outlineTargets = state.outlineItems.map((item) => ({
    item, block: state.blocks.get(item.dataset.paragraphId),
  })).filter(({ block }) => order.has(block)).sort((a, b) => order.get(a.block) - order.get(b.block));
}

// One layout read per binary-search step instead of scanning the whole paper
// every animation frame. Read live geometry so async changes never stale it.
function lastPassedElement(items, top, elementFor = (item) => item) {
  let low = 0;
  let high = items.length - 1;
  let found = null;
  while (low <= high) {
    const middle = (low + high) >>> 1;
    const item = items[middle];
    if (elementFor(item).getBoundingClientRect().top <= top) {
      found = item;
      low = middle + 1;
    } else high = middle - 1;
  }
  return found;
}

function setReadingText(element, value) {
  if (element.textContent !== value) element.textContent = value;
}

function activateOutline(current) {
  if (state.activeOutline === current) return;
  state.activeOutline?.classList.remove('active');
  current?.classList.add('active');
  state.activeOutline = current;
}

function updateReadingPosition() {
  if (!state.paper || $('reader-view').hidden) return;
  if (state.readMode === 'pdf') {
    const page = state.originalPage;
    const items = state.outlineItems;
    let current = null;
    for (const item of items) if (Number(item.dataset.page) <= page) current = item;
    activateOutline(current);
    setReadingText($('reading-location'), `原版 PDF · 定位到第 ${page} 页 / 共 ${state.paper.page_count || 1} 页`);
    setReadingText($('read-progress'), '');
    return;
  }
  const scroll = $('reader-scroll');
  const max = scroll.scrollHeight - scroll.clientHeight;
  const progress = `${max > 0 ? Math.min(100, Math.round(scroll.scrollTop / max * 100)) : 100}%`;
  const top = scroll.getBoundingClientRect().top + 100;
  const current = lastPassedElement(state.outlineTargets, top, (target) => target.block)?.item || state.outlineItems[0] || null;
  const block = lastPassedElement(state.readingBlocks, top);
  const page = Number(block?.dataset.page) || 1;
  const title = current?.querySelector('.outline-english').textContent;
  const location = isPdfPaper(state.paper)
    ? title ? `${title} · 第 ${page} 页` : `第 ${page} 页 / 共 ${state.paper.page_count || 1} 页`
    : title || '正在阅读';
  // All geometry reads precede DOM writes to avoid a forced layout in this frame.
  activateOutline(current);
  setReadingText($('read-progress'), progress);
  setReadingText($('reading-location'), location);
}

function setFontSize(size) {
  state.fontSize = Math.min(32, Math.max(12, Number(size) || 18));
  document.documentElement.style.setProperty('--reading-size', `${state.fontSize}px`);
  $('font-size-label').textContent = state.fontSize;
  $('font-minus').disabled = state.fontSize <= 12;
  $('font-plus').disabled = state.fontSize >= 32;
  localStorage.setItem('folio.fontSize', state.fontSize);
  document.dispatchEvent(new CustomEvent('folio:font-size-change', { detail: { size: state.fontSize } }));
}

function setBilingual(value) {
  setReadingMode(value ? 'bilingual' : 'original');
}

function setReadingMode(mode) {
  if (mode === 'pdf' && (!state.paper || isDemo(state.paper) || !isPdfPaper(state.paper))) mode = 'bilingual';
  const modeChanged = state.readMode !== mode;
  const pdf = mode === 'pdf';
  state.readMode = mode;
  state.bilingual = mode === 'bilingual';
  $('paper-article').classList.toggle('original-only', !state.bilingual);
  $('reader-view').classList.toggle('showing-original-pdf', pdf);
  $('reader-scroll').hidden = pdf;
  $('original-pdf-view').hidden = !pdf;
  for (const [id, active] of [['view-bilingual', state.bilingual], ['view-original', mode === 'original'], ['view-pdf', pdf]]) {
    $(id).classList.toggle('active', active);
    $(id).setAttribute('aria-pressed', String(active));
  }
  $('toggle-search').disabled = pdf;
  $('toggle-search').title = pdf ? '切回双语阅读或原文重排，可搜索文字' : '搜索文内内容';
  if (pdf) {
    if (!$('search-bar').hidden) closeSearch();
    const created = renderOriginalPages();
    if (modeChanged || created) setOriginalPage(state.originalPage);
  }
  if (state.paper) renderTranslationStatus();
  if (state.search && modeChanged) updateSearchHits(true);
  requestAnimationFrame(updateReadingPosition);
}

function renderOriginalPages() {
  if (!state.paper || !isPdfPaper(state.paper) || isDemo(state.paper) || state.originalPaperId === state.paper.id) return false;
  const fragment = document.createDocumentFragment();
  const paperId = state.paper.id;
  for (let page = 1; page <= (Number(state.paper.page_count) || 1); page++) {
    const figure = node('figure', 'original-pdf-page');
    figure.id = `original-pdf-page-${page}`;
    figure.dataset.page = page;
    const image = document.createElement('img');
    image.loading = 'lazy';
    image.decoding = 'async';
    image.alt = `论文原版第 ${page} 页，完整保留该页图文位置`;
    const dimensions = state.paper.page_dimensions?.[page - 1];
    image.width = Number(dimensions?.width) || 1190;
    image.height = Number(dimensions?.height) || 1684;
    const fallback = node('p', 'original-page-error', `第 ${page} 页暂未显示。可在下方打开原 PDF 查看。`);
    fallback.hidden = true;
    const url = `/api/papers/${encodeURIComponent(paperId)}/pages/${page}.png`;
    fallback.append(imageRetryButton(image, url));
    image.addEventListener('error', () => { image.hidden = true; fallback.hidden = false; });
    image.addEventListener('load', () => { image.hidden = false; fallback.hidden = true; });
    image.src = url;
    figure.append(image, fallback, node('figcaption', '', `第 ${page} 页`));
    fragment.append(figure);
  }
  $('original-pdf-pages').replaceChildren(fragment);
  state.originalPaperId = paperId;
  return true;
}

function syncOriginalPageControls() {
  const total = Math.max(1, Number(state.paper?.page_count) || 1);
  const page = state.originalPage;
  $('original-page').value = page;
  $('original-page').max = total;
  $('original-page-total').textContent = `/ ${total}`;
  $('original-page-prev').disabled = page <= 1;
  $('original-page-next').disabled = page >= total;
}

function setOriginalPage(value) {
  if (!state.paper || isDemo(state.paper)) return;
  const total = Math.max(1, Number(state.paper.page_count) || 1);
  const page = Math.max(1, Math.min(total, Math.trunc(Number(value)) || 1));
  state.originalPage = page;
  syncOriginalPageControls();
  const target = $(`original-pdf-page-${page}`);
  target?.scrollIntoView({ block: 'start', behavior: 'instant' });
  updateReadingPosition();
}

function updateOriginalPageFromScroll() {
  if (state.readMode !== 'pdf' || !state.paper) return;
  const region = $('original-pdf-pages');
  const top = region.getBoundingClientRect().top + 60;
  const element = lastPassedElement(region.children, top);
  const page = Number(element?.dataset.page) || 1;
  if (state.originalPage === page) return;
  state.originalPage = page;
  if (document.activeElement !== $('original-page')) syncOriginalPageControls();
  updateReadingPosition();
}

function openSearch() {
  if (state.readMode === 'pdf') return;
  $('search-bar').hidden = false;
  $('document-search').focus();
}

function closeSearch() {
  $('search-bar').hidden = true;
  $('document-search').value = '';
  state.search = '';
  state.hits = [];
  state.hitIndex = -1;
  renderParagraphs(true);
  for (const block of state.blocks.values()) block.classList.remove('current-hit');
}

function updateSearchHits(jump = true) {
  const previous = state.hits[state.hitIndex]?.dataset.paragraphId;
  state.hits = [];
  for (const block of state.blocks.values()) {
    const originalHit = block.querySelector('.paragraph-original .search-hit, .paragraph-original[data-math-search-hit="true"]');
    const translatedHit = state.bilingual && block.querySelector('.paragraph-translation .search-hit, .paragraph-translation[data-math-search-hit="true"]');
    if (originalHit || translatedHit) state.hits.push(block);
    block.classList.remove('current-hit');
  }
  state.hitIndex = jump ? 0 : Math.max(0, state.hits.findIndex((b) => b.dataset.paragraphId === previous));
  if (!state.hits.length) state.hitIndex = -1;
  selectSearchHit(jump);
}

function selectSearchHit(scroll = true) {
  for (const block of state.blocks.values()) block.classList.remove('current-hit');
  const current = state.hits[state.hitIndex];
  if (current) {
    current.classList.add('current-hit');
    if (scroll) current.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  $('search-results').textContent = state.search ? state.hits.length ? `${state.hitIndex + 1} / ${state.hits.length} 段` : '未找到' : '';
  $('search-prev').disabled = !state.hits.length;
  $('search-next').disabled = !state.hits.length;
}

function moveSearchHit(direction) {
  if (!state.hits.length) return;
  state.hitIndex = (state.hitIndex + direction + state.hits.length) % state.hits.length;
  selectSearchHit();
}

function providerInfo(id) {
  return state.providers.find((provider) => provider.id === id) || {
    id: id || 'custom', name: '自定义 OpenAI 兼容接口', endpoint: '', models: [],
    requires_key: id !== 'ollama', note: '使用 OpenAI Chat Completions 兼容接口，填写完整的请求地址。',
  };
}

function requiresKey(settings) {
  return providerInfo(settings?.provider).requires_key !== false;
}

function isModelConfigured(settings = state.settings) {
  return !!(settings?.endpoint?.trim() && settings?.model?.trim() && (!requiresKey(settings) || settings.has_key));
}

function renderProviders() {
  const providers = state.providers.length ? state.providers : [providerInfo('custom')];
  $('api-provider').replaceChildren(...providers.map((provider) => {
    const option = node('option', '', provider.name);
    option.value = provider.id;
    return option;
  }));
}

const modelPicker = { suggestions: [], active: -1, filtered: false };

function closeModelSuggestions() {
  $('api-model-menu').hidden = true;
  $('api-model').setAttribute('aria-expanded', 'false');
  $('api-model').removeAttribute('aria-activedescendant');
  $('api-model-toggle').setAttribute('aria-expanded', 'false');
  $('api-model-toggle').setAttribute('aria-label', '展开模型建议');
  modelPicker.active = -1;
  modelPicker.filtered = false;
}

function modelSuggestions() {
  return [...new Set((providerInfo($('api-provider').value).models || [])
    .filter((model) => typeof model === 'string' && model.trim()))];
}

function openModelSuggestions(filter = false) {
  if ($('api-model').disabled) return;
  const all = modelSuggestions();
  const query = filter ? $('api-model').value.trim().toLowerCase() : '';
  modelPicker.filtered = !!query;
  modelPicker.suggestions = all.filter((model) => !query || model.toLowerCase().includes(query));
  modelPicker.active = -1;
  $('api-model-options').replaceChildren(...modelPicker.suggestions.map((model, index) => {
    const option = node('button', 'model-option', model);
    option.type = 'button';
    option.id = `api-model-option-${index}`;
    option.tabIndex = -1;
    option.dataset.modelIndex = String(index);
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', 'false');
    return option;
  }));
  $('api-model-empty').textContent = all.length
    ? '没有匹配的建议；可直接使用你输入的模型 ID，或点击箭头查看全部建议。'
    : '此服务暂无预设模型，请按服务商说明手动输入模型 ID。';
  $('api-model-empty').hidden = modelPicker.suggestions.length > 0;
  $('api-model-menu').hidden = false;
  $('api-model').setAttribute('aria-expanded', 'true');
  $('api-model').removeAttribute('aria-activedescendant');
  $('api-model-toggle').setAttribute('aria-expanded', 'true');
  $('api-model-toggle').setAttribute('aria-label', '收起模型建议');
}

function highlightModelSuggestion(index) {
  if (!modelPicker.suggestions.length) return;
  modelPicker.active = (index + modelPicker.suggestions.length) % modelPicker.suggestions.length;
  for (const [position, option] of [...$('api-model-options').children].entries()) {
    option.setAttribute('aria-selected', String(position === modelPicker.active));
    if (position === modelPicker.active) {
      $('api-model').setAttribute('aria-activedescendant', option.id);
      option.scrollIntoView({ block: 'nearest' });
    }
  }
}

function chooseModelSuggestion(index) {
  const model = modelPicker.suggestions[index];
  if (model === undefined || $('api-model').disabled) return;
  $('api-model').value = model;
  $('api-model').focus({ preventScroll: true });
  closeModelSuggestions();
  $('api-model').dispatchEvent(new Event('change', { bubbles: true }));
}

function wireModelPicker() {
  $('api-model').addEventListener('focus', () => openModelSuggestions());
  $('api-model').addEventListener('input', (e) => { if (!e.isComposing) openModelSuggestions(true); });
  $('api-model').addEventListener('compositionend', () => openModelSuggestions(true));
  $('api-model').addEventListener('keydown', (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    const open = !$('api-model-menu').hidden;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!open) openModelSuggestions();
      const next = modelPicker.active < 0
        ? (e.key === 'ArrowDown' ? 0 : modelPicker.suggestions.length - 1)
        : modelPicker.active + (e.key === 'ArrowDown' ? 1 : -1);
      highlightModelSuggestion(next);
    } else if (e.key === 'Enter' && open) {
      e.preventDefault();
      if (modelPicker.active >= 0) chooseModelSuggestion(modelPicker.active);
      else closeModelSuggestions();
    } else if (e.key === 'Escape' && open) {
      e.preventDefault();
      e.stopPropagation();
      closeModelSuggestions();
    } else if (e.key === 'Tab') closeModelSuggestions();
  });
  $('api-model-toggle').addEventListener('pointerdown', (e) => e.preventDefault());
  $('api-model-toggle').addEventListener('click', () => {
    const showAll = $('api-model-menu').hidden || modelPicker.filtered;
    $('api-model').focus({ preventScroll: true });
    if (showAll) openModelSuggestions();
    else closeModelSuggestions();
  });
  $('api-model-options').addEventListener('pointerdown', (e) => e.preventDefault());
  $('api-model-options').addEventListener('click', (e) => {
    const option = e.target.closest('[data-model-index]');
    if (option) chooseModelSuggestion(Number(option.dataset.modelIndex));
  });
  $('api-model-picker').addEventListener('focusout', (e) => {
    if (!$('api-model-picker').contains(e.relatedTarget)) closeModelSuggestions();
  });
  document.addEventListener('pointerdown', (e) => {
    if (!$('api-model-picker').contains(e.target)) closeModelSuggestions();
  });
}

function renderProviderDetails() {
  const provider = providerInfo($('api-provider').value);
  $('provider-mark').textContent = provider.id === 'custom' ? '↗' : provider.name.slice(0, 1);
  $('provider-name').textContent = provider.name;
  $('provider-note').textContent = provider.note || '兼容 OpenAI Chat Completions 接口';
  $('api-endpoint').placeholder = provider.endpoint || 'https://your-service.example/v1/chat/completions';
  $('api-model').placeholder = '选择建议或输入你有权限使用的模型';
  closeModelSuggestions();
  $('api-model-help').textContent = modelSuggestions().length
    ? '可选择建议，也可手动输入模型 ID。'
    : '此服务暂无预设列表，请按服务商说明手动输入模型 ID。';
  $('api-key-label').textContent = provider.requires_key === false ? 'API Key（可选）' : 'API Key';
  $('api-key-help').textContent = provider.requires_key === false
    ? '本机 Ollama 通常不需要密钥。密钥仅保存在本机配置中，不会写入导出文稿。'
    : '填写该服务商提供的密钥。密钥仅保存在本机配置中，不会写入导出文稿。';
}

function credentialContextChanged(message) {
  state.keyDirty = true;
  $('api-key').value = '';
  $('api-key').type = 'password';
  $('api-key').placeholder = requiresKey({ provider: $('api-provider').value }) ? '请填写此服务对应的 API Key' : '本机服务可留空';
  $('toggle-key').textContent = '显示';
  $('clear-key').hidden = true;
  settingsMessage(message);
}

function changeProvider() {
  const provider = providerInfo($('api-provider').value);
  $('api-endpoint').value = provider.endpoint || '';
  $('api-model').value = '';
  renderProviderDetails();
  credentialContextChanged(provider.requires_key === false
    ? '已切换为本机服务。请选择或输入已安装的模型，确认地址后保存。'
    : '已切换服务商。请自行选择或输入模型，并重新填写该服务商的密钥。');
}

function renderSettingsStatus() {
  const connected = isModelConfigured();
  $('api-dot').classList.toggle('connected', !!connected);
  $('api-status-label').textContent = connected ? state.settings.model || '翻译模型已配置' : '连接翻译模型';
  $('api-status-detail').textContent = connected ? `${providerInfo(state.settings.provider).name} · 点击修改` : '自行选择服务商与模型';
}

function openSettings() {
  const settings = state.settings || {};
  const providerId = settings.provider || 'custom';
  if (![...$('api-provider').options].some((option) => option.value === providerId)) {
    const option = node('option', '', providerInfo(providerId).name);
    option.value = providerId;
    $('api-provider').append(option);
  }
  $('api-provider').value = providerId;
  $('api-endpoint').value = settings.endpoint || '';
  $('api-model').value = settings.model || '';
  $('api-concurrency').value = String(settings.concurrency || 2);
  $('api-key').value = '';
  $('api-key').type = 'password';
  $('api-key').placeholder = settings.has_key ? '已保存 · 留空保留原密钥' : '输入你的 API Key';
  $('toggle-key').textContent = '显示';
  $('clear-key').hidden = !settings.has_key;
  state.keyDirty = false;
  $('settings-message').hidden = true;
  renderProviderDetails();
  if (!$('settings-dialog').open) $('settings-dialog').showModal();
}

function settingsPayload() {
  const value = {
    provider: $('api-provider').value,
    endpoint: $('api-endpoint').value.trim(),
    model: $('api-model').value.trim(),
    concurrency: Number($('api-concurrency').value),
  };
  if (state.keyDirty) value.api_key = $('api-key').value.trim();
  return value;
}

function settingsMessage(message, type = '') {
  const el = $('settings-message');
  el.className = `form-message${type ? ` ${type}` : ''}`;
  el.textContent = message;
  el.hidden = false;
}

function setSettingsBusy(busy) {
  $('test-connection').disabled = busy;
  $('save-settings').disabled = busy;
  $('settings-close').disabled = busy;
  if (busy) closeModelSuggestions();
  for (const id of ['api-provider', 'api-endpoint', 'api-model', 'api-model-toggle', 'api-concurrency', 'api-key', 'clear-key', 'toggle-key']) {
    $(id).disabled = busy;
  }
}

async function saveSettings(e) {
  e.preventDefault();
  if (!$('settings-form').reportValidity()) return;
  const payload = settingsPayload();
  setSettingsBusy(true);
  try {
    const result = await api('/settings', { method: 'PUT', body: JSON.stringify(payload) });
    state.settings = 'endpoint' in result ? result : await api('/settings');
    renderSettingsStatus();
    $('settings-dialog').close();
    $('api-key').value = '';
    toast('翻译设置已保存。');
  } catch (error) { settingsMessage(error.message, 'error'); }
  finally { setSettingsBusy(false); }
}

async function testConnection() {
  if (!$('settings-form').reportValidity()) return;
  const payload = settingsPayload();
  if (requiresKey(payload) && !(payload.api_key || (!state.keyDirty && state.settings?.has_key))) {
    settingsMessage('请先填写该服务商的 API Key，再测试连接。', 'error');
    $('api-key').focus();
    return;
  }
  setSettingsBusy(true);
  settingsMessage('正在使用表单中的服务商、地址和模型发送简短测试请求，测试不会保存设置…', 'loading');
  try {
    const result = await api('/settings/test', { method: 'POST', body: JSON.stringify(payload) });
    settingsMessage(result.message || (result.ok ? '连接成功，可以开始翻译。' : '连接未成功，请检查配置。'), result.ok ? '' : 'error');
  } catch (error) { settingsMessage(error.message, 'error'); }
  finally { setSettingsBusy(false); }
}

async function exportPaper(format) {
  $('export-menu').hidden = true;
  if (!state.paper) return;
  const link = document.createElement('a');
  link.href = `/api/papers/${encodeURIComponent(state.paper.id)}/export?format=${format}`;
  link.download = `${state.paper.title || '双语论文'}.${format === 'pdf' ? 'pdf' : format === 'html' ? 'html' : 'md'}`;
  document.body.append(link);
  link.click();
  link.remove();
}

function wireEvents() {
  $('brand-home').addEventListener('click', (e) => { e.preventDefault(); if (!state.loading) showWelcome(); });
  $('sidebar-import').addEventListener('click', () => $('pdf-input').click());
  $('welcome-import').addEventListener('click', (e) => { e.stopPropagation(); $('pdf-input').click(); });
  $('drop-zone').addEventListener('click', () => { if (!state.importing) $('pdf-input').click(); });
  $('drop-zone').addEventListener('keydown', (e) => { if (!state.importing && e.target === $('drop-zone') && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); $('pdf-input').click(); } });
  $('pdf-input').addEventListener('change', (e) => importPaper(e.target.files[0]));
  let dragDepth = 0;
  $('drop-zone').addEventListener('dragenter', (e) => { e.preventDefault(); dragDepth++; $('drop-zone').classList.add('dragging'); });
  $('drop-zone').addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  $('drop-zone').addEventListener('dragleave', () => { dragDepth--; if (dragDepth <= 0) $('drop-zone').classList.remove('dragging'); });
  $('drop-zone').addEventListener('drop', (e) => { e.preventDefault(); dragDepth = 0; $('drop-zone').classList.remove('dragging'); importPaper(e.dataTransfer.files[0]); });
  document.addEventListener('dragover', (e) => e.preventDefault());
  document.addEventListener('drop', (e) => e.preventDefault());
  $('load-demo').addEventListener('click', loadDemo);
  $('tab-library').addEventListener('click', () => switchSidebarTab('library'));
  $('tab-outline').addEventListener('click', () => switchSidebarTab('outline'));
  $('toggle-sidebar').addEventListener('click', () => { $('sidebar').classList.add('open'); $('sidebar-backdrop').hidden = false; });
  $('sidebar-backdrop').addEventListener('click', closeSidebar);
  $('open-settings').addEventListener('click', openSettings);
  $('top-settings').addEventListener('click', openSettings);
  $('settings-close').addEventListener('click', () => $('settings-dialog').close());
  $('settings-dialog').addEventListener('click', (e) => { if (e.target === $('settings-dialog') && e.offsetX < 0) $('settings-dialog').close(); });
  $('settings-dialog').addEventListener('close', () => { closeModelSuggestions(); $('api-key').value = ''; $('api-key').type = 'password'; });
  $('settings-form').addEventListener('submit', saveSettings);
  $('test-connection').addEventListener('click', testConnection);
  $('api-provider').addEventListener('change', changeProvider);
  wireModelPicker();
  $('api-endpoint').addEventListener('input', () => {
    credentialContextChanged('API 地址已修改。为避免将密钥发送到其他地址，请重新填写该地址对应的密钥。');
  });
  $('api-key').addEventListener('input', () => { state.keyDirty = true; });
  $('toggle-key').addEventListener('click', () => { const visible = $('api-key').type === 'password'; $('api-key').type = visible ? 'text' : 'password'; $('toggle-key').textContent = visible ? '隐藏' : '显示'; });
  $('clear-key').addEventListener('click', () => { state.keyDirty = true; $('api-key').value = ''; $('api-key').placeholder = '保存后将清除原密钥'; settingsMessage('点击「保存设置」后将清除已保存的密钥。'); });
  $('translate-button').addEventListener('click', () => translatePaper());
  $('ai-layout-button').addEventListener('click', openAiLayout);
  $('ai-layout-form').addEventListener('submit', startAiLayout);
  $('ai-layout-close').addEventListener('click', () => $('ai-layout-dialog').close());
  $('ai-layout-cancel').addEventListener('click', () => $('ai-layout-dialog').close());
  $('ai-layout-pause').addEventListener('click', pauseAiLayout);
  for (const id of ['ai-mode-text', 'ai-mode-vision', 'ai-layout-scope', 'ai-layout-page', 'ai-layout-translate']) {
    $(id).addEventListener('change', updateAiLayoutDescription);
  }
  $('ai-layout-dialog').addEventListener('cancel', (event) => { if (state.aiRequestPending) event.preventDefault(); });
  $('paper-chat-button').addEventListener('click', openPaperChat);
  $('paper-chat-close').addEventListener('click', () => closePaperChat());
  $('paper-chat-backdrop').addEventListener('click', () => closePaperChat());
  $('paper-chat-form').addEventListener('submit', (event) => { event.preventDefault(); askPaper($('paper-chat-input').value); });
  $('paper-chat-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); askPaper(event.currentTarget.value); }
  });
  for (const button of document.querySelectorAll('.paper-chat-prompts button')) {
    button.addEventListener('click', () => { openPaperChat(); askPaper(button.textContent); });
  }
  $('retry-failed').addEventListener('click', () => translatePaper(true));
  $('font-minus').addEventListener('click', () => setFontSize(state.fontSize - 1));
  $('font-plus').addEventListener('click', () => setFontSize(state.fontSize + 1));
  $('view-bilingual').addEventListener('click', () => setBilingual(true));
  $('view-original').addEventListener('click', () => setBilingual(false));
  $('view-pdf').addEventListener('click', () => setReadingMode('pdf'));
  $('original-page-prev').addEventListener('click', () => setOriginalPage(state.originalPage - 1));
  $('original-page-next').addEventListener('click', () => setOriginalPage(state.originalPage + 1));
  $('original-page').addEventListener('change', () => setOriginalPage($('original-page').value));
  $('original-page').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); setOriginalPage($('original-page').value); } });
  $('original-zoom').addEventListener('change', () => {
    $('original-pdf-pages').style.setProperty('--original-page-scale', $('original-zoom').value);
    setOriginalPage(state.originalPage);
  });
  $('original-pdf-pages').addEventListener('scroll', () => {
    if (state.originalScrollFrame) return;
    state.originalScrollFrame = requestAnimationFrame(() => { state.originalScrollFrame = null; updateOriginalPageFromScroll(); });
  }, { passive: true });
  $('toggle-search').addEventListener('click', () => $('search-bar').hidden ? openSearch() : closeSearch());
  $('search-close').addEventListener('click', closeSearch);
  $('document-search').addEventListener('input', () => { state.search = $('document-search').value.trim(); renderParagraphs(true); updateSearchHits(true); });
  $('document-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); moveSearchHit(e.shiftKey ? -1 : 1); } });
  $('search-prev').addEventListener('click', () => moveSearchHit(-1));
  $('search-next').addEventListener('click', () => moveSearchHit(1));
  $('export-button').addEventListener('click', (e) => { e.stopPropagation(); $('export-menu').hidden = !$('export-menu').hidden; });
  $('export-menu').addEventListener('click', (e) => { const button = e.target.closest('[data-export]'); if (button) exportPaper(button.dataset.export); });
  document.addEventListener('click', (e) => { if (!e.target.closest('.export-container')) $('export-menu').hidden = true; });
  $('back-to-top').addEventListener('click', () => $('reader-scroll').scrollTo({ top: 0, behavior: 'smooth' }));
  $('reader-scroll').addEventListener('scroll', () => { if (state.scrollFrame) return; state.scrollFrame = requestAnimationFrame(() => { state.scrollFrame = null; updateReadingPosition(); }); }, { passive: true });
  window.addEventListener('resize', updateReadingPosition);
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && state.paper && state.readMode !== 'pdf' && !$('settings-dialog').open && !$('confirm-dialog').open && !$('ai-layout-dialog').open && $('paper-chat-panel').hidden) { e.preventDefault(); openSearch(); }
    if (e.key === 'Escape') { $('export-menu').hidden = true; closeSidebar(); closePaperChat(); if (!$('search-bar').hidden && !$('settings-dialog').open) closeSearch(); }
  });
}

async function init() {
  wireEvents();
  setFontSize(state.fontSize);
  initAppearance({ onFontSizeChange: (size) => setFontSize(size) });
  initLayout();
  setBilingual(true);
  const [settings, papers, providers] = await Promise.allSettled([api('/settings'), api('/papers'), api('/providers')]);
  if (providers.status === 'fulfilled') {
    const values = Array.isArray(providers.value) ? providers.value : providers.value.providers || [];
    state.providers = values.filter((provider) => typeof provider.id === 'string' && typeof provider.name === 'string');
  }
  renderProviders();
  if (settings.status === 'fulfilled') { state.settings = settings.value; renderSettingsStatus(); }
  else toast(settings.reason.message, true);
  if (papers.status === 'fulfilled') { state.papers = Array.isArray(papers.value) ? papers.value : papers.value.papers || []; renderLibrary(); }
  else if (settings.status === 'fulfilled') toast(papers.reason.message, true);
  const last = localStorage.getItem('folio.lastPaper');
  if (last && state.papers.some((p) => p.id === last)) await loadPaper(last);
  if (settings.status === 'fulfilled' && !isModelConfigured()) openSettings();
}

init();
