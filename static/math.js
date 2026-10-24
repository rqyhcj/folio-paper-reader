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

function flushWaiting() {
  if (typeof window.renderMathInElement !== 'function') return;
  for (const element of waiting) {
    waiting.delete(element);
    if (element.isConnected) renderMath(element);
  }
}

export function renderMath(element) {
  if (!element) return false;
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
    return true;
  } catch {
    // A malformed expression must never interrupt reading the rest of the paper.
    return false;
  }
}
