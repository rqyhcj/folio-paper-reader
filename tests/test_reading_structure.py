"""Synthetic full sentences, pseudocode and table coordinates, without papers."""
import pymupdf as fitz

from backend.pdf_parser import MAX_PARAGRAPH_CHARS, _chunk_paragraph, extract_pdf


def parse(draw):
    with fitz.open() as document:
        draw(document)
        return extract_pdf(document.tobytes(), "synthetic-structure.pdf")


def test_word_and_sentence_continue_across_pages_without_fabricated_coordinates():
    def draw(document):
        first = document.new_page(width=600, height=800)
        first.insert_text((70, 720), "The measured transformation preserves a consistent repre-", fontsize=11)
        second = document.new_page(width=600, height=800)
        second.insert_text((70, 100), "sentation throughout the evaluation.", fontsize=11)
    result = parse(draw)
    assert len(result["paragraphs"]) == 1
    paragraph = result["paragraphs"][0]
    assert paragraph["text"] == "The measured transformation preserves a consistent representation throughout the evaluation."
    assert paragraph["page"] == 1 and paragraph["bbox"][1] > 700
    assert [region["page"] for region in paragraph["source_regions"]] == [1, 2]
    assert paragraph["source_regions"][1]["bbox"][1] < 110


def test_sentence_continues_across_a_bottom_footnote_and_page_break():
    def draw(document):
        first = document.new_page(width=600, height=800)
        first.insert_text((70, 700), "The fused kernel is designed to cut", fontsize=11)
        first.insert_text((70, 745), "* Supporting implementation note.", fontsize=7)
        second = document.new_page(width=600, height=800)
        second.insert_text((70, 90), "off redundant memory access. The result remains efficient.", fontsize=11)
    result = parse(draw)
    paragraph = next(p for p in result["paragraphs"] if "fused kernel" in p["text"])
    assert "cut off redundant memory access" in paragraph["text"]
    assert any("Supporting implementation note" in p["text"] for p in result["paragraphs"])


def test_word_continues_from_bottom_left_to_top_right_column():
    def draw(document):
        page = document.new_page(width=600, height=800)
        page.insert_text((50, 100), "Left column text provides context.", fontsize=11)
        page.insert_text((50, 115), "Another complete sentence ends here.", fontsize=11)
        page.insert_text((50, 710), "The process follows a trans-", fontsize=11)
        page.insert_text((330, 100), "formation preserving useful data.", fontsize=11)
        page.insert_text((330, 115), "The next sentence explains the result.", fontsize=11)
    result = parse(draw)
    paragraph = next(p for p in result["paragraphs"] if "process follows" in p["text"])
    assert "transformation preserving" in paragraph["text"]
    assert len(paragraph["source_regions"]) == 2
    assert paragraph["source_regions"][0]["bbox"][2] < paragraph["source_regions"][1]["bbox"][0]
    assert paragraph["bbox"][2] < 300


def test_finished_paragraph_and_next_lower_case_quote_stay_distinct():
    def draw(document):
        page = document.new_page(width=600, height=800)
        page.insert_text((70, 100), "The first paragraph is already complete.", fontsize=11)
        page.insert_text((70, 140), "a separately quoted statement belongs here.", fontsize=11)
    result = parse(draw)
    assert len(result["paragraphs"]) == 2


def test_bold_broken_word_is_not_a_section_heading():
    def draw(document):
        page = document.new_page(width=600, height=800)
        page.insert_text((70, 100), "Representation shaping combines a com-", fontname="hebo", fontsize=11)
        page.insert_text((70, 114), "pact transformation with measured reconstruction.", fontsize=11)
    result = parse(draw)
    assert len(result["paragraphs"]) == 1
    assert result["paragraphs"][0]["kind"] == "paragraph"
    assert "compact transformation" in result["paragraphs"][0]["text"]


def test_body_sized_bold_lead_in_with_next_sentence_is_not_a_heading():
    def draw(document):
        page = document.new_page(width=600, height=800)
        page.insert_text((70, 100), "Measured execution defines the reference. For each sampled", fontname="hebo", fontsize=11)
        page.insert_text((70, 114), "input, we evaluate the unchanged operator.", fontsize=11)
    result = parse(draw)
    assert len(result["paragraphs"]) == 1
    assert result["paragraphs"][0]["kind"] == "paragraph"
    assert "each sampled input," in result["paragraphs"][0]["text"]


def _paragraph(text, bbox, *, page=1, flow="left", **extra):
    return {"kind": "paragraph", "text": text, "page": page, "bbox": bbox,
            "page_width": 600, "page_height": 800, "_first_flow": flow, "_last_flow": flow, **extra}


def _float(kind, bbox, *, page=1):
    block = _paragraph("Algorithm 1 Steps" if kind == "algorithm" else "Measured table", bbox, page=page)
    block["kind"] = kind
    block.pop("_first_flow")
    block.pop("_last_flow")
    return block


def test_sentence_continues_below_a_floating_algorithm_in_the_next_column():
    from backend.reading_structure import stitch_sentences
    source = [_paragraph(r"We normalize \(X\) and solve", [50, 690, 285, 720]),
              _float("algorithm", [325, 70, 550, 330]),
              _paragraph("Eq. (3) using the measured observations.", [325, 350, 550, 380], flow="right")]
    result = stitch_sentences(source, 10)
    assert [p["kind"] for p in result] == ["paragraph", "algorithm"]
    assert result[0]["text"] == r"We normalize \(X\) and solve Eq. (3) using the measured observations."
    assert result[0]["bbox"] == source[0]["bbox"]
    assert [r["bbox"] for r in result[0]["source_regions"]] == [source[0]["bbox"], source[2]["bbox"]]
    assert result[1]["bbox"] == source[1]["bbox"]
    assert source[0]["text"].endswith("solve")  # no mutation of input evidence


def test_sentence_continues_below_a_tall_floating_algorithm_on_the_next_page():
    from backend.reading_structure import stitch_sentences
    source = [_paragraph("The original operations remain separate; replacing", [325, 650, 550, 720], flow="right"),
              _float("algorithm", [50, 70, 285, 370], page=2),
              _paragraph("them changes the measured behavior.", [50, 395, 285, 420], page=2)]
    result = stitch_sentences(source, 10)
    assert len(result) == 2 and result[1]["kind"] == "algorithm"
    assert "replacing them changes" in result[0]["text"]
    assert [r["page"] for r in result[0]["source_regions"]] == [1, 2]
    assert result[0]["bbox"] == [325, 650, 550, 720]


def test_same_sentence_can_continue_in_a_third_fragment_after_the_float():
    from backend.reading_structure import stitch_sentences
    source = [_paragraph("The procedure preserves a consistent", [50, 690, 285, 720]),
              _float("algorithm", [325, 70, 550, 330]),
              _paragraph("representation across the", [325, 350, 550, 365], flow="right"),
              _paragraph("entire computation.", [325, 380, 550, 395], flow="right")]
    result = stitch_sentences(source, 10)
    assert len(result) == 2 and result[1]["kind"] == "algorithm"
    assert result[0]["text"] == "The procedure preserves a consistent representation across the entire computation."
    assert len(result[0]["source_regions"]) == 3
    assert result[0]["bbox"] == source[0]["bbox"]
    assert not any(key.startswith("_stitch") for key in result[0])


def test_captioned_table_can_float_between_two_parts_of_one_sentence():
    from backend.reading_structure import stitch_sentences
    source = [_paragraph("We evaluate the complete trans-", [50, 690, 285, 720]),
              _paragraph("Table 2. Measured values", [325, 70, 550, 90], flow="right"),
              _float("table", [325, 100, 550, 260]),
              _paragraph("formation using the same observations.", [325, 280, 550, 310], flow="right")]
    result = stitch_sentences(source, 10)
    assert [p["kind"] for p in result] == ["paragraph", "paragraph", "table"]
    assert result[0]["text"] == "We evaluate the complete transformation using the same observations."
    assert result[1]["text"] == source[1]["text"]
    assert len(result[0]["source_regions"]) == 2


def test_floats_do_not_join_complete_sentences_or_unrelated_flow_or_distant_pages():
    from backend.reading_structure import stitch_sentences
    valid = [_paragraph("We preserve the current representation", [50, 690, 285, 720]),
             _float("algorithm", [325, 70, 550, 330]),
             _paragraph("throughout the measured computation.", [325, 350, 550, 380], flow="right")]
    for replacement in (
        (0, _paragraph("The current statement is already complete.", [50, 690, 285, 720])),
        (1, _float("algorithm", [50, 70, 285, 330])),
        (2, _paragraph("throughout a distant page.", [325, 350, 550, 380], page=3, flow="right")),
        (2, _paragraph("throughout a distant paragraph.", [325, 460, 550, 490], flow="right")),
        (1, {**valid[1], "kind": "heading", "text": "A new section"}),
    ):
        source = list(valid)
        source[replacement[0]] = replacement[1]
        assert len(stitch_sentences(source, 10)) == 3


def test_float_sentence_merge_preserves_and_renumbers_protected_math_slots():
    from backend.reading_structure import stitch_sentences
    first_box, last_box = [50, 690, 285, 720], [325, 350, 550, 380]
    first_formula = {"id": "m-1", "placeholder": "⟦FOLIO_MATH_1⟧", "latex": "x^2",
                     "source": [{"page": 1, "bbox": [100, 690, 120, 705]}]}
    last_formula = {"id": "m-1", "placeholder": "⟦FOLIO_MATH_1⟧",
                    "source": [{"page": 1, "bbox": [410, 350, 430, 365]}]}
    source = [_paragraph("We normalize x2 and solve", first_box, math_status="source-image",
                         inline_math=[first_formula], translation_text="We normalize ⟦FOLIO_MATH_1⟧ and solve",
                         equation_image={"width": 235}, equation_source={"page": 1, "bbox": first_box}),
              _float("algorithm", [325, 70, 550, 330]),
              _paragraph("Eq. (3) using the q expression.", last_box, flow="right", math_status="source-image",
                         inline_math=[last_formula], translation_text="Eq. (3) using the ⟦FOLIO_MATH_1⟧ expression.",
                         equation_image={"width": 225}, equation_source={"page": 1, "bbox": last_box})]
    result = stitch_sentences(source, 10)
    merged = result[0]
    assert len(result) == 2 and merged["math_status"] == "source-inline"
    assert merged["text"] == "We normalize x2 and solve Eq. (3) using the q expression."
    assert merged["translation_text"] == "We normalize ⟦FOLIO_MATH_1⟧ and solve Eq. (3) using the ⟦FOLIO_MATH_2⟧ expression."
    assert [slot["id"] for slot in merged["inline_math"]] == ["m-1", "m-2"]
    assert [slot["source"] for slot in merged["inline_math"]] == [first_formula["source"], last_formula["source"]]
    assert merged["inline_math"][0]["latex"] == "x^2"
    assert "equation_image" not in merged and "equation_source" not in merged
    assert merged["needs_visual_review"] and len(merged["source_regions"]) == 2


def test_chunking_keeps_a_long_single_sentence_whole():
    text = "A single sentence " + "contains descriptive words " * 190 + "until it finally ends."
    assert len(text) > MAX_PARAGRAPH_CHARS
    result = _chunk_paragraph({"kind": "paragraph", "text": text})
    assert len(result) == 1 and result[0]["text"] == text


def test_chunking_uses_complete_sentence_boundaries():
    sentence = "A complete experimental sentence provides enough information to be understood."
    text = " ".join([sentence] * 150)
    chunks = _chunk_paragraph({"kind": "paragraph", "text": text})
    assert len(chunks) > 1
    assert all(p["text"].endswith(".") for p in chunks)
    assert " ".join(p["text"] for p in chunks) == text


def test_chunking_prefers_two_to_four_complete_sentences_for_bilingual_reading():
    sentences = [f"Sentence {index} explains one complete idea." for index in range(1, 10)]
    chunks = _chunk_paragraph({"kind": "paragraph", "text": " ".join(sentences)})
    assert [chunk["text"].count(".") for chunk in chunks] == [4, 4, 1]
    assert " ".join(chunk["text"] for chunk in chunks) == " ".join(sentences)


def test_chunking_keeps_protected_inline_formula_slots_with_their_complete_sentences():
    sentences = [f"Sentence {index} explains one complete idea." for index in range(1, 7)]
    protected = list(sentences)
    protected[1] = "Sentence 2 uses ⟦FOLIO_MATH_1⟧ in one complete idea."
    protected[4] = "Sentence 5 uses ⟦FOLIO_MATH_2⟧ in one complete idea."
    paragraph = {"kind": "paragraph", "text": " ".join(sentences),
                 "source_text": " ".join(sentences), "translation_text": " ".join(protected),
                 "inline_math": [{"placeholder": "⟦FOLIO_MATH_1⟧"}, {"placeholder": "⟦FOLIO_MATH_2⟧"}]}
    chunks = _chunk_paragraph(paragraph)
    assert [chunk["text"].count(".") for chunk in chunks] == [4, 2]
    assert [len(chunk["inline_math"]) for chunk in chunks] == [1, 1]
    assert chunks[0]["inline_math"][0]["placeholder"] == "⟦FOLIO_MATH_1⟧"
    assert chunks[1]["inline_math"][0]["placeholder"] == "⟦FOLIO_MATH_2⟧"


def test_algorithm_keeps_statements_indentation_and_wrapped_words_as_one_block():
    def draw(document):
        page = document.new_page(width=600, height=800)
        for y in (65, 90, 205):
            page.draw_line((70, y), (520, y), width=.5)
        page.insert_text((70, 80), "Algorithm 1 Iterative observation", fontsize=11, fontname="hebo")
        page.insert_text((70, 106), "Require: initial measurements", fontsize=10)
        page.insert_text((75, 126), "1: for each observation do", fontsize=10)
        page.insert_text((75, 146), "2:", fontsize=10)
        page.insert_text((108, 146), "Update the measured repre-", fontsize=10)
        page.insert_text((108, 159), "sentation with its observation.", fontsize=10)
        page.insert_text((75, 181), "3: end for", fontsize=10)
        page.insert_text((70, 245), "The following paragraph explains the ordinary experiment.", fontsize=11)
    result = parse(draw)
    algorithm = next(p for p in result["paragraphs"] if p["kind"] == "algorithm")
    assert len(algorithm["algorithm_lines"]) == 5
    assert "representation with its observation." in algorithm["text"]
    assert "following paragraph" not in algorithm["text"]
    update = next(row for row in algorithm["algorithm_lines"] if row["text"].startswith("2:"))
    loop = next(row for row in algorithm["algorithm_lines"] if row["text"].startswith("1:"))
    assert update["indent"] > loop["indent"]
    assert algorithm["source_regions"] == [{"page": 1, "bbox": algorithm["bbox"]}]
    assert sum("Update the measured" in p["text"] for p in result["paragraphs"]) == 1


def draw_table(page, top, name="Table 1. Measured values"):
    page.insert_text((70, top - 14), name, fontsize=11)
    for y in (top, top + 20, top + 75):
        page.draw_line((80, y), (360, y), width=.5)
    for index, (label, value) in enumerate((("Measurement", "Value"), ("Sample count", "2048"), ("Year", "2026"), ("Fraction", "0.125"))):
        y = top + 14 if index == 0 else top + 14 + index * 17
        page.insert_text((90, y), label, fontsize=10)
        page.insert_text((290, y), value, fontsize=10)


def test_captioned_ruled_table_preserves_rows_columns_and_numbers():
    def draw(document):
        draw_table(document.new_page(width=600, height=800), 110)
    result = parse(draw)
    table = next(p for p in result["paragraphs"] if p["kind"] == "table")
    assert table["table_rows"] == [["Measurement", "Value"], ["Sample count", "2048"], ["Year", "2026"], ["Fraction", "0.125"]]
    assert table["bbox"] == [80.0, 110.0, 360.0, 185.0]
    assert any(p["text"].startswith("Table 1.") for p in result["paragraphs"] if p["kind"] != "table")


def test_two_tables_with_identical_width_do_not_merge_across_the_second_caption():
    def draw(document):
        page = document.new_page(width=600, height=800)
        draw_table(page, 110)
        draw_table(page, 260, "Table 2. Other measured values")
    result = parse(draw)
    tables = [p for p in result["paragraphs"] if p["kind"] == "table"]
    assert len(tables) == 2
    assert all(len(p["table_rows"]) == 4 for p in tables)


def test_uncaptioned_ruled_diagram_is_not_guessed_to_be_a_table():
    def draw(document):
        page = document.new_page(width=600, height=800)
        draw_table(page, 110, "An ordinary diagram explains several measurements.")
    result = parse(draw)
    assert not any(p["kind"] == "table" for p in result["paragraphs"])


def _span(text, x0, x1, baseline, font="Helvetica"):
    value = {"text": text, "bbox": [x0, baseline - 10, x1, baseline + 2],
             "size": 10, "font": font, "origin": [x0, baseline]}
    if font.startswith("CM"):
        value["chars"] = [{"c": text, "bbox": value["bbox"], "origin": [x0, baseline]}]
    return value


def _line(spans, baseline):
    from backend.pdf_parser import _Line
    return _Line(" ".join(s["text"] for s in spans), min(s["bbox"][0] for s in spans), baseline - 10,
                 max(s["bbox"][2] for s in spans), baseline + 2, 10, False, 0, 1, 600, 800,
                 baseline=baseline, spans=spans)


def test_partial_math_conversion_marks_the_algorithm_step_for_visual_review():
    from backend.reading_structure import _algorithm
    caption = _line([_span("Algorithm 1 Example", 70, 230, 80)], 80)
    mixed = _line([_span("2: Set ", 90, 120, 140), _span("x", 122, 127, 140, "CMMI10"),
                   _span(" and ", 128, 150, 140), _span("q", 153, 159, 140, "CMEX10")], 140)
    lines = [caption, _line([_span("Require: measurements", 70, 230, 105)], 105),
             _line([_span("1: for each item do", 75, 230, 120)], 120), mixed,
             _line([_span("3: end for", 75, 150, 165)], 165)]
    algorithm, _ = _algorithm(lines, caption, [70, 68, 500, 180], 10)
    step = next(row for row in algorithm["algorithm_lines"] if row["text"].startswith("2:"))
    assert r"\(x\)" in step["text"]  # partial success must not hide the failed glyph
    assert step["needs_visual_review"] is True
    assert algorithm["needs_visual_review"] is True and algorithm["structure_warnings"]
    assert "equation_image" not in algorithm


def test_metric_direction_marker_stays_with_its_long_label_across_header_midline():
    from backend.reading_structure import _table
    caption = _line([_span("Table 1. Measurements", 70, 240, 80)], 80)
    lines = [
        _line([_span("Metric", 90, 120, 110), _span("Value", 240, 260, 110)], 110),
        _line([_span("Long metric label", 90, 185, 130), _span("↓", 187, 193, 130),
               _span("0.5", 238, 260, 130)], 130),
        _line([_span("Other metric", 90, 160, 150), _span("0.7", 238, 260, 150)], 150),
    ]
    table, _ = _table(lines, caption, [80, 95, 270, 165], 10)
    assert table["table_rows"][1] == ["Long metric label ↓", "0.5"]
    assert not table.get("needs_visual_review")


def test_unresolved_table_math_reports_its_cell_without_forcing_a_screenshot():
    from backend.reading_structure import _table
    caption = _line([_span("Table 1. Measurements", 70, 240, 80)], 80)
    lines = [
        _line([_span("Metric", 90, 120, 110), _span("Value", 240, 260, 110)], 110),
        _line([_span("Input", 90, 150, 130), _span("q", 240, 246, 130, "CMEX10")], 130),
        _line([_span("Output", 90, 150, 150), _span("0.7", 238, 260, 150)], 150),
    ]
    table, _ = _table(lines, caption, [80, 95, 270, 165], 10)
    assert table["needs_visual_review"] is True
    assert table["table_review_cells"] == [{"row": 1, "column": 1}]
    assert table["structure_warnings"]
    assert "equation_image" not in table
