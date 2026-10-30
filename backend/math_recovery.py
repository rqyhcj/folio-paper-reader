"""Conservative TeX recovery from PDF character positions, never from prose guesses.

Characters, script positions and explicit vector rules are reconstructed only
when their structure is unambiguous. Unsupported encodings or layouts retain
their original PDF region rather than becoming a plausible, false equation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import re
import unicodedata


_MATH_FONT = re.compile(r"(?:CMMI|CMMIB|CMSY|CMEX|MSAM|MSBM|Math|Symbol)", re.I)
_CM_FONT = re.compile(r"(?:^|\+)(?:CM(?:R|BX|TI|MI|MIB|SY|SS)|MSBM)\d", re.I)
_NUMBER = re.compile(r"^\s*[（(](\d+[a-z]?)[）)]\s*$", re.I)
_OPERATORS = {"arg", "min", "max", "argmin", "argmax", "round", "floor", "ceil", "clip", "clamp",
              "sin", "cos", "tan", "log", "ln", "exp", "lim", "sup", "inf", "det", "diag", "rank",
              "tr", "Tr", "Var", "Cov", "Pr", "SVD", "SVDk", "ReLU"}
_GREEK_NAMES = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "varepsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ϑ": "vartheta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi", "ϖ": "varpi",
    "ρ": "rho", "ϱ": "varrho", "σ": "sigma", "ς": "varsigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "ϕ": "varphi", "χ": "chi", "ψ": "psi", "ω": "omega", "Γ": "Gamma",
    "Δ": "Delta", "∆": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma",
    "Υ": "Upsilon", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega",
}
_SYMBOLS = {
    "−": "-", "×": r"\times ", "·": r"\cdot ", "⋅": r"\cdot ", "÷": r"\div ",
    "±": r"\pm ", "∓": r"\mp ", "≈": r"\approx ", "≃": r"\simeq ", "≅": r"\cong ",
    "≠": r"\ne ", "≤": r"\le ", "≥": r"\ge ", "≪": r"\ll ", "≫": r"\gg ",
    "∼": r"\sim ", "∝": r"\propto ", "∞": r"\infty ", "∈": r"\in ", "∉": r"\notin ",
    "⊂": r"\subset ", "⊆": r"\subseteq ", "⊃": r"\supset ", "⊇": r"\supseteq ",
    "∪": r"\cup ", "∩": r"\cap ", "∅": r"\varnothing ", "∀": r"\forall ", "∃": r"\exists ",
    "∂": r"\partial ", "∇": r"\nabla ", "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ",
    "→": r"\to ", "←": r"\leftarrow ", "↔": r"\leftrightarrow ", "↦": r"\mapsto ",
    "⇒": r"\Rightarrow ", "⇔": r"\Leftrightarrow ", "⊤": r"\top ", "⊥": r"\perp ",
    "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}", "ℂ": r"\mathbb{C}",
    "ℚ": r"\mathbb{Q}", "ℓ": r"\ell ", "′": r"\prime ", "…": r"\ldots ", "∥": r"\Vert ",
    "⌊": r"\lfloor ", "⌋": r"\rfloor ", "⌈": r"\lceil ", "⌉": r"\rceil ",
    "⟨": r"\langle ", "⟩": r"\rangle ", "{": r"\{", "}": r"\}", "%": r"\%", "_": r"\_",
    "√": r"\sqrt ", "≮": r"\nless ", "≯": r"\ngtr ", "†": r"\dagger ", "∗": r"\ast ",
}
_ACCENTS = {"ˆ": r"\hat", "˜": r"\tilde"}


@dataclass(frozen=True)
class _Glyph:
    char: str
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    font: str
    name: str = ""
    latex: str | None = None


def keep_span_characters(span: dict) -> bool:
    """Retain character geometry only for bounded potential mathematical runs.

    Most PDF text is prose; retaining rawdict's nested per-character records
    for the entire paper would multiply memory usage unnecessarily.
    """
    if _MATH_FONT.search(span.get("font", "")):
        return True
    text = span.get("text", "").strip()
    if not text or len(text) > 450:
        return False
    words = re.findall(r"[A-Za-z]+", text)
    allowed = all(len(word) == 1 or word in _OPERATORS for word in words)
    return allowed and all(char.isspace() or char in _GREEK_NAMES or char in _SYMBOLS or char in _ACCENTS
                           or bool(re.fullmatch(r"[A-Za-z0-9=+\-*/.,:;!|()\[\]<>?]", char)) for char in text)


def _glyphs(spans: list[dict]) -> list[_Glyph]:
    result = []
    for span in spans:
        for char in span.get("chars", []):
            value = char.get("c", "")
            if not value or (value.isspace() and not char.get("_glyph_name")):
                continue
            box = char.get("bbox", span["bbox"])
            origin = char.get("origin", span.get("origin", (box[0], box[3])))
            result.append(_Glyph(value, *map(float, box), float(origin[1]), float(span["size"]), span["font"], char.get("_glyph_name", "")))
    return result


def _tex(glyph: _Glyph) -> str | None:
    char = glyph.char
    if glyph.latex is not None:
        return glyph.latex
    if char in _ACCENTS:
        return None  # Accents are valid only after geometric attachment to a base.
    if char == "√":
        return None  # A radical requires its proven overbar and radicand.
    named = {"parenleftbig": "(", "parenrightbig": ")", "parenleftBig": "(", "parenrightBig": ")",
             "parenleftbigg": "(", "parenrightbigg": ")", "parenleftBigg": "(", "parenrightBigg": ")",
             "bracketleftbig": "[", "bracketrightbig": "]", "bracketleftBig": "[", "bracketrightBig": "]",
             "bracketleftbigg": "[", "bracketrightbigg": "]", "bracketleftBigg": "[", "bracketrightBigg": "]",
             "summationtext": r"\sum", "summationdisplay": r"\sum", "vextenddouble": r"\Vert "}
    if glyph.name in named:
        return named[glyph.name]
    # Computer Modern's extension font often decodes accents/delimiters as
    # unrelated ASCII letters. We cannot establish their meaning from a PDF
    # text extraction, even when the resulting string happens to look valid.
    if "CMEX" in glyph.font.upper() or len(char) != 1 or unicodedata.combining(char):
        return None
    if char in _GREEK_NAMES:
        value = "\\" + _GREEK_NAMES[char] + " "
    elif char in _SYMBOLS:
        value = _SYMBOLS[char]
    elif re.fullmatch(r"[A-Za-z0-9=+\-*/.,:;!|()\[\]<>?]", char):
        value = char
    else:
        return None
    if re.fullmatch(r"[A-Za-z]", char) and "MSBM" in glyph.font.upper():
        value = r"\mathbb{" + value + "}"
    elif re.fullmatch(r"[A-Za-z]", char) and re.search(r"CMMIB|CMBX", glyph.font, re.I):
        value = r"\mathbf{" + value + "}"
    elif char in _GREEK_NAMES and re.search(r"CMMIB|CMBX", glyph.font, re.I):
        value = r"\boldsymbol{" + value.strip() + "}"
    return value


def _balanced(glyphs: list[_Glyph]) -> bool:
    stack = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for glyph in sorted(glyphs, key=lambda item: item.x0):
        char = _tex(glyph) if glyph.name.startswith(("paren", "bracket")) else glyph.char
        if char and char in "([{":
            stack.append(char)
        elif char in pairs:
            if not stack or stack.pop() != pairs[char]:
                return False
    return not stack


def _recover_flat_expression(spans: list[dict], *, numbered: bool = False,
                             fragment: bool = False) -> str | None:
    """Return TeX only when the extracted characters and layout are unambiguous.

    ``spans`` must contain rawdict ``chars`` with original font, size, origin,
    and bbox. No model, OCR or text-only equation guessing is performed.
    """
    if not spans:
        return None
    # Do not silently recover a subset after prose geometry was discarded.
    if any(span.get("text", "").strip() and not span.get("chars") for span in spans):
        return None
    equation_number = None
    if numbered:
        other = [span for span in spans if not _NUMBER.fullmatch(span.get("text", ""))]
        right = max((span["bbox"][2] for span in other), default=0)
        kept = []
        for span in spans:
            match = _NUMBER.fullmatch(span.get("text", ""))
            if match and span["bbox"][0] > right + float(span["size"]):
                if equation_number is not None:
                    return None
                equation_number = match.group(1)
            else:
                kept.append(span)
        spans = kept
    glyphs = _glyphs(spans)
    if not glyphs or len(glyphs) > 450:
        return None
    result = _flat_glyphs(glyphs, numbered=numbered, continuation=fragment,
                          allow_trailing=fragment)
    if result and equation_number:
        result += r"\tag{" + equation_number + "}"
    return result


def _flat_glyphs(glyphs: list[_Glyph], *, numbered: bool = False, continuation: bool = False,
                 allow_trailing: bool = False) -> str | None:
    # Any glyph that cannot be decoded also invalidates the surrounding
    # expression. A plausible subset would silently alter its meaning.
    if any(_tex(glyph) is None for glyph in glyphs):
        return None
    # A long subscript can contain more characters than its base variable.
    # Frequency alone would incorrectly promote that subscript to the main row.
    base_size = max(round(glyph.size, 1) for glyph in glyphs)
    main = [glyph for glyph in glyphs if glyph.size >= base_size * .88]
    if not main:
        return None
    baseline = Counter(round(glyph.baseline, 1) for glyph in main).most_common(1)[0][0]
    if any(abs(glyph.baseline - baseline) > base_size * .32 for glyph in main):
        return None  # multiple main rows / a stacked fraction or matrix
    scripts = [glyph for glyph in glyphs if glyph not in main]
    if any(glyph.size < base_size * .58 or abs(glyph.baseline - baseline) < base_size * .055 for glyph in scripts):
        return None
    if not _balanced(main):
        return None
    if numbered and not any(glyph.char in "=<>≈≃≅≠≤≥≪≫∈∉∼∝→↔⇒⇔" for glyph in main):
        return None  # an isolated tail/index of a larger, fragmented equation
    main.sort(key=lambda item: item.x0)
    # Non-space glyphs overlapping horizontally on one baseline are usually
    # combining overlays or inaccessible font encodings, not two variables.
    for left, right in zip(main, main[1:]):
        overlap = min(left.x1, right.x1) - max(left.x0, right.x0)
        if overlap > min(left.x1 - left.x0, right.x1 - right.x0) * .55:
            return None
    main = _named_operators(main)

    attachments: dict[int, dict[str, list[_Glyph]]] = {}
    script_groups: list[tuple[str, list[_Glyph]]] = []
    for glyph in sorted(scripts, key=lambda item: item.x0):
        key = "_" if glyph.baseline > baseline else "^"
        group = next((items for candidate_key, items in reversed(script_groups)
                      if candidate_key == key and abs(items[-1].baseline - glyph.baseline) <= base_size * .15
                      and -base_size * .12 <= glyph.x0 - items[-1].x1 <= base_size * .45), None)
        if group is None:
            script_groups.append((key, [glyph]))
        else:
            group.append(glyph)
    for key, group in script_groups:
        glyph = group[0]
        center = (group[0].x0 + group[-1].x1) / 2
        centered = [(index, item) for index, item in enumerate(main)
                    if item.name.startswith("operator:") and item.x0 <= center <= item.x1
                    and abs(glyph.baseline - baseline) >= base_size * .4]
        if len(centered) == 1:
            index, anchor = centered[0]
            name = anchor.name.split(":", 1)[1]
            main[index] = replace(anchor, latex=r"\operatorname*{" + name + r"}\limits")
        else:
            candidates = [(index, item) for index, item in enumerate(main)
                          if item.x0 <= glyph.x0 + base_size * .10 and item.x1 <= glyph.x0 + base_size * .55]
            if not candidates:
                return None
            index, anchor = min(candidates, key=lambda pair: abs(pair[1].x1 - glyph.x0))
            if glyph.x0 - anchor.x1 > base_size * .75:
                return None
        attachments.setdefault(index, {}).setdefault(key, []).extend(group)

    pieces = []
    for index, glyph in enumerate(main):
        value = _tex(glyph)
        for key in ("_", "^"):
            script = sorted(attachments.get(index, {}).get(key, []), key=lambda item: item.x0)
            if not script:
                continue
            for left, right in zip(script, script[1:]):
                if abs(left.baseline - right.baseline) > base_size * .15 or right.x0 < left.x1 - base_size * .12:
                    return None  # e.g. numerator/denominator stacked at same x
            if not _balanced(script):
                return None
            value += key + "{" + "".join(_tex(item) for item in script).strip() + "}"
        pieces.append(value)
    result = "".join(pieces).strip()
    if ((main[0].char and main[0].char in ("*/" if continuation else "=<>*/"))
            or (not allow_trailing and main[-1].char and main[-1].char in "=<>+*/-")):
        return None
    return result or None


def _named_operators(main: list[_Glyph]) -> list[_Glyph]:
    """Group verified roman names before assigning a centered limit.

    Attaching a limit to the nearest individual letter can turn ``min`` into
    ``m_{x}in``. Word boundaries and roman font evidence keep the whole name
    intact, while adjacent italic variables remain independent variables.
    """
    def roman(item):
        return (not item.name and item.latex is None and bool(re.fullmatch(r"[A-Za-z]", item.char))
                and bool(re.search(r"(?:^|\+)(?:CMR\d|CMSS\d)|Times-Roman|NimbusRom[^+]*Regu", item.font, re.I)))

    result = []
    index = 0
    while index < len(main):
        end = index + 1
        if roman(main[index]):
            while (end < len(main) and roman(main[end]) and main[end].font == main[index].font
                   and 0 <= main[end].x0 - main[end - 1].x1 <= main[index].size * .12):
                end += 1
        group = main[index:end]
        name = "".join(item.char for item in group)
        if len(group) > 1 and name in _OPERATORS:
            result.append(replace(_atom(group, r"\operatorname{" + name + "}", group[0].baseline,
                                        group[0].size), name="operator:" + name))
        else:
            result.extend(group)
        index = end
    return result


def annotate_page_math(page, lines: list) -> None:
    """Retain structural evidence omitted by PDF text extraction.

    Fraction/radical rules are vector strokes, not text. Extension-font
    letters are decoded only when the embedded Type 1 Encoding supplies the
    actual glyph name. No font program is executed and nothing leaves the PDF.
    The annotations stay on temporary spans and never enter the stored paper.
    """
    mathematical = [span for line in lines for span in line.spans if span.get("chars")]
    if not mathematical:
        return
    rules = []
    try:
        for path in page.get_drawings():
            if path.get("type") != "s" or not 0 < float(path.get("width") or 0) <= 1.4:
                continue
            for item in path.get("items", []):
                if item[0] != "l":
                    continue
                first, last = item[1:3]
                x0, x1 = sorted((float(first.x), float(last.x)))
                if abs(float(first.y) - float(last.y)) <= .15 and 1.0 <= x1 - x0 <= 400:
                    rules.append((x0, (float(first.y) + float(last.y)) / 2, x1))
                if len(rules) >= 4096:
                    break
            if len(rules) >= 4096:
                break
    except Exception:
        rules = []
    # A shared list avoids duplicating page geometry for every mathematical run.
    for span in mathematical:
        span["_math_rules"] = rules
    extension = [span for span in mathematical if "CMEX" in span.get("font", "").upper()]
    if not extension:
        return
    encodings = {}
    try:
        for font in page.get_fonts(full=True):
            name = font[3].split("+")[-1]
            if "CMEX" not in name.upper():
                continue
            _, format_name, _, raw = page.parent.extract_font(font[0])
            if format_name.lower() != "pfa":
                continue
            header = raw[:65536].split(b"currentfile eexec", 1)[0].decode("latin-1")
            encoding = {int(code): glyph for code, glyph in re.findall(
                r"\bdup\s+(\d+)\s+/([A-Za-z][A-Za-z0-9]*)\s+put\b", header)}
            # Only verified font-program names are used. A meaningful Unicode
            # mapping takes precedence; C0 control-character mappings do not —
            # TeX PDFs frequently map CMEX delimiters to their raw font codes
            # (for example 0x12) even though the PFA identifies a large left
            # parenthesis unambiguously.
            unicode_codes = set()
            unicode_values = set()
            obj = page.parent.xref_object(font[0])
            # A PDF-level Encoding can override the font program's built-in
            # positions. Until that encoding is decoded, names from the PFA
            # alone are insufficient evidence for these glyphs.
            if re.search(r"/Encoding\b", obj):
                continue
            ref = re.search(r"/ToUnicode\s+(\d+)\s+0\s+R", obj)
            if ref:
                cmap = page.parent.xref_stream(int(ref.group(1))).decode("latin-1")
                for section in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
                    for code, value in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section):
                        mapped = int(value, 16) if len(value) <= 4 else 0
                        if mapped >= 32 and mapped != 127:
                            unicode_codes.add(int(code, 16))
                            unicode_values.add(mapped)
                for section in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
                    for low, high, values in re.findall(
                            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", section):
                        start, end = int(low, 16), min(int(high, 16), 255)
                        if values.startswith("["):
                            mapped = [int(value, 16) for value in re.findall(r"<([0-9A-Fa-f]{1,4})>", values)]
                            unicode_codes.update(code for code, value in zip(range(start, end + 1), mapped)
                                                 if value >= 32 and value != 127)
                            unicode_values.update(value for value in mapped if value >= 32 and value != 127)
                        elif len(values) <= 6:
                            first = int(values[1:-1], 16)
                            if first >= 32 and first != 127:
                                unicode_codes.update(range(start, end + 1))
                                unicode_values.update(range(first, first + max(0, end - start + 1)))
            encodings[name] = {code: glyph for code, glyph in encoding.items()
                               if code < 128 and code not in unicode_codes and code not in unicode_values}
    except Exception:
        encodings = {}
    for span in extension:
        encoding = encodings.get(span["font"].split("+")[-1], {})
        for char in span.get("chars", []):
            value = char.get("c", "")
            if len(value) == 1 and ord(value) in encoding:
                char["_glyph_name"] = encoding[ord(value)]
        # A tall double norm delimiter is built from repeated vertical
        # extension pieces.  Once the embedded font program has verified every
        # piece as ``vextenddouble``, expose one semantic glyph spanning the
        # complete stack.  Keeping the raw control characters would either
        # drop the delimiter or render three literal bars.
        pieces = span.get("chars", [])
        if pieces and all(char.get("_glyph_name") == "vextenddouble" for char in pieces):
            first = dict(pieces[0])
            first["c"] = "∥"
            first["bbox"] = span["bbox"]
            first["origin"] = (span["bbox"][0], pieces[-1].get("origin", (0, span["bbox"][3]))[1])
            span["chars"] = [first]
            span["text"] = "∥"


def _atom(items: list[_Glyph], latex: str, baseline: float, size: float) -> _Glyph:
    return _Glyph("", min(item.x0 for item in items), min(item.y0 for item in items),
                  max(item.x1 for item in items), max(item.y1 for item in items),
                  baseline, size, "", latex=latex)


def _structured_expression(glyphs: list[_Glyph], rules: list[tuple], *, fragment: bool = False) -> str | None:
    """Bottom-up layout recovery using explicit PDF glyphs and vector rules.

    Every consumed character belongs to one validated node. Unsupported
    structure leaves a node unresolved, causing the whole expression to use
    the source fallback instead of emitting a plausible partial equation.
    """
    if not glyphs or len(glyphs) > 450:
        return None
    items = list(glyphs)
    size = max(item.size for item in items)
    # PDF fonts can emit a zero-width negation slash followed by a relation.
    # Only that precise overlap is composed; a detached accent is not ignored.
    for slash in list(items):
        if slash.char != "\u0338" or slash.x1 - slash.x0 > size * .1:
            continue
        relations = [item for item in items if item.char in "=<>" and
                     abs(item.baseline - slash.baseline) < size * .1 and
                     -.15 * size <= item.x0 - slash.x0 <= size * .5]
        if len(relations) == 1:
            relation = relations[0]
            items[items.index(relation)] = replace(relation, char={"=": "≠", "<": "≮", ">": "≯"}[relation.char])
            items.remove(slash)
    # PDF's extension glyph origin is at the top rather than the baseline.
    # For verified delimiters, surrounding normal-sized text supplies the row.
    ordinary = [item for item in items if "CMEX" not in item.font.upper() and item.size >= size * .88]
    if ordinary:
        baseline = Counter(round(item.baseline, 1) for item in ordinary).most_common(1)[0][0]
        items = [replace(item, baseline=baseline, char=_tex(item)) if item.name.startswith(("paren", "bracket")) and _tex(item)
                 else item for item in items]
    active_rules = list(dict.fromkeys(tuple(rule) for rule in rules))
    roots = [item for item in items if item.char == "√" or item.name in {"radicalbig", "radicalBig", "radicalbigg", "radicalBigg"}]
    root_bars = {}
    for root in roots:
        matches = [rule for rule in active_rules if
                   -.2 * size <= rule[0] - root.x1 <= size * .7 and
                   root.y0 - size * .1 <= rule[1] <= root.y1 + size * .2]
        if matches:
            root_bars[root] = min(matches, key=lambda rule: abs(rule[1] - root.baseline))
    fraction_bars = sorted([rule for rule in active_rules if rule not in root_bars.values()], key=lambda rule: rule[2] - rule[0])

    # Inline Computer Modern sums place their upper and lower limits in two
    # small rows immediately to the right of the extension glyph.  PyMuPDF
    # exposes the verified Type 1 glyph name (``summationtext``), but its PDF
    # origin is at the top of the box rather than on the surrounding equation
    # baseline.  Build one operator atom from that explicit name and geometry
    # before recovering an enclosing radical.  Without this step the raw font
    # codes leak into prose as strings such as ``qPmin(m,n)``.
    for operator in [item for item in list(items)
                     if item.name in {"summationtext", "summationdisplay"}]:
        small = [item for item in items if item is not operator and item.size <= operator.size * .86
                 and operator.x1 - operator.size * .08 <= item.x0 <= operator.x1 + operator.size * 4.5]
        if not small:
            continue
        center_y = (operator.y0 + operator.y1) / 2

        def contiguous(candidates: list[_Glyph]) -> list[_Glyph]:
            row = []
            for item in sorted(candidates, key=lambda glyph: glyph.x0):
                if not row:
                    if item.x0 - operator.x1 > operator.size * .30:
                        continue
                    row.append(item)
                elif (abs(item.baseline - row[-1].baseline) <= operator.size * .16
                      and -.12 * operator.size <= item.x0 - row[-1].x1 <= operator.size * .35):
                    row.append(item)
                else:
                    break
            return row

        upper = contiguous([item for item in small if (item.y0 + item.y1) / 2 < center_y])
        lower = contiguous([item for item in small if (item.y0 + item.y1) / 2 > center_y])
        upper_tex = _flat_glyphs(upper) if upper else None
        lower_tex = _flat_glyphs(lower) if lower else None
        if (upper and not upper_tex) or (lower and not lower_tex) or not (upper_tex or lower_tex):
            continue
        command = r"\sum"
        if lower_tex:
            command += "_{" + lower_tex + "}"
        if upper_tex:
            command += "^{" + upper_tex + "}"
        consumed = [operator, *upper, *lower]
        right = max(item.x1 for item in consumed)
        row_neighbors = [item for item in items if item not in consumed and item.size >= operator.size * .88
                         and right - operator.size * .08 <= item.x0 <= right + operator.size * .65
                         and "CMEX" not in item.font.upper()]
        baseline = min(row_neighbors, key=lambda item: item.x0).baseline if row_neighbors else operator.y1
        atom = _atom(consumed, command, baseline, operator.size)
        items = [item for item in items if item not in consumed] + [atom]

    for _ in range(min(64, len(items) + len(active_rules))):
        changed = False
        for accent in list(items):
            command = (_ACCENTS.get(accent.char) or
                       (r"\widehat" if accent.name in {"hatwide", "hatwider", "hatwidest"} else
                        r"\widetilde" if accent.name in {"tildewide", "tildewider", "tildewidest"} else None))
            if command is None:
                continue
            body = [item for item in items if item != accent and item.size >= accent.size * .88
                    and accent.baseline + size * .1 <= item.baseline <= accent.baseline + size * .65
                    and accent.x0 - size * .15 <= (item.x0 + item.x1) / 2 <= accent.x1 + size * .15
                    and (item.latex is not None or item.char.isalnum() or item.char in _GREEK_NAMES)]
            value = _flat_glyphs(body) if body else None
            if not value:
                continue
            atom = _atom([accent, *body], command + "{" + value + "}", body[0].baseline, body[0].size)
            items = [item for item in items if item != accent and item not in body] + [atom]
            changed = True
        for root, rule in list(root_bars.items()):
            if root not in items:
                continue
            x0, y, x1 = rule
            radicand = [item for item in items if item != root and
                        x0 - size * .12 <= (item.x0 + item.x1) / 2 <= x1 + size * .12 and
                        y + size * .15 <= item.baseline <= y + max(size * 1.6, root.y1 - y + size * .2)]
            value = _flat_glyphs(radicand) if radicand else None
            if value:
                local_size = max(item.size for item in radicand)
                baseline = max(item.baseline for item in radicand if item.size >= local_size * .88)
                atom = _atom([root, *radicand], r"\sqrt{" + value + "}", baseline, size)
                items = [item for item in items if item != root and item not in radicand] + [atom]
                root_bars.pop(root)
                changed = True
        for rule in list(fraction_bars):
            x0, y, x1 = rule
            within = [item for item in items if not item.name.startswith("bracehtip") and
                      x0 - size * .12 <= (item.x0 + item.x1) / 2 <= x1 + size * .12]
            numerator = [item for item in within if y - size * 2.0 <= item.baseline < y - size * .05]
            denominator = [item for item in within if y + size * .35 < item.baseline <= y + size * 2.0]
            if not numerator or not denominator:
                continue
            top, bottom = _flat_glyphs(numerator), _flat_glyphs(denominator)
            if not top or not bottom:
                continue
            # A fraction rule must span every numerator/denominator character,
            # including script boxes; nearby columns cannot be consumed.
            if any(item.x0 < x0 - size * .2 or item.x1 > x1 + size * .2
                   for item in [*numerator, *denominator]):
                continue
            atom = _atom([*numerator, *denominator], r"\frac{" + top + "}{" + bottom + "}", y + size * .25, size)
            items = [item for item in items if item not in numerator and item not in denominator] + [atom]
            fraction_bars.remove(rule)
            changed = True
        # A horizontal underbrace consists of four named Type 1 tip glyphs.
        # ASCII lookalikes from an unknown font cannot activate this rule.
        tips = sorted([item for item in items if item.name.startswith("bracehtip")], key=lambda item: item.x0)
        for start in range(max(0, len(tips) - 3)):
            brace = tips[start:start + 4]
            if [item.name for item in brace] != ["bracehtipupleft", "bracehtipdownright", "bracehtipdownleft", "bracehtipupright"]:
                continue
            baseline = brace[0].baseline
            if any(abs(item.baseline - baseline) > size * .1 for item in brace):
                continue
            x0, x1 = brace[0].x0, brace[-1].x1
            above = [item for item in items if item not in brace and
                     item.x0 >= x0 - size * .15 and item.x1 <= x1 + size * .15 and
                     baseline - size * 3.0 <= item.baseline < baseline - size * .4]
            label = [item for item in items if item not in brace and
                     x0 - size * .15 <= (item.x0 + item.x1) / 2 <= x1 + size * .15 and
                     baseline + size * .4 < item.baseline <= baseline + size * 1.6]
            top, bottom = _flat_glyphs(above) if above else None, _flat_glyphs(label) if label else None
            if not top or not bottom:
                continue
            atom = _atom([*above, *brace, *label], r"\underbrace{" + top + "}_{" + bottom + "}", above[0].baseline, size)
            items = [item for item in items if item not in above and item not in brace and item not in label] + [atom]
            changed = True
            break
        if not changed:
            break
    return (_flat_glyphs(items, continuation=fragment, allow_trailing=fragment)
            or _continued_rows(items))


def _continued_rows(glyphs: list[_Glyph]) -> str | None:
    """Preserve clearly separated equation continuations as explicit rows.

    Only a full first relation followed by rows beginning with another
    relation qualifies. Arbitrary stacked numerators, matrix cells or two
    unrelated equations cannot become a multiline display through this path.
    """
    size = max((item.size for item in glyphs), default=0)
    main = sorted([item for item in glyphs if item.size >= size * .88], key=lambda item: item.baseline)
    rows = []
    for item in main:
        if not rows or abs(item.baseline - rows[-1][0].baseline) > size * .32:
            rows.append([item])
        else:
            rows[-1].append(item)
    if not 2 <= len(rows) <= 5:
        return None
    if any(not size * .65 <= bottom[0].baseline - top[0].baseline <= size * 2.2
           for top, bottom in zip(rows, rows[1:])):
        return None
    relation = lambda item: item.char in "=<>≈≃≅≠≤≥≪≫∈∉∼∝→↔⇒⇔" and bool(item.char)
    if not any(relation(item) for item in rows[0]):
        return None
    if any(not relation(min(row, key=lambda item: item.x0)) for row in rows[1:]):
        return None
    for item in glyphs:
        if item in main:
            continue
        distances = sorted((abs(item.baseline - row[0].baseline), index) for index, row in enumerate(rows))
        if distances[0][0] > size * .8 or distances[1][0] - distances[0][0] < size * .12:
            return None
        rows[distances[0][1]].append(item)
    values = [_flat_glyphs(row, continuation=index > 0) for index, row in enumerate(rows)]
    if not all(values):
        return None
    return r"\begin{aligned}" + r"\\".join("&" + value for value in values) + r"\end{aligned}"


def recover_expression(spans: list[dict], *, numbered: bool = False,
                       fragment: bool = False) -> str | None:
    simple = _recover_flat_expression(spans, numbered=numbered, fragment=fragment)
    if simple:
        return simple
    if not spans or any(span.get("text", "").strip() and not span.get("chars") for span in spans):
        return None
    number = None
    if numbered:
        other = [span for span in spans if not _NUMBER.fullmatch(span.get("text", ""))]
        right = max((span["bbox"][2] for span in other), default=0)
        kept = []
        for span in spans:
            match = _NUMBER.fullmatch(span.get("text", ""))
            if match and span["bbox"][0] > right + float(span["size"]):
                if number is not None:
                    return None
                number = match.group(1)
            else:
                kept.append(span)
        spans = kept
    glyphs = _glyphs(spans)
    groups = {id(span["_math_rules"]): span["_math_rules"] for span in spans if span.get("_math_rules")}
    box = (min((item.x0 for item in glyphs), default=0), min((item.y0 for item in glyphs), default=0),
           max((item.x1 for item in glyphs), default=0), max((item.y1 for item in glyphs), default=0))
    rules = [rule for group in groups.values() for rule in group
             if rule[0] >= box[0] - 2 and rule[2] <= box[2] + 2 and box[1] - 2 <= rule[1] <= box[3] + 2]
    result = _structured_expression(glyphs, rules, fragment=fragment)
    if result and numbered and not re.search(r"[=<>]|\\(?:le|ge|ne|in|sim|to)\b", result):
        return None
    if result and number:
        result += r"\tag{" + number + "}"
    return result


def _span_text(spans: list[dict]) -> str:
    result = []
    previous = None
    for span in spans:
        text = span.get("text", "")
        if previous and result and not result[-1].endswith(" ") and not text.startswith(" "):
            gap = span["bbox"][0] - previous["bbox"][2]
            if gap > min(span["size"], previous["size"]) * .18:
                result.append(" ")
        result.append(text)
        previous = span
    return "".join(result)


def _is_seed(span: dict) -> bool:
    text = span.get("text", "").strip()
    return bool(text and (_MATH_FONT.search(span.get("font", "")) or "√" in text
                         or (any(char in _GREEK_NAMES for char in text) and not re.search(r"[A-Za-z]{2}", text))))


def _is_context(span: dict) -> bool:
    text = span.get("text", "").strip()
    if not text:
        return True
    if _is_seed(span):
        return True
    if not _CM_FONT.search(span.get("font", "")):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    return all(len(word) == 1 or word in _OPERATORS for word in words)


def recover_inline(spans: list[dict], original: str) -> tuple[str, bool]:
    """Recover contiguous math-font runs while leaving surrounding prose plain."""
    parts = []
    changed = False
    index = 0
    while index < len(spans):
        if not _is_context(spans[index]):
            parts.append(([spans[index]], None))
            index += 1
            continue
        end = index + 1
        while end < len(spans) and _is_context(spans[end]):
            end += 1
        run = spans[index:end]
        latex = recover_expression(run) if any(_is_seed(span) for span in run) else None
        if latex:
            raw = _span_text(run)
            prefix = " " if raw[:1].isspace() else ""
            suffix = " " if raw[-1:].isspace() else ""
            parts.append((run, prefix + r"\(" + latex + r"\)" + suffix))
            changed = True
        else:
            parts.append((run, None))
        index = end
    if not changed:
        return original, False
    # Preserve the same geometry-based gap policy across the new boundaries.
    result = []
    previous = None
    for run, formatted in parts:
        value = formatted if formatted is not None else _span_text(run)
        first = run[0]
        if previous and result and not result[-1].endswith(" ") and not value.startswith(" "):
            gap = first["bbox"][0] - previous["bbox"][2]
            if gap > min(first["size"], previous["size"]) * .18:
                result.append(" ")
        result.append(value)
        previous = run[-1]
    return "".join(result).strip(), True


def _protect_inline_translation(paragraph: dict, lines: list) -> None:
    """Send prose plus opaque formula slots, never broken PDF math, to a model.

    The original coordinates remain local. Adjacent mathematical runs spanning
    two physical rows share one slot with multiple source rectangles, so the
    translator cannot mistake a root's decoded ``p`` for prose or invent its
    radicand. A formula with trustworthy character geometry may also supply
    TeX for display; this does not change the protected translation input.
    """
    result = []
    slots = []
    slot_spans = []
    slot_runs = []
    active = None
    for line in lines:
        chunks = []
        for span in line.spans:
            if not span.get("text", "").strip():
                continue
            mathematical = _is_context(span)
            if chunks and chunks[-1][0] == mathematical:
                chunks[-1][1].append(span)
            else:
                chunks.append((mathematical, [span]))
        for mathematical, spans in chunks:
            if not mathematical:
                text = _span_text(spans).strip()
                if text:
                    result.append(text)
                    active = None
                continue
            if active is None:
                number = len(slots) + 1
                active = {"id": f"m-{number}", "placeholder": f"⟦FOLIO_MATH_{number}⟧", "source": []}
                slots.append(active)
                slot_spans.append([])
                slot_runs.append([])
                result.append(active["placeholder"])
            box = [round(min(span["bbox"][0] for span in spans), 3),
                   round(min(span["bbox"][1] for span in spans), 3),
                   round(max(span["bbox"][2] for span in spans), 3),
                   round(max(span["bbox"][3] for span in spans), 3)]
            # TeX positions a radical glyph above the real row, but its font
            # bbox includes empty ascent reaching into the preceding prose.
            # The radicand / numerator gives the physical row's upper bound;
            # keep that bound for inline crops while the complete original
            # paragraph region remains unchanged. Unknown delimiters are not
            # clamped: unlike a recognized radical their ink may be tall.
            root = lambda span: ("√" in span.get("text", "") or
                                 ("CMEX" in span.get("font", "").upper()
                                  and span.get("text", "").strip() in {"p", "q", "r", "s"}))
            ordinary = [span for span in spans if not root(span)]
            if ordinary and any(root(span) for span in spans):
                box[1] = round(min(span["bbox"][1] for span in ordinary), 3)
            active["source"].append({"page": paragraph["page"], "bbox": box})
            slot_spans[len(slots) - 1].extend(spans)
            slot_runs[len(slots) - 1].append(spans)
    if not slots:
        return
    for slot, spans, runs in zip(slots, slot_spans, slot_runs):
        slot["source_text"] = _span_text(spans).strip()
        latex = recover_expression(spans)
        if not latex and len(runs) > 1:
            fragments = [recover_expression(run, fragment=True) for run in runs]
            if all(fragments):
                latex = "".join(fragments)
        if latex:
            slot["latex"] = latex
    from .pdf_parser import _join_lines
    paragraph["translation_text"] = _join_lines(result)
    paragraph["inline_math"] = slots


def _ambiguous_inline_math(lines: list) -> bool:
    for line in lines:
        run = []
        for span in [*line.spans, None]:
            if span is not None and _is_context(span):
                run.append(span)
                continue
            if any(_is_seed(item) for item in run) and recover_expression(run) is None:
                return True
            run = []
    return False


def recover_paragraph(paragraph: dict, lines: list) -> dict:
    """Annotate a parsed paragraph without exposing raw PDF spans in the API."""
    if paragraph["kind"] == "equation":
        paragraph["source_text"] = paragraph["text"]
        paragraph["equation_source"] = {"page": paragraph["page"], "bbox": paragraph["bbox"]}
        spans = [span for line in lines for span in line.spans]
        latex = recover_expression(spans, numbered=True)
        if latex:
            paragraph["text"] = "$$" + latex + "$$"
            paragraph["math_status"] = "latex"
        else:
            box = paragraph["bbox"]
            paragraph["math_status"] = "source-image"
            paragraph["equation_image"] = {"width": max(1, round(box[2] - box[0])),
                                           "height": max(1, round(box[3] - box[1])), "latex_available": False}
        return paragraph
    if paragraph["kind"] == "heading":
        return paragraph
    if _ambiguous_inline_math(lines):
        # Keep prose selectable and isolate only the uncertain mathematical
        # runs. Previously the entire paragraph became a screenshot, which was
        # visually heavy and made captions look like duplicate figures. Each
        # unresolved slot still retains its exact local source rectangle and
        # can later be replaced by visual LaTeX recognition.
        paragraph["source_text"] = paragraph["text"]
        paragraph["math_status"] = "source-inline"
        paragraph["ambiguous_inline_math"] = True
        _protect_inline_translation(paragraph, lines)
        return paragraph
    changed = False
    texts = []
    for line in lines:
        text, converted = recover_inline(line.spans, line.text)
        texts.append(text)
        changed = changed or converted
    if changed:
        # Import locally to reuse the extractor's dehyphenation and CJK rules.
        from .pdf_parser import _join_lines
        paragraph["source_text"] = paragraph["text"]
        paragraph["text"] = _join_lines(texts)
        paragraph["math_status"] = "latex"
    return paragraph


def expand_equation_source(paragraph: dict, group: list, page_lines: list, body_size: float) -> None:
    """Include adjacent mathematical fragments in an original-equation crop.

    Complex layouts may split a single equation across several PDF blocks.
    Such fragments must remain visible in the source image even when the
    conservative paragraph assembler did not join their text.
    """
    if paragraph.get("kind") != "equation":
        return
    baselines = [line.baseline for line in group if line.equation] or [line.baseline for line in group]
    group_ids = {id(line) for line in group}
    neighbors = [line for line in page_lines if id(line) not in group_ids and line.flow == group[0].flow
                 and min(abs(line.baseline - baseline) for baseline in baselines) <= body_size * 1.1
                 and (line.math_ratio > .40 or line.size < body_size * .85
                      or (len(line.text) < 90 and bool(re.search(r"[=∑∫∏≈≤≥]", line.text))))]
    if not neighbors:
        return
    box = list(paragraph["bbox"])
    for line in neighbors:
        box = [min(box[0], line.x0), min(box[1], line.y0), max(box[2], line.x1), max(box[3], line.y1)]
    paragraph["equation_source"]["bbox"] = [round(value, 3) for value in box]
    # A valid TeX string describing only one fragment still misrepresents the
    # complete source equation. Prefer the full original region instead.
    paragraph["text"] = paragraph["source_text"]
    paragraph["math_status"] = "source-image"
    paragraph["equation_image"] = {"width": max(1, round(box[2] - box[0])),
                                   "height": max(1, round(box[3] - box[1])), "latex_available": False}
