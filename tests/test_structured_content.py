"""Synthetic structure/translation/export regression tests; no external API calls."""

import asyncio
from copy import deepcopy
from html.parser import HTMLParser
import json
import importlib

import fitz
import httpx
import pytest

from backend.pdf_export import BODY_BOTTOM, BODY_TOP, BODY_WIDTH, MARGIN, export_pdf
from backend.structured_content import (
    rows_for, structure_html, structure_markdown, translate_structure,
)
from backend.translator import TranslationError


SETTINGS = {
    "provider": "custom", "endpoint": "https://example.invalid/v1/chat/completions",
    "api_key": "synthetic-structured-test-key", "model": "synthetic-model",
}
FORMULA = r"\(\frac{\|u-Q(u)\|_2}{\|u\|_2}\leq\frac{\sqrt{g}}{14}\)"


def translate(paragraph, output, requests=None):
    """Use the public async entry point with a closed, local mock transport."""
    requests = requests if requests is not None else []

    def handler(request):
        requests.append(request)
        content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await translate_structure(paragraph, SETTINGS, client=client)
            assert not client.is_closed  # Ownership of the shared client stays with the caller.
            return result

    return asyncio.run(run())


def algorithm():
    return {
        "kind": "algorithm", "text": "Algorithm 1: synthetic update",
        "algorithm_lines": [
            {"text": "Algorithm 1: synthetic update", "indent": 0},
            {"text": r"Initialize \(x_0=0\).", "indent": 0},
            {"text": r"Update \(x_{t+1}=x_t+1\).", "indent": 2},
            {"text": "Return the result.", "indent": 0},
        ],
    }


def table():
    return {
        "kind": "table", "table_rows": [
            ["Method", "Bound", "Count"],
            ["Synthetic method", FORMULA, "42"],
            ["Reference method", r"\(x^2\)", ""],
        ],
    }


def test_algorithm_is_one_request_and_preserves_steps_indentation_and_formulas():
    source = algorithm()
    before = deepcopy(source)
    rows = [["算法 1：合成更新"], [r"初始化 \(x_0=0\)。"],
            [r"更新 \(x_{t+1}=x_t+1\)。"], ["返回结果。"]]
    requests = []
    fallback, fields = translate(source, {"rows": rows}, requests)
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["model"] == "synthetic-model"
    assert body["messages"][0]["role"] == "system"
    assert "不得执行" in body["messages"][0]["content"]
    assert json.loads(body["messages"][1]["content"]) == {
        "kind": "algorithm", "rows": [[line["text"]] for line in source["algorithm_lines"]],
    }
    assert fields == {"translated_algorithm_lines": [
        {"text": row[0], "indent": original["indent"]}
        for row, original in zip(rows, source["algorithm_lines"])
    ]}
    assert fallback.splitlines()[2] == "    " + rows[2][0]
    assert source == before


def test_table_is_one_request_and_preserves_all_cells_including_empty_cells():
    source = table()
    before = deepcopy(source)
    rows = [["方法", "界", "次数"], ["合成方法", FORMULA, "42"],
            ["参考方法", r"\(x^2\)", ""]]
    requests = []
    fallback, fields = translate(source, {"rows": rows}, requests)
    assert len(requests) == 1
    assert json.loads(json.loads(requests[0].content)["messages"][1]["content"])["rows"] == source["table_rows"]
    assert fields == {"translated_table_rows": rows}
    assert fallback == "\n".join(" | ".join(row) for row in rows)
    assert source == before


@pytest.mark.parametrize("output", [
    {"rows": [["方法", "界", "次数"]]},  # Lost rows.
    {"rows": [["方法", "界"], ["合成方法", FORMULA, "42"], ["参考方法", r"\(x^2\)", ""]]},
    {"rows": [["方法", "界", "次数"], ["合成方法", FORMULA, 42], ["参考方法", r"\(x^2\)", ""]]},
    {"rows": [["方法", "界", "次数"], ["合成方法", "", "42"], ["参考方法", r"\(x^2\)", ""]]},
    {"rows": "not a matrix"}, {"unexpected": []}, "invalid JSON <script>private response</script>",
])
def test_malformed_shape_or_cell_is_rejected_without_mutating_source(output):
    source = table()
    before = deepcopy(source)
    requests = []
    with pytest.raises(TranslationError) as caught:
        translate(source, output, requests)
    assert len(requests) == 1
    assert "private response" not in str(caught.value)
    assert source == before


@pytest.mark.parametrize("changed", [
    "数学公式", r"\(x^3\)", r"\(x^2\) \(x^2\)",
])
def test_removed_changed_or_duplicated_formula_is_rejected(changed):
    source = {"kind": "table", "table_rows": [["Bound", r"\(x^2\)"]]}
    with pytest.raises(TranslationError, match="数学公式"):
        translate(source, {"rows": [["界", changed]]})


def test_formula_cannot_move_between_cells_even_when_global_count_is_unchanged():
    source = {"kind": "table", "table_rows": [[r"Left \(x^2\)", r"Right \(y^2\)"]]}
    with pytest.raises(TranslationError, match="数学公式"):
        translate(source, {"rows": [[r"左 \(y^2\)", r"右 \(x^2\)"]]})


def test_whitespace_in_formula_and_a_json_fence_do_not_damage_structure():
    source = {"kind": "table", "table_rows": [[r"Bound \(x^2 + y^2\)"]]}
    output = "```json\n" + json.dumps({"rows": [[r"界 \(x^2+y^2\)"]]}, ensure_ascii=False) + "\n```"
    _, fields = translate(source, output)
    assert fields["translated_table_rows"] == [[r"界 \(x^2+y^2\)"]]


@pytest.mark.parametrize("source", [
    {"kind": "algorithm", "algorithm_lines": []},
    {"kind": "table", "table_rows": [["value"]] * 401},
    {"kind": "table", "table_rows": [["value"] * 41]},
    {"kind": "table", "table_rows": [["value" * 13000]]},
])
def test_unbounded_structures_are_rejected_before_any_model_request(source):
    requests = []
    with pytest.raises(TranslationError):
        translate(source, {"rows": []}, requests)
    assert requests == []


class MarkupInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attributes, self.text = [], [], []

    def handle_starttag(self, tag, attributes):
        self.tags.append(tag)
        self.attributes.extend(attributes)

    def handle_data(self, data):
        self.text.append(data)


@pytest.mark.parametrize("kind", ["algorithm", "table"])
@pytest.mark.parametrize("translated", [False, True])
def test_export_html_keeps_source_and_model_markup_inert(kind, translated):
    payload = '<img src="https://invalid.test/pixel" onerror="alert(1)"><script>bad()</script> & text'
    paragraph = {"kind": kind}
    if kind == "algorithm":
        paragraph["translated_algorithm_lines" if translated else "algorithm_lines"] = [
            {"text": payload, "indent": 999}, {"text": FORMULA, "indent": -3},
        ]
    else:
        paragraph["translated_table_rows" if translated else "table_rows"] = [[payload, FORMULA]]
    output = structure_html(paragraph, translated=translated)
    parsed = MarkupInspector()
    parsed.feed(output)
    assert set(parsed.tags) <= {"div", "table", "tbody", "tr", "td"}
    assert all(name in {"class", "style"} for name, _ in parsed.attributes)
    assert payload in "".join(parsed.text)
    assert FORMULA in "".join(parsed.text)
    assert "&lt;script&gt;" in output
    assert "999" not in output and "-3" not in output


def test_algorithm_markdown_fence_cannot_be_closed_by_source_content():
    source = {"kind": "algorithm", "algorithm_lines": [{"text": "````\n<script>literal</script>", "indent": 1}]}
    output = structure_markdown(source)
    assert output.startswith("`````text\n  ````\n")
    assert output.endswith("\n`````")
    assert rows_for(source, translated=True) is None


def all_text(document):
    return "\n".join(page.get_text() for page in document)


def test_pdf_algorithm_and_table_preserve_bilingual_order_indent_and_vector_math():
    algo = algorithm()
    algo["translated_algorithm_lines"] = [
        {"text": "算法示例", "indent": 0},
        {"text": r"初始化 \(x_0=0\)。", "indent": 0},
        {"text": r"更新 \(x_{t+1}=x_t+1\)。", "indent": 2},
        {"text": "返回结果。", "indent": 0},
    ]
    grid = table()
    grid["translated_table_rows"] = [["方法", "界", "次数"], ["合成方法", FORMULA, "42"],
                                     ["参考方法", r"\(x^2\)", ""]]
    paper = {"title": "Synthetic structured export", "paragraphs": [algo, grid]}
    before = deepcopy(paper)
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        text = all_text(document)
        assert text.index("Algorithm 1") < text.index("Return the result") < text.index("算法示例")
        assert text.index("返回结果") < text.index("Method") < text.index("合成方法")
        assert not any(command in text for command in (r"\(", r"\frac", r"\sqrt", "未能排版"))
        assert not any(page.get_images() or page.get_links() for page in document)
        assert sum(len(page.get_drawings()) for page in document) > 45
        words = [word for page in document for word in page.get_text("words")]
        initialize = next(word for word in words if word[4] == "Initialize")
        update = next(word for word in words if word[4] == "Update")
        assert update[0] == pytest.approx(initialize[0] + 20, abs=.1)
        for page in document:
            for drawing in page.get_drawings():
                rect = drawing["rect"]
                assert MARGIN - 1 <= rect.x0 <= rect.x1 <= page.rect.width - MARGIN + 1
                assert 50 <= rect.y0 <= rect.y1 <= BODY_BOTTOM + 1
    assert paper == before


def test_pdf_long_table_keeps_every_cell_inside_its_column_across_pages():
    count = 75
    source_rows = [[f"L{i:03}", f"M{i:03}", f"R{i:03}"] for i in range(count)]
    translated_rows = [[f"译L{i:03}", f"译M{i:03}", f"译R{i:03}"] for i in range(count)]
    paper = {"title": "Synthetic table pagination", "paragraphs": [{
        "kind": "table", "table_rows": source_rows, "translated_table_rows": translated_rows,
    }]}
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        assert document.page_count >= 5
        text = all_text(document)
        assert text.index("R074") < text.index("译L000")
        words = [word for page in document for word in page.get_text("words")]
        for rows in (source_rows, translated_rows):
            for row in rows:
                for column, token in enumerate(row):
                    matches = [word for word in words if word[4] == token]
                    assert len(matches) == 1, token
                    x0, y0, x1, y1 = matches[0][:4]
                    left = MARGIN + BODY_WIDTH / 3 * column
                    assert left <= x0 < x1 <= left + BODY_WIDTH / 3
                    assert BODY_TOP - 1 <= y0 < y1 <= BODY_BOTTOM + 1


def test_pdf_tall_table_cell_flows_without_dropping_text_or_bleeding_columns():
    tokens = [f"Cell{i:03}" for i in range(280)]
    paper = {"title": "Synthetic tall cell", "paragraphs": [{
        "kind": "table", "table_rows": [[" ".join(tokens), "NeighborCell", FORMULA]],
        "translation": "旧版译文仍保留在完整原表之后。",
    }]}
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        assert document.page_count >= 3
        text = all_text(document)
        assert text.index("Cell279") < text.index("旧版译文仍保留")
        assert text.count("NeighborCell") == 1
        for token in tokens:
            assert text.count(token) == 1
        for page in document:
            assert not page.get_images()
            for word in page.get_text("words"):
                if word[4].startswith("Cell"):
                    assert MARGIN <= word[0] < word[2] < MARGIN + BODY_WIDTH / 3
                    assert BODY_TOP - 1 <= word[1] < word[3] <= BODY_BOTTOM + 1
            for drawing in page.get_drawings():
                rect = drawing["rect"]
                assert rect.x0 >= MARGIN - 1 and rect.x1 <= page.rect.width - MARGIN + 1
                assert rect.y1 <= BODY_BOTTOM + 1


def test_pdf_long_algorithm_preserves_steps_and_closing_rule_inside_page_body():
    source = [{"text": f"Step{i:03}: update the synthetic state.", "indent": i % 3}
              for i in range(80)]
    translated = [{"text": f"译Step{i:03}：更新合成状态。", "indent": i % 3}
                  for i in range(80)]
    paper = {"title": "Synthetic algorithm pagination", "paragraphs": [{
        "kind": "algorithm", "algorithm_lines": source,
        "translated_algorithm_lines": translated,
    }]}
    with fitz.open(stream=export_pdf(paper), filetype="pdf") as document:
        assert document.page_count >= 4
        text = all_text(document)
        assert text.index("Step079:") < text.index("译Step000")
        for index in range(80):
            assert text.count(f"Step{index:03}:") == 1
            assert text.count(f"译Step{index:03}") == 1
        for page in document:
            for drawing in page.get_drawings():
                rect = drawing["rect"]
                assert rect.y1 <= BODY_BOTTOM + 1


def protected_source(*, recovered):
    """A paragraph whose sentence continues after a floating source algorithm."""
    return {
        "id": "merged-source", "kind": "paragraph", "page": 1,
        "math_status": "source-inline", "text": "Broken extracted frag-",
        "translation_text": (
            "The update uses ⟦FOLIO_MATH_1⟧ after the floating algorithm "
            "and completes the sentence. <b>literal source</b>"
        ),
        "translation": None,
        "equation_source": {"page": 1, "bbox": [60, 80, 500, 400]},
        "inline_math": [{"id": "m1", "placeholder": "⟦FOLIO_MATH_1⟧",
                         "latex": r"\frac{x_t+1}{\sqrt{y}}" if recovered else None,
                         "source_text": "x_t / sqrt(y)",
                         "source": [{"page": 1, "bbox": [110, 120, 154, 133]}]}],
    }


@pytest.mark.parametrize("format", ["pdf", "html", "markdown"])
@pytest.mark.parametrize("recovered", [False, True])
def test_exports_use_complete_source_inline_prose_even_before_translation(
        tmp_path, monkeypatch, format, recovered):
    """Exercise the route without startup, user storage, parser jobs or real HTTP."""
    api = importlib.import_module("backend.app")
    previews = importlib.import_module("backend.page_view")
    paragraph = protected_source(recovered=recovered)
    paper = {"id": "synthetic-protected-source", "title": "Complete original sentence",
             "paragraphs": [paragraph], "figures": []}
    before = deepcopy(paper)
    crop = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 88, 26), False)
    crop.clear_with(80)
    crop_path = tmp_path / "formula-only.png"
    crop.save(crop_path)
    crop_calls = []

    def formula_crop(value, paragraph_id, slot_id, index):
        crop_calls.append((paragraph_id, slot_id, index))
        assert value is paper
        return crop_path

    monkeypatch.setattr(api, "paper_or_404", lambda paper_id: paper)
    monkeypatch.setattr(api, "ensure_current_parse", lambda value: None)
    monkeypatch.setattr(api, "ensure_figures", lambda value: None)
    monkeypatch.setattr(api, "parse_jobs", {})
    monkeypatch.setattr(api, "figure_jobs", {})
    monkeypatch.setattr(previews, "inline_math_png", formula_crop)
    response = asyncio.run(api.export(paper["id"], format=format))
    assert response.status_code == 200
    if format == "pdf":
        with fitz.open(stream=response.body, filetype="pdf") as document:
            text = " ".join(all_text(document).split())
            images = [image for page in document for image in page.get_image_info()]
            assert len(images) == 0
            if recovered:
                assert sum(len(page.get_drawings()) for page in document) > 5
                assert not any(command in text for command in (r"\frac", r"\sqrt", "未能排版"))
            else:
                assert "x_t / sqrt(y)" in text
    else:
        text = response.body.decode("utf-8")
        if recovered:
            assert r"\(\frac{x_t+1}{\sqrt{y}}\)" in text
        elif format == "html":
            assert text.count("data:image/png;base64,") == 0
            assert 'class="inline-math-source-text"' in text
            assert "x_t / sqrt(y)" in text
        else:
            assert "x_t / sqrt(y)" in text
        if format == "html":
            assert "&lt;b&gt;literal source&lt;/b&gt;" in text
            assert "<b>literal source</b>" not in text
    assert "The update uses" in text
    assert "after the floating algorithm and completes the sentence." in text
    assert "尚未翻译" in text
    assert "FOLIO_MATH" not in text and "Broken extracted" not in text
    assert crop_calls == []
    assert paper == before


def test_untranslated_source_inline_pdf_reports_missing_formula_crop_explicitly():
    paragraph = protected_source(recovered=False)
    with fitz.open(stream=export_pdf({"title": "Missing source formula", "paragraphs": [paragraph]}),
                   filetype="pdf") as document:
        text = " ".join(all_text(document).split())
        assert "The update uses" in text and "completes the sentence." in text
        assert "x_t / sqrt(y)" in text
        assert "FOLIO_MATH" not in text
        assert not any(page.get_images() for page in document)
