"""Review numbering is layout metadata, not a reason to remove arbitrary digits."""
import re

import fitz

from backend.pdf_parser import _Line, _remove_review_line_numbers, extract_pdf


def _line(text, x0, baseline, *, size=11, spans=None):
    spans = spans or [{"text": text, "bbox": [x0, baseline - size, x0 + len(text) * size * .5, baseline],
                       "origin": [x0, baseline], "size": size, "font": "Body", "color": 0}]
    return _Line(text, min(s["bbox"][0] for s in spans), baseline - size,
                 max(s["bbox"][2] for s in spans), baseline, size, False, int(baseline), 1, 600, 800,
                 baseline=baseline, spans=spans)


def _marker(value, x0, baseline):
    return _line(str(value).zfill(3), x0, baseline, size=8)


def test_review_rails_removed_before_paragraph_joining_and_cropping():
    document = fitz.open()
    page = document.new_page(width=600, height=800)
    left = ["An example method has difficult-", "ies that are easier to explain.",
            "Its accuracy improves in 2024.", "There are 16 independent samples.",
            "The scale is 142 in this example.", "A final sentence ends this paragraph."]
    for i, text in enumerate(left):
        page.insert_text((20, 120 + i * 15), f"{i + 1:03}", fontsize=8, fontname="hebo", color=(.4,.4,1))
        page.insert_text((60, 120 + i * 15), text, fontsize=11)
        page.insert_text((570, 120 + i * 15), f"{i + 7:03}", fontsize=8, fontname="hebo", color=(.4,.4,1))
        page.insert_text((325, 120 + i * 15), "The other column has ordinary prose.", fontsize=11)
    raw = document.tobytes()
    document.close()
    parsed = extract_pdf(raw, "example.pdf")
    text = " ".join(p["text"] for p in parsed["paragraphs"])
    assert "difficulties" in text
    assert "2024" in text and "16" in text and "142" in text
    assert not re.search(r"(?<!\d)00[1-9](?!\d)", text)
    assert len(parsed["removed_line_numbers"]) == 12
    assert {item["text"] for item in parsed["removed_line_numbers"]} == {f"{i:03}" for i in range(1,13)}
    assert all(p["bbox"][0] >= 60 for p in parsed["paragraphs"])
    assert all(p["bbox"][2] < 570 for p in parsed["paragraphs"])


def test_same_pdf_line_loses_only_confirmed_margin_span():
    lines = []
    for i in range(6):
        baseline = 100 + i * 15
        number = _marker(i + 1, 20, baseline).spans[0]
        body = _line("Ordinary prose stays selectable.", 60, baseline).spans[0]
        lines.append(_line(f"{i + 1:03} Ordinary prose stays selectable.", 20, baseline, spans=[number, body]))
    removed = []
    cleaned = _remove_review_line_numbers(lines, removed)
    assert len(cleaned) == 6 and len(removed) == 6
    assert all(line.x0 == 60 and line.text == "Ordinary prose stays selectable." for line in cleaned)
    assert all(len(line.spans) == 1 and "_review_signature" not in line.spans[0] for line in cleaned)


def test_short_opposite_rail_requires_confirmed_sequence_and_matching_typography():
    lines = []
    for i in range(2):
        baseline = 100 + i * 15
        lines += [_marker(98 + i, 20, baseline), _line("Ordinary text in a short left column.", 60, baseline)]
    for i in range(6):
        baseline = 100 + i * 15
        lines += [_marker(100 + i, 570, baseline), _line("Ordinary text in the longer column.", 325, baseline)]
    # An unrelated integer on the same rail must survive the valid sequence.
    lines += [_marker(314, 20, 300), _line("A separate measured value is discussed.", 60, 300)]
    removed = []
    cleaned = _remove_review_line_numbers(lines, removed)
    assert len(removed) == 8
    assert any(line.text == "314" for line in cleaned)


def test_years_equation_labels_body_tables_and_numbered_lists_survive():
    lines = []
    for i in range(6):
        baseline = 100 + i * 15
        lines += [_marker(2020 + i, 20, baseline), _line("A chronological event remains content.", 60, baseline)]
        # Body/table numbers do not occupy an outer page-margin rail.
        lines += [_marker(i + 1, 150, baseline + 150)]
        # Ordinary list numbers have the same size as their accompanying prose.
        lines += [_line(str(i + 1), 20, baseline + 300), _line("An ordinary numbered list item.", 60, baseline + 300)]
    lines += [_line("(2)", 570, 500, size=8), _marker(999, 20, 530)]
    removed = []
    cleaned = _remove_review_line_numbers(lines, removed)
    assert cleaned == lines and not removed


def test_small_table_values_on_the_body_boundary_are_not_margin_markers():
    lines = []
    for i in range(8):
        lines.append(_line("This establishes the ordinary left text boundary.", 60, 80 + i * 15))
    for i in range(6):
        baseline = 300 + i * 15
        lines += [_marker(i + 1, 60, baseline), _line("A table row has its own long description.", 100, baseline)]
    removed = []
    cleaned = _remove_review_line_numbers(lines, removed)
    assert cleaned == lines and not removed
