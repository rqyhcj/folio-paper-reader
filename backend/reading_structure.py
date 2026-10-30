"""Conservative sentence and structured-region recovery from PDF geometry.

Only PDF text and ruled regions are consumed. No OCR, model, network or file
lookup is involved; uncertain tables remain ordinary source text for review.
"""
from __future__ import annotations

from copy import deepcopy
import re
from types import SimpleNamespace

from .math_recovery import recover_inline, _span_text, _ambiguous_inline_math


ALGORITHM = re.compile(r"^(?:Algorithm|算法)\s*\d+\b", re.I)
TABLE = re.compile(r"^(?:Table|Tab\.?|表)\s*(?:\d+|[IVXLCDM]+)(?:[.：:\s]|$)", re.I)
CAPTION = re.compile(r"^(?:Figure|Fig\.?|Table|Tab\.?|Algorithm|图|表|算法)\s*(?:\d+|[IVXLCDM]+)(?:[.：:\s]|$)", re.I)
LIST = re.compile(r"^(?:[•●▪▷▹]|\d+[.)]\s|\([a-z0-9]+\)\s|\[\d+\]\s)")
CONTROL = re.compile(r"^(?:\d+\s*:\s*)?(?:for\b|while\b|if\b|else\b|end\b|return\b|require\s*:|ensure\s*:|input\s*:|output\s*:|repeat\b|until\b|function\b)", re.I)


def _horizontal_rules(page, body_size):
    rules = []
    try:
        for path in page.get_drawings():
            box = path["rect"]
            if box.height < .8 and box.width >= body_size * 7:
                rules.append((float(box.x0), float((box.y0 + box.y1) / 2), float(box.x1)))
            for item in path.get("items", []):
                if item[0] == "l":
                    a, b = item[1:3]
                    if abs(a.y - b.y) < .4 and abs(a.x - b.x) >= body_size * 7:
                        rules.append((float(min(a.x, b.x)), float((a.y + b.y) / 2), float(max(a.x, b.x))))
            if len(rules) > 2000:
                break
    except Exception:
        return []
    return sorted(set(tuple(round(v, 2) for v in rule) for rule in rules), key=lambda item: item[1])


def _rows(lines, size):
    rows = []
    for line in sorted(lines, key=lambda item: (item.baseline, item.x0)):
        row = next((row for row in reversed(rows[-3:]) if abs(row[0].baseline - line.baseline) <= size * .42), None)
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    for row in rows:
        row.sort(key=lambda line: line.x0)
    return rows


def _region(page, box):
    return {"page": page, "bbox": [round(v, 3) for v in box]}


def _needs_visual_review(spans, plain):
    if "\ufffd" in plain or any(ord(char) < 32 and char not in "\n\t\r" for char in plain):
        return True
    # Standalone up/down arrows in metric labels are readable Unicode, not
    # failed formula structures. Other math-font runs must fully recover.
    mathematical = [span for span in spans if span.get("text", "").strip() not in {"↑", "↓"}]
    return _ambiguous_inline_math([SimpleNamespace(spans=mathematical)])


def _content(row):
    spans = [span for line in row for span in line.spans]
    spans.sort(key=lambda span: (span["bbox"][0], span["bbox"][1]))
    plain = _span_text(spans).strip() if spans else " ".join(line.text for line in row)
    text, _ = recover_inline(spans, plain) if spans else (plain, False)
    return text, plain, _needs_visual_review(spans, plain)


def _algorithm(lines, caption, box, body_size):
    selected = [line for line in lines if box[0] - 1 <= line.x0 and line.x1 <= box[2] + 1
                and box[1] <= (line.y0 + line.y1) / 2 <= box[3]]
    rows = _rows(selected, body_size)
    if not rows or len(rows) > 250:
        return None
    raw = [_content(row) for row in rows]
    if sum(bool(CONTROL.match(text)) for text, _, _ in raw) < 2:
        return None
    contents = []
    body_origins = []
    for row, (text, plain, needs_review) in zip(rows, raw):
        number = re.match(r"^(\d+)\s*:\s*", plain)
        origins = [span["bbox"][0] for line in row for span in line.spans
                   if span.get("text", "").strip() and not re.fullmatch(r"\s*\d+\s*:\s*", span.get("text", ""))]
        origin = min(origins, default=row[0].x0)
        if number and origin <= row[0].x0 + 1:
            # Some PDFs place the number and statement in one span. Estimate
            # only indentation in that case; statement text remains exact.
            origin += len(number.group()) * row[0].size * .45
        if number:
            body_origins.append(origin)
        contents.append((row, text, plain, number, origin, needs_review))
    base = min(body_origins, default=caption.x0)
    output = []
    sources = []
    for row, text, plain, number, origin, needs_review in contents:
        is_title = bool(ALGORITHM.match(plain))
        label = bool(re.match(r"^(?:Require|Ensure|Input|Output)\s*:", plain, re.I))
        indent = 0 if is_title or label else max(0, min(12, round((origin - base) / max(1, body_size * 1.25))))
        # A wrapped statement has no new step number/control keyword. Join it
        # to its existing logical step instead of presenting a broken word.
        continuation = (output and not is_title and not label and not number and not CONTROL.match(plain)
                        and not ALGORITHM.match(output[-1]["source_text"])
                        and not re.search(r"\b(?:do|then|else|end\s+\w+)\s*$", output[-1]["source_text"], re.I)
                        and row[0].baseline - contents[len(sources) - 1][0][0].baseline < body_size * 1.9)
        if continuation:
            from .pdf_parser import _join_lines
            output[-1]["text"] = _join_lines([output[-1]["text"], text])
            output[-1]["source_text"] = _join_lines([output[-1]["source_text"], plain])
            if needs_review:
                output[-1]["needs_visual_review"] = True
        else:
            output.append({"text": text, "indent": indent, "source_text": plain,
                           **({"needs_visual_review": True} if needs_review else {})})
        sources.append(plain)
    if len(output) < 3:
        return None
    source = _region(caption.page, box)
    review = any(item.get("needs_visual_review") for item in output)
    return {"kind": "algorithm", "level": 0, "text": "\n".join(item["text"] for item in output),
            "source_text": "\n".join(sources), "algorithm_lines": output,
            "page": caption.page, "bbox": source["bbox"], "source_regions": [source],
            "page_width": caption.width, "page_height": caption.height,
            **({"needs_visual_review": True, "structure_warnings": ["部分算法公式尚未可靠恢复，请用视觉识别或原版核对。"]} if review else {})}, selected


def _table(lines, caption, box, body_size):
    selected = [line for line in lines if box[0] - 1 <= line.x0 and line.x1 <= box[2] + 1
                and box[1] <= (line.y0 + line.y1) / 2 <= box[3]]
    rows = _rows(selected, body_size)
    if not 3 <= len(rows) <= 250:
        return None
    header_spans = sorted([span for line in rows[0] for span in line.spans if span.get("text", "").strip()],
                          key=lambda span: span["bbox"][0])
    if not header_spans:
        return None
    header = []
    for span in header_spans:
        if not header or span["bbox"][0] - max(item["bbox"][2] for item in header[-1]) > body_size * .75:
            header.append([span])
        else:
            header[-1].append(span)
    if not 2 <= len(header) <= 16:
        return None
    boundaries = [(max(span["bbox"][2] for span in left) + min(span["bbox"][0] for span in right)) / 2
                  for left, right in zip(header, header[1:])]
    cells = []
    originals = []
    review_cells = []
    for row in rows:
        buckets = [[] for _ in header]
        spans = sorted([span for line in row for span in line.spans if span.get("text", "").strip()],
                       key=lambda span: span["bbox"][0])
        for index, span in enumerate(spans):
            center = (span["bbox"][0] + span["bbox"][2]) / 2
            # A direction marker belongs to its adjacent label/value even
            # when a long label crosses the header-derived column midline.
            if span.get("text", "").strip() in {"↑", "↓"}:
                neighbors = []
                if index:
                    neighbors.append((max(0, span["bbox"][0] - spans[index - 1]["bbox"][2]), spans[index - 1]))
                if index + 1 < len(spans):
                    neighbors.append((max(0, spans[index + 1]["bbox"][0] - span["bbox"][2]), spans[index + 1]))
                neighbors.sort(key=lambda item: item[0])
                if neighbors and neighbors[0][0] <= body_size * .6:
                    neighbor = neighbors[0][1]
                    center = (neighbor["bbox"][0] + neighbor["bbox"][2]) / 2
            column = sum(center > boundary for boundary in boundaries)
            buckets[column].append(span)
        values, raw = [], []
        for column, bucket in enumerate(buckets):
            bucket.sort(key=lambda span: span["bbox"][0])
            plain = _span_text(bucket).strip()
            text, _ = recover_inline(bucket, plain) if bucket else ("", False)
            values.append(text)
            raw.append(plain)
            if _needs_visual_review(bucket, plain):
                review_cells.append({"row": len(cells), "column": column})
        cells.append(values)
        originals.append(raw)
    if sum(sum(bool(value) for value in row) >= 2 for row in cells) < max(3, len(cells) * .75):
        return None
    source = _region(caption.page, box)
    return {"kind": "table", "level": 0, "table_rows": cells,
            "text": "\n".join("\t".join(row) for row in cells),
            "source_text": "\n".join("\t".join(row) for row in originals),
            "page": caption.page, "bbox": source["bbox"], "source_regions": [source],
            "page_width": caption.width, "page_height": caption.height,
            **({"needs_visual_review": True, "table_review_cells": review_cells,
                "structure_warnings": ["部分表格公式尚未可靠恢复，请用视觉识别或原版核对。"]} if review_cells else {})}, selected


def extract_structures(page, lines, body_size):
    """Return serializable algorithm/table blocks and consumed source line IDs."""
    rules = _horizontal_rules(page, body_size)
    output, consumed = [], set()
    for caption in [line for line in lines if ALGORITHM.match(line.text)]:
        matching = [rule for rule in rules if abs(rule[0] - caption.x0) < body_size
                    and rule[2] >= caption.x1 - body_size]
        under = next((rule for rule in matching if caption.y1 <= rule[1] <= caption.y1 + body_size * 1.8), None)
        if under is None:
            continue
        bottom = next((rule for rule in matching if rule[1] >= under[1] + body_size * 4
                       and abs(rule[2] - under[2]) < body_size), None)
        if bottom is None or bottom[1] - caption.y0 > caption.height * .8:
            continue
        box = [under[0], caption.y0 - 1, under[2], bottom[1]]
        result = _algorithm([line for line in lines if id(line) not in consumed], caption, box, body_size)
        if result:
            block, selected = result
            output.append(block)
            consumed.update(map(id, selected))
    # Tables require both an explicit nearby caption and consistent ruled
    # edges. Diagrams, algorithms and aligned ordinary prose are not tables.
    groups = []
    for rule in rules:
        group = next((items for items in groups if abs(rule[0] - items[0][0]) <= 1.5
                      and abs(rule[2] - items[0][2]) <= 1.5), None)
        if group is None:
            groups.append([rule])
        else:
            group.append(rule)
    captions = [line for line in lines if TABLE.match(line.text) and id(line) not in consumed]
    separated = []
    for group in groups:
        group.sort(key=lambda rule: rule[1])
        run = []
        for rule in group:
            if run and any(run[-1][1] < line.y0 < rule[1] and rule[0] - body_size * 5 <= line.x0 <= rule[0] + body_size * 2
                           and line.x1 >= rule[0] for line in captions):
                separated.append(run)
                run = []
            run.append(rule)
        if run:
            separated.append(run)
    for group in separated:
        if len(group) < 2:
            continue
        group.sort(key=lambda rule: rule[1])
        x0, top, x1 = group[0]
        bottom = group[-1][1]
        if bottom - top < body_size * 2 or bottom - top > float(page.rect.height) * .7:
            continue
        candidates = [line for line in captions if x0 - body_size * 5 <= line.x0 <= x0 + body_size * 2
                      and line.x1 >= x0 and (
                          0 <= top - line.y1 <= body_size * 12 or 0 <= line.y0 - bottom <= body_size * 3)]
        if not candidates:
            continue
        caption = min(candidates, key=lambda line: min(abs(top - line.y1), abs(line.y0 - bottom)))
        result = _table([line for line in lines if id(line) not in consumed], caption,
                        [x0, top, x1, bottom], body_size)
        if result:
            block, selected = result
            output.append(block)
            consumed.update(map(id, selected))
    return output, consumed


def sentence_finished(text):
    text = text.strip()
    if re.search(r"\b(?:e\.g|i\.e|et al|Fig|Eq|Sec|Dr|Prof|vs)\.$", text, re.I):
        return False
    return bool(re.search(r"[.!?。！？][\"'”’）)\]}]*$", text))


def _floating_predecessor(result):
    """Find prose before a bounded run of independently positioned floats."""
    index = len(result) - 1
    skipped = []
    while index >= 0 and len(skipped) < 4:
        item = result[index]
        footnote = (item.get("kind") == "paragraph"
                    and item.get("bbox") and item.get("page_height")
                    and item["bbox"][1] >= item["page_height"] * .82
                    and len(item.get("text", "")) <= 500
                    and bool(re.match(r"^(?:[∗*†‡§]|\(?\d+[.)])", item.get("text", "").lstrip())))
        if item.get("kind") in {"algorithm", "table"} or footnote:
            skipped.append(item)
        elif item.get("kind") == "paragraph" and (ALGORITHM.match(item["text"]) or TABLE.match(item["text"])):
            skipped.append(item)
        else:
            break
        index -= 1
    return (index, list(reversed(skipped))) if index >= 0 and skipped else (len(result) - 1, [])


def _float_continuation(previous, item, skipped, old_region, body_size):
    """Require floats to account for the exact gap in this reading flow."""
    if not skipped:
        return False
    kinds = {part.get("kind") for part in skipped}
    if any(part.get("kind") == "paragraph" and not (
            part.get("bbox") and part.get("page_height") and part["bbox"][1] >= part["page_height"] * .82
            and re.match(r"^(?:[∗*†‡§]|\(?\d+[.)])", part.get("text", "").lstrip())) and (
            (ALGORITHM.match(part["text"]) and "algorithm" not in kinds)
            or (TABLE.match(part["text"]) and "table" not in kinds)) for part in skipped):
        return False
    old_box, new_box = old_region["bbox"], item["bbox"]
    boxes = [part["bbox"] for part in skipped]
    # All skipped content must belong to the receiving column (or its full
    # width), not to an unrelated neighboring table or caption.
    for box in boxes:
        overlap = min(box[2], new_box[2]) - max(box[0], new_box[0])
        if overlap < min(box[2] - box[0], new_box[2] - new_box[0]) * .6:
            return False
    top, bottom = min(box[1] for box in boxes), max(box[3] for box in boxes)
    same_page = old_region["page"] == item["page"]
    next_page = item["page"] == old_region["page"] + 1
    same_flow = previous.get("_last_flow") == item.get("_first_flow")
    if next_page and all(part["page"] == old_region["page"] for part in skipped):
        return (old_box[3] <= top + body_size and top >= previous.get("page_height", 800) * .75
                and new_box[1] <= item.get("page_height", 800) * .4)
    if any(part["page"] != item["page"] for part in skipped):
        return False
    if not -body_size * .5 <= new_box[1] - bottom <= body_size * 4:
        return False
    if same_page and same_flow:
        return -body_size * .5 <= top - old_box[3] <= body_size * 4
    # A column/page starts with the float, followed by the actual continuation.
    # Its text may legitimately start well below the old 40%-page cutoff.
    if top > item.get("page_height", 800) * .25 or old_box[3] < previous.get("page_height", 800) * .55:
        return False
    return next_page or (same_page and old_box[2] < new_box[0] and new_box[1] < old_box[1])


def _merge_prose(previous, item, close):
    from .pdf_parser import _join_lines
    old_text, new_text = previous["text"], item["text"]
    old_source = previous.get("source_text", old_text)
    new_source = item.get("source_text", new_text)
    protected = bool(previous.get("inline_math") or item.get("inline_math"))
    if protected:
        slots, parts = [], []
        for part in (previous, item):
            token_map = {}
            for formula in part.get("inline_math", []):
                formula = deepcopy(formula)
                old_token = formula["placeholder"]
                number = len(slots) + 1
                formula.update(id=f"m-{number}", placeholder=f"⟦FOLIO_MATH_{number}⟧")
                token_map[old_token] = formula["placeholder"]
                slots.append(formula)
            # One substitution pass avoids collisions when both fragments
            # originally use m-1, or a previous merge already has many slots.
            parts.append(re.sub(r"⟦FOLIO_MATH_\d+⟧", lambda match: token_map.get(match[0], match[0]),
                                part.get("translation_text", part["text"])))
        previous["inline_math"] = slots
        previous["translation_text"] = _join_lines(parts)
        previous["math_status"] = "source-inline"
        previous["ambiguous_inline_math"] = True
        previous["needs_visual_review"] = True
        warnings = list(dict.fromkeys(previous.get("structure_warnings", []) + item.get("structure_warnings", [])))
        warning = "段落已按原文续接，未恢复的行内公式仍需视觉识别或原版核对。"
        if warning not in warnings:
            warnings.append(warning)
        previous["structure_warnings"] = warnings
        # A single crop cannot represent prose spanning two columns/pages.
        # Formula slots retain their exact individual source rectangles.
        previous.pop("equation_image", None)
        previous.pop("equation_source", None)
    elif item.get("math_status") == "latex":
        previous["math_status"] = "latex"
    previous["text"] = _join_lines([old_text, new_text])
    previous["source_text"] = _join_lines([old_source, new_source])
    regions = previous.setdefault("source_regions", [_region(previous["page"], previous["bbox"])])
    regions.extend(item.get("source_regions") or [_region(item["page"], item["bbox"])])
    if close and previous["page"] == item["page"] and previous.get("_first_flow") == item.get("_first_flow"):
        new_box = item["bbox"]
        previous["bbox"] = [min(previous["bbox"][0], new_box[0]), min(previous["bbox"][1], new_box[1]),
                            max(previous["bbox"][2], new_box[2]), max(previous["bbox"][3], new_box[3])]
    previous["_last_flow"] = item.get("_last_flow")


def stitch_sentences(paragraphs, body_size):
    """Join only evidenced prose continuations, preserving every source region."""
    result = []
    for paragraph in paragraphs:
        item = deepcopy(paragraph)
        if result:
            index, skipped = _floating_predecessor(result)
            previous = result[index]
            # A float stays in the output after a successful merge. Once its
            # continuation has been consumed, it must not block another
            # adjacent fragment of that same unfinished sentence.
            skipped = [part for part in skipped if id(part) not in previous.get("_stitched_floats", set())]
            ordinary = all(p.get("kind") == "paragraph" and not CAPTION.match(p["text"])
                           and (p.get("math_status") != "source-image" or (skipped and p.get("inline_math") and p.get("translation_text")))
                           and (not p.get("inline_math") or skipped or p.get("math_status") == "source-inline")
                           for p in (previous, item)) and not LIST.match(item["text"])
            first = item["text"].lstrip()
            continued_word = bool(re.search(r"[A-Za-z]{2,}-$", previous["text"]) and re.match(r"^[a-z]", first))
            reference = bool(re.match(r"^(?:(?:Eqs?|Figs?|Secs?)\.\s*|(?:Equation|Figure|Section)\s+)\(?\d", first))
            continued_sentence = not sentence_finished(previous["text"]) and (reference or bool(re.match(r"^[a-z(\\$\u3400-\u9fff]", first)))
            last_region = (previous.get("source_regions") or [_region(previous["page"], previous["bbox"])])[-1]
            old_box, new_box = last_region["bbox"], item["bbox"]
            same_page = last_region["page"] == item["page"]
            same_flow = previous.get("_last_flow") == item.get("_first_flow")
            close = same_page and same_flow and -body_size <= new_box[1] - old_box[3] <= body_size * 2.5
            next_column = same_page and not same_flow and old_box[2] < new_box[0] and new_box[1] < old_box[1]
            next_page = (item["page"] == last_region["page"] + 1 and old_box[3] >= previous.get("page_height", 800) * .55
                         and new_box[1] <= item.get("page_height", 800) * .4)
            geometry = _float_continuation(previous, item, skipped, last_region, body_size) if skipped else close or next_column or next_page
            if ordinary and (continued_word or continued_sentence) and geometry:
                _merge_prose(previous, item, close and not skipped)
                if skipped:
                    previous.setdefault("_stitched_floats", set()).update(map(id, skipped))
                continue
        result.append(item)
    for paragraph in result:
        paragraph.pop("_first_flow", None)
        paragraph.pop("_last_flow", None)
        paragraph.pop("_stitched_floats", None)
    return result
