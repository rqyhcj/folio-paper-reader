// Reading preferences stay in this browser. No preference contains API credentials.
const STORAGE_KEY = 'folio.appearance.v1';
const FONTS = {
  serif: { label: '系统衬线', stack: "Georgia, 'Times New Roman', 'Noto Serif SC', 'SimSun', serif" },
  sans: { label: '系统无衬线', stack: "system-ui, -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif" },
  songti: { label: '宋体', stack: "'SimSun', 'Songti SC', 'Noto Serif SC', serif" },
  yahei: { label: '微软雅黑', stack: "'Microsoft YaHei', 'PingFang SC', 'Noto Sans SC', sans-serif" },
  mono: { label: '等宽字体', stack: "'Cascadia Code', Consolas, 'SFMono-Regular', 'Microsoft YaHei', monospace" },
};
const THEMES = {
  light: { label: '清浅白', background: '#fbfcf8', sidebar: '#f0f1e9', accent: '#2e6456', englishColor: '#354534', chineseColor: '#52615a' },
  cream: { label: '暖纸色', background: '#fbf3e3', sidebar: '#efe5d1', accent: '#875c38', englishColor: '#40362a', chineseColor: '#695039' },
  dark: { label: '夜读黑', background: '#171f21', sidebar: '#11191b', accent: '#79b6a2', englishColor: '#e1e8e4', chineseColor: '#b9d1c4' },
};
const DEFAULTS = {
  theme: 'light', ...THEMES.light, englishFont: 'serif', chineseFont: 'songti', interfaceFont: 'sans',
  englishSize: 18, chineseSize: 18, interfaceSize: 14, englishWeight: 400, chineseWeight: 400,
  lineHeight: 1.9, contentWidth: 860,
};
delete DEFAULTS.label;
const COLOR_FIELDS = ['background', 'sidebar', 'accent', 'englishColor', 'chineseColor'];
const LIMITS = { englishSize: [12, 32], chineseSize: [12, 32], interfaceSize: [12, 18], lineHeight: [1.4, 2.4], contentWidth: [640, 1200] };

function hexRgb(hex) {
  return [1, 3, 5].map((offset) => parseInt(hex.slice(offset, offset + 2), 16));
}
function mix(a, b, amount) {
  const first = hexRgb(a), second = hexRgb(b);
  return `#${first.map((n, i) => Math.round(n + (second[i] - n) * amount).toString(16).padStart(2, '0')).join('')}`;
}
function luminance(hex) {
  const [r, g, b] = hexRgb(hex).map((n) => { const v = n / 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
  return .2126 * r + .7152 * g + .0722 * b;
}
function contrast(a, b) {
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + .05) / (values[1] + .05);
}
function foreground(background) {
  const preferred = contrast('#edf3ef', background) > contrast('#243b33', background) ? '#edf3ef' : '#243b33';
  if (contrast(preferred, background) >= 4.5) return preferred;
  return contrast('#ffffff', background) > contrast('#000000', background) ? '#ffffff' : '#000000';
}
function readableAccent(accent, background) {
  const target = foreground(background);
  for (let amount = 0; amount <= 1; amount += .05) {
    const value = mix(accent, target, amount);
    if (contrast(value, background) >= 4.5) return value;
  }
  return target;
}
function normalise(value) {
  const next = { ...DEFAULTS };
  if (!value || typeof value !== 'object' || Array.isArray(value)) return next;
  next.theme = Object.hasOwn(THEMES, value.theme) ? value.theme : 'custom';
  for (const key of COLOR_FIELDS) if (/^#[\da-f]{6}$/i.test(value[key] || '')) next[key] = value[key].toLowerCase();
  for (const key of ['englishFont', 'chineseFont', 'interfaceFont']) if (Object.hasOwn(FONTS, value[key])) next[key] = value[key];
  for (const [key, [min, max]] of Object.entries(LIMITS)) {
    const n = Number(value[key]);
    if (Number.isFinite(n)) next[key] = Math.min(max, Math.max(min, key === 'lineHeight' ? Math.round(n * 10) / 10 : Math.round(n)));
  }
  for (const key of ['englishWeight', 'chineseWeight']) if ([400, 500, 600, 700].includes(Number(value[key]))) next[key] = Number(value[key]);
  return next;
}

function fontOptions() {
  return Object.entries(FONTS).map(([key, font]) => `<option value="${key}">${font.label}</option>`).join('');
}
function rangeControl(key, label, min, max, step, unit) {
  return `<div class="appearance-range"><div class="appearance-label-row"><label for="appearance-${key}">${label}</label><output for="appearance-${key}" data-output="${key}"></output></div><input id="appearance-${key}" data-setting="${key}" type="range" min="${min}" max="${max}" step="${step}" aria-describedby="appearance-${key}-hint"><div class="appearance-range-limits" id="appearance-${key}-hint"><span>${min}${unit}</span><span>${max}${unit}</span></div></div>`;
}
function fontControl(language, label) {
  return `<div class="appearance-type-card"><div class="appearance-type-label"><span>${language === 'english' ? 'Aa' : '文'}</span><strong>${label}</strong></div><label for="appearance-${language}Font">字体</label><select id="appearance-${language}Font" data-setting="${language}Font">${fontOptions()}</select><div class="appearance-weight-row"><label for="appearance-${language}Weight">字重</label><select id="appearance-${language}Weight" data-setting="${language}Weight"><option value="400">常规 · 400</option><option value="500">适中 · 500</option><option value="600">半粗 · 600</option><option value="700">粗体 · 700</option></select></div>${rangeControl(`${language}Size`, '字号', 12, 32, 1, 'px')}</div>`;
}
function createDialog() {
  const dialog = document.createElement('dialog');
  dialog.id = 'appearance-dialog';
  dialog.className = 'appearance-dialog';
  dialog.setAttribute('aria-labelledby', 'appearance-title');
  dialog.setAttribute('aria-describedby', 'appearance-description');
  dialog.innerHTML = `
    <div class="appearance-header"><div class="appearance-heading"><span class="appearance-emblem" aria-hidden="true">Aa</span><div><span class="mini-label">YOUR PAGE, YOUR PACE</span><h2 id="appearance-title">读成你喜欢的样子</h2></div></div><button type="button" class="icon-button" data-appearance-close aria-label="关闭阅读外观设置"><svg aria-hidden="true"><use href="#i-close"/></svg></button></div>
    <p class="appearance-description" id="appearance-description">调整即刻生效，在这个浏览器里为你记住。</p>
    <div class="appearance-body"><div class="appearance-controls">
      <section class="appearance-section" aria-labelledby="appearance-color-heading"><div class="appearance-section-heading"><span>01</span><h3 id="appearance-color-heading">页面与色彩</h3><small id="appearance-theme-label">清浅白</small></div><div class="appearance-theme-grid" role="group" aria-label="页面主题">${Object.entries(THEMES).map(([key, value]) => `<button type="button" data-theme="${key}" class="appearance-theme appearance-theme-${key}" aria-pressed="false"><span class="appearance-theme-mini" aria-hidden="true"><i></i><span><b></b><b></b><b></b></span><em>✓</em></span><strong>${value.label}</strong></button>`).join('')}</div><div class="appearance-color-grid">${[['background', '页面背景'], ['sidebar', '侧栏背景'], ['accent', '强调颜色'], ['englishColor', '英文字色'], ['chineseColor', '中文字色']].map(([key, label]) => `<label class="appearance-color-field" for="appearance-${key}"><span>${label}</span><span class="appearance-color-value"><input id="appearance-${key}" type="color" data-setting="${key}" aria-label="${label}"><code data-color-value="${key}"></code></span></label>`).join('')}</div></section>
      <section class="appearance-section" aria-labelledby="appearance-type-heading"><div class="appearance-section-heading"><span>02</span><h3 id="appearance-type-heading">字体与节奏</h3></div><div class="appearance-type-grid">${fontControl('english', '英文原文')}${fontControl('chinese', '中文译文')}</div><div class="appearance-layout-grid">${rangeControl('lineHeight', '段落行距', 1.4, 2.4, .1, ' 倍')}${rangeControl('contentWidth', '正文宽度', 640, 1200, 20, 'px')}</div><div class="appearance-interface-row"><div><label for="appearance-interfaceFont">界面字体</label><select id="appearance-interfaceFont" data-setting="interfaceFont">${fontOptions()}</select></div>${rangeControl('interfaceSize', '界面字号', 12, 18, 1, 'px')}</div><p class="appearance-font-help">字体由本机提供；没有安装时会使用相近的系统字体。工具栏 A− / A+ 调整英文原文字号。</p></section>
    </div><aside class="appearance-preview-column" aria-label="外观实时预览"><div class="appearance-preview-label"><span></span>实时预览</div><article class="appearance-preview"><div class="appearance-preview-kicker">A SPACE TO THINK</div><h3>留白，给每一个想法。</h3><div class="appearance-preview-rule"></div><p class="appearance-preview-english" lang="en">Reading is a conversation with an idea. A quiet page, familiar type, and a comfortable rhythm help us follow each thought from one paragraph to the next.</p><p class="appearance-preview-chinese" lang="zh-CN">阅读，是与一个想法的对话。安静的页面、熟悉的字体与舒适的节奏，让我们从这一段，走进下一段。</p><div class="appearance-preview-end" aria-hidden="true">✧</div></article><div class="appearance-contrast" id="appearance-contrast" role="status" aria-live="polite"></div><p class="appearance-preview-note">设置也会同时应用到当前页面。<br>阅读栏宽度以实际阅读窗口为准。</p></aside></div>
    <div class="appearance-footer"><div><button type="button" class="appearance-reset" id="appearance-reset">恢复默认</button><span class="appearance-save-status" id="appearance-save-status" role="status">已自动保存</span></div><button type="button" class="button primary" data-appearance-close>开始阅读<svg aria-hidden="true"><use href="#i-check"/></svg></button></div>`;
  return dialog;
}

/** Add the appearance panel once. onFontSizeChange synchronises app.js's toolbar state. */
export function initAppearance({ onFontSizeChange } = {}) {
  if (document.getElementById('appearance-dialog')) return window.folioAppearance;
  let saved, storageUnavailable = false;
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch { storageUnavailable = true; }
  let state = normalise(saved);
  if (!saved) {
    try {
      const previousSize = Number(localStorage.getItem('folio.fontSize'));
      if (previousSize >= 12 && previousSize <= 32) state.englishSize = previousSize;
    } catch { storageUnavailable = true; }
  }
  const root = document.documentElement;
  const dialog = createDialog();
  document.body.append(dialog);
  let opener;
  const controls = [...dialog.querySelectorAll('[data-setting]')];
  function persist() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); storageUnavailable = false; }
    catch { storageUnavailable = true; }
    dialog.querySelector('#appearance-save-status').textContent = storageUnavailable ? '本浏览器无法保存，当前设置仍生效' : '已自动保存';
  }
  function paint(notifyFontSize = false, save = true) {
    const background = state.background, side = state.sidebar;
    const text = foreground(background), sideText = foreground(side);
    const dark = luminance(text) > .5;
    const panel = mix(background, '#ffffff', dark ? .045 : .65);
    const tint = mix(background, state.accent, dark ? .13 : .07);
    const variables = {
      '--canvas': background, '--sidebar': side, '--teal': state.accent,
      '--teal-dark': mix(state.accent, '#000000', .14), '--teal-light': tint,
      '--ink': text, '--text': text, '--muted': mix(text, background, .29),
      '--line': mix(background, text, .15), '--sans': FONTS[state.interfaceFont].stack,
      '--serif': FONTS[state.englishFont].stack, '--zh-serif': FONTS[state.chineseFont].stack,
      '--folio-panel': panel, '--folio-panel-soft': tint, '--folio-control': mix(background, text, .025),
      '--folio-ui-size': `${state.interfaceSize}px`, '--folio-sidebar-ink': sideText,
      '--folio-sidebar-muted': mix(sideText, side, .28), '--folio-sidebar-line': mix(side, sideText, .15),
      '--folio-sidebar-hover': mix(side, sideText, .065), '--folio-sidebar-active': mix(side, state.accent, .14),
      '--folio-accent-ink': readableAccent(state.accent, background),
      '--folio-sidebar-accent': readableAccent(state.accent, side),
      '--folio-accent-on': contrast('#ffffff', state.accent) >= contrast('#15241d', state.accent) ? '#ffffff' : '#15241d',
      '--folio-input-ink': text, '--folio-original-ink': state.englishColor, '--folio-translation-ink': state.chineseColor,
      '--folio-original-font': FONTS[state.englishFont].stack, '--folio-translation-font': FONTS[state.chineseFont].stack,
      '--folio-original-size': `${state.englishSize}px`, '--folio-translation-size': `${state.chineseSize}px`,
      '--folio-original-weight': String(state.englishWeight), '--folio-translation-weight': String(state.chineseWeight),
      '--folio-line-height': String(state.lineHeight), '--folio-content-width': `${state.contentWidth}px`,
      '--folio-warning-ink': dark ? '#e5be84' : '#8b5d2c', '--folio-warning-bg': mix(background, '#b47a31', .10),
      '--folio-danger-ink': dark ? '#efb2a4' : '#9a4f3c', '--folio-danger-bg': mix(background, '#bb634b', .10),
    };
    for (const [key, value] of Object.entries(variables)) root.style.setProperty(key, value);
    root.dataset.folioAppearance = 'true';
    root.dataset.folioTheme = dark ? 'dark' : 'light';
    root.style.colorScheme = dark ? 'dark' : 'light';
    for (const input of controls) input.value = state[input.dataset.setting];
    for (const output of dialog.querySelectorAll('[data-output]')) {
      const key = output.dataset.output;
      output.value = key === 'lineHeight' ? `${state[key].toFixed(1)} 倍` : `${state[key]} px`;
    }
    for (const value of dialog.querySelectorAll('[data-color-value]')) value.textContent = state[value.dataset.colorValue].toUpperCase();
    for (const button of dialog.querySelectorAll('[data-theme]')) button.setAttribute('aria-pressed', String(button.dataset.theme === state.theme));
    dialog.querySelector('#appearance-theme-label').textContent = THEMES[state.theme]?.label || '自定义';
    const englishRatio = contrast(state.englishColor, background);
    const chineseRatio = contrast(state.chineseColor, tint);
    const contrastNote = dialog.querySelector('#appearance-contrast');
    const low = englishRatio < 4.5 || chineseRatio < 4.5;
    contrastNote.classList.toggle('is-low', low);
    contrastNote.textContent = `${low ? '△ 对比度偏低，可继续使用或调整字色' : '✓ 正文字色清晰'} · 英文 ${englishRatio.toFixed(1)}:1 / 中文 ${chineseRatio.toFixed(1)}:1`;
    if (save) persist();
    else dialog.querySelector('#appearance-save-status').textContent = storageUnavailable ? '本浏览器无法保存，当前设置仍生效' : '在此浏览器自动保存';
    if (notifyFontSize && typeof onFontSizeChange === 'function') onFontSizeChange(state.englishSize);
    document.dispatchEvent(new CustomEvent('folio:appearance-change', { detail: { ...state } }));
  }
  function open(event) {
    opener = event?.currentTarget || document.activeElement;
    if (!dialog.open) dialog.showModal();
  }
  function close() { dialog.close(); }
  function reset() { state = { ...DEFAULTS }; paint(true); }
  for (const input of controls) {
    const update = () => {
      const key = input.dataset.setting;
      state = normalise({ ...state, [key]: input.value });
      if (COLOR_FIELDS.includes(key)) state.theme = 'custom';
      paint(key === 'englishSize');
    };
    input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', update);
  }
  for (const button of dialog.querySelectorAll('[data-theme]')) button.addEventListener('click', () => {
    const key = button.dataset.theme;
    const { label: _label, ...colors } = THEMES[key];
    state = { ...state, ...colors, theme: key };
    paint();
  });
  for (const button of dialog.querySelectorAll('[data-appearance-close]')) button.addEventListener('click', close);
  dialog.querySelector('#appearance-reset').addEventListener('click', reset);
  dialog.addEventListener('close', () => { if (opener?.isConnected) opener.focus(); });
  dialog.addEventListener('click', (event) => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
  });
  // Keep the existing global Ctrl+F/Escape shortcuts from interacting with a modal.
  dialog.addEventListener('keydown', (event) => { event.stopPropagation(); });
  const topbar = document.querySelector('.topbar-right');
  const topButton = document.createElement('button');
  topButton.type = 'button';
  topButton.id = 'open-appearance';
  topButton.className = 'appearance-open';
  topButton.title = '阅读外观：主题、字体与排版';
  topButton.setAttribute('aria-label', '阅读外观设置');
  topButton.setAttribute('aria-haspopup', 'dialog');
  topButton.setAttribute('aria-controls', dialog.id);
  topButton.innerHTML = '<span aria-hidden="true">Aa</span><span class="appearance-open-label">阅读外观</span>';
  topButton.addEventListener('click', open);
  topbar?.insertBefore(topButton, topbar.querySelector('#top-settings') || topbar.firstChild);
  document.addEventListener('folio:font-size-change', (event) => {
    const size = Number(event.detail?.size);
    if (!Number.isFinite(size) || size === state.englishSize) return;
    state.englishSize = Math.min(32, Math.max(12, Math.round(size)));
    paint();
  });
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return;
    try { state = normalise(JSON.parse(event.newValue || 'null')); paint(true, false); } catch { /* Retain valid preferences if another tab wrote invalid JSON. */ }
  });
  const controller = { open, close, reset, getState: () => ({ ...state }) };
  window.folioAppearance = controller;
  paint(true, false);
  return controller;
}
