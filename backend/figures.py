"""Recover original raster figures and vector chart regions as local PNGs."""
from __future__ import annotations

from pathlib import Path
import re
import time
from collections.abc import Callable
import pymupdf as fitz

from .pdf_runtime import PDF_LOCK

CAPTION = re.compile(r"^(?:fig(?:ure)?\.?|图)\s*(?:s\s*)?\d", re.I)
_SECTION_TITLE = re.compile(
    r"^(?:abstract|introduction|background|related work|methods?|materials and methods|"
    r"results?(?: and discussion)?|discussion|conclusions?|limitations?|"
    r"acknowledg(?:e)?ments?|references|bibliography|appendi(?:x|ces)|"
    r"supplementary (?:material|information)|摘要|引言|绪论|方法|结果|讨论|结论|参考文献)$", re.I)
_NUMBERED_SECTION = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*|[IVXLCDM]+)[.)]?\s+\S", re.I)


def _image_label(paragraph, rect):
    """Identify only text already visibly reproduced inside a recovered figure.

    Bold diagram labels may have been classified as headings by the parser.
    Their geometry still proves duplication, but protect conventional and
    numbered section titles from suppression if an image region is too broad.
    """
    if paragraph.get("kind") in {"algorithm", "table"}:
        return False
    text = paragraph.get("text", "").strip()
    if len(text) > 300 or CAPTION.match(text) or not _inside(paragraph, rect):
        return False
    if paragraph.get("kind") == "heading":
        return (len(text) <= 120 and not _SECTION_TITLE.fullmatch(text)
                and not _NUMBERED_SECTION.match(text)
                and not re.match(r"^第[一二三四五六七八九十百\d]+[章节篇]", text))
    return True


def _inside(paragraph, rect):
    bbox = paragraph.get("bbox")
    if not bbox or len(bbox) != 4:
        return False
    area = _area(bbox)
    return area > 0 and _area(_intersect(bbox, rect)) / area >= .9


def _area(rect):
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _intersect(a, b):
    return max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])


def _union(a, b):
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _cluster_rectangles(rectangles, bounds, should_stop=None):
    """Run the upstream neighborhood/expanding-bounds rules on plain tuples.

    Rules are adapted from PyMuPDF Page.cluster_drawings (AGPL-3.0; see NOTICE).
    Duplicate path boxes cannot alter expanding bounds and are removed first.
    Tiny unique paths are retained because they can connect neighboring parts.
    This function makes no MuPDF calls and must run outside PDF_LOCK.
    """
    stopped = should_stop or (lambda: False)
    pending = sorted({tuple(r) for r in rectangles if r[0] >= bounds[0] and r[2] <= bounds[2]
                      and r[1] >= bounds[1] and r[3] <= bounds[3]}, key=lambda r: (r[3], r[0]))
    result = []
    while pending:
        if stopped():
            return []
        rect = pending[0]
        repeat = True
        while repeat:
            repeat = False
            for index in range(len(pending) - 1, 0, -1):
                if index % 1024 == 0 and stopped():
                    return []
                other = pending[index]
                # Use normalized endpoints, matching upstream degenerate-line handling.
                ax0, ax1 = (rect[0], rect[2]) if rect[2] > rect[0] else (rect[2], rect[0])
                ay0, ay1 = (rect[1], rect[3]) if rect[3] > rect[1] else (rect[3], rect[1])
                bx0, bx1 = (other[0], other[2]) if other[2] > other[0] else (other[2], other[0])
                by0, by1 = (other[1], other[3]) if other[3] > other[1] else (other[3], other[1])
                if not (ax1 < bx0 - 3 or ax0 > bx1 + 3 or ay1 < by0 - 3 or ay0 > by1 + 3):
                    rect = _union(rect, other)
                    del pending[index]
                    repeat = True
        result.append(rect)
        del pending[0]
        pending.sort(key=lambda r: (r[3], r[0]))
    return [r for r in sorted(set(result), key=lambda r: (r[3], r[0])) if r[2] - r[0] > 3 and r[3] - r[1] > 3]


def _page_snapshot(page):
    """Copy only the coordinates/text needed later while MuPDF is locked."""
    try:
        drawings = page.get_cdrawings()
        rectangles = [tuple(path["rect"]) for path in drawings if "rect" in path]
    except Exception:
        rectangles = []
    blocks = page.get_text("blocks", flags=fitz.TEXTFLAGS_BLOCKS & ~fitz.TEXT_PRESERVE_IMAGES)
    captions = [(tuple(b[:4]), re.sub(r"\s+", " ", b[4]).strip()) for b in blocks if
                len(b) > 4 and isinstance(b[4], str) and CAPTION.match(b[4].strip())]
    return {"bounds": tuple(page.rect), "images": [tuple(image["bbox"]) for image in page.get_image_info()],
            "drawings": rectangles, "captions": captions}


def _regions(snapshot, should_stop=None):
    bounds = snapshot["bounds"]
    regions = []
    for image in snapshot["images"]:
        rect = _intersect(image, bounds)
        if rect[2] - rect[0] >= 35 and rect[3] - rect[1] >= 30 and 1200 < _area(rect) < _area(bounds) * .92:
            regions.append(rect)
    # Vector plots and diagrams are common in scholarly PDFs.
    clusters = _cluster_rectangles(snapshot["drawings"], bounds, should_stop)
    for cluster in clusters:
        rect = _intersect(cluster, bounds)
        if rect[2] - rect[0] >= 55 and rect[3] - rect[1] >= 38 and _area(rect) < _area(bounds) * .8:
            regions.append(rect)
    merged = []
    for candidate in sorted(regions, key=_area, reverse=True):
        if any(_area(_intersect(candidate, existing)) >= _area(candidate) * .8 for existing in merged):
            continue
        expanded = candidate[0] - 8, candidate[1] - 8, candidate[2] + 8, candidate[3] + 8
        overlapping = [r for r in merged if _area(_intersect(expanded, r)) > 0]
        if overlapping:
            for existing in overlapping:
                candidate = _union(candidate, existing)
                merged.remove(existing)
        merged.append(candidate)
    return sorted(merged, key=lambda r: (r[1], r[0]))


def _anchors(rect, page_paras, caption):
    """Keep art beside its original text, before a following caption/body block."""
    # Labels already reproduced inside the image cannot be placement anchors.
    page_paras = [p for p in page_paras if not _inside(p, rect)]
    if not page_paras:
        return None, None
    following = []
    for index, paragraph in enumerate(page_paras):
        bbox = paragraph.get("bbox")
        if bbox and len(bbox) == 4:
            overlap = max(0, min(bbox[2], rect[2]) - max(bbox[0], rect[0]))
            if bbox[1] >= rect[3] - 3 and overlap >= min(bbox[2] - bbox[0], rect[2] - rect[0]) * .25:
                following.append((bbox[1] - rect[3], index, paragraph))
    caption_index = next((i for i, p in enumerate(page_paras) if caption and
                          (caption[:45].lower() in p["text"].lower() or p["text"][:45].lower() in caption.lower())), None)
    if caption_index is not None:
        before_index = caption_index
    elif following:
        before_index = min(following, key=lambda item: (item[0], item[1]))[1]
    else:
        before_index = None
    if before_index is not None:
        # after remains a usable fallback for older exporters/clients.
        after_index = max(0, before_index - 1)
        return page_paras[after_index]["id"], page_paras[before_index]["id"]
    return page_paras[-1]["id"], None


def extract_figures(data: bytes, paragraphs: list[dict], destination: Path, paper_id: str,
                    *, on_progress: Callable | None = None,
                    should_stop: Callable[[], bool] | None = None,
                    suggested_regions: list[dict] | None = None,
                    page_filter: set[int] | None = None, id_prefix: str = "f-") -> list[dict]:
    if not re.fullmatch(r"f-(?:ai-[0-9]+-)?", id_prefix):
        raise ValueError("Invalid generated image prefix")
    figures = []
    total_bytes = 0
    stopped = should_stop or (lambda: False)
    if stopped():
        return figures
    destination.mkdir(parents=True, exist_ok=True)
    def render_crop(page, rect):
        crop = (fitz.Rect(rect) + (-6, -6, 6, 6)) & page.rect
        scale = min(1.8, 1600 / crop.width, 1800 / crop.height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=crop, alpha=False)
        return pixmap.tobytes("png"), pixmap.width, pixmap.height

    def extract_page(snapshot, page_index):
        nonlocal total_bytes
        page_paras = [p for p in paragraphs if p["page"] == page_index + 1]
        captions = snapshot["captions"]
        suggestions = [r for r in suggested_regions or [] if r.get("page") == page_index + 1]
        regions = list(_regions(snapshot, stopped))
        for suggestion in suggestions:
            rect = suggestion["bbox"]
            regions = [r for r in regions if _area(_intersect(r, rect)) < min(_area(r), _area(rect)) * .5]
            regions.append(tuple(rect))
        structures = [p for p in page_paras if p.get("kind") in {"algorithm", "table"} and p.get("bbox")]
        for rect in sorted(regions, key=lambda r: (r[1], r[0])):
            # A recovered algorithm/table has actual selectable text. Do not
            # reproduce its horizontal rules as a second screenshot figure.
            if any(_area(_intersect(rect, p["bbox"])) >= _area(rect) * .75 for p in structures):
                continue
            if stopped():
                return
            if len(figures) >= 300 or total_bytes > 80 * 1024 * 1024:
                return
            candidates = [(box, text) for box, text in captions if abs(box[1] - rect[3]) < 160 and box[2] > rect[0] and box[0] < rect[2]]
            caption = min(candidates, key=lambda item: abs(item[0][1] - rect[3]))[1] if candidates else ""
            chosen = next((r for r in suggestions if list(rect) == list(r["bbox"])), None)
            if chosen and chosen.get("caption"):
                caption = chosen["caption"]
            after, before = _anchors(rect, page_paras, caption)
            if after is None and before is None:
                previous = [p for p in paragraphs if p["page"] < page_index + 1]
                following = [p for p in paragraphs if p["page"] > page_index + 1]
                after = previous[-1]["id"] if previous else None
                before = following[0]["id"] if following else None
            with PDF_LOCK:
                if stopped():
                    return
                content, width, height = render_crop(document[page_index], rect)
            if stopped():
                return
            fid = f"{id_prefix}{len(figures) + 1}"
            (destination / f"{fid}.png").write_bytes(content)
            total_bytes += len(content)
            figures.append({"id": fid, "page": page_index + 1, "after_paragraph_id": after,
                            "before_paragraph_id": before, "bbox": list(rect),
                            "page_width": snapshot["bounds"][2] - snapshot["bounds"][0],
                            "page_height": snapshot["bounds"][3] - snapshot["bounds"][1],
                            "text_paragraph_ids": [p["id"] for p in page_paras if _image_label(p, rect)],
                            "url": f"/api/papers/{paper_id}/figures/{fid}.png", "caption": caption,
                            "width": width, "height": height})

    with PDF_LOCK:
        document = fitz.open(stream=data, filetype="pdf")
        page_count = len(document)
    try:
        for page_index in range(page_count):
            if stopped():
                break
            if page_filter is not None and page_index + 1 not in page_filter:
                continue
            with PDF_LOCK:
                snapshot = _page_snapshot(document[page_index])
            extract_page(snapshot, page_index)
            if on_progress:
                on_progress(list(figures), page_index + 1, page_count)
            if len(figures) >= 300 or total_bytes > 80 * 1024 * 1024:
                break
            # Let a newly queued text import acquire the runtime before the next page.
            time.sleep(.01)
    finally:
        with PDF_LOCK:
            document.close()
    return figures
