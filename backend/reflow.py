"""Conservatively carry saved translations across a new local PDF text parse.

This module performs no I/O. Every saved translation/error is archived before
matching; uncertain fragments are presented as prior parts, never as a complete
translation of newly assembled text.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import math
import re
import unicodedata

from .storage import refresh_progress, translation_paragraphs
from .translation_quality import response_issue


def _source(paragraph: dict) -> str:
    return str(paragraph.get("source_text") or paragraph.get("text") or "")


def _normal(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])", "", text)
    return re.sub(r"\s+", "", text)


def _box(value):
    if isinstance(value, dict):
        value = value.get("bbox")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = tuple(float(c) for c in value)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(c) for c in box) or box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _overlap(old_box, new_box, *, fragment=False) -> float:
    if old_box is None or new_box is None:
        return 0.0
    x0, y0 = max(old_box[0], new_box[0]), max(old_box[1], new_box[1])
    x1, y1 = min(old_box[2], new_box[2]), min(old_box[3], new_box[3])
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    old_area = (old_box[2] - old_box[0]) * (old_box[3] - old_box[1])
    new_area = (new_box[2] - new_box[0]) * (new_box[3] - new_box[1])
    return intersection / (old_area if fragment else min(old_area, new_area))


def _without_review_line_numbers(paragraph: dict, removed: list[dict], positions: dict) -> dict:
    """Clean only numbered gutter spans recorded inside this old paragraph.

    Reflow matching may ignore a removed margin marker, but never arbitrary
    prose numbers. Ambiguous repeated numeric tokens are left untouched. The
    unmodified record remains in translation_history before this helper runs.
    """
    box = _box(positions.get(paragraph.get("id"))) or _box(paragraph.get("bbox"))
    if box is None:
        return paragraph
    source = _source(paragraph)
    markers = []
    for item in removed:
        marker_box = _box(item.get("bbox"))
        value = str(item.get("text", ""))
        if (item.get("page") != paragraph.get("page") or marker_box is None
                or not re.fullmatch(r"[0-9]{1,4}", value)):
            continue
        if (marker_box[0] >= box[0] - 1 and marker_box[2] <= box[2] + 1
                and box[1] - 1 <= (marker_box[1] + marker_box[3]) / 2 <= box[3] + 1):
            pattern = r"(?<![\w.])" + re.escape(value) + r"(?![\w.])"
            if len(re.findall(pattern, source)) == 1:
                markers.append((value, pattern))
    if not markers:
        return paragraph
    cleaned = deepcopy(paragraph)
    first = re.match(r"^\s*([0-9]{1,4})(?=\s)", source)
    for _, pattern in markers:
        # A removed marker records a real PDF line boundary. Retaining that
        # boundary lets the existing conservative matcher rejoin broken words.
        source = re.sub(r"[ \t]*" + pattern + r"[ \t]*", "\n", source)
    cleaned["source_text"] = source.strip()
    if first and first.group(1) in {value for value, _ in markers}:
        translation = cleaned.get("translation")
        if isinstance(translation, str):
            value = first.group(1)
            alternatives = {value, str(int(value))}
            prefix = r"^\s*(?:" + "|".join(map(re.escape, alternatives)) + r")(?=\s|[\u3400-\u9fff])\s*"
            cleaned["translation"] = re.sub(prefix, "", translation, count=1)
    translation = cleaned.get("translation")
    if isinstance(translation, str):
        # A translated margin marker inside prose has no reliable alignment to
        # Chinese words. Keep it as a labelled prior result instead of deleting
        # a possibly meaningful value or presenting contaminated text as final.
        values = {variant for value, _ in markers for variant in (value, str(int(value)))}
        if any(re.search(r"(?<![A-Za-z0-9_.])" + re.escape(value) + r"(?![A-Za-z0-9_.])", translation) for value in values):
            cleaned["_review_line_number_residue"] = True
    return cleaned


def _part(paragraph: dict) -> dict:
    return {"id": paragraph["id"], "text": paragraph.get("text", ""),
            "translation": paragraph["translation"]}


def _covered_paths(target: str, candidates: list[dict]) -> list[list[dict]]:
    """Find at most two complete, ordered coverings; two means ambiguity."""
    starts = defaultdict(list)
    for item in candidates:
        variants = [item["normal"]]
        source = _source(item["paragraph"]).rstrip()
        # Only a line-end word break may lose its hyphen; a formula minus stays.
        if re.search(r"[A-Za-z]-$", source):
            variants.append(item["normal"][:-1])
        for value in dict.fromkeys(variants):
            if not value:
                continue
            offset = target.find(value)
            while offset != -1:
                starts[offset].append((offset + len(value), item))
                offset = target.find(value, offset + 1)
    stack = [(0, -1, [])]
    complete = []
    visits = Counter()
    while stack and len(complete) < 2:
        offset, previous, path = stack.pop()
        state = (offset, previous)
        visits[state] += 1
        if visits[state] > 2:
            continue
        if offset == len(target):
            # Validate with the actual joined source, including safe word-break repair.
            if _normal("\n".join(_source(item["paragraph"]) for item in path)) == target:
                signature = tuple(item["index"] for item in path)
                if not any(tuple(item["index"] for item in p) == signature for p in complete):
                    complete.append(path)
            continue
        for end, item in starts.get(offset, []):
            if item["index"] > previous:
                stack.append((end, item["index"], path + [item]))
    return complete


def _legacy_composition(paragraph: dict, history: list[dict]) -> list[dict] | None:
    """Recognize a previous reflow's verbatim concatenation, never infer one.

    A complete covering of English source says nothing about the grammatical
    order of separately translated Chinese clauses. Older versions joined
    these translations with newlines; identify that exact fingerprint so an
    unchanged source cannot make the invalid composition look trustworthy.
    """
    translation = paragraph.get("translation") or ""
    if "\n" not in translation:
        return None
    target = _normal(_source(paragraph))
    box = _box(paragraph.get("bbox"))
    fingerprint = _normal(translation)
    for entry in history:
        candidates = []
        for index, prior in enumerate(entry.get("paragraphs") or []):
            normal = _normal(_source(prior))
            if (not prior.get("translation") or prior.get("page") != paragraph.get("page")
                    or not normal or normal == target
                    or _overlap(_box(prior.get("bbox")), box, fragment=True) < .65):
                continue
            if normal in target or (re.search(r"[A-Za-z]-$", _source(prior).rstrip()) and normal[:-1] in target):
                candidates.append({"paragraph": prior, "index": index, "normal": normal})
        for path in _covered_paths(target, candidates):
            if len(path) > 1 and _normal("\n".join(item["paragraph"]["translation"] for item in path)) == fingerprint:
                return [item["paragraph"] for item in path]
    return None


def review_saved_translation(paragraph: dict, history: list[dict] | None = None,
                             *, target: dict | None = None) -> dict | None:
    """Describe a cached result that must not be reused as a finished translation.

    The result is read-only diagnostic data. The caller retains the original
    record in ``translation_history``; ``parts`` contains only useful previous
    translations suitable for a clearly labelled, collapsed reference view.
    """
    text = paragraph.get("translation")
    if not text:
        return None
    if paragraph.get("_review_line_number_residue"):
        return {"reason": "review-line-number-residue", "parts": [_part(paragraph)]}
    if response_issue(text, _source(paragraph)):
        return {"reason": "model-requested-source", "parts": []}
    protected = re.findall(r"⟦FOLIO_MATH_\d+⟧", str((target or paragraph).get("translation_text") or ""))
    if Counter(protected) != Counter(re.findall(r"⟦FOLIO_MATH_\d+⟧", text)):
        return {"reason": "protected-math-source", "parts": []}
    previous = _legacy_composition(paragraph, history or [])
    if previous:
        return {"reason": "paragraph-reassembled", "parts": [_part(item) for item in previous
                if not response_issue(item["translation"], _source(item))]}
    return None


def reflow_paper(old: dict, parsed: dict, positions: dict | None = None) -> dict:
    """Return a new paper using parsed IDs/outline and preserving every prior result.

    ``positions`` optionally supplies original PDF bounding boxes for old IDs.
    Exact unique text can survive without geometry. Contained fragments require
    matching page coordinates; partial coverage remains explicitly untranslated.
    """
    positions = positions or {}
    result = deepcopy(old)
    history = deepcopy(old.get("translation_history") or [])
    history.append({"parse_version": old.get("parse_version", 1),
                    "target_parse_version": parsed.get("parse_version", 1),
                    "status": old.get("status"), "error": old.get("error"),
                    "paragraphs": [deepcopy(p) for p in old["paragraphs"] if
                                   p.get("translation") or p.get("error") or p.get("previous_translation_parts")]})
    protected = {"id", "filename", "created_at", "is_demo", "translation_history",
                 "status", "error", "figures", "figure_status", "figure_progress", "figure_version", "figures_error"}
    result.update({key: deepcopy(value) for key, value in parsed.items() if key not in protected})
    result["translation_history"] = history
    result.update(figures=[], figure_status="pending", figure_progress={"done": 0, "total": result.get("page_count", 0)},
                  error=None, status="paused")
    result.pop("figure_version", None)
    result.pop("figures_error", None)

    records, exact = [], defaultdict(list)
    removed_line_numbers = parsed.get("removed_line_numbers") or []
    for index, paragraph in enumerate(old["paragraphs"]):
        paragraph = _without_review_line_numbers(paragraph, removed_line_numbers, positions)
        item = {"paragraph": paragraph, "index": index, "normal": _normal(_source(paragraph)),
                "box": _box(positions.get(paragraph["id"])) or _box(paragraph.get("bbox")),
                "review": review_saved_translation(paragraph, old.get("translation_history") or [])}
        records.append(item)
        if item["normal"]:
            exact[(paragraph.get("page"), item["normal"])].append(item)
    new_counts = Counter((p.get("page"), _normal(_source(p))) for p in result["paragraphs"])
    used = set()
    for paragraph in result["paragraphs"]:
        paragraph.update(translation=None, error=None)
        paragraph.pop("previous_translation_parts", None)
        paragraph.pop("translation_review_reason", None)
        if paragraph.get("kind") == "equation":
            continue
        normal = _normal(_source(paragraph))
        if not normal:
            continue
        key = (paragraph.get("page"), normal)
        new_box = _box(paragraph.get("bbox"))
        matches = [item for item in exact.get(key, []) if item["index"] not in used]
        chosen = None
        if len(matches) == 1 and new_counts[key] == 1:
            item = matches[0]
            if item["box"] is None or new_box is None or _overlap(item["box"], new_box) >= .5:
                chosen = item
        elif matches and new_box is not None:
            spatial = sorted((( _overlap(item["box"], new_box), item) for item in matches),
                             key=lambda pair: pair[0], reverse=True)
            if spatial[0][0] >= .5 and (len(spatial) == 1 or spatial[0][0] - spatial[1][0] >= .2):
                chosen = spatial[0][1]
        if chosen is not None:
            review = chosen["review"] or review_saved_translation(chosen["paragraph"], target=paragraph)
            if review:
                paragraph["translation_review_reason"] = review["reason"]
                if review["parts"]:
                    paragraph["previous_translation_parts"] = deepcopy(review["parts"])
            elif chosen["paragraph"].get("translation"):
                paragraph["translation"] = chosen["paragraph"]["translation"]
            elif chosen["paragraph"].get("previous_translation_parts"):
                safe_parts = [part for part in chosen["paragraph"]["previous_translation_parts"]
                              if part.get("translation") and not response_issue(part["translation"], part.get("text", ""))]
                if safe_parts:
                    paragraph["previous_translation_parts"] = deepcopy(safe_parts)
                paragraph["translation_review_reason"] = "paragraph-reassembled"
            elif chosen["paragraph"].get("translation_review_reason"):
                paragraph["translation_review_reason"] = chosen["paragraph"]["translation_review_reason"]
            used.add(chosen["index"])
            continue

        candidates, partial = [], []
        for item in records:
            source = item["paragraph"]
            if (item["index"] in used or not source.get("translation") or item["review"]
                    or source.get("page") != paragraph.get("page")
                    or not item["normal"] or _overlap(item["box"], new_box, fragment=True) < .65):
                continue
            if item["normal"] in normal:
                # A lone axis letter must not match the middle of another word.
                if len(item["normal"]) <= 2 and item["normal"] != normal:
                    token = re.escape(_source(source).strip())
                    if not token or not re.search(r"(?<!\w)" + token + r"(?!\w)", _source(paragraph)):
                        continue
                partial.append(item)
                # An ambiguous exact match must not become "unique" merely
                # because only one repeated old occurrence had a translation.
                if item["normal"] != normal:
                    candidates.append(item)
            elif normal in item["normal"] and _overlap(item["box"], new_box) >= .8:
                partial.append(item)
            elif item["normal"].endswith("-") and item["normal"][:-1] in normal:
                candidates.append(item)
                partial.append(item)
        paths = _covered_paths(normal, candidates)
        if len(paths) == 1:
            selected = paths[0]
            paragraph["previous_translation_parts"] = [_part(item["paragraph"]) for item in selected]
            paragraph["translation_review_reason"] = "paragraph-reassembled"
            used.update(item["index"] for item in selected)
        elif partial:
            paragraph["previous_translation_parts"] = [_part(item["paragraph"]) for item in partial]
            paragraph["translation_review_reason"] = "paragraph-reassembled"

    refresh_progress(result)
    if all(p.get("translation") for p in translation_paragraphs(result)):
        result["status"] = "completed"
    return result
