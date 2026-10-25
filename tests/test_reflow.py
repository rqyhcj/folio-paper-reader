"""Saved translations survive a safer reparse without guessed paragraph matches."""
from copy import deepcopy

from backend.reflow import reflow_paper, review_saved_translation


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


def test_complete_source_coverage_does_not_make_separately_translated_clauses_a_complete_translation():
    old = paper([paragraph("line-1", "A complete", translation="完整的", bbox=[60, 100, 180, 112]),
                 paragraph("line-2", "source paragraph.", translation="原文段落。", bbox=[60, 114, 230, 126])])
    new = parsed([paragraph("p-1", "A complete source paragraph.", bbox=[60, 100, 230, 126])])
    result = reflow_paper(old, new)
    target = result["paragraphs"][0]
    assert target["translation"] is None
    assert target["previous_translation_parts"] == [dict(id=p["id"], text=p["text"], translation=p["translation"])
                                                     for p in old["paragraphs"]]
    assert target["translation_review_reason"] == "paragraph-reassembled"
    assert result["status"] == "paused"
    assert result["progress"] == dict(total=1, done=0, failed=0)


def test_a_line_end_word_break_can_be_rejoined_without_losing_either_saved_translation():
    old = paper([paragraph("line-1", "A quantiza-", translation="量化", bbox=[60, 100, 180, 112]),
                 paragraph("line-2", "tion method.", translation="方法。", bbox=[60, 114, 230, 126])])
    result = reflow_paper(old, parsed([paragraph("p-1", "A quantization method.", bbox=[60, 100, 230, 126])]))
    assert result["paragraphs"][0]["translation"] is None
    assert [p["translation"] for p in result["paragraphs"][0]["previous_translation_parts"]] == ["量化", "方法。"]


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


def test_previously_composed_translation_is_not_reused_by_an_exact_source_match():
    fragments = [paragraph("old-1", "Sampling starts from noise", bbox=[50, 100, 260, 112],
                           translation="采样从噪声开始，求解如下方程。"),
                 paragraph("old-2", "N(0,I), which matches the ground-", bbox=[50, 114, 260, 126],
                           translation="N(0,I)，这与 ground- 相匹配"),
                 paragraph("old-3", "truth dynamics.", bbox=[50, 128, 160, 140], translation="真实动力学。")]
    joined = paragraph("current", "Sampling starts from noise N(0,I), which matches the groundtruth dynamics.",
                       bbox=[50, 100, 260, 140], translation="\n".join(p["translation"] for p in fragments))
    old = paper([joined], translation_history=[{"parse_version": 1, "paragraphs": fragments}])
    result = reflow_paper(old, parsed([paragraph("new", joined["text"], bbox=joined["bbox"])]))
    target = result["paragraphs"][0]
    assert target["translation"] is None
    assert target["translation_review_reason"] == "paragraph-reassembled"
    assert [p["id"] for p in target["previous_translation_parts"]] == ["old-1", "old-2", "old-3"]
    assert result["translation_history"][0]["paragraphs"] == fragments
    assert result["translation_history"][-1]["paragraphs"] == [joined]


def test_complete_translation_of_reassembled_source_is_preserved_when_not_an_old_concatenation():
    fragments = [paragraph("a", "A complete", bbox=[50, 100, 200, 112], translation="完整的"),
                 paragraph("b", "source paragraph.", bbox=[50, 114, 240, 126], translation="原文段落。")]
    joined = paragraph("current", "A complete source paragraph.", bbox=[50, 100, 240, 126],
                       translation="这是完整原文段落的通顺译文。\n其中包括整体句意。")
    old = paper([joined], translation_history=[{"paragraphs": fragments}])
    result = reflow_paper(old, parsed([paragraph("new", joined["text"], bbox=joined["bbox"])]))
    assert result["paragraphs"][0]["translation"] == joined["translation"]
    assert "translation_review_reason" not in result["paragraphs"][0]


def test_request_for_source_reply_is_archived_without_becoming_visible_translation_or_prior_part():
    reply = "您提供的消息中只有字符“p”，没有可供翻译的论文片段。请粘贴需要翻译的英文原文。"
    bad = paragraph("fragment", "p", bbox=[80, 100, 88, 112], translation=reply)
    old = paper([bad])
    exact = reflow_paper(old, parsed([paragraph("p-1", "p", bbox=bad["bbox"])]))
    assert exact["paragraphs"][0]["translation"] is None
    assert exact["paragraphs"][0]["translation_review_reason"] == "model-requested-source"
    assert "previous_translation_parts" not in exact["paragraphs"][0]
    expanded = reflow_paper(old, parsed([paragraph("p-1", "A value p describes the distribution.", bbox=[60, 100, 260, 112])]))
    assert expanded["paragraphs"][0]["translation"] is None
    assert "previous_translation_parts" not in expanded["paragraphs"][0]
    assert expanded["translation_history"][-1]["paragraphs"][0]["translation"] == reply


def test_protected_math_input_does_not_inherit_an_unguarded_formula_guess():
    source = "where sigma controls stochasticity."
    old = paper([paragraph("old", source, translation=r"其中 $\sigma=0$ 控制随机性。")])
    new = paragraph("new", source, translation_text="where ⟦FOLIO_MATH_1⟧ controls stochasticity.",
                    inline_math=[{"id": "FOLIO_MATH_1"}])
    result = reflow_paper(old, parsed([new]))
    target = result["paragraphs"][0]
    assert target["translation"] is None
    assert target["translation_review_reason"] == "protected-math-source"
    assert "previous_translation_parts" not in target
    assert result["translation_history"][-1]["paragraphs"] == old["paragraphs"]


def test_protected_math_translation_with_exact_matching_tokens_can_be_reused():
    text = "where ⟦FOLIO_MATH_1⟧ controls stochasticity."
    translated = "其中 ⟦FOLIO_MATH_1⟧ 控制随机性。"
    old = paper([paragraph("old", text, translation=translated, translation_text=text)])
    result = reflow_paper(old, parsed([paragraph("new", text, translation_text=text)]))
    assert result["paragraphs"][0]["translation"] == translated


def test_newly_recovered_latex_cannot_inherit_old_unresolved_image_tokens():
    source = "The inner product is a transposed matrix product."
    text = "内积为 ⟦FOLIO_MATH_1⟧。"
    old = paper([paragraph("old", source, translation=text,
                           translation_text="The inner product is ⟦FOLIO_MATH_1⟧.",
                           inline_math=[dict(id="FOLIO_MATH_1")])])
    new = paragraph("new", r"The inner product is \(A^T B\).", source_text=source)
    result = reflow_paper(old, parsed([new]))
    target = result["paragraphs"][0]
    assert target["translation"] is None
    assert target["translation_review_reason"] == "protected-math-source"
    assert "previous_translation_parts" not in target
    assert result["translation_history"][-1]["paragraphs"][0]["translation"] == text


def test_review_saved_translation_does_not_modify_inputs():
    saved = paragraph("old", "p", translation="请提供需要翻译的英文论文片段。")
    before = deepcopy(saved)
    assert review_saved_translation(saved) == {"reason": "model-requested-source", "parts": []}
    assert saved == before


def test_confirmed_margin_numbers_and_word_breaks_keep_a_whole_saved_translation():
    old = paper([paragraph("old", "011 A quantiza- 012 tion method preserves 128 values.",
                           bbox=[24, 100, 280, 130], translation="011 这种量化方法保留 128 个值。")])
    new = parsed([paragraph("new", "A quantization method preserves 128 values.", bbox=[50, 100, 280, 130])],
                 removed_line_numbers=[dict(page=1, text="011", bbox=[24, 101, 37, 109]),
                                       dict(page=1, text="012", bbox=[24, 117, 37, 125])])
    result = reflow_paper(old, new)
    assert result["paragraphs"][0]["translation"] == "这种量化方法保留 128 个值。"
    assert result["translation_history"][-1]["paragraphs"] == old["paragraphs"]
    assert old["paragraphs"][0]["translation"].startswith("011 ")


def test_only_matching_page_and_recorded_gutter_geometry_can_remove_a_number():
    old = paper([paragraph("old", "143 is a real sample count.", bbox=[50, 100, 280, 120],
                           translation="143 是真实样本数量。")])
    for marker in (dict(page=2, text="143", bbox=[50, 102, 65, 110]),
                   dict(page=1, text="143", bbox=[24, 102, 37, 110])):
        new = parsed([paragraph("new", "143 is a real sample count.", bbox=[50, 100, 280, 120])],
                     removed_line_numbers=[marker])
        result = reflow_paper(old, new)
        assert result["paragraphs"][0]["translation"] == "143 是真实样本数量。"


def test_ambiguous_repeated_numbers_cannot_be_removed_from_saved_prose():
    old = paper([paragraph("old", "011 We used 011 samples.", bbox=[24, 100, 280, 120],
                           translation="011 我们使用了 011 个样本。")])
    new = parsed([paragraph("new", "We used 011 samples.", bbox=[50, 100, 280, 120])],
                 removed_line_numbers=[dict(page=1, text="011", bbox=[24, 102, 37, 110])])
    result = reflow_paper(old, new)
    assert result["paragraphs"][0]["translation"] is None
    assert result["translation_history"][-1]["paragraphs"][0]["translation"] == old["paragraphs"][0]["translation"]


def test_interior_review_markers_in_old_translation_remain_a_labelled_prior_result():
    old = paper([paragraph("old", "011 A quantiza- 012 tion method works.",
                           bbox=[24, 100, 280, 130], translation="011 量化 012 方法有效。")])
    new = parsed([paragraph("new", "A quantization method works.", bbox=[50, 100, 280, 130])],
                 removed_line_numbers=[dict(page=1, text="011", bbox=[24, 101, 37, 109]),
                                       dict(page=1, text="012", bbox=[24, 117, 37, 125])])
    result = reflow_paper(old, new)
    target = result["paragraphs"][0]
    assert target["translation"] is None
    assert target["translation_review_reason"] == "review-line-number-residue"
    assert target["previous_translation_parts"][0]["translation"] == "量化 012 方法有效。"
    assert result["translation_history"][-1]["paragraphs"] == old["paragraphs"]
