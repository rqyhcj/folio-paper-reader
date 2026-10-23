const STORAGE_KEY = 'folio.layout.v1';
const DEFAULTS = { sidebarWidth: 240, collapsed: false, compact: true, focus: false, fluid: true };
const MIN_WIDTH = 180, MAX_WIDTH = 420;
function normalise(value) {
  const next = { ...DEFAULTS };
  if (!value || typeof value !== 'object') return next;
  if (typeof value.sidebarWidth === 'number' && Number.isFinite(value.sidebarWidth)) next.sidebarWidth = Math.round(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, value.sidebarWidth)));
  for (const key of ['collapsed', 'compact', 'focus', 'fluid']) if (typeof value[key] === 'boolean') next[key] = value[key];
  return next;
}
const ICONS = {
  sidebar: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16m5-11 3 3-3 3"/>',
  collapse: '<path d="m14 6-6 6 6 6"/>',
  compact: '<path d="M4 4h16M4 20h16M12 6v12m-3-9 3-3 3 3m-6 6 3 3 3-3"/>',
  focus: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  width: '<path d="M3 5v14M21 5v14M5 12h14m-10-4-4 4 4 4m6-8 4 4-4 4"/>',
};
function button(id, icon, label) {
  const element = document.createElement('button');
  element.type = 'button';
  element.id = id;
  element.className = 'layout-control';
  element.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[icon]}</svg>`;
  element.title = label;
  element.setAttribute('aria-label', label);
  return element;
}

/** Independent layout preferences. Call after app.js binds its drawer events. */
export function initLayout() {
  if (window.folioLayout) return window.folioLayout;
  const body = document.body;
  const sidebar = document.getElementById('sidebar');
  const toggle = document.getElementById('toggle-sidebar');
  const reader = document.getElementById('reader-view');
  const tools = document.querySelector('.reader-tools');
  if (!sidebar || !toggle || !reader || !tools) return null;
  let saved;
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch { /* Use defaults when local storage is unavailable. */ }
  let state = normalise(saved);
  const desktopQuery = window.matchMedia('(min-width: 741px)');
  const separator = document.createElement('div');
  separator.id = 'layout-sidebar-resizer';
  separator.className = 'layout-sidebar-resizer';
  separator.tabIndex = 0;
  separator.setAttribute('role', 'separator');
  separator.setAttribute('aria-orientation', 'vertical');
  separator.setAttribute('aria-controls', 'sidebar');
  separator.setAttribute('aria-label', '调整目录侧栏宽度');
  separator.setAttribute('aria-valuemin', String(MIN_WIDTH));
  separator.setAttribute('aria-valuemax', String(MAX_WIDTH));
  separator.title = '拖动调整目录宽度；方向键调整，双击恢复默认';
  separator.setAttribute('aria-describedby', 'layout-resizer-help');
  sidebar.after(separator);
  const live = document.createElement('div');
  live.className = 'layout-sr-only';
  live.setAttribute('role', 'status');
  live.setAttribute('aria-live', 'polite');
  const help = document.createElement('span');
  help.id = 'layout-resizer-help';
  help.className = 'layout-sr-only';
  help.textContent = '左右方向键调整10像素，按住Shift调整30像素；Home最窄，End最宽；双击恢复默认。';
  body.append(help, live);
  const collapse = button('layout-sidebar-collapse', 'collapse', '收起目录侧栏');
  collapse.classList.add('layout-sidebar-collapse');
  collapse.setAttribute('aria-controls', 'sidebar');
  sidebar.append(collapse);
  const density = button('layout-density-toggle', 'compact', '展开顶部区域');
  density.setAttribute('aria-keyshortcuts', 'Alt+Shift+C');
  document.querySelector('.topbar-right')?.prepend(density);
  const restore = button('layout-reader-sidebar', 'sidebar', '显示目录侧栏');
  restore.classList.add('layout-reader-sidebar');
  restore.setAttribute('aria-controls', 'sidebar');
  const toolbar = document.querySelector('.reader-toolbar');
  const switcher = toolbar.querySelector('.view-switch');
  const navigation = document.createElement('div');
  navigation.className = 'layout-reader-navigation';
  toolbar.prepend(navigation);
  navigation.append(restore, switcher);
  const fluid = button('layout-fluid-toggle', 'width', '固定正文宽度（使用阅读外观设置）');
  const focus = button('layout-focus-toggle', 'focus', '进入专注阅读');
  focus.setAttribute('aria-keyshortcuts', 'Alt+Shift+F');
  const focusLabel = document.createElement('span');
  focusLabel.className = 'layout-control-label';
  focusLabel.textContent = '专注';
  focus.append(focusLabel);
  tools.prepend(fluid, focus);
  toggle.setAttribute('aria-controls', 'sidebar');
  toggle.setAttribute('aria-keyshortcuts', 'Alt+Shift+B');
  let drag = null, resizeFrame = null;
  let lastAppearanceWidth = window.folioAppearance?.getState().contentWidth;
  function persist() { try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch { /* Current preferences remain usable without persistence. */ } }
  function announce(message) { live.textContent = message; }
  function label(element, text) { element.title = text; element.setAttribute('aria-label', text); }
  function apply(save = true) {
    const desktop = desktopQuery.matches;
    const focused = state.focus && !reader.hidden;
    body.dataset.folioLayout = 'true';
    body.classList.toggle('folio-sidebar-collapsed', desktop && state.collapsed);
    body.classList.toggle('folio-sidebar-narrow', desktop && state.sidebarWidth < 230);
    body.classList.toggle('folio-toolbar-compact', state.compact);
    body.classList.toggle('folio-focus-mode', focused);
    body.classList.toggle('folio-reading-fluid', state.fluid);
    body.style.setProperty('--folio-layout-sidebar-width', `${state.sidebarWidth}px`);
    separator.setAttribute('aria-valuenow', String(state.sidebarWidth));
    separator.setAttribute('aria-valuetext', `${state.sidebarWidth} 像素`);
    separator.tabIndex = desktop && !state.collapsed ? 0 : -1;
    if (desktop && sidebar.classList.contains('open')) {
      sidebar.classList.remove('open');
      document.getElementById('sidebar-backdrop').hidden = true;
    }
    const visible = desktop ? !state.collapsed : sidebar.classList.contains('open');
    sidebar.inert = !visible;
    sidebar.setAttribute('aria-hidden', String(!visible));
    toggle.setAttribute('aria-expanded', String(visible));
    restore.setAttribute('aria-expanded', String(visible));
    label(toggle, visible ? '收起目录侧栏（Alt+Shift+B）' : '显示目录侧栏（Alt+Shift+B）');
    label(restore, visible ? '收起目录侧栏' : '显示目录侧栏');
    density.setAttribute('aria-pressed', String(state.compact));
    label(density, state.compact ? '展开顶部区域（Alt+Shift+C）' : '收紧顶部区域（Alt+Shift+C）');
    focus.setAttribute('aria-pressed', String(focused));
    label(focus, focused ? '退出专注阅读（Alt+Shift+F）' : '进入专注阅读（Alt+Shift+F）');
    focusLabel.textContent = focused ? '退出专注' : '专注';
    fluid.setAttribute('aria-pressed', String(state.fluid));
    label(fluid, state.fluid ? '固定正文宽度（使用阅读外观设置）' : '正文自适应宽度（利用全部阅读空间）');
    if (save) persist();
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = null;
      document.getElementById('reader-scroll')?.dispatchEvent(new Event('scroll'));
      document.dispatchEvent(new CustomEvent('folio:layout-change', { detail: { ...state } }));
    });
  }
  function toggleSidebar() {
    if (desktopQuery.matches) {
      const focusWasInside = sidebar.contains(document.activeElement);
      state.collapsed = !state.collapsed;
      apply();
      if (state.collapsed && focusWasInside) (reader.hidden ? toggle : restore).focus();
    } else if (sidebar.classList.contains('open')) {
      sidebar.classList.remove('open');
      document.getElementById('sidebar-backdrop').hidden = true;
      apply(false);
      (reader.hidden ? toggle : restore).focus();
    } else toggle.click();
  }
  toggle.addEventListener('click', (event) => {
    if (!desktopQuery.matches && !sidebar.classList.contains('open')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    toggleSidebar();
  }, true);
  collapse.addEventListener('click', toggleSidebar);
  restore.addEventListener('click', toggleSidebar);
  density.addEventListener('click', () => { state.compact = !state.compact; apply(); });
  focus.addEventListener('click', () => { state.focus = !state.focus; apply(); });
  fluid.addEventListener('click', () => { state.fluid = !state.fluid; apply(); });
  function setWidth(value, save = true) { state.sidebarWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.round(value))); apply(save); }
  separator.addEventListener('pointerdown', (event) => {
    if (!desktopQuery.matches || event.button !== 0) return;
    event.preventDefault();
    drag = { pointerId: event.pointerId, width: state.sidebarWidth, left: sidebar.getBoundingClientRect().left };
    separator.setPointerCapture(event.pointerId);
    body.classList.add('folio-sidebar-resizing');
  });
  separator.addEventListener('pointermove', (event) => {
    if (drag && event.pointerId === drag.pointerId) setWidth(event.clientX - drag.left, false);
  });
  function finishDrag(event) {
    if (!drag || event && event.pointerId !== drag.pointerId) return;
    if (separator.hasPointerCapture(drag.pointerId)) separator.releasePointerCapture(drag.pointerId);
    drag = null;
    body.classList.remove('folio-sidebar-resizing');
    persist();
    announce(`目录宽度 ${state.sidebarWidth} 像素`);
  }
  separator.addEventListener('pointerup', finishDrag);
  separator.addEventListener('pointercancel', finishDrag);
  separator.addEventListener('lostpointercapture', finishDrag);
  separator.addEventListener('dblclick', () => { setWidth(DEFAULTS.sidebarWidth); announce('目录已恢复默认宽度'); });
  separator.addEventListener('keydown', (event) => {
    const step = event.shiftKey ? 30 : 10;
    let value;
    if (event.key === 'ArrowLeft') value = state.sidebarWidth - step;
    else if (event.key === 'ArrowRight') value = state.sidebarWidth + step;
    else if (event.key === 'Home') value = MIN_WIDTH;
    else if (event.key === 'End') value = MAX_WIDTH;
    else if (event.key === 'Escape' && drag) { setWidth(drag.width); finishDrag(); }
    else return;
    event.preventDefault();
    if (value !== undefined) setWidth(value);
  });
  document.addEventListener('keydown', (event) => {
    if (!event.altKey || !event.shiftKey || event.ctrlKey || event.metaKey || event.repeat || document.querySelector('dialog[open]')) return;
    if (event.target.closest('input,textarea,select,[contenteditable="true"]')) return;
    if (event.code === 'KeyB') toggleSidebar();
    else if (event.code === 'KeyC') { state.compact = !state.compact; apply(); }
    else if (event.code === 'KeyF' && !reader.hidden) { state.focus = !state.focus; apply(); }
    else return;
    event.preventDefault();
    event.stopPropagation();
  }, true);
  new MutationObserver(() => apply(false)).observe(reader, { attributes: true, attributeFilter: ['hidden'] });
  new MutationObserver(() => apply(false)).observe(sidebar, { attributes: true, attributeFilter: ['class'] });
  desktopQuery.addEventListener('change', () => { if (drag) finishDrag(); apply(false); });
  document.addEventListener('folio:appearance-change', (event) => {
    const width = event.detail?.contentWidth;
    if (lastAppearanceWidth !== undefined && width !== lastAppearanceWidth) { state.fluid = false; apply(); }
    lastAppearanceWidth = width;
  });
  window.addEventListener('storage', (event) => {
    if (event.key !== STORAGE_KEY) return;
    try { state = normalise(JSON.parse(event.newValue || 'null')); apply(false); } catch { /* Ignore invalid cross-tab preferences. */ }
  });
  const controller = { getState: () => ({ ...state }), setSidebarWidth: setWidth, toggleSidebar, reset: () => { state = { ...DEFAULTS }; apply(); } };
  window.folioLayout = controller;
  apply(false);
  return controller;
}
