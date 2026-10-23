import { initAppearance } from './appearance.js';
import { initLayout } from './layout.js';
import { renderMath, splitMathText } from './math.js';

const $ = (id) => document.getElementById(id);
const icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const state = {
  papers: [], paper: null, settings: null, providers: [], poll: null, loading: false, importing: false,
  loadToken: 0, keyDirty: false, search: '', hits: [], hitIndex: -1,
  fontSize: Number(localStorage.getItem('folio.fontSize')) || 18,
  bilingual: true, readMode: 'bilingual', originalPage: 1, originalPaperId: null, originalScrollFrame: null,
  blocks: new Map(), figures: new Map(), outlineSignature: '', scrollFrame: null,
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
    meta.append(node('span', '', `${paper.page_count || 0} 页`));
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
  const summary = { ...paper };
  delete summary.paragraphs;
  delete summary.outline;
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
  clearTimeout(state.poll);
  state.loadToken++;
  state.paper = null;
  state.blocks.clear();
  state.figures.clear();
  state.outlineSignature = '';
  state.loading = false;
  $('breadcrumb-title').textContent = '开始探索';
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
  if (!/\.pdf$/i.test(file.name)) { toast('请选择 PDF 格式的论文文件。', true); return; }
  if (file.size > 50 * 1024 * 1024) { toast('PDF 不能超过 50 MB，请选择较小的文件。', true); return; }
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
  $('import-status-text').textContent = `正在导入「${file.name}」· 优先提取正文，插图随后在后台整理`;
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

function displayPaper(paper, fresh = false) {
  const newDocument = state.paper?.id !== paper.id;
  const wasDemo = state.paper?.id === paper.id && state.paper?.is_demo;
  state.paper = paper;
  if (wasDemo) state.paper.is_demo = true;
  state.loading = false;
  showView('reader');
  $('breadcrumb-title').textContent = paper.title || paper.filename;
  $('paper-title').textContent = paper.title || paper.filename;
  $('paper-date').textContent = dateLabel(paper.created_at);
  $('paper-type').textContent = isDemo(paper) ? 'FOLIO SAMPLE · 示例文稿' : 'YOUR RESEARCH, IN FOCUS';
  $('paper-pages').textContent = `${paper.page_count || 0} 页`;
  $('paper-paragraphs').textContent = `${paper.paragraph_count || paper.paragraphs?.length || 0} 段`;
  $('paper-translated').textContent = `已翻译 ${paper.translated_count || 0} 段`;
  $('paper-translated').hidden = !(paper.translated_count > 0);
  $('open-original').href = `/api/papers/${encodeURIComponent(paper.id)}/original`;
  $('open-original').hidden = !!isDemo(paper);
  $('original-pdf-fallback').href = $('open-original').href;
  $('view-pdf').disabled = !!isDemo(paper);
  $('view-pdf').title = isDemo(paper) ? '示例文稿没有原始 PDF，请导入自己的论文体验原版阅读' : '保留论文原有双栏、图片及公式的位置';
  if (newDocument) {
    state.originalPage = 1;
    $('original-pdf-pages').replaceChildren();
    state.originalPaperId = null;
  }
  const notice = isDemo(paper) ? '示例文稿 · 本文为演示阅读排版而创作，内容与译文仅用于体验，不是真实发表的论文。你可以导入自己的 PDF，开启正式阅读。' : (paper.warnings || []).join(' ');
  $('paper-notice').hidden = !notice;
  $('paper-notice').textContent = notice;
  if (fresh) {
    state.blocks.clear();
    state.figures.clear();
    state.outlineSignature = '';
    state.search = '';
    state.hits = [];
    state.hitIndex = -1;
    $('document-search').value = '';
    $('search-bar').hidden = true;
    $('paragraphs').replaceChildren();
    $('reader-scroll').scrollTop = 0;
    switchSidebarTab('outline');
  }
  renderParagraphs();
  renderFigures();
  renderOutline();
  renderTranslationStatus();
  renderFigureStatus();
  setReadingMode(state.readMode === 'pdf' && isDemo(paper) ? 'bilingual' : state.readMode);
  updateSummary(paper);
  localStorage.setItem('folio.lastPaper', paper.id);
  schedulePoll();
  if (fresh) requestAnimationFrame(updateReadingPosition);
}

function translatedText(paragraph) {
  return typeof paragraph.translation === 'string' ? paragraph.translation : '';
}

function paragraphText(element, text) {
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

function renderParagraphs(force = false) {
  const paragraphs = state.paper?.paragraphs || [];
  const figureTextIds = new Set((state.paper?.figures || []).flatMap((figure) => Array.isArray(figure.text_paragraph_ids) ? figure.text_paragraph_ids.map(String) : []));
  let searchChanged = false;
  for (const paragraph of paragraphs) {
    const id = String(paragraph.id);
    // The figure already contains these labels at their original coordinates.
    // Keep source text and translations in storage; suppress only confirmed duplicates here.
    if (paragraph.kind !== 'heading' && figureTextIds.has(id)) {
      const duplicate = state.blocks.get(id);
      if (duplicate) { duplicate.remove(); state.blocks.delete(id); searchChanged = true; }
      continue;
    }
    let block = state.blocks.get(id);
    if (!block) {
      block = node('section', `paragraph-block${paragraph.kind === 'heading' ? ' is-heading' : ''}`);
      block.id = `paragraph-${id}`;
      block.dataset.paragraphId = id;
      block.dataset.level = paragraph.level || 1;
      block.dataset.page = paragraph.page || 1;
      block.append(node('span', 'paragraph-page', `P.${paragraph.page || 1}`));
      block.append(node(paragraph.kind === 'heading' ? 'h2' : 'div', 'paragraph-original'));
      block.append(node('div', 'paragraph-translation'));
      state.blocks.set(id, block);
      $('paragraphs').append(block);
    }
    const text = paragraph.text || '';
    const translation = translatedText(paragraph);
    const retainedParts = !translation && Array.isArray(paragraph.previous_translation_parts)
      ? paragraph.previous_translation_parts.filter((part) => typeof part.translation === 'string' && part.translation.trim()) : [];
    const signature = JSON.stringify([text, translation, paragraph.error, state.search, paragraph.math_status, paragraph.equation_image, retainedParts]);
    if (!force && block.dataset.signature === signature) continue;
    block.dataset.signature = signature;
    const original = block.querySelector('.paragraph-original');
    const sourceEquation = paragraph.math_status === 'source-image' && paragraph.equation_image;
    original.classList.toggle('has-equation-source', !!sourceEquation);
    if (sourceEquation) renderEquationSource(original, paragraph);
    else {
      delete original.dataset.equationSignature;
      paragraphText(original, text);
      if (paragraph.math_status === 'latex' && paragraph.equation_source && !original.querySelector('.equation-original-link')) {
        const source = node('a', 'equation-original-link', '查看原式 ↗');
        source.href = `/api/papers/${encodeURIComponent(state.paper.id)}/equations/${encodeURIComponent(paragraph.id)}.png`;
        source.target = '_blank';
        source.rel = 'noopener';
        original.append(source);
      }
    }
    const target = block.querySelector('.paragraph-translation');
    target.className = 'paragraph-translation';
    target.hidden = !translation && !paragraph.error && !retainedParts.length;
    block.classList.toggle('has-translation', !!translation || retainedParts.length > 0);
    block.classList.toggle('has-translation-error', !!paragraph.error && !translation);
    if (translation) paragraphText(target, translation);
    else if (retainedParts.length) {
      delete target.dataset.textSignature;
      delete target.dataset.mathSearchHit;
      target.classList.add('partial');
      target.replaceChildren(node('small', 'retained-translation-note', '已保存的部分译文，继续翻译可补全'));
      for (const part of retainedParts) {
        const fragment = node('div', 'retained-translation-part');
        paragraphText(fragment, part.translation);
        target.append(fragment);
      }
      if (paragraph.error) {
        const retry = node('button', 'retained-translation-retry', '重试此段');
        retry.title = paragraph.error;
        retry.addEventListener('click', () => retryParagraph(paragraph.id, retry));
        target.append(retry);
      }
    }
    else if (paragraph.error) {
      delete target.dataset.textSignature;
      delete target.dataset.mathSearchHit;
      target.classList.add('failed');
      target.replaceChildren();
      target.append(node('span', '', '这一段暂未译出，可重试继续。'));
      const retry = node('button', '');
      retry.innerHTML = `${icon('retry')}重试`;
      retry.title = paragraph.error;
      retry.addEventListener('click', () => retryParagraph(paragraph.id, retry));
      target.append(retry);
    } else {
      delete target.dataset.textSignature;
      delete target.dataset.mathSearchHit;
      // Empty translations take no space. A single instruction lives in the article header.
      target.replaceChildren();
    }
    searchChanged = true;
  }
  if (state.search && searchChanged) updateSearchHits(false);
}

function renderEquationSource(element, paragraph) {
  const signature = JSON.stringify([state.paper.id, paragraph.id, paragraph.text, paragraph.equation_image]);
  if (element.dataset.equationSignature === signature) return;
  element.dataset.equationSignature = signature;
  delete element.dataset.textSignature;
  delete element.dataset.mathSearchHit;
  element.replaceChildren();
  const url = `/api/papers/${encodeURIComponent(state.paper.id)}/equations/${encodeURIComponent(paragraph.id)}.png`;
  const link = node('a', 'equation-source-link');
  link.href = url;
  link.target = '_blank';
  link.rel = 'noopener';
  link.title = '在新窗口查看原数学公式';
  const image = document.createElement('img');
  image.loading = 'lazy';
  image.decoding = 'async';
  image.alt = `第 ${paragraph.page || 1} 页的原数学公式`;
  const dimensions = paragraph.equation_image;
  if (Number(dimensions.width) > 0 && Number(dimensions.height) > 0) {
    image.width = Math.min(10000, Number(dimensions.width));
    image.height = Math.min(10000, Number(dimensions.height));
    // PDF coordinates are points; scale the source glyphs with the reader text.
    image.style.width = `${Math.min(10000, Number(dimensions.width)) / 11}em`;
  }
  const note = node('small', 'equation-source-note');
  const noteText = node('span', '', '原式（复杂公式保留原图）');
  note.append(noteText, imageRetryButton(image, url));
  image.addEventListener('error', () => { image.hidden = true; noteText.textContent = '原式图片暂未加载，可重试或打开原版 PDF 查看。'; });
  image.addEventListener('load', () => { image.hidden = false; noteText.textContent = '原式（复杂公式保留原图）'; });
  image.src = url;
  link.append(image);
  element.append(link, note);
  if (paragraph.text) {
    const details = node('details', 'equation-extracted-source');
    details.append(node('summary', '', '查看抽取文字'), node('code', '', paragraph.text));
    element.append(details);
  }
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
  const tails = new Map();
  for (const [index, figure] of figures.entries()) {
    const id = String(figure.id || `figure-${figure.page || 1}-${index}`);
    active.add(id);
    const caption = figure.caption || `原文插图 · 第 ${figure.page || 1} 页`;
    const url = figureURL(figure.url);
    const signature = JSON.stringify([caption, url, figure.width, figure.height]);
    let block = state.figures.get(id);
    if (!block) {
      block = node('figure', 'paper-figure');
      block.dataset.figureId = id;
      state.figures.set(id, block);
    }
    if (block.dataset.signature !== signature) {
      block.dataset.signature = signature;
      block.replaceChildren();
      const fallback = node('p', 'figure-error', '这张插图暂时无法显示，可点击工具栏的「查看原始 PDF」阅读。');
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
      const compactCaption = caption.replace(/\s+/g, ' ').trim();
      captionNode.hidden = (state.paper.paragraphs || []).some((paragraph) => Number(paragraph.page || 1) === Number(figure.page || 1) && (paragraph.text || '').replace(/\s+/g, ' ').trim() === compactCaption);
      block.append(fallback, captionNode);
    }
    const beforeAnchor = figure.before_paragraph_id == null ? null : state.blocks.get(String(figure.before_paragraph_id));
    let anchor = beforeAnchor || state.blocks.get(String(figure.after_paragraph_id));
    if (!anchor) {
      const preceding = (state.paper.paragraphs || []).filter((paragraph) => Number(paragraph.page || 1) <= Number(figure.page || 1));
      anchor = state.blocks.get(String(preceding[preceding.length - 1]?.id));
    }
    const anchorId = `${beforeAnchor ? 'before' : 'after'}:${anchor?.dataset.paragraphId || 'end'}`;
    const previous = tails.get(anchorId) || (beforeAnchor ? null : anchor);
    if (previous) {
      if (block.previousElementSibling !== previous) previous.after(block);
    } else if (beforeAnchor) {
      if (block.nextElementSibling !== beforeAnchor) beforeAnchor.before(block);
    } else if (block.parentElement !== $('paragraphs')) $('paragraphs').append(block);
    tails.set(anchorId, block);
  }
  for (const [id, block] of state.figures) {
    if (!active.has(id)) { block.remove(); state.figures.delete(id); }
  }
  let notice = $('figure-load-notice');
  if (state.paper?.figures_error) {
    if (!notice) { notice = node('p', 'figure-error'); notice.id = 'figure-load-notice'; }
    notice.textContent = '部分插图未能加载，正文仍可阅读。请打开原始 PDF 查看完整图表。';
    $('paragraphs').append(notice);
  } else notice?.remove();
}

function getOutline() {
  if (state.paper?.outline?.length) return state.paper.outline;
  return (state.paper?.paragraphs || []).filter((p) => p.kind === 'heading').map((p) => ({ id: p.id, title: p.text, level: p.level || 1, page: p.page, paragraph_id: p.id }));
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
    item.append(node('span', 'outline-page', String(section.page || '')));
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
  updateReadingPosition();
}

function renderTranslationStatus() {
  const paper = state.paper;
  if (!paper) return;
  const paragraphs = paper.paragraphs || [];
  const total = paper.progress?.total ?? paper.paragraph_count ?? paragraphs.length;
  const done = paper.progress?.done ?? paper.translated_count ?? paragraphs.filter((p) => !!p.translation).length;
  const failed = paper.progress?.failed ?? paragraphs.filter((p) => !!p.error && !p.translation).length;
  const noTextToTranslate = total === 0 && paragraphs.length > 0;
  const running = paper.status === 'translating';
  const paused = paper.status === 'paused';
  const completed = paper.status === 'completed' || done >= total && total > 0;
  const banner = $('translation-banner');
  banner.hidden = !running && !paused && !failed && !paper.error && !done;
  banner.classList.toggle('running', running);
  banner.classList.toggle('has-error', !!failed || paper.status === 'error');
  $('translation-status').textContent = running ? '翻译进行中' : completed ? '双语文稿已就绪' : paused ? '翻译已暂停' : failed || paper.error ? '部分内容需要重试' : '已保留当前译文';
  $('translation-detail').textContent = `${done} / ${total} 段${failed ? ` · ${failed} 段未成功` : running ? ' · 可随时阅读已完成的部分' : ''}`;
  if (paper.error) $('translation-detail').title = paper.error;
  $('translation-progress').style.width = `${Math.min(100, total ? done / total * 100 : 0)}%`;
  $('retry-failed').hidden = !failed || running;
  const button = $('translate-button');
  button.innerHTML = `${icon(running ? 'pause' : completed || noTextToTranslate ? 'check' : 'spark')}<span>${running ? '暂停翻译' : noTextToTranslate ? '无需翻译' : completed ? '翻译完成' : paused || done ? '继续翻译' : '全文翻译'}</span>`;
  button.disabled = noTextToTranslate || completed && !failed;
  button.title = running ? '暂停后会保留已完成的译文' : noTextToTranslate ? '当前文稿没有需要翻译的正文段落' : completed ? '所有正文段落均已完成翻译' : '用当前配置的模型逐段翻译';
  $('reader-help').hidden = noTextToTranslate || running || done > 0 || !!paper.error || !!failed || !state.bilingual;
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

function schedulePoll() {
  clearTimeout(state.poll);
  if (state.paper?.status !== 'translating' && !['extracting', 'pending'].includes(state.paper?.figure_status)) return;
  const id = state.paper.id;
  state.poll = setTimeout(async () => {
    try {
      const paper = await api(`/papers/${encodeURIComponent(id)}`);
      if (state.paper?.id !== id || state.loading) return;
      const previous = state.paper.status;
      displayPaper(paper);
      if (previous === 'translating' && paper.status === 'completed') toast('全文翻译完成，安心读下去。');
      if (previous === 'translating' && paper.status === 'error') toast(paper.error || '翻译暂时遇到问题，已保留完成的译文。', true);
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
  $('confirm-description').textContent = `「${paper.title || paper.filename}」及已保存的译文将被移除，源 PDF 文件不受影响。`;
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

function updateReadingPosition() {
  if (!state.paper || $('reader-view').hidden) return;
  if (state.readMode === 'pdf') {
    const page = state.originalPage;
    const items = [...$('outline-list').querySelectorAll('.outline-item')];
    let current = null;
    for (const item of items) if (Number(item.dataset.page) <= page) current = item;
    for (const item of items) item.classList.toggle('active', item === current);
    $('reading-location').textContent = `原版 PDF · 定位到第 ${page} 页 / 共 ${state.paper.page_count || 1} 页`;
    $('read-progress').textContent = '';
    return;
  }
  const scroll = $('reader-scroll');
  const max = scroll.scrollHeight - scroll.clientHeight;
  $('read-progress').textContent = `${max > 0 ? Math.min(100, Math.round(scroll.scrollTop / max * 100)) : 100}%`;
  const top = scroll.getBoundingClientRect().top + 100;
  const outlineItems = [...$('outline-list').querySelectorAll('.outline-item')];
  let current = outlineItems[0];
  for (const item of outlineItems) {
    const block = state.blocks.get(item.dataset.paragraphId);
    if (block && block.getBoundingClientRect().top <= top) current = item;
  }
  for (const item of outlineItems) item.classList.toggle('active', item === current);
  let page = 1;
  for (const block of state.blocks.values()) {
    if (block.getBoundingClientRect().top <= top) page = Number(block.dataset.page) || page;
    else break;
  }
  $('reading-location').textContent = current ? `${current.querySelector('.outline-english').textContent} · 第 ${page} 页` : `第 ${page} 页 / 共 ${state.paper.page_count || 1} 页`;
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
  if (!state.paper || isDemo(state.paper) || state.originalPaperId === state.paper.id) return false;
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
  let page = 1;
  for (const element of region.children) {
    if (element.getBoundingClientRect().top <= top) page = Number(element.dataset.page) || page;
    else break;
  }
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

function renderProviderDetails() {
  const provider = providerInfo($('api-provider').value);
  $('provider-mark').textContent = provider.id === 'custom' ? '↗' : provider.name.slice(0, 1);
  $('provider-name').textContent = provider.name;
  $('provider-note').textContent = provider.note || '兼容 OpenAI Chat Completions 接口';
  $('api-endpoint').placeholder = provider.endpoint || 'https://your-service.example/v1/chat/completions';
  $('api-model').placeholder = '选择建议或输入你有权限使用的模型';
  $('api-model-options').replaceChildren(...(provider.models || []).map((model) => {
    const option = document.createElement('option');
    option.value = model;
    return option;
  }));
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
  for (const id of ['api-provider', 'api-endpoint', 'api-model', 'api-concurrency', 'api-key', 'clear-key', 'toggle-key']) {
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
  $('settings-dialog').addEventListener('close', () => { $('api-key').value = ''; $('api-key').type = 'password'; });
  $('settings-form').addEventListener('submit', saveSettings);
  $('test-connection').addEventListener('click', testConnection);
  $('api-provider').addEventListener('change', changeProvider);
  $('api-endpoint').addEventListener('input', () => {
    credentialContextChanged('API 地址已修改。为避免将密钥发送到其他地址，请重新填写该地址对应的密钥。');
  });
  $('api-key').addEventListener('input', () => { state.keyDirty = true; });
  $('toggle-key').addEventListener('click', () => { const visible = $('api-key').type === 'password'; $('api-key').type = visible ? 'text' : 'password'; $('toggle-key').textContent = visible ? '隐藏' : '显示'; });
  $('clear-key').addEventListener('click', () => { state.keyDirty = true; $('api-key').value = ''; $('api-key').placeholder = '保存后将清除原密钥'; settingsMessage('点击「保存设置」后将清除已保存的密钥。'); });
  $('translate-button').addEventListener('click', () => translatePaper());
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
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f' && state.paper && state.readMode !== 'pdf' && !$('settings-dialog').open && !$('confirm-dialog').open) { e.preventDefault(); openSearch(); }
    if (e.key === 'Escape') { $('export-menu').hidden = true; closeSidebar(); if (!$('search-bar').hidden && !$('settings-dialog').open) closeSearch(); }
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
