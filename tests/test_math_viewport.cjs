/* Run with: node tests/test_math_viewport.cjs. Real KaTeX, fake viewport observer. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');

class TestNode {
  static TEXT_NODE = 3;
  constructor(name, type = 1, text = '') {
    this.nodeName = type === 1 ? name.toUpperCase() : name;
    this.nodeType = type;
    this.childNodes = [];
    this.parentNode = null;
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.className = '';
    this._text = text;
    this.listeners = {};
  }
  get textContent() { return this.nodeType === 3 ? this._text : this.childNodes.map((child) => child.textContent).join(''); }
  set textContent(value) {
    if (this.nodeType === 3) this._text = String(value);
    else this.replaceChildren(new TestNode('#text', 3, String(value)));
  }
  get isConnected() { return this.nodeName === 'BODY' || !!this.parentNode?.isConnected; }
  get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] || null; }
  appendChild(child) {
    if (child.nodeType === 11) for (const item of [...child.childNodes]) this.appendChild(item);
    else { child.remove(); this.childNodes.push(child); child.parentNode = this; }
    return child;
  }
  append(...children) { for (const child of children) this.appendChild(child); }
  replaceChildren(...children) {
    for (const child of this.childNodes) child.parentNode = null;
    this.childNodes = [];
    this.append(...children);
  }
  replaceChild(replacement, child) {
    const index = this.childNodes.indexOf(child);
    assert.notEqual(index, -1);
    const children = replacement.nodeType === 11 ? [...replacement.childNodes] : [replacement];
    for (const item of children) item.remove();
    this.childNodes.splice(index, 1, ...children);
    child.parentNode = null;
    for (const item of children) item.parentNode = this;
    return child;
  }
  remove() {
    if (!this.parentNode) return;
    const siblings = this.parentNode.childNodes;
    siblings.splice(siblings.indexOf(this), 1);
    this.parentNode = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'class') this.className = String(value); }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) { this.listeners[type] = (this.listeners[type] || []).filter((value) => value !== handler); }
  dispatch(type) { for (const handler of this.listeners[type] || []) handler({ target: this }); }
}

function walk(node) { return [node, ...node.childNodes.flatMap(walk)]; }
function formulas(node) { return walk(node).filter((item) => item.className.split(/\s+/).includes('katex')); }

async function main() {
  const body = new TestNode('body');
  const reader = new TestNode('div');
  body.append(reader);
  const script = new TestNode('script');
  const observers = [];
  class FakeIntersectionObserver {
    constructor(callback, options) { this.callback = callback; this.options = options; this.targets = new Set(); observers.push(this); }
    observe(element) { this.targets.add(element); }
    unobserve(element) { this.targets.delete(element); }
    disconnect() { this.targets.clear(); }
    emit(element, isIntersecting) { this.callback([{ target: element, isIntersecting }]); }
  }
  const document = {
    compatMode: 'CSS1Compat', body, listeners: {},
    createElement: (name) => new TestNode(name),
    createElementNS: (_, name) => new TestNode(name),
    createTextNode: (text) => new TestNode('#text', 3, text),
    createDocumentFragment: () => new TestNode('#document-fragment', 11),
    getElementById: (id) => id === 'reader-scroll' ? reader : id === 'katex-auto-render-script' ? script : null,
    addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); },
  };
  const context = vm.createContext({ document, Node: TestNode, console, URL, setTimeout, clearTimeout,
    IntersectionObserver: FakeIntersectionObserver, localStorage: { getItem: () => null } });
  context.window = context;
  context.self = context;
  vm.runInContext(read('static/math.js').replaceAll('export function ', 'function '), context);
  const render = (element) => { context.element = element; return vm.runInContext('renderMath(element)', context); };
  const paragraph = (text, parent = reader) => { const element = new TestNode('p'); element.textContent = text; parent.append(element); return element; };

  const delayed = paragraph('The formula is $\\frac{x_0}{x_t}$ and \\(x^2\\).');
  assert.equal(render(delayed), false);
  assert.equal(observers.length, 1);
  const observer = observers[0];
  assert.equal(observer.options.root, reader);
  assert.equal(observer.options.rootMargin, '800px 0px');
  assert.equal(formulas(delayed).length, 0, 'offscreen math stays inexpensive source text');
  observer.emit(delayed, true);
  assert.equal(formulas(delayed).length, 0, 'a nearby formula waits for local scripts');
  observer.emit(delayed, false);
  vm.runInContext(read('static/vendor/katex/katex.min.js'), context);
  vm.runInContext(read('static/vendor/katex/contrib/auto-render.min.js'), context);
  script.dispatch('load');
  assert.equal(formulas(delayed).length, 0, 'loading scripts does not render a paragraph that scrolled away');
  observer.emit(delayed, true);
  assert.equal(formulas(delayed).length, 2, 'viewport entry directly reaches the real KaTeX renderer');
  assert.equal(walk(delayed).filter((item) => item.nodeName === 'MATH').length, 2);
  assert.ok(!observer.targets.has(delayed), 'rendered formulas stop observing');
  const firstFormula = formulas(delayed)[0];
  assert.equal(render(delayed), true);
  assert.equal(formulas(delayed)[0], firstFormula, 'unchanged formula DOM is retained');

  const chatPanel = new TestNode('aside');
  body.append(chatPanel);
  const chatAnswer = paragraph('结论为 \\(x_t\\)，并且 \\[Q(X)=s_X Q_X\\]。', chatPanel);
  assert.equal(render(chatAnswer), true, 'paper Q&A outside the reader renders immediately');
  assert.equal(formulas(chatAnswer).length, 2);
  assert.ok(!observer.targets.has(chatAnswer), 'chat math never waits on the article viewport observer');

  const plain = paragraph('No formula in prose, or an unmatched $ delimiter.');
  assert.equal(render(plain), true);
  assert.ok(!observer.targets.has(plain), 'only complete mathematical expressions are observed');
  const removed = paragraph('$x+1$');
  render(removed);
  removed.remove();
  observer.emit(removed, true);
  assert.equal(formulas(removed).length, 0, 'queued callbacks never render removed paper content');
  assert.ok(!observer.targets.has(removed));

  const dynamic = paragraph('$x$');
  render(dynamic);
  dynamic.textContent = '$y^2$';
  render(dynamic);
  assert.equal(formulas(dynamic).length, 0);
  observer.emit(dynamic, true);
  assert.equal(formulas(dynamic).length, 1);
  assert.ok(dynamic.textContent.includes('y^2'));
  dynamic.textContent = '$z_0$ and $z_1$';
  render(dynamic);
  observer.emit(dynamic, true);
  assert.equal(formulas(dynamic).length, 2, 'changed translated text can render after the first cached version');
  dynamic.textContent = 'A replacement without formulas.';
  render(dynamic);
  assert.ok(!observer.targets.has(dynamic));

  const details = new TestNode('details');
  reader.append(details);
  const collapsed = paragraph('Old translation $x_t$ and $x_0$.', details);
  render(collapsed);
  assert.equal(formulas(collapsed).length, 0);
  assert.ok(!observer.targets.has(collapsed), 'collapsed retained translation needs no observer');
  details.open = true;
  details.dispatch('toggle');
  assert.ok(observer.targets.has(collapsed));
  observer.emit(collapsed, true);
  assert.equal(formulas(collapsed).length, 2);

  const lateDetails = new TestNode('details');
  reader.append(lateDetails);
  const lateFragment = new TestNode('div');
  lateFragment.textContent = '$x_{t-1}$';
  render(lateFragment);
  lateDetails.append(lateFragment);
  observer.emit(lateFragment, true);
  assert.equal(formulas(lateFragment).length, 0, 'a fragment appended to closed details is checked again at render time');
  assert.ok(!observer.targets.has(lateFragment));
  lateDetails.open = true;
  lateDetails.dispatch('toggle');
  observer.emit(lateFragment, true);
  assert.equal(formulas(lateFragment).length, 1);

  // The application builds translation spans before appending them. Deferral
  // must permit that construction while pruning old detached paper elements.
  const temporary = new TestNode('span');
  temporary.textContent = '\\(\\sqrt{x_t}\\)';
  render(temporary);
  reader.append(temporary);
  const abandoned = paragraph('$old$');
  render(abandoned);
  abandoned.remove();
  await Promise.resolve();
  assert.ok(observer.targets.has(temporary));
  assert.ok(!observer.targets.has(abandoned));
  observer.emit(temporary, true);
  assert.equal(formulas(temporary).length, 1);

  vm.runInContext(read('static/app.js').replace(/^import[^\n]+\n/gm, '').replace(/\ninit\(\);\s*$/, ''), context);
  const markdownBubble = new TestNode('div');
  chatPanel.append(markdownBubble);
  context.element = markdownBubble;
  context.chatMarkdown = String.raw`# 核心方法

* **异常值迁移** 使用 $\hat{W}$。
* 保留低秩分支。

\[Q(X)=s_X Q_X\]

<script>alert(1)</script>`;
  vm.runInContext("state.search=''; renderChatMarkdown(element, chatMarkdown);", context);
  assert.equal(formulas(markdownBubble).length, 2, 'connected Q&A Markdown renders inline and display LaTeX immediately');
  assert.ok(walk(markdownBubble).some((item) => item.nodeName === 'STRONG' && item.textContent.includes('异常值迁移')));
  assert.equal(walk(markdownBubble).filter((item) => item.nodeName === 'UL').length, 1);
  assert.equal(walk(markdownBubble).filter((item) => item.nodeName === 'LI').length, 2);
  assert.ok(markdownBubble.textContent.includes('<script>alert(1)</script>'));
  assert.ok(!walk(markdownBubble).some((item) => item.nodeName === 'SCRIPT'), 'model HTML stays inert text');
  assert.ok(!markdownBubble.textContent.includes('**'), 'Markdown markers do not leak into the answer');
  const search = paragraph('');
  context.element = search;
  vm.runInContext("state.search = 'x'; paragraphText(element, 'Find x in $x_t$ and $x_0$, without splitting formulas.');", context);
  assert.equal(formulas(search).length, 0);
  assert.equal(search.dataset.mathSearchHit, 'true', 'formula search knows matches before KaTeX exists');
  assert.ok(walk(search).some((item) => item.nodeName === 'MARK' && item.textContent === 'x'));
  observer.emit(search, true);
  assert.equal(formulas(search).length, 2);
  assert.equal(search.dataset.mathSearchHit, 'true');
  const searchFormula = formulas(search)[0];
  vm.runInContext("paragraphText(element, 'Find x in $x_t$ and $x_0$, without splitting formulas.');", context);
  assert.equal(formulas(search)[0], searchFormula);
  vm.runInContext("state.search = ''; paragraphText(element, 'Find x in $x_t$ and $x_0$, without splitting formulas.');", context);
  observer.emit(search, true);
  assert.equal(formulas(search).length, 2);
  assert.ok(!walk(search).some((item) => item.nodeName === 'MARK'));
  assert.equal(search.dataset.mathSearchHit, undefined);

  // An already-offscreen target may be removed without another observer entry.
  // The article update hook also runs when its replacement is plain text or an
  // image and therefore never calls renderMath itself.
  const oldOffscreen = paragraph('$old_t$');
  render(oldOffscreen);
  observer.emit(oldOffscreen, false);
  await Promise.resolve();
  oldOffscreen.remove();
  paragraph('A plain TXT replacement with no mathematical expressions.');
  vm.runInContext('pruneMathObservations()', context);
  assert.ok(!observer.targets.has(oldOffscreen), 'plain-text article updates release already-offscreen targets');
  const oldBeforeImage = paragraph('$before_{image}$');
  render(oldBeforeImage);
  observer.emit(oldBeforeImage, false);
  await Promise.resolve();
  oldBeforeImage.remove();
  reader.append(new TestNode('img'));
  vm.runInContext('pruneMathObservations()', context);
  assert.ok(!observer.targets.has(oldBeforeImage), 'an image-only replacement also releases old formula observations');
  const staleDetails = new TestNode('details');
  reader.append(staleDetails);
  const staleFragment = paragraph('$stale$', staleDetails);
  render(staleFragment);
  staleDetails.remove();
  vm.runInContext('pruneMathObservations()', context);
  assert.equal(staleDetails.listeners.toggle.length, 0, 'removed details release their listeners and target sets');
  assert.equal(vm.runInContext('detailsWaiting.size', context), 0);
  const emptyDetails = new TestNode('details');
  reader.append(emptyDetails);
  const replacedFragment = paragraph('$replaced$', emptyDetails);
  render(replacedFragment);
  replacedFragment.remove();
  vm.runInContext('pruneMathObservations()', context);
  assert.equal(emptyDetails.listeners.toggle.length, 0, 'a connected details with no remaining pending targets is also released');

  const welcomeTarget = paragraph('$old_{paper}$');
  render(welcomeTarget);
  observer.emit(welcomeTarget, false);
  const welcomeDetails = new TestNode('details');
  reader.append(welcomeDetails);
  const welcomeFragment = paragraph('$retained$', welcomeDetails);
  render(welcomeFragment);
  const oldToggle = welcomeDetails.listeners.toggle[0];
  await Promise.resolve();
  vm.runInContext('clearMathObservations()', context);
  assert.equal(observer.targets.size, 0, 'welcome/fresh imports release all observers even before removing old DOM');
  assert.equal(vm.runInContext('observed.size + waiting.size + detailsWaiting.size', context), 0);
  welcomeDetails.open = true;
  oldToggle();
  welcomeDetails.dispatch('toggle');
  observer.emit(welcomeTarget, true);
  assert.equal(observer.targets.size, 0, 'a stale details event or observer callback cannot revive a cleared paper');
  assert.equal(formulas(welcomeTarget).length + formulas(welcomeFragment).length, 0);

  const nextPaper = paragraph('$new_{paper}$');
  render(nextPaper);
  observer.emit(nextPaper, true);
  assert.equal(formulas(nextPaper).length, 1, 'a new paper can reuse the cleared observer');
  const renderer = context.renderMathInElement;
  context.renderMathInElement = undefined;
  const scriptsPending = paragraph('$pending$');
  render(scriptsPending);
  observer.emit(scriptsPending, true);
  assert.equal(vm.runInContext('waiting.size', context), 1);
  vm.runInContext('clearMathObservations()', context);
  context.renderMathInElement = renderer;
  vm.runInContext('flushWaiting()', context);
  assert.equal(formulas(scriptsPending).length, 0, 'a late script completion cannot render content cleared by navigation');
  assert.equal(vm.runInContext('waiting.size', context), 0);

  // No IntersectionObserver support keeps the synchronous compatibility path.
  const fallbackContext = vm.createContext({ ...context, IntersectionObserver: undefined });
  fallbackContext.window = fallbackContext;
  vm.runInContext(read('static/math.js').replaceAll('export function ', 'function '), fallbackContext);
  const fallback = paragraph('$a+b$');
  fallbackContext.element = fallback;
  assert.equal(vm.runInContext('renderMath(element)', fallbackContext), true);
  assert.equal(formulas(fallback).length, 1);
  const fallbackDetails = new TestNode('details');
  reader.append(fallbackDetails);
  const fallbackCollapsed = paragraph('$a_0$', fallbackDetails);
  fallbackContext.element = fallbackCollapsed;
  assert.equal(vm.runInContext('renderMath(element)', fallbackContext), false);
  assert.equal(formulas(fallbackCollapsed).length, 0);
  fallbackDetails.open = true;
  fallbackDetails.dispatch('toggle');
  assert.equal(formulas(fallbackCollapsed).length, 1);
  console.log('PASS: real KaTeX waits for the nearby viewport; scripts, removals, details, dynamic text, search, caching and synchronous fallback remain correct.');
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
