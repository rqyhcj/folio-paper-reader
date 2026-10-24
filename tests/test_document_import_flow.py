"""Import, translate, export and remove non-PDF papers using isolated storage."""
import asyncio
import base64
import importlib
import io
import zipfile

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from backend import storage as db

api = importlib.import_module("backend.app")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path / "documents")
    api.jobs.clear()
    api.figure_jobs.clear()
    # These formats must not enter the PDF-only artwork extraction path.
    def no_pdf(*args, **kwargs):
        pytest.fail("non-PDF import attempted PDF figure extraction")
    monkeypatch.setattr(api, "extract_figures", no_pdf)
    with TestClient(api.app) as session:
        yield session
    assert not api.figure_jobs


def image_bytes():
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 6), False)
    image.clear_with(180)
    return image.tobytes("png")


def word_bytes():
    xml = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Local Document Study</w:t></w:r></w:p>
      <w:p><w:r><w:t>First English paragraph describes a local experiment.</w:t></w:r></w:p>
      <w:p><w:r><w:drawing><a:blip r:embed="rId1"/></w:drawing></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Methods</w:t></w:r></w:p>
      <w:p><w:r><w:t>The second paragraph preserves the reading order.</w:t></w:r></w:p></w:body></w:document>'''
    relationships = '''<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/local.png"/>
      </Relationships>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
        archive.writestr("word/document.xml", xml)
        archive.writestr("word/_rels/document.xml.rels", relationships)
        archive.writestr("word/media/local.png", image_bytes())
    return output.getvalue()


@pytest.mark.parametrize("filename,raw,kind", [
    ("notes.md", b"# Local Document Study\n\nFirst English paragraph.\n\n## Methods\n\nFormula \\(x_t=x_0\\).", "markdown"),
    ("notes.markdown", b"# Local Document Study\n\nFirst English paragraph.\n\n## Methods\n\nSecond paragraph.", "markdown"),
    ("notes.txt", "Local Document Study\n\nFirst English paragraph.\n\n第二段中文。".encode("utf-16"), "txt"),
    ("notes.TXT", "Local Document Study\n\n第一段中文与English。".encode("gb18030"), "txt"),
    ("notes.htm", b"<html><title>Local Document Study</title><h1>Introduction</h1><p>First English paragraph.</p><h2>Methods</h2><p>Second paragraph.</p><script>window.stealKey()</script></html>", "html"),
    ("notes.docx", word_bytes(), "docx"),
])
def test_formats_round_trip_source_outline_and_exports(client, filename, raw, kind):
    response = client.post("/api/papers", files={"file": (filename, raw, "application/octet-stream")})
    assert response.status_code == 200, response.text
    paper = response.json()
    assert paper["source_format"] == kind
    assert paper["paragraphs"] and paper["outline"]
    assert paper["figure_status"] == "completed"
    assert "_assets" not in paper
    assert "stealKey" not in " ".join(p["text"] for p in paper["paragraphs"])
    assert client.get(f"/api/papers/{paper['id']}").json()["paragraphs"] == paper["paragraphs"]
    assert client.get("/api/papers").json()[0]["source_format"] == kind
    original = client.get(f"/api/papers/{paper['id']}/original")
    assert original.status_code == 200 and original.content == raw
    assert original.headers["content-disposition"].startswith("attachment;")
    assert client.get(f"/api/papers/{paper['id']}/pages/1.png").status_code == 404
    if kind == "html":
        assert "sandbox" in original.headers["content-security-policy"]
    for fmt in ("markdown", "html", "pdf"):
        exported = client.get(f"/api/papers/{paper['id']}/export?format={fmt}")
        assert exported.status_code == 200, exported.text[:200] if fmt != "pdf" else "PDF export failed"
        if fmt == "pdf":
            with fitz.open(stream=exported.content, filetype="pdf") as document:
                assert "Local Document Study" in "".join(page.get_text() for page in document)
        else:
            assert "Local Document Study" in exported.text


@pytest.mark.parametrize("kind", ["docx", "html", "markdown"])
def test_embedded_images_follow_source_paragraph_and_survive_export(client, kind):
    png = image_bytes()
    uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    if kind == "docx":
        raw, filename = word_bytes(), "images.docx"
    elif kind == "html":
        raw = f"<h1>Image Study</h1><p>Before the figure.</p><img src='{uri}' alt='Original chart'><p>After the figure.</p>".encode()
        filename = "images.html"
    else:
        raw = f"# Image Study\n\nBefore the figure.\n\n![Original chart]({uri})\n\nAfter the figure.".encode()
        filename = "images.md"
    response = client.post("/api/papers", files={"file": (filename, raw)})
    assert response.status_code == 200, response.text
    paper = response.json()
    assert len(paper["figures"]) == 1
    figure = paper["figures"][0]
    paragraph_ids = [p["id"] for p in paper["paragraphs"]]
    assert figure.get("after_paragraph_id") in paragraph_ids or figure.get("before_paragraph_id") in paragraph_ids
    assert client.get(figure["url"]).content.startswith(b"\x89PNG")
    assert "data:image/png;base64," in client.get(f"/api/papers/{paper['id']}/export?format=html").text
    pdf = client.get(f"/api/papers/{paper['id']}/export?format=pdf")
    with fitz.open(stream=pdf.content, filetype="pdf") as document:
        assert any(page.get_images() for page in document)
    assert client.delete(f"/api/papers/{paper['id']}").json() == {"ok": True}
    assert list((db.DATA / "papers").iterdir()) == []


def test_non_pdf_translation_uses_same_persistent_bilingual_flow(client, monkeypatch):
    raw = b"# Translation Study\n\nA paragraph with \\(x_t=x_0\\)."
    paper = client.post("/api/papers", files={"file": ("study.md", raw)}).json()
    seen = []
    async def translate(text, config):
        seen.append(text)
        await asyncio.sleep(0)
        return "已保存译文：" + text
    monkeypatch.setattr(api, "translate_text", translate)
    monkeypatch.setattr(api, "merged_settings", lambda _: {"provider": "ollama", "endpoint": "http://localhost:11434/v1/chat/completions", "concurrency": 1})
    for paragraph in paper["paragraphs"]:
        result = client.post(f"/api/papers/{paper['id']}/paragraphs/{paragraph['id']}/translate")
        assert result.status_code == 200, result.text
    complete = client.get(f"/api/papers/{paper['id']}").json()
    assert len(seen) == len(paper["paragraphs"])
    assert all(p["translation"].startswith("已保存译文") for p in complete["paragraphs"])
    exported = client.get(f"/api/papers/{paper['id']}/export?format=html").text
    assert exported.index("A paragraph with") < exported.index("已保存译文：A paragraph with")
    assert not (db.DATA / "settings.json").exists()


@pytest.mark.parametrize("filename,raw", [("old.doc", b"old word format"), ("empty.txt", b"  \n"), ("fake.docx", b"not a ZIP"), ("archive.zip", b"not a document")])
def test_unsupported_empty_and_malformed_documents_leave_no_imported_copies(client, filename, raw):
    response = client.post("/api/papers", files={"file": (filename, raw)})
    assert response.status_code == 400
    assert client.get("/api/papers").json() == []
    assert list((db.DATA / "papers").iterdir()) == []


def test_failed_document_save_cleans_only_new_program_copies(client, monkeypatch):
    # An unrelated stored source must survive cleanup of this failed import.
    sentinel = db.DATA / "papers" / "existing.pdf"
    sentinel.write_bytes(b"existing original, never remove")
    def failed_save(paper):
        raise OSError("simulated unavailable storage")
    monkeypatch.setattr(db, "save_paper", failed_save)
    with pytest.raises(OSError, match="simulated unavailable"):
        client.post("/api/papers", files={"file": ("new.docx", word_bytes())})
    assert sentinel.read_bytes() == b"existing original, never remove"
    assert list((db.DATA / "papers").iterdir()) == [sentinel]
    assert client.get("/api/papers").json() == []
