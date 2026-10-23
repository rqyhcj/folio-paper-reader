/* Run with: node tests/test_model_picker.cjs. No browser or API calls required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');

class TestEvent {
  constructor(type, values = {}) { Object.assign(this, { type, bubbles: false, defaultPrevented: false }, values); }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this.stopped = true; }
}

class TestNode {
  constructor(tag, id = '') {
    Object.assign(this, { tagName: tag.toUpperCase(), id, children: [], parentNode: null, attributes: {}, dataset: {}, listeners: {}, value: '', hidden: false, disabled: false });
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  append(...children) {
    for (const child of children) { child.parentNode = this; this.children.push(child); }
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this.append(...children);
  }
  get options() { return this.children; }
  contains(target) { return target === this || this.children.some((child) => child.contains(target)); }
  closest(selector) {
    if (selector === '[data-model-index]' && this.dataset.modelIndex !== undefined) return this;
    return this.parentNode?.closest(selector) || null;
  }
  dispatchEvent(event) {
    event.target ||= this;
    for (const listener of this.listeners[event.type] || []) listener(event);
    if (event.bubbles && !event.stopped) this.parentNode?.dispatchEvent(event);
    return !event.defaultPrevented;
  }
  focus() {
    if (document.activeElement === this || this.disabled) return;
    const previous = document.activeElement;
    document.activeElement = this;
    previous?.dispatchEvent(new TestEvent('focusout', { bubbles: true, relatedTarget: this }));
    this.dispatchEvent(new TestEvent('focus'));
  }
  scrollIntoView() { this.scrolled = true; }
  showModal() { this.open = true; }
}

const elements = new Map();
const get = (id) => {
  if (!elements.has(id)) elements.set(id, new TestNode('div', id));
  return elements.get(id);
};
const document = {
  activeElement: null, listeners: {},
  getElementById: get,
  createElement: (tag) => new TestNode(tag),
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); },
};
const picker = get('api-model-picker');
const input = get('api-model');
const toggle = get('api-model-toggle');
const menu = get('api-model-menu');
const options = get('api-model-options');
const empty = get('api-model-empty');
picker.append(input, toggle, menu);
menu.append(options, empty);
menu.hidden = true;

const context = vm.createContext({
  document, Event: TestEvent, console,
  localStorage: { getItem: () => null },
  fetch: () => { throw new Error('The model picker must not call a model or save settings.'); },
});
vm.runInContext(read('static/app.js').replace(/^import[^\n]+\n/gm, '').replace(/\ninit\(\);\s*$/, ''), context);
const run = (code) => vm.runInContext(code, context);
const fire = (element, type, values = {}) => {
  const event = new TestEvent(type, values);
  element.dispatchEvent(event);
  return event;
};
const key = (value, extra = {}) => fire(input, 'keydown', { key: value, ...extra });
const names = () => options.children.map((child) => child.textContent);
const checkOpen = (open) => {
  assert.equal(menu.hidden, !open);
  assert.equal(input.getAttribute('aria-expanded'), String(open));
  assert.equal(toggle.getAttribute('aria-expanded'), String(open));
};

run(`state.providers = [
  { id: 'deepseek', name: 'DeepSeek', models: ['deepseek-flash', 'deepseek-v4-pro', 'deepseek-flash', '<custom/model>'], endpoint: 'https://example.invalid/chat/completions' },
  { id: 'custom', name: 'Custom', models: [] },
  { id: 'tju', name: 'TJU', models: ['tju-llm'] },
]; renderProviders(); wireModelPicker();`);

// Reopening settings preserves a saved custom ID and never chooses a suggestion.
run(`state.settings = { provider: 'deepseek', endpoint: 'https://example.invalid/chat/completions', model: 'org/my-custom-model:latest', has_key: true }; openSettings();`);
assert.equal(input.value, 'org/my-custom-model:latest');
assert.equal(run('state.keyDirty'), false);
assert.equal(get('api-key').value, '');
checkOpen(false);

// Focus/arrow exposes every suggestion even if the saved value matches none of them.
input.focus();
checkOpen(true);
assert.deepEqual(names(), ['deepseek-flash', 'deepseek-v4-pro', '<custom/model>']);
assert.equal(input.value, 'org/my-custom-model:latest');
assert.equal(input.getAttribute('aria-activedescendant'), null);
for (const option of options.children) {
  assert.equal(option.type, 'button');
  assert.equal(option.tabIndex, -1);
  assert.equal(option.getAttribute('role'), 'option');
  assert.equal(option.getAttribute('aria-selected'), 'false');
}
assert.equal(options.children[2].children.length, 0, 'Model IDs use textContent rather than HTML.');

// Pointer selection retains input focus and changes only the form's model value.
assert.equal(fire(options, 'pointerdown').defaultPrevented, true);
fire(options.children[1], 'click', { bubbles: true });
assert.equal(input.value, 'deepseek-v4-pro');
assert.equal(document.activeElement, input);
assert.equal(run('state.settings.model'), 'org/my-custom-model:latest');
assert.equal(run('state.keyDirty'), false);
checkOpen(false);
fire(toggle, 'click');
assert.equal(names().length, 3);
checkOpen(true);

// Filtering is case insensitive; one click on the arrow returns the full list.
input.value = 'FLASH';
fire(input, 'input');
assert.deepEqual(names(), ['deepseek-flash']);
fire(toggle, 'click');
assert.equal(names().length, 3);
assert.equal(input.value, 'FLASH');
input.value = 'my/private-model';
fire(input, 'input');
assert.deepEqual(names(), []);
assert.equal(empty.hidden, false);
assert.ok(empty.textContent.includes('没有匹配'));
fire(toggle, 'click');
assert.equal(names().length, 3, 'The full list returns on the first click after no matches.');
assert.equal(input.value, 'my/private-model');

// Arrow keys highlight without changing the text; Enter commits without submission.
assert.equal(key('ArrowDown').defaultPrevented, true);
assert.equal(input.value, 'my/private-model');
assert.equal(input.getAttribute('aria-activedescendant'), options.children[0].id);
assert.equal(options.children[0].getAttribute('aria-selected'), 'true');
assert.equal(key('ArrowUp').defaultPrevented, true);
assert.equal(options.children[2].getAttribute('aria-selected'), 'true');
assert.equal(key('Enter').defaultPrevented, true);
assert.equal(input.value, '<custom/model>');
checkOpen(false);
assert.equal(key('Enter').defaultPrevented, false, 'Enter may submit after the suggestions have closed.');
key('ArrowUp');
assert.equal(input.getAttribute('aria-activedescendant'), options.children[2].id);
const escape = key('Escape');
assert.equal(escape.defaultPrevented, true);
assert.equal(escape.stopped, true, 'The first Escape must not cancel the settings dialog.');
checkOpen(false);
assert.equal(input.getAttribute('aria-activedescendant'), null);

// Tab and outside focus preserve free text and do not trap keyboard focus.
input.value = 'arbitrary-custom-model';
fire(input, 'input');
assert.equal(key('Tab').defaultPrevented, false);
checkOpen(false);
fire(toggle, 'click');
toggle.focus();
checkOpen(true);
get('api-key').focus();
checkOpen(false);
assert.equal(input.value, 'arbitrary-custom-model');
input.focus();
for (const listener of document.listeners.pointerdown) listener({ target: get('api-key') });
checkOpen(false);

// Composing Chinese or other IME text must not select suggestions on Enter.
input.value = 'my-model';
fire(input, 'input');
key('ArrowDown');
const composing = key('Enter', { isComposing: true });
assert.equal(composing.defaultPrevented, false);
assert.equal(input.value, 'my-model');
checkOpen(true);
fire(input, 'compositionend');
assert.equal(run('modelPicker.active'), -1);
key('Enter');
checkOpen(false);

// Changing providers removes old suggestions and leaves explicit manual entry.
get('api-provider').value = 'custom';
run('changeProvider();');
assert.equal(input.value, '');
assert.equal(run('state.keyDirty'), true);
assert.ok(get('api-model-help').textContent.includes('手动输入'));
checkOpen(false);
fire(toggle, 'click');
assert.deepEqual(names(), []);
assert.equal(empty.hidden, false);
assert.ok(empty.textContent.includes('暂无预设模型'));
input.value = 'my-local-model:8b';
fire(input, 'input');
assert.equal(key('ArrowDown').defaultPrevented, true);
assert.equal(input.getAttribute('aria-activedescendant'), null);
assert.equal(key('Enter').defaultPrevented, true);
assert.equal(input.value, 'my-local-model:8b');
checkOpen(false);
get('api-provider').value = 'tju';
run('changeProvider();');
fire(toggle, 'click');
assert.deepEqual(names(), ['tju-llm']);
assert.equal(input.value, '', 'Changing providers must not automatically select a model.');

// Test/save requests close and disable the picker, then restore normal use.
run('setSettingsBusy(true);');
checkOpen(false);
assert.equal(input.disabled, true);
assert.equal(toggle.disabled, true);
run('openModelSuggestions();');
checkOpen(false);
run('setSettingsBusy(false);');
assert.equal(input.disabled, false);
assert.equal(toggle.disabled, false);
fire(toggle, 'click');
checkOpen(true);

const html = read('static/index.html');
assert.ok(!html.includes('<datalist'));
assert.match(html, /<input id="api-model"[^>]*role="combobox"[^>]*aria-controls="api-model-options"/);
assert.match(html, /<button type="button" id="api-model-toggle"/);
assert.match(html, /id="api-model-options"[^>]*role="listbox"/);
assert.ok(html.includes('/providers.css?v=model-picker-2'));
assert.ok(html.includes('/app.js?v=model-picker-2'));
console.log('PASS: model suggestions support saved/custom IDs, empty providers, one-click full lists, pointer/keyboard selection, IME, focus dismissal, safe text and busy state without model requests or settings writes.');
