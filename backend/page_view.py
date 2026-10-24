"""Local, on-demand page previews and faithful equation crops."""
from __future__ import annotations

from hashlib import sha256
import math
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
    if paper is None or paper.get("is_demo") or paper.get("source_format", "pdf") != "pdf":
        raise HTTPException(404, "未找到可查看的原始 PDF。")
    return paper


def _asset_dir(paper_id: str, name: str) -> Path:
    directory = (db.DATA / "papers" / paper_id / name).resolve()
    if not directory.is_relative_to((db.DATA / "papers").resolve()):
        raise HTTPException(404, "未找到原文页面。")
    return directory


def _render(paper: dict, page_number: int, destination: Path, bbox=None, *, padding=(4, 5)) -> Path:
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
                    horizontal, vertical = padding
                    clip = (fitz.Rect(bbox) + (-horizontal, -vertical, horizontal, vertical)) & page.rect
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


def inline_math_png(paper: dict, paragraph_id: str, math_id: str, piece_index: int) -> Path:
    """Render only a recorded local formula piece, never URL-supplied coordinates."""
    paragraph = next((p for p in paper.get("paragraphs", []) if p.get("id") == paragraph_id), None)
    slots = (paragraph or {}).get("inline_math") or []
    slot = next((item for item in slots if isinstance(item, dict) and item.get("id") == math_id), None)
    pieces = (slot or {}).get("source")
    if (not isinstance(pieces, list) or not isinstance(piece_index, int)
            or piece_index < 0 or piece_index >= len(pieces)):
        raise HTTPException(404, "未找到这个行内公式。")
    piece = pieces[piece_index]
    if not isinstance(piece, dict):
        raise HTTPException(404, "这个行内公式没有可用的原文位置。")
    page_number, box = piece.get("page"), piece.get("bbox")
    if (not isinstance(page_number, int) or isinstance(page_number, bool)
            or not 1 <= page_number <= paper.get("page_count", 0)
            or not isinstance(box, (list, tuple)) or len(box) != 4
            or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in box)
            or box[2] <= box[0] or box[3] <= box[1]):
        raise HTTPException(404, "这个行内公式没有可用的原文位置。")
    # Tight padding avoids including adjacent prose such as "When" in the
    # formula shown inside a translated sentence.
    signature = sha256(repr((page_number, box, "inline", .4, .6)).encode()).hexdigest()[:16]
    destination = _asset_dir(paper["id"], "inline-math") / f"m-{signature}.png"
    return _render(paper, page_number, destination, box, padding=(.4, .6))


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


@router.get("/api/papers/{paper_id}/inline-math/{paragraph_id}/{math_id}/{piece_index}.png")
def original_inline_math(paper_id: str, paragraph_id: str, math_id: str, piece_index: int):
    path = inline_math_png(_paper(paper_id), paragraph_id, math_id, piece_index)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
