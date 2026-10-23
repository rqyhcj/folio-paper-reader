"""Column geometry must follow the text gutter rather than the page center."""

from backend.pdf_parser import _Line, _column_split, _reading_order


def _line(text, x0, x1, y, block=0, size=10):
    return _Line(text, x0, y, x1, y + 10, size, False, block, 1, 612, 792)


def test_asymmetric_margins_choose_the_actual_narrow_gutter():
    lines = []
    for index in range(8):
        y = 100 + 12 * index
        # The page center is 306, while the actual gutter is 291.2..307.
        # Some shorter endings made the old fixed candidate 290.7 look viable.
        left_end = 270 if index < 2 else 289.4 if index % 2 else 291.2
        lines.append(_line(f"LEFT {index} wrapped prose continues over a full column.",
                           55.4, left_end, y, 0))
        lines.append(_line(f"RIGHT {index} prose continues in the second column.",
                           307, 541.4, y, 1))
    split = _column_split(lines, 10)
    assert 291.2 < split < 307
    ordered = _reading_order(lines, 10)
    assert [line.text.split()[0] for line in ordered] == ["LEFT"] * 8 + ["RIGHT"] * 8
    assert len({line.flow for line in ordered}) == 2


def test_full_width_title_divides_bands_without_breaking_column_order():
    title = _line("A full width heading across the two columns", 55, 542, 80, 2, 18)
    middle = _line("A new full width section heading", 55, 542, 220, 3, 14)
    lines = [middle, title]
    for start, prefix in ((110, "TOP"), (250, "BOTTOM")):
        for index in range(4):
            y = start + 12 * index
            lines.extend([
                _line(f"{prefix} LEFT {index} is a full column of scientific prose.", 55.4, 291.2, y, 4),
                _line(f"{prefix} RIGHT {index} is a full column of scientific prose.", 307, 541.4, y, 5),
            ])
    ordered = _reading_order(lines, 10)
    assert ordered[0] is title
    assert ordered[9] is middle
    assert [line.text.split()[:2] for line in ordered[1:9]] == [["TOP", "LEFT"]] * 4 + [["TOP", "RIGHT"]] * 4
    assert [line.text.split()[:2] for line in ordered[10:]] == [["BOTTOM", "LEFT"]] * 4 + [["BOTTOM", "RIGHT"]] * 4


def test_single_column_prose_with_short_endings_is_not_split():
    lines = []
    for index in range(8):
        y = 100 + 24 * index
        lines.extend([
            _line("This is a continuous full width paragraph with ordinary scientific prose.", 55, 541, y, index),
            _line("The same paragraph has a short ending.", 55, 291, y + 12, index),
            _line("A figure label exists on the right side.", 307, 541, y + 12, 50 + index),
        ])
    assert _column_split(lines, 10) is None


def test_unrelated_text_boxes_at_different_heights_do_not_create_columns():
    lines = [
        _line(f"LEFT {index} scientific prose in a top text box.", 55, 291, 100 + index * 12)
        for index in range(4)
    ] + [
        _line(f"RIGHT {index} scientific prose in a lower text box.", 307, 541, 500 + index * 12)
        for index in range(4)
    ]
    assert _column_split(lines, 10) is None


def test_short_justified_reference_fragments_do_not_create_a_gutter():
    # Reference entries may have wide spaces around separately emitted links.
    # Their narrow fragment band is still part of surrounding full-width prose.
    lines = [
        _line("A complete reference with authors and a sufficiently long article title.", 108, 504, 635),
        _line("Another full width reference line introduces a resource and its data.", 108, 504, 647),
        _line("Left reference authors and year.", 118, 291, 659),
        _line("A right hand linked resource title.", 332, 504, 659),
        _line("The beginning of a second line.", 118, 280, 671),
        _line("Another separate URL text fragment.", 325, 504, 671),
        _line("The long continuation of the resource description crosses the center.", 118, 537, 683),
        _line("The concluding full width URL and publication year remain in the entry.", 118, 504, 695),
    ]
    assert _column_split(lines, 10) is None


def test_right_aligned_pseudocode_comments_are_not_a_second_prose_column():
    lines = []
    for index in range(4):
        y = 200 + index * 20
        lines.extend([
            _line(f"Algorithm statement {index} with a mathematical operation.", 108, 291, y),
            _line(f"▷ Comment {index} explains this operation.", 358, 504, y),
        ])
    assert _column_split(lines, 10) is None
