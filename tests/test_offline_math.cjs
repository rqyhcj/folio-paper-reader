/* The exported HTML's exact bootstrap must render all standard delimiters. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const fixture = read('tests/test_math.cjs');
const domFixture = fixture.slice(fixture.indexOf('class TestNode {'), fixture.indexOf('const body ='));
const context = vm.createContext({ assert, console, URL });
vm.runInContext(domFixture + `
  const body = new TestNode('body');
  const document = {compatMode:'CSS1Compat',body,
    createElement: name => new TestNode(name),
    createElementNS: (_, name) => new TestNode(name),
    createTextNode: text => new TestNode('#text',3,text),
    createDocumentFragment: () => new TestNode('#document-fragment',11)};
`, context);
context.window = context;
context.self = context;
vm.runInContext(read('static/vendor/katex/katex.min.js'), context);
vm.runInContext(read('static/vendor/katex/contrib/auto-render.min.js'), context);
const bootstrap = read('backend/math_html.py').match(/options = r'''([\s\S]*?)'''/)[1];
context.inlineSource = String.raw`Input \(X\in\mathbb{R}^{m\times d}\), $$x^2$$, $y_t$, and \[\frac{a}{b}\].`;
context.displaySource = String.raw`\[\underbrace{\frac{\|v\|_\infty}{\|v\|_2}}_{\kappa(v)}\]`;
vm.runInContext(`
  const inline = new TestNode('p'); inline.textContent = inlineSource; body.append(inline);
  const complex = new TestNode('p'); complex.textContent = displaySource; body.append(complex);
`, context);
vm.runInContext(bootstrap, context);
assert.equal(vm.runInContext("withClass(body,'katex').length", context), 5);
assert.equal(vm.runInContext("withClass(body,'katex-display').length", context), 3);
assert.equal(vm.runInContext("withClass(body,'katex-error').length", context), 0);
assert.equal(vm.runInContext("walk(body).filter(node=>node.nodeName==='MATH').length", context), 5);
console.log('PASS: standalone HTML bootstrap renders inline/display, double-dollar, fractions and underbraces using exact offline delimiters.');
