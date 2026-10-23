/* Run with: node tests/test_outline_labels.cjs. No browser or model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const helperSource = source.slice(source.indexOf('function getFigureTextIds()'), source.indexOf('function renderOutline()'));
const state = { paper: null };
const context = vm.createContext({ state, Set });
vm.runInContext(helperSource, context);
const outline = () => JSON.parse(JSON.stringify(vm.runInContext('getOutline()', context)));

assert.deepEqual(outline(), []);
state.paper = {
  figures: [{ text_paragraph_ids: ['label'] }],
  outline: [{ id: 's1', paragraph_id: 'real', title: '1 Introduction' },
            { id: 's2', paragraph_id: 'label', title: 'After DNS (Ours)' }],
  paragraphs: [{ id: 'real', kind: 'heading', text: '1 Introduction', translation: '1 引言' },
               { id: 'label', kind: 'heading', text: 'After DNS (Ours)', translation: '使用 DNS 后' }],
};
const original = JSON.stringify(state.paper);
assert.deepEqual(outline().map((entry) => entry.title), ['1 Introduction']);
assert.equal(JSON.stringify(state.paper), original, 'derive the display without modifying cached source or translations');

state.paper.outline = [];
assert.deepEqual(outline().map((entry) => entry.title), ['1 Introduction']);
state.paper.figures = [];
assert.deepEqual(outline().map((entry) => entry.title), ['1 Introduction', 'After DNS (Ours)']);
state.paper.figures = [{ text_paragraph_ids: [42] }, {}];
state.paper.outline = [{ id: 42, title: 'Numerical label ID' }, { id: 43, title: 'A real section' }];
assert.deepEqual(outline().map((entry) => entry.title), ['A real section']);
console.log('Outline label regression checks passed.');
