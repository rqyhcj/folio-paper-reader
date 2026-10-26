"""Apply validated page proposals while retaining unrelated reading history."""
from copy import deepcopy

from .ai_layout import apply_page_analysis
from .pdf_parser import _build_outline
from .reflow import reflow_paper
from .storage import refresh_progress


def apply_layout_update(paper: dict, proposal: dict) -> dict:
    result = apply_page_analysis(paper, proposal)
    changed = [group for group in proposal["groups"] if group.get("changed")]
    old_ids = {pid for group in changed for pid in group["ids"]}
    new_ids = {group["ids"][0] for group in changed}
    if changed:
        old = {**paper, "paragraphs": [p for p in paper["paragraphs"] if p["id"] in old_ids],
               "translation_history": []}
        parsed = {"paragraphs": [p for p in result["paragraphs"] if p["id"] in new_ids],
                  "parse_version": paper.get("parse_version", 7)}
        reflowed = reflow_paper(old, parsed)
        migrated = {p["id"]: p for p in reflowed["paragraphs"]}
        # A transcription from an image may change an equation while keeping
        # the same raw extracted source. Never reuse its old guessed translation.
        for group in changed:
            target = migrated[group["ids"][0]]
            if group.get("ai_reconstructed") and target.get("translation"):
                target.update(translation=None, error=None, translation_review_reason="ai-layout-reconstructed")
                target.pop("translated_algorithm_lines", None)
                target.pop("translated_table_rows", None)
        result["paragraphs"] = [migrated.get(p["id"], p) for p in result["paragraphs"]]
        entry = reflowed["translation_history"][-1]
        if entry["paragraphs"]:
            entry.update(reason="ai-layout", page=proposal["page"], model=proposal.get("model", ""))
            result.setdefault("translation_history", []).append(entry)
    mapping = {old_id: group["ids"][0] for group in proposal["groups"] for old_id in group["ids"]}
    kinds = {p["id"]: p.get("kind") for p in result["paragraphs"]}
    for figure in result.get("figures", []):
        for name in ("before_paragraph_id", "after_paragraph_id"):
            figure[name] = mapping.get(figure.get(name), figure.get(name))
        # A merged paragraph must not disappear just because one of its former
        # fragments was classified as a chart label.
        figure["text_paragraph_ids"] = [pid for pid in figure.get("text_paragraph_ids", [])
                                       if pid not in old_ids and kinds.get(pid) not in {"algorithm", "table"}]
    result["outline"] = _build_outline(result["paragraphs"], [])
    if proposal.get("vision"):
        result["ai_layout_regions"] = [r for r in paper.get("ai_layout_regions", []) if r.get("page") != proposal["page"]] + deepcopy(proposal["regions"])
    return refresh_progress(result)
