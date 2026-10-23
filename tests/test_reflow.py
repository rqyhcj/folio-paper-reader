"""Saved translations survive a safer reparse without guessed paragraph matches."""
from copy import deepcopy

from backend.reflow import reflow_paper


def paragraph(pid, text, *, page=1, bbox=None, translation=None, error=None, **extra):
    return dict(id=pid, text=text, page=page, bbox=bbox, translation=translation, error=error,
                kind="paragraph", level=0, section_id="section-1", **extra)


def paper(paragraphs, **extra):
    return dict(id="saved-paper", title="A saved paper", filename="saved.pdf", created_at="2026-01-01",
                page_count=2, paragraphs=paragraphs, outline=[], warnings=[], parse_version=1,
                status="error", error="Old task error", is_demo=False, **extra)


def parsed(paragraphs, **extra):
    return dict(title="Corrected title", page_count=2, paragraphs=paragraphs, parse_version=3,
                outline=[dict(id="section-1", paragraph_id=paragraphs[0]["id"], title="Introduction", page=1, level=1)],
                warnings=[], **extra)


def test_unique_exact_sources_keep_translation_and_new_ids_with_complete_raw_archive():
    old = paper([paragraph("old-7", "A saved paragraph.", translation="原样保留\n**旧译文**", error="Prior warning"),
                 paragraph("old-8", "Untranslated source.", error="Previous API failure")],
                figures=[dict(id="f-1")], figure_version=2, figure_status="completed", figures_error="old image failure")
    new = parsed([paragraph("p-1", "A saved paragraph."), paragraph("p-2", "Untranslated source.")])
    before_old, before_new = deepcopy(old), deepcopy(new)
    result = reflow_paper(old, new)
    assert old == before_old and new == before_new
    assert result["id"] == "saved-paper" and result["filename"] == "saved.pdf"
    assert result["title"] == "Corrected title" and result["parse_version"] == 3
    assert [p["id"] for p in result["paragraphs"]] == ["p-1", "p-2"]
    assert result["outline"] == new["outline"]
    assert result["paragraphs"][0]["translation"] == "原样保留\n**旧译文**"
    assert all(p["error"] is None for p in result["paragraphs"])
    assert result["error"] is None and result["status"] == "paused"
    assert result["translation_history"][-1] == dict(parse_version=1, target_parse_version=3, status="error", error="Old task error", paragraphs=old["paragraphs"])
    assert result["figures"] == [] and result["figure_status"] == "pending"
    assert "figure_version" not in result and "figures_error" not in result


def test_exact_matching_uses_pre_math_source_text_and_normalizes_space_and_ligatures():
    old = paper([paragraph("old", "An ef\ufb01cient\nmethod works.", translation="方法有效。")])
    new = parsed([paragraph("p-1", r"An efficient method $x$ works.", source_text="An efficient method works.")])
    result = reflow_paper(old, new)
    assert result["paragraphs"][0]["translation"] == "方法有效。"
    assert result["status"] == "completed"


def test_same_text_on_another_page_cannot_supply_a_translation():
    old = paper([paragraph("old", "Repeated words.", page=1, translation="第一页译文")])
    result = reflow_paper(old, parsed([paragraph("p-1", "Repeated words.", page=2)]))
    assert result["paragraphs"][0]["translation"] is None
    assert result["translation_history"][-1]["paragraphs"][0]["translation"] == "第一页译文"


def test_repeated_short_exact_text_uses_coordinates_not_the_first_translated_occurrence():
    old = paper([paragraph("top", "x", translation="上方变量"), paragraph("bottom", "x", translation="下方变量")])
    new = parsed([paragraph("p-1", "x", bbox=[100, 500, 110, 515])])
    result = reflow_paper(old, new, positions={"top": [100, 100, 110, 115], "bottom": [100, 500, 110, 515]})
    assert result["paragraphs"][0]["translation"] == "下方变量"
    ambiguous = reflow_paper(old, new)
    assert ambiguous["paragraphs"][0]["translation"] is None


def test_only_one_translated_repeated_occurrence_does_not_remove_spatial_ambiguity():
    box = [100, 100, 110, 115]
    old = paper([paragraph("a", "x", bbox=box, translation="旧变量"), paragraph("b", "x", bbox=box)])
    result = reflow_paper(old, parsed([paragraph("p-1", "x", bbox=box)]))
    assert result["paragraphs"][0]["translation"] is None
    assert result["paragraphs"][0]["previous_translation_parts"] == [dict(id="a", text="x", translation="旧变量")]


def test_complete_ordered_fragments_merge_only_with_full_source_coverage():
    old = paper([paragraph("line-1", "A complete", translation="完整的", bbox=[60, 100, 180, 112]),
                 paragraph("line-2", "source paragraph.", translation="原文段落。", bbox=[60, 114, 230, 126])])
    new = parsed([paragraph("p-1", "A complete source paragraph.", bbox=[60, 100, 230, 126])])
    result = reflow_paper(old, new)
    assert result["paragraphs"][0]["translation"] == "完整的\n原文段落。"
    assert "previous_translation_parts" not in result["paragraphs"][0]
    assert result["status"] == "completed"
    assert result["progress"] == dict(total=1, done=1, failed=0)


def test_a_line_end_word_break_can_be_rejoined_without_losing_either_saved_translation():
    old = paper([paragraph("line-1", "A quantiza-", translation="量化", bbox=[60, 100, 180, 112]),
                 paragraph("line-2", "tion method.", translation="方法。", bbox=[60, 114, 230, 126])])
    result = reflow_paper(old, parsed([paragraph("p-1", "A quantization method.", bbox=[60, 100, 230, 126])]))
    assert result["paragraphs"][0]["translation"] == "量化\n方法。"


def test_partial_saved_fragments_stay_explicitly_incomplete():
    old = paper([paragraph("first", "A complete", translation="已有首段译文", bbox=[60, 100, 180, 112]),
                 paragraph("next", "source paragraph.", bbox=[60, 114, 230, 126])])
    result = reflow_paper(old, parsed([paragraph("p-1", "A complete source paragraph.", bbox=[60, 100, 230, 126])]))
    new = result["paragraphs"][0]
    assert new["translation"] is None
    assert new["previous_translation_parts"] == [dict(id="first", text="A complete", translation="已有首段译文")]
    assert result["translated_count"] == 0 and result["status"] == "paused"


def test_contained_fragment_without_coordinates_or_from_other_column_is_not_attached():
    old = paper([paragraph("left", "A saved fragment", translation="旧译文", bbox=[50, 100, 220, 115])])
    for bbox in (None, [320, 100, 540, 130]):
        result = reflow_paper(old, parsed([paragraph("p-1", "A saved fragment followed by new text.", bbox=bbox)]))
        assert result["paragraphs"][0]["translation"] is None
        assert "previous_translation_parts" not in result["paragraphs"][0]
        assert len(result["translation_history"][-1]["paragraphs"]) == 1


def test_single_letter_inside_another_word_does_not_count_as_a_saved_fragment():
    old = paper([paragraph("label", "a", translation="图内标签", bbox=[50, 100, 60, 115])])
    result = reflow_paper(old, parsed([paragraph("p-1", "parameter", bbox=[50, 100, 220, 115])]))
    assert "previous_translation_parts" not in result["paragraphs"][0]


def test_unmatched_translations_and_existing_history_survive_and_equations_are_not_translated():
    old = paper([paragraph("formula", "x = y", translation="旧公式译文"),
                 paragraph("unmatched", "Source no longer present.", translation="不能丢失的译文")],
                translation_history=[dict(parse_version=0, paragraphs=[dict(translation="更早的译文")])])
    equation = paragraph("p-1", "$$x = y$$", source_text="x = y")
    equation.update(kind="equation", math_status="latex")
    result = reflow_paper(old, parsed([equation]))
    assert result["paragraphs"][0]["translation"] is None
    assert result["translation_history"][0] == old["translation_history"][0]
    assert {p["translation"] for p in result["translation_history"][-1]["paragraphs"]} == {"旧公式译文", "不能丢失的译文"}
    assert result["status"] == "completed" and result["progress"]["total"] == 0
