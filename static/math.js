/* TeX stays plain text until the locally bundled, untrusted-input-safe renderer runs. */
const DELIMITERS = [
  { left: '$$', right: '$$', display: true },
  { left: '\\[', right: '\\]', display: true },
  { left: '\\(', right: '\\)', display: false },
  { left: '$', right: '$', display: false },
];

// Some compatible providers omit delimiters around a response containing only
// TeX. Add delimiters only to an unambiguous mathematical expression; do not
// infer equations from prose, paths, code or malformed brace groups.
export function normalizeMathText(text) {
  if (typeof text !== 'string' || !/\\(?:frac|sqrt|cdot|sum|prod|int|alpha|beta|sigma|epsilon|mu|theta|bar|hat)\b/.test(text)) return text;
  if (/[$\u3400-\u9fff`]/u.test(text) || /\\[()[\]]/.test(text)) return text;
  const allowed = new Set(['frac', 'sqrt', 'cdot', 'times', 'sum', 'prod', 'int', 'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'varepsilon', 'sigma', 'mu', 'theta', 'lambda', 'pi', 'rho', 'phi', 'psi', 'omega', 'bar', 'hat', 'tilde', 'vec', 'mathbf', 'mathbb', 'mathrm', 'boldsymbol', 'left', 'right', 'le', 'ge', 'ne', 'in', 'sim', 'approx', 'partial', 'nabla', 'infty']);
  const commands = [...text.matchAll(/\\([A-Za-z]+)/g)].map((match) => match[1]);
  if (commands.some((command) => !allowed.has(command))) return text;
  const remaining = text.replace(/\\[A-Za-z]+\s*/g, '').replace(/\\[,;! ]/g, '');
  if (/[A-Za-z]{2}|[^A-Za-z0-9\s{}_^+\-−=.,:;!|()\[\]<>*/\u0370-\u03ff]/u.test(remaining)) return text;
  let braces = 0;
  for (const char of remaining) {
    if (char === '{') braces++;
    else if (char === '}' && --braces < 0) return text;
  }
  if (braces) return text;
  return `\\(${text.trim()}\\)`;
}

function closingDelimiter(text, start, right) {
  let braces = 0;
  for (let index = start; index < text.length; index++) {
    if (braces <= 0 && text.startsWith(right, index)) return index;
    if (text[index] === '\\') index++;
    else if (text[index] === '{') braces++;
    else if (text[index] === '}') braces--;
  }
  return -1;
}

// Preserve each complete TeX expression in one text node when highlighting prose.
// This mirrors auto-render's escape and brace handling, including multiline math.
export function splitMathText(text) {
  const parts = [];
  let plainStart = 0;
  for (let index = 0; index < text.length; index++) {
    const delimiter = DELIMITERS.find(({ left }) => text.startsWith(left, index));
    if (delimiter) {
      const end = closingDelimiter(text, index + delimiter.left.length, delimiter.right);
      if (end !== -1) {
        if (index > plainStart) parts.push({ text: text.slice(plainStart, index), math: false });
        const next = end + delimiter.right.length;
        parts.push({ text: text.slice(index, next), math: true });
        index = next - 1;
        plainStart = next;
        continue;
      }
      // Auto-render leaves the remainder untouched after an unmatched opener.
      break;
    }
  }
  if (plainStart < text.length || !parts.length) parts.push({ text: text.slice(plainStart), math: false });
  return parts;
}

const waiting = new Set();
let waitingForScripts = false;
const rendered = new WeakMap();
const detailsWaiting = new Map();
const observed = new Set();
let nearViewport = new WeakSet();
let mathObserver = null;
let pruneQueued = false;

function stopObserving(element) {
  mathObserver?.unobserve(element);
  observed.delete(element);
  nearViewport.delete(element);
}

function renderedUnchanged(element) {
  const previous = rendered.get(element);
  return previous && previous.text === element.textContent && previous.firstChild === element.childNodes[0];
}

function waitForClosedDetails(element) {
  for (let parent = element.parentNode; parent; parent = parent.parentNode) {
    if (parent.nodeName !== 'DETAILS' || parent.open) continue;
    let record = detailsWaiting.get(parent);
    if (!record) {
      record = { elements: new Set(), handler: null };
      record.handler = () => {
        if (!parent.open || detailsWaiting.get(parent) !== record) return;
        const targets = [...record.elements];
        releaseDetails(parent, record);
        for (const target of targets) if (target.isConnected) renderMath(target);
      };
      detailsWaiting.set(parent, record);
      parent.addEventListener('toggle', record.handler);
    }
    record.elements.add(element);
    waiting.delete(element);
    stopObserving(element);
    return true;
  }
  return false;
}

function releaseDetails(details, record) {
  record.elements.clear();
  details.removeEventListener?.('toggle', record.handler);
  detailsWaiting.delete(details);
}

// Call after article DOM updates, including updates that contain no new math.
// Browser observers do not guarantee a second false entry for a removed target
// that was already offscreen, so callbacks alone cannot release old papers.
export function pruneMathObservations() {
  for (const element of observed) {
    if (!element.isConnected) { stopObserving(element); waiting.delete(element); }
  }
  for (const element of waiting) if (!element.isConnected) waiting.delete(element);
  for (const [details, record] of detailsWaiting) {
    for (const element of record.elements) if (!element.isConnected) record.elements.delete(element);
    if (!details.isConnected || !record.elements.size) releaseDetails(details, record);
  }
}

// Fresh imports and the welcome screen release all pending work immediately;
// the next paper can reuse the observer without retaining any previous target.
export function clearMathObservations() {
  mathObserver?.disconnect();
  observed.clear();
  waiting.clear();
  nearViewport = new WeakSet();
  for (const [details, record] of detailsWaiting) releaseDetails(details, record);
}

function getMathObserver() {
  if (mathObserver) return mathObserver;
  const root = document.getElementById('reader-scroll');
  if (!root || typeof window.IntersectionObserver !== 'function') return null;
  try {
    mathObserver = new window.IntersectionObserver((entries) => {
      for (const entry of entries) {
        const element = entry.target;
        if (!observed.has(element)) continue;
        if (!element.isConnected) { stopObserving(element); waiting.delete(element); continue; }
        if (!entry.isIntersecting) { nearViewport.delete(element); continue; }
        nearViewport.add(element);
        // The observer has already established visibility. Do not queue this
        // element through renderMath again or it would never reach KaTeX.
        renderMathNow(element);
      }
    }, { root, rootMargin: '800px 0px', threshold: 0 });
  } catch {
    // Older webviews still get the same synchronous, safe renderer.
    return null;
  }
  return mathObserver;
}

function pruneDetached() {
  if (pruneQueued) return;
  pruneQueued = true;
  // Translation spans are queued before being appended. Wait until the DOM
  // update finishes, then release observations belonging to the previous paper.
  Promise.resolve().then(() => {
    pruneQueued = false;
    pruneMathObservations();
  });
}

function flushWaiting() {
  if (typeof window.renderMathInElement !== 'function') return;
  for (const element of waiting) {
    waiting.delete(element);
    if (!element.isConnected) { stopObserving(element); continue; }
    // An element may have scrolled away while the local scripts were loading.
    if (observed.has(element) && !nearViewport.has(element)) continue;
    renderMathNow(element);
  }
}

function renderMathNow(element) {
  if (renderedUnchanged(element)) { stopObserving(element); return true; }
  if (waitForClosedDetails(element)) return false;
  if (typeof window.renderMathInElement !== 'function') {
    waiting.add(element);
    if (!waitingForScripts) {
      waitingForScripts = true;
      document.getElementById('katex-auto-render-script')?.addEventListener('load', flushWaiting, { once: true });
      document.addEventListener('DOMContentLoaded', flushWaiting, { once: true });
    }
    return false;
  }
  waiting.delete(element);
  try {
    window.renderMathInElement(element, {
      delimiters: DELIMITERS,
      trust: false,
      throwOnError: false,
      maxExpand: 100,
      maxSize: 20,
      ignoredClasses: ['katex', 'katex-display', 'katex-error'],
      // Formula syntax errors remain visible as source text, without console noise.
      errorCallback: () => {},
    });
    rendered.set(element, { text: element.textContent, firstChild: element.childNodes[0] });
    stopObserving(element);
    return true;
  } catch {
    // A malformed expression must never interrupt reading the rest of the paper.
    stopObserving(element);
    return false;
  }
}

export function renderMath(element) {
  if (!element) return false;
  if (renderedUnchanged(element)) return true;
  // Most paper paragraphs contain no TeX. Neither observe them nor build a
  // second mathematical DOM tree until a complete delimited formula is present.
  if (!splitMathText(element.textContent).some((part) => part.math)) {
    waiting.delete(element);
    rendered.delete(element);
    stopObserving(element);
    return true;
  }
  if (waitForClosedDetails(element)) return false;
  const observer = getMathObserver();
  if (!observer) return renderMathNow(element);
  observed.add(element);
  observer.observe(element);
  pruneDetached();
  return false;
}
