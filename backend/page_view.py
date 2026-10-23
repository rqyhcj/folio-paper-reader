"""Local, on-demand page previews and faithful equation crops."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import tempfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
import pymupdf as fitz

from . import storage as db
from .pdf_runtime import PDF_LOCK

router = APIRouter()


def _paper(paper_id: str) -> dict:
    paper = db.get_paper(paper_id)
    if paper is None or paper.get("is_demo"):
        raise HTTPException(404, "未找到可查看的原始 PDF。")
    return paper


def _asset_dir(paper_id: str, name: str) -> Path:
    directory = (db.DATA / "papers" / paper_id / name).resolve()
    if not directory.is_relative_to((db.DATA / "papers").resolve()):
        raise HTTPException(404, "未找到原文页面。")
    return directory


def _render(paper: dict, page_number: int, destination: Path, bbox=None) -> Path:
    source = db.DATA / "papers" / f"{paper['id']}.pdf"
    if not source.is_file():
        raise HTTPException(404, "原始 PDF 文件不存在。")
    with PDF_LOCK:
        if destination.is_file():
            return destination
        try:
            with fitz.open(source) as document:
                if not 1 <= page_number <= len(document):
                    raise HTTPException(404, "未找到这个原文页码。")
                page = document[page_number - 1]
                clip = page.rect
                if bbox is not None:
                    clip = (fitz.Rect(bbox) + (-4, -5, 4, 5)) & page.rect
                    if clip.is_empty or clip.is_infinite:
                        raise HTTPException(404, "这个公式没有可用的原文位置。")
                scale = min(2.0, 2000 / clip.width, 2600 / clip.height)
                content = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False).tobytes("png")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            try:
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "原文页面暂时无法显示，请打开原始 PDF 核对。") from None
    return destination


def equation_png(paper: dict, paragraph_id: str) -> Path:
    paragraph = next((p for p in paper["paragraphs"] if p["id"] == paragraph_id), None)
    region = (paragraph or {}).get("equation_source")
    if not region or not isinstance(region.get("bbox"), list) or len(region["bbox"]) != 4:
        raise HTTPException(404, "这个段落没有可用的公式原图。")
    signature = sha256(repr((region["page"], region["bbox"])).encode()).hexdigest()[:16]
    # A source-coordinate digest also invalidates crops after paragraph reflow.
    destination = _asset_dir(paper["id"], "equations") / f"e-{signature}.png"
    return _render(paper, int(region["page"]), destination, region["bbox"])


@router.get("/api/papers/{paper_id}/pages/{page_number}.png")
def original_page(paper_id: str, page_number: int):
    paper = _paper(paper_id)
    if not 1 <= page_number <= paper.get("page_count", 0):
        raise HTTPException(404, "未找到这个原文页码。")
    destination = _asset_dir(paper_id, "pages") / f"page-{page_number}.png"
    path = _render(paper, page_number, destination)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/api/papers/{paper_id}/equations/{paragraph_id}.png")
def original_equation(paper_id: str, paragraph_id: str):
    path = equation_png(_paper(paper_id), paragraph_id)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
