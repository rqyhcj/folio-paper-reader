/* Run with: node tests/test_math.cjs. Uses real bundled KaTeX and a tiny DOM. */
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

  get textContent() {
    return this.nodeType === 3 ? this._text : this.childNodes.map((child) => child.textContent).join('');
  }

  set textContent(value) {
    if (this.nodeType === 3) this._text = String(value);
    else this.replaceChildren(new TestNode('#text', 3, String(value)));
  }

  get isConnected() { return this.nodeName === 'BODY' || !!this.parentNode?.isConnected; }
  get nextSibling() { return this.parentNode?.childNodes[this.parentNode.childNodes.indexOf(this) + 1] || null; }

  appendChild(child) {
    if (child.nodeType === 11) {
      for (const item of [...child.childNodes]) this.appendChild(item);
    } else {
      child.remove();
      this.childNodes.push(child);
      child.parentNode = this;
    }
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

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'class') this.className = String(value);
  }

  addEventListener(type, handler) { this.listeners[type] = handler; }
}

function walk(node) { return [node, ...node.childNodes.flatMap(walk)]; }
function withClass(node, name) { return walk(node).filter((item) => item.className.split(/\s+/).includes(name)); }

const body = new TestNode('body');
const script = new TestNode('script');
const document = {
  compatMode: 'CSS1Compat', body, listeners: {},
  createElement: (name) => new TestNode(name),
  createElementNS: (_, name) => new TestNode(name),
  createTextNode: (text) => new TestNode('#text', 3, text),
  createDocumentFragment: () => new TestNode('#document-fragment', 11),
  getElementById: (id) => id === 'katex-auto-render-script' ? script : null,
  addEventListener(type, handler) { this.listeners[type] = handler; },
};
const context = vm.createContext({
  document, Node: TestNode, console, URL, setTimeout, clearTimeout,
  localStorage: { getItem: () => null },
});
context.window = context;
context.self = context;
vm.runInContext(read('static/math.js').replaceAll('export function ', 'function '), context);

// The module also works if deferred local scripts finish after the app starts.
const delayed = new TestNode('p');
delayed.textContent = '$k$';
body.append(delayed);
context.delayed = delayed;
assert.equal(vm.runInContext('renderMath(delayed)', context), false);
vm.runInContext(read('static/vendor/katex/katex.min.js'), context);
vm.runInContext(read('static/vendor/katex/contrib/auto-render.min.js'), context);
script.listeners.load();
assert.equal(withClass(delayed, 'katex').length, 1);

const render = (text) => {
  const element = new TestNode('p');
  element.textContent = text;
  context.element = element;
  assert.equal(vm.runInContext('renderMath(element)', context), true);
  return element;
};

const inline = render('For $k$ and $k+1$, use \\(\\frac{k}{k+1}\\).');
assert.equal(withClass(inline, 'katex').length, 3);
assert.equal(walk(inline).filter((item) => item.nodeName === 'MATH').length, 3);
const firstFormula = withClass(inline, 'katex')[0];
context.element = inline;
assert.equal(vm.runInContext('renderMath(element)', context), true);
assert.equal(withClass(inline, 'katex').length, 3);
assert.equal(withClass(inline, 'katex')[0], firstFormula);

const display = render('$$\\begin{aligned}\na &= b+c \\\\\nd &= e\n\\end{aligned}$$ and \\[x^2 + y^2\\]');
assert.equal(withClass(display, 'katex-display').length, 2);
const malformed = render('An invalid formula $\\frac{$ and normal text.');
assert.ok(malformed.textContent.includes('normal text'));
assert.ok(walk(malformed).length < 100);
const expansion = render('$\\def\\a{\\a}\\a$');
assert.ok(withClass(expansion, 'katex-error').length >= 1);

const hostile = render('Before $\\href{javascript:alert(1)}{click}$ $\\includegraphics{https://example.invalid/image}$ after.');
assert.ok(!walk(hostile).some((item) => ['A', 'IMG', 'SCRIPT'].includes(item.nodeName)));

// Use the real application's text/highlight path, with no application startup.
vm.runInContext(read('static/app.js').replace(/^import[^\n]+\n/gm, '').replace(/\ninit\(\);\s*$/, ''), context);
const safe = new TestNode('p');
context.element = safe;
vm.runInContext(`state.search = 'k'; paragraphText(element, 'A <img src=x onerror=alert(1)> block has $k$ and $k+1$; keep reading.');`, context);
assert.equal(withClass(safe, 'katex').length, 2);
assert.ok(!walk(safe).some((item) => item.nodeName === 'IMG'));
assert.ok(walk(safe).some((item) => item.nodeName === 'MARK' && item.textContent === 'k'));
assert.equal(safe.dataset.mathSearchHit, 'true');
const originalFormula = withClass(safe, 'katex')[0];
vm.runInContext(`paragraphText(element, 'A <img src=x onerror=alert(1)> block has $k$ and $k+1$; keep reading.');`, context);
assert.equal(withClass(safe, 'katex')[0], originalFormula);
vm.runInContext(`state.search=''; paragraphText(element, 'A <img src=x onerror=alert(1)> block has $k$ and $k+1$; keep reading.');`, context);
assert.equal(withClass(safe, 'katex').length, 2);
assert.ok(!walk(safe).some((item) => item.nodeName === 'MARK'));
assert.equal(safe.dataset.mathSearchHit, undefined);

// Asset references must remain local, including the stylesheet's font files.
const index = read('static/index.html');
assert.ok(index.includes('src="/vendor/katex/katex.min.js"'));
assert.ok(index.includes('src="/vendor/katex/contrib/auto-render.min.js"'));
for (const match of read('static/vendor/katex/katex.min.css').matchAll(/url\(["']?(fonts\/[^)"']+)/g)) {
  assert.ok(fs.existsSync(path.join(root, 'static/vendor/katex', match[1])));
}
assert.ok(read('static/math.css').includes('overflow-x: auto'));
console.log('PASS: real local KaTeX renders inline/display/multiline math; safe search and unchanged-node cache; malformed/hostile formulas; deferred loading and offline fonts.');
