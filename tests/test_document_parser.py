"""Local document import keeps text/math/image order and never resolves URLs."""
import base64
import io
import struct
import zipfile

import fitz
import pytest

from backend import document_parser as parser


@pytest.fixture
def png():
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 12, 8), False)
    image.clear_with(200)
    return image.tobytes("png")


def docx(body, *, media=None, relationships="", styles="", extra=None, encoding="utf-8"):
    document = ('<?xml version="1.0"?><w:document '
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                f'<w:body>{body}</w:body></w:document>')
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document.encode(encoding))
        if relationships:
            archive.writestr("word/_rels/document.xml.rels", relationships)
        if styles:
            archive.writestr("word/styles.xml", styles)
        for name, data in (media or {}).items():
            archive.writestr("word/media/" + name, data)
        for name, data in (extra or {}).items():
            info = zipfile.ZipInfo("placeholder")
            info.filename = name
            info.orig_filename = name
            archive.writestr(info, data)
    return stream.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "gb18030"])
def test_text_encodings_keep_paragraphs_and_latex(encoding):
    source = "1 Introduction\n\n论文正文与公式 \\(x_t^2+\\alpha\\)。\nContinued sentence.\n\n2 Methods\n\nSecond paragraph."
    paper = parser.extract_document(source.encode(encoding), "reading.TXT")
    assert paper["source_format"] == "txt"
    assert [p["text"] for p in paper["paragraphs"]] == ["1 Introduction", r"论文正文与公式 \(x_t^2+\alpha\)。 Continued sentence.", "2 Methods", "Second paragraph."]
    assert [o["paragraph_id"] for o in paper["outline"]] == ["p-1", "p-3"]
    assert paper["paragraphs"][3]["section_id"] == paper["outline"][1]["id"]
    assert paper["page_count"] == 1 and paper["_assets"] == []


def test_markdown_math_frontmatter_links_and_embedded_image_stay_in_reading_order(png):
    image = base64.b64encode(png).decode()
    source = (f'---\ntitle: "Research note"\n---\n# Introduction\n\nA **complete** paragraph '
              'with $x_{t}^{2} + \\alpha_t$ and [citation](https://example.test).\n\n'
              f'![Figure one](data:image/png;base64,{image})\n\n## Methods\n\n'
              '$$\n\\frac{x_0}{y_t}\n$$\n\nA final paragraph.')
    paper = parser.extract_document(source.encode(), "paper.markdown")
    assert paper["title"] == "Research note"
    assert paper["source_format"] == "markdown"
    assert paper["paragraphs"][1]["text"] == r"A complete paragraph with $x_{t}^{2} + \alpha_t$ and citation."
    assert paper["paragraphs"][3]["text"] == '$$\n\\frac{x_0}{y_t}\n$$'
    asset, = paper["_assets"]
    assert (asset["after_paragraph_id"], asset["before_paragraph_id"]) == ("p-2", "p-3")
    assert asset["caption"] == "Figure one"
    assert asset["data"].startswith(b"\x89PNG")
    assert (asset["width"], asset["height"]) == (12, 8)


def test_markdown_setext_code_and_math_underscores_are_not_lost():
    source = "A title\n=======\n\nText with **bold** and $x_a + b_c$.\n\n```python\nx = 1\nprint(x)\n```\n\n2. A list item."
    paper = parser.extract_document(source.encode(), "paper.md")
    assert paper["title"] == "A title"
    assert paper["paragraphs"][1]["text"] == "Text with bold and $x_a + b_c$."
    assert paper["paragraphs"][2]["text"] == "x = 1\nprint(x)"
    assert paper["paragraphs"][3]["text"] == "A list item."


def test_html_removes_active_and_hidden_content_without_consuming_following_body(png):
    source = (f'<html><head><title>Study &amp; Notes</title><script>ignore</script></head><body>'
              '<h1>Introduction</h1><p>A <em>complete</em> paragraph with \\(x_t\\).</p>'
              '<script>fetch("secret")</script><style>.x{display:none}</style>'
              '<div hidden>Hidden <span>nested<img src="https://bad.test/never"></span></div>'
              '<p aria-hidden="true">Hidden aria</p><p style="color:red; display: none !important">Hidden css</p>'
              '<embed src="https://bad.test/never"><p>Visible afterward.</p>'
              f'<img alt="Local diagram" src="data:image/png;base64,{base64.b64encode(png).decode()}">'
              '<h2>Methods</h2><table><tr><td>Value</td><td>Result</td></tr></table></body></html>')
    paper = parser.extract_document(source.encode(), "page.html")
    assert paper["title"] == "Study & Notes"
    assert [p["text"] for p in paper["paragraphs"]] == ["Introduction", r"A complete paragraph with \(x_t\).", "Visible afterward.", "Methods", "Value | Result"]
    assert len(paper["_assets"]) == 1 and not paper["warnings"]
    assert paper["_assets"][0]["before_paragraph_id"] == "p-4"


@pytest.mark.parametrize("filename,source", [
    ("page.html", '<p>Readable.</p><img src="https://invalid.test/image.png"><img src="file:///C:/private.png">'),
    ("paper.md", '# Readable\n\n![No network](https://invalid.test/image.png)\n\n![No disk](C:/private.png)'),
])
def test_markup_external_images_are_never_requested(filename, source, monkeypatch):
    def no_image_loader(_):
        pytest.fail("external source reached the image decoder")
    monkeypatch.setattr(parser, "_png", no_image_loader)
    paper = parser.extract_document(source.encode(), filename)
    assert paper["paragraphs"] and paper["_assets"] == []
    assert "外链或本机路径" in "".join(paper["warnings"])


def test_docx_preserves_headings_math_table_and_embedded_image(png):
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Introduction</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>A full sentence with </w:t></w:r><m:oMath><m:f><m:num><m:r><m:t>x</m:t></m:r></m:num>'
            '<m:den><m:rad><m:e><m:r><m:t>α</m:t></m:r></m:e></m:rad></m:den></m:f></m:oMath><w:r><w:t> remains intact.</w:t></w:r></w:p>'
            '<w:p><w:r><w:drawing><a:blip r:embed="r1"/></w:drawing></w:r></w:p>'
            '<w:p><w:r><w:t>Figure caption.</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>First</w:t></w:r></w:p><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>Result</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    rels = '<Relationships><Relationship Id="r1" Target="media/local.png"/></Relationships>'
    paper = parser.extract_document(docx(body, media={"local.png": png}, relationships=rels), "paper.docx")
    assert paper["source_format"] == "docx" and paper["title"] == "Introduction"
    assert paper["paragraphs"][1]["text"] == r"A full sentence with \(\frac{x}{\sqrt{\alpha }}\) remains intact."
    assert paper["paragraphs"][-1]["text"] == "First Second | Result"
    asset, = paper["_assets"]
    assert (asset["after_paragraph_id"], asset["before_paragraph_id"]) == ("p-2", "p-3")
    assert not paper["warnings"]


def test_docx_bottom_bar_multi_delimiter_and_fraction_styles_keep_all_operands():
    math = ('<m:bar><m:barPr><m:pos m:val="bot"/></m:barPr><m:e><m:r><m:t>x</m:t></m:r></m:e></m:bar>'
            '<m:d><m:dPr><m:begChr m:val="{"/><m:endChr m:val="}"/><m:sepChr m:val=","/></m:dPr>'
            '<m:e><m:r><m:t>a</m:t></m:r></m:e><m:e><m:r><m:t>b</m:t></m:r></m:e></m:d>'
            '<m:f><m:fPr><m:type m:val="noBar"/></m:fPr><m:num><m:r><m:t>n</m:t></m:r></m:num><m:den><m:r><m:t>k</m:t></m:r></m:den></m:f>')
    body = '<w:p><m:oMath>' + math + '</m:oMath></w:p>'
    paper = parser.extract_document(docx(body), "paper.docx")
    assert paper["paragraphs"][0]["text"] == r"\(\underline{x}\{a,b\}\genfrac{}{}{0pt}{}{n}{k}\)"
    assert not paper["warnings"]


def test_docx_unknown_math_style_warns_instead_of_silently_discarding_operands():
    body = '<w:p><m:oMath><m:f><m:fPr><m:type m:val="unsupported"/></m:fPr><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath></w:p>'
    paper = parser.extract_document(docx(body), "paper.docx")
    assert r"\frac{a}{b}" in paper["paragraphs"][0]["text"]
    assert "分式样式" in "".join(paper["warnings"])


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_docx_entity_declarations_rejected_in_every_xml_encoding(encoding):
    declaration = '<?xml version="1.0"?><!DOCTYPE root [<!ENTITY p "expanded">]><root>&p;</root>'
    with pytest.raises(parser.DocumentExtractionError, match="实体"):
        parser._xml(declaration.encode(encoding))


@pytest.mark.parametrize("unsafe", ["../outside.png", "/absolute.png", "word/../outside.xml", "word\\evil.xml", "C:/evil.xml"])
def test_docx_unsafe_package_names_are_rejected(unsafe):
    body = '<w:p><w:r><w:t>Body.</w:t></w:r></w:p>'
    with pytest.raises(parser.DocumentExtractionError, match="不安全"):
        parser.extract_document(docx(body, extra={unsafe: b"no writes"}), "paper.docx")


def test_docx_external_image_relationship_is_not_followed(monkeypatch):
    def no_image_loader(_):
        pytest.fail("external relationship reached decoder")
    monkeypatch.setattr(parser, "_png", no_image_loader)
    body = '<w:p><w:r><w:t>Readable.</w:t><w:drawing><a:blip r:link="r1"/></w:drawing></w:r></w:p>'
    rels = '<Relationships><Relationship Id="r1" TargetMode="External" Target="https://invalid.test/secret"/></Relationships>'
    paper = parser.extract_document(docx(body, relationships=rels), "paper.docx")
    assert paper["_assets"] == [] and paper["warnings"]


def test_docx_inflated_package_limits_are_checked_before_parsing(monkeypatch):
    monkeypatch.setattr(parser, "MAX_ARCHIVE_BYTES", 100)
    with pytest.raises(parser.DocumentExtractionError, match="解压内容过大"):
        parser.extract_document(docx('<w:p><w:r><w:t>Readable.</w:t></w:r></w:p>'), "paper.docx")


def test_oversized_image_dimensions_are_checked_before_decode(monkeypatch):
    large = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 50000, 50000)
    assert parser._png(large) is None


def test_image_limit_is_enforced_after_png_conversion(monkeypatch):
    monkeypatch.setattr(parser, "MAX_ASSET_BYTES", 12)
    monkeypatch.setattr(parser, "_png", lambda data: (b"converted larger", 1, 1))
    builder = parser._Builder("paper.md")
    builder.image(b"tiny")
    assert builder.assets == [] and builder.asset_bytes == 0


@pytest.mark.parametrize("filename,content", [("paper.doc", b"legacy"), ("paper.docx", b"broken"), ("paper.md", b""), ("paper.txt", b"\x00binary"), ("paper.md", b"notes\x010\x02"), ("paper.md", b"notes\x019999\x02"), ("paper.html", b"<script>only script</script>")])
def test_invalid_input_is_an_actionable_document_error(filename, content):
    with pytest.raises(parser.DocumentExtractionError):
        parser.extract_document(content, filename)
