from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import html
import io
import base64
import os
import shutil
from pathlib import Path
from threading import Event
from time import monotonic
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import storage as db
from .demo import make_demo
from .pdf_parser import extract_pdf, PDFExtractionError
from .translator import TranslationError, test_connection, translate_text, validate_endpoint
from .providers import PROVIDERS, requires_key
from .figures import extract_figures
from .pdf_export import export_pdf
from .math_html import offline_math_assets
from .page_view import equation_png, router as page_view_router
from .pdf_runtime import PDF_LOCK
from .translation_input import paragraph_input, validate_translation

jobs: dict[str, asyncio.Task] = {}
figure_jobs: dict[str, asyncio.Task] = {}
FIGURE_VERSION = 2
MAX_UPLOAD = 50 * 1024 * 1024


@asynccontextmanager
async def lifespan(app):
    db.init_storage()
    yield
    active = list(jobs.values()) + list(figure_jobs.values())
    for task in active:
        task.cancel()
    await asyncio.gather(*active, return_exceptions=True)


app = FastAPI(title="纸间 · Folio", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])


@app.middleware("http")
async def local_guard(request: Request, call_next):
    # Reject browser writes initiated by unrelated sites; the app is local-only.
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "请从本机纸间阅读页面操作。"}, status_code=403)
        if origin:
            try:
                source = urlsplit(origin)
                target = request.url
                valid = source.hostname in ("127.0.0.1", "localhost", "::1") and source.port == target.port and source.scheme == target.scheme
            except ValueError:
                valid = False
            if not valid:
                return JSONResponse({"detail": "请从本机纸间阅读页面操作。"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({"detail": "请求参数不正确，请检查设置或重新选择文件。"}, status_code=422)


class SettingsInput(BaseModel):
    provider: str | None = None
    endpoint: str | None = None
    model: str | None = None
    api_key: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=4)


class TranslateInput(BaseModel):
    retry_failed: bool = False


def paper_or_404(paper_id):
    paper = db.get_paper(paper_id)
    if paper is None:
        raise HTTPException(404, "未找到这篇论文，请刷新文库。")
    return paper


def figure_dir(paper_id: str) -> Path:
    return db.DATA / "papers" / paper_id / "figures"


def ensure_figures(paper: dict, raw: bytes | None = None) -> dict:
    """Schedule artwork separately; neither import nor opening a paper waits for it."""
    paper_id = paper["id"]
    if paper_id in figure_jobs and not figure_jobs[paper_id].done():
        return paper
    if paper.get("figure_version") == FIGURE_VERSION and paper.get("figure_status") in ("completed", "error"):
        return paper
    source = db.DATA / "papers" / f"{paper['id']}.pdf"
    if paper.get("is_demo") or (raw is None and not source.exists()):
        paper.update(figures=paper.get("figures", []), figure_status="completed", figure_version=FIGURE_VERSION,
                     figure_progress={"done": paper.get("page_count", 0), "total": paper.get("page_count", 0)})
        db.save_paper(paper)
        return paper
    paper.update(figures=[], figure_status="extracting", figure_version=FIGURE_VERSION,
                 figure_progress={"done": 0, "total": paper.get("page_count", 0)})
    paper.pop("figures_error", None)
    db.save_paper(paper)
    figure_jobs[paper_id] = asyncio.create_task(figure_job(paper_id, raw))
    return paper


async def figure_job(paper_id: str, raw: bytes | None = None):
    """Publish each completed page on the event loop, preserving concurrent translations."""
    stop = Event()
    worker = None
    last_publish = 0.0
    loop = asyncio.get_running_loop()

    def publish(figures, done, total):
        # All read/modify/save operations run on the same event loop as translation.
        # A cancelled/deleted job must never resurrect a paper or its old text.
        if stop.is_set():
            return
        current = db.get_paper(paper_id)
        if current is not None:
            current.update(figures=figures, figure_progress={"done": done, "total": total})
            db.save_paper(db.refresh_progress(current))

    def progress(figures, done, total):
        nonlocal last_publish
        if not stop.is_set():
            now = monotonic()
            if done < total and now - last_publish < .2:
                return
            last_publish = now
            loop.call_soon_threadsafe(publish, figures, done, total)

    try:
        initial = db.get_paper(paper_id)
        if initial is None:
            return
        data = raw if raw is not None else await asyncio.to_thread((db.DATA / "papers" / f"{paper_id}.pdf").read_bytes)
        worker = asyncio.create_task(asyncio.to_thread(extract_figures, data, initial["paragraphs"],
                                                       figure_dir(paper_id), paper_id,
                                                       on_progress=progress, should_stop=stop.is_set))
        figures = await asyncio.shield(worker)
        current = db.get_paper(paper_id)
        if current is not None:
            current.update(figures=figures, figure_status="completed", figure_version=FIGURE_VERSION,
                           figure_progress={"done": current.get("page_count", 0), "total": current.get("page_count", 0)})
            db.save_paper(db.refresh_progress(current))
    except asyncio.CancelledError:
        stop.set()
        # Wait for the extractor's next safe checkpoint before deleting its files.
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)
        current = db.get_paper(paper_id)
        if current is not None:
            current["figure_status"] = "pending"
            db.save_paper(current)
        raise
    except Exception:
        current = db.get_paper(paper_id)
        if current is not None:
            current.update(figure_status="error", figures_error="部分插图未能提取，可打开原 PDF 查看。")
            db.save_paper(current)
    finally:
        stop.set()
        figure_jobs.pop(paper_id, None)


def merged_settings(payload: SettingsInput) -> dict:
    try:
        config = db.settings_private()
    except RuntimeError as exc:
        # A newly supplied key allows recovering after moving data to another user.
        if payload.api_key:
            config = dict(db.DEFAULTS, api_key="")
        else:
            raise HTTPException(400, str(exc)) from None
    previous = dict(config)
    for key, value in payload.model_dump(exclude_none=True).items():
        config[key] = value.strip() if isinstance(value, str) else value
    if config["provider"] not in {p["id"] for p in PROVIDERS}:
        raise HTTPException(400, "请选择有效的模型服务商。")
    if payload.api_key is None and (previous["endpoint"] != config["endpoint"] or previous["provider"] != config["provider"]):
        config["api_key"] = ""
    try:
        config["endpoint"] = validate_endpoint(config["endpoint"])
    except TranslationError as exc:
        raise HTTPException(400, str(exc)) from None
    if not config["model"] or len(config["model"]) > 150 or any(ord(c) < 32 for c in config["model"]):
        raise HTTPException(400, "请输入有效的模型名称。")
    if len(config.get("api_key", "")) > 4096 or any(c.isspace() or ord(c) < 32 or ord(c) > 126 for c in config.get("api_key", "")):
        raise HTTPException(400, "API Key 格式不正确，请检查空格和换行。")
    return config


@app.get("/api/health")
def health():
    return {"ok": True, "app": "folio"}


@app.get("/api/settings")
def get_settings():
    try:
        return db.public_settings(db.settings_private())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/providers")
def get_providers():
    return PROVIDERS


@app.put("/api/settings")
def put_settings(payload: SettingsInput):
    config = merged_settings(payload)
    db.save_settings(config)
    return db.public_settings(config)


@app.post("/api/settings/test")
async def check_connection(payload: SettingsInput = SettingsInput()):
    config = merged_settings(payload)
    if requires_key(config) and not config.get("api_key"):
        raise HTTPException(400, "请先填写 API Key。")
    try:
        return await test_connection(config)
    except TranslationError as exc:
        raise HTTPException(400, str(exc)) from None


@app.get("/api/papers")
def list_papers():
    return db.list_papers()


@app.post("/api/papers")
async def import_paper(file: UploadFile = File(...)):
    try:
        raw = bytearray()
        while chunk := await file.read(1024 * 1024):
            raw.extend(chunk)
            if len(raw) > MAX_UPLOAD:
                raise HTTPException(413, "PDF 不能超过 50 MB。")
    finally:
        await file.close()
    if not bytes(raw[:1024]).lstrip().startswith(b"%PDF-"):
        raise HTTPException(400, "请选择有效的 PDF 文件。")
    filename = Path((file.filename or "paper.pdf").replace("\\", "/")).name
    try:
        result = await asyncio.to_thread(extract_pdf, bytes(raw), filename)
    except PDFExtractionError as exc:
        raise HTTPException(400, str(exc)) from None
    paper_id = uuid4().hex
    paper = db.refresh_progress(dict(result, id=paper_id, filename=filename, created_at=datetime.now(timezone.utc).isoformat(), status="idle", error=None, is_demo=False))
    (db.DATA / "papers" / f"{paper_id}.pdf").write_bytes(raw)
    db.save_paper(paper)
    return ensure_figures(paper, bytes(raw))


@app.get("/api/demo")
def demo():
    paper = db.get_paper("demo")
    if not paper:
        paper = make_demo()
        db.save_paper(paper)
    return paper


@app.get("/api/papers/{paper_id}")
async def get_paper(paper_id: str):
    return ensure_figures(paper_or_404(paper_id))


async def translation_job(paper_id: str, config: dict, paragraph_ids: list[str] | None = None):
    try:
        initial = paper_or_404(paper_id)
        pending = paragraph_ids if paragraph_ids is not None else [p["id"] for p in db.translation_paragraphs(initial) if not p.get("translation")]
        queue = asyncio.Queue()
        for pid in pending:
            queue.put_nowait(pid)

        async def worker():
            while not queue.empty():
                pid = queue.get_nowait()
                paper = paper_or_404(paper_id)
                source = next((p for p in db.translation_paragraphs(paper) if p["id"] == pid), None)
                if source is None:
                    queue.task_done()
                    continue
                try:
                    translated = validate_translation(source, await translate_text(paragraph_input(source), config))
                    error = None
                except TranslationError as exc:
                    translated, error = None, str(exc)
                except Exception:
                    translated, error = None, "这一段翻译失败，请稍后重试。"
                paper = paper_or_404(paper_id)
                target = next(p for p in paper["paragraphs"] if p["id"] == pid)
                target.update(translation=translated, error=error)
                if translated:
                    target.pop("translation_review_reason", None)
                    target.pop("previous_translation_parts", None)
                db.save_paper(db.refresh_progress(paper))
                queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(min(config.get("concurrency", 2), len(pending)))]
        try:
            await asyncio.gather(*workers)
        finally:
            for child in workers:
                if not child.done():
                    child.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        paper = db.refresh_progress(paper_or_404(paper_id))
        paper["status"] = "error" if paper["progress"]["failed"] else ("completed" if paper["translated_count"] == paper["progress"]["total"] else "paused")
        paper["error"] = "部分段落未完成，点击继续翻译可重试。" if paper["progress"]["failed"] else None
        db.save_paper(paper)
    except asyncio.CancelledError:
        paper = db.get_paper(paper_id)
        if paper:
            paper["status"] = "paused"
            db.save_paper(db.refresh_progress(paper))
        raise
    except Exception:
        paper = db.get_paper(paper_id)
        if paper:
            paper.update(status="error", error="翻译任务中断，已完成的译文已保存，可继续翻译。")
            db.save_paper(paper)
    finally:
        jobs.pop(paper_id, None)


@app.post("/api/papers/{paper_id}/translate")
async def start_translation(paper_id: str, payload: TranslateInput = TranslateInput()):
    paper = paper_or_404(paper_id)
    if paper_id in jobs and not jobs[paper_id].done():
        return paper
    if any(not task.done() for task in jobs.values()):
        raise HTTPException(409, "已有一篇论文正在翻译，请先暂停后切换。")
    if all(p.get("translation") for p in db.translation_paragraphs(paper)):
        return paper
    config = merged_settings(SettingsInput())
    if requires_key(config) and not config.get("api_key"):
        raise HTTPException(400, "请先在 API 设置中填写密钥。")
    for p in db.translation_paragraphs(paper):
        if not p.get("translation"):
            p["error"] = None
    paper.update(status="translating", error=None)
    db.save_paper(db.refresh_progress(paper))
    jobs[paper_id] = asyncio.create_task(translation_job(paper_id, config))
    return paper


@app.post("/api/papers/{paper_id}/pause")
async def pause_translation(paper_id: str):
    paper_or_404(paper_id)
    task = jobs.get(paper_id)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return paper_or_404(paper_id)


@app.post("/api/papers/{paper_id}/paragraphs/{paragraph_id}/translate")
async def retry_paragraph(paper_id: str, paragraph_id: str):
    paper = paper_or_404(paper_id)
    if any(not task.done() for task in jobs.values()):
        raise HTTPException(409, "请先暂停当前翻译，再重新翻译单段。")
    paragraph = next((p for p in paper["paragraphs"] if p["id"] == paragraph_id), None)
    if not paragraph:
        raise HTTPException(404, "未找到这个段落。")
    if paragraph_id not in {p["id"] for p in db.translation_paragraphs(paper)}:
        raise HTTPException(400, "该内容为公式或原图内文字，无需逐段翻译。")
    config = merged_settings(SettingsInput())
    if requires_key(config) and not config.get("api_key"):
        raise HTTPException(400, "请先在 API 设置中填写密钥。")
    paper.update(status="translating", error=None)
    db.save_paper(paper)
    task = asyncio.create_task(translation_job(paper_id, config, [paragraph_id]))
    jobs[paper_id] = task
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
    return paper_or_404(paper_id)


@app.delete("/api/papers/{paper_id}")
async def remove_paper(paper_id: str):
    paper = paper_or_404(paper_id)
    await pause_translation(paper_id)
    task = figure_jobs.get(paper_id)
    if task and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    db.delete_paper(paper_id)

    def remove_files():
        root = (db.DATA / "papers").resolve()
        source = (root / f"{paper_id}.pdf").resolve()
        directory = (root / paper_id).resolve()
        if source.parent != root or directory == root or not directory.is_relative_to(root):
            return
        # A page render already in progress finishes before its cache is removed.
        with PDF_LOCK:
            source.unlink(missing_ok=True)
            if directory.exists():
                shutil.rmtree(directory)

    await asyncio.to_thread(remove_files)
    return {"ok": True}


@app.get("/api/papers/{paper_id}/figures/{figure_id}.png")
async def original_figure(paper_id: str, figure_id: str):
    paper = paper_or_404(paper_id)
    if figure_id not in {f["id"] for f in paper.get("figures", [])}:
        raise HTTPException(404, "未找到插图。")
    path = figure_dir(paper_id) / f"{figure_id}.png"
    if not path.exists():
        raise HTTPException(404, "插图文件不存在，请查看原始 PDF。")
    return FileResponse(path, media_type="image/png")


@app.get("/api/papers/{paper_id}/original")
def original(paper_id: str):
    paper = paper_or_404(paper_id)
    if paper.get("is_demo"):
        raise HTTPException(404, "示例文稿没有原始 PDF，请导入自己的论文。")
    return FileResponse(db.DATA / "papers" / f"{paper_id}.pdf", media_type="application/pdf", headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(paper['filename'])}"})


@app.get("/api/papers/{paper_id}/export")
async def export(paper_id: str, format: str = "markdown"):
    from .inline_math_export import html_with_math, text_with_math
    from .page_view import inline_math_png

    paper = paper_or_404(paper_id)
    if format not in ("markdown", "html", "pdf"):
        raise HTTPException(400, "支持导出 markdown、html 或 pdf。")
    if format in ("pdf", "html"):
        ensure_figures(paper)
        task = figure_jobs.get(paper_id)
        if task and not task.done():
            await asyncio.shield(task)
        paper = paper_or_404(paper_id)
    title = paper["title"]
    equation_paths = {}
    inline_paths = {}
    if format in ("pdf", "html"):
        for paragraph in paper["paragraphs"]:
            if paragraph.get("math_status") == "source-image":
                try:
                    equation_paths[paragraph["id"]] = await asyncio.to_thread(equation_png, paper, paragraph["id"])
                except Exception:
                    # Exporters render a readable placeholder when the original crop is unavailable.
                    pass
            for slot in paragraph.get("inline_math") or []:
                if slot.get("latex"):
                    continue
                for index, _ in enumerate(slot.get("source") or []):
                    try:
                        inline_paths[(paragraph["id"], slot["id"], index)] = await asyncio.to_thread(
                            inline_math_png, paper, paragraph["id"], slot["id"], index)
                    except Exception:
                        pass
    if format == "pdf":
        paths = {f["id"]: figure_dir(paper_id) / f"{f['id']}.png" for f in paper.get("figures", [])}
        content = await asyncio.to_thread(export_pdf, paper, paths, equation_paths, inline_paths)
        media, extension = "application/pdf", "pdf"
    elif format == "html":
        sections = []
        inserted = set()

        def image_html(figure):
            if figure["id"] in inserted:
                return ""
            inserted.add(figure["id"])
            path = figure_dir(paper_id) / f"{figure['id']}.png"
            if path.is_file():
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                art = f'<img style="max-width:100%;height:auto" src="data:image/png;base64,{encoded}" alt="原文插图">'
            else:
                art = "<p>[插图文件缺失，请在原始 PDF 中查看]</p>"
            return f'<figure>{art}<figcaption>{html.escape(figure.get("caption") or "原文插图")}</figcaption></figure>'

        hidden = {pid for f in paper.get("figures", []) for pid in f.get("text_paragraph_ids", [])}
        for p in paper["paragraphs"]:
            if p["id"] in hidden:
                continue
            tag = "h2" if p["kind"] == "heading" else "p"
            section = "".join(image_html(f) for f in paper.get("figures", []) if f.get("before_paragraph_id") == p["id"])
            if p.get("kind") == "equation" or p.get("math_status") == "source-image":
                if p.get("math_status") == "source-image":
                    path = equation_paths.get(p["id"])
                    if path is not None and path.is_file():
                        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                        equation = f'<img style="max-width:100%;height:auto" src="data:image/png;base64,{encoded}" alt="原文片段与数学公式">'
                    else:
                        equation = "<p>[公式原图未能导出，请查看原始 PDF]</p>"
                else:
                    equation = f'<p class="en">{html.escape(p["text"])}</p>'
                section += f'<section class="equation">{equation}</section>'
                if p.get("kind") != "equation":
                    section += f'<p class="zh">{html_with_math(p, p.get("translation") or "[尚未翻译]", inline_paths)}</p>'
            else:
                section += f'<section><{tag} class="en">{html.escape(p["text"])}</{tag}><p class="zh">{html_with_math(p, p.get("translation") or "[尚未翻译]", inline_paths)}</p></section>'
            for f in paper.get("figures", []):
                if not f.get("before_paragraph_id") and f.get("after_paragraph_id") == p["id"]:
                    section += image_html(f)
            sections.append(section)
        sections.extend(image_html(f) for f in paper.get("figures", []) if f["id"] not in inserted)
        body = "".join(sections)
        content = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(title)}</title><style>body{{max-width:820px;margin:64px auto;padding:0 24px;color:#273638;background:#faf9f6;line-height:1.9}}h1{{line-height:1.3}}.en{{font-family:Georgia,serif;font-size:18px}}.zh{{font-family:system-ui;color:#476364;font-size:17px}}section{{margin:32px 0}}h2{{font-size:24px}}@media print{{body{{margin:0;background:white}}}}</style><h1>{html.escape(title)}</h1><p>纸间 · 英中对照阅读</p>{body}</html>'
        media, extension = "text/html", "html"
        content = content.replace("</html>", offline_math_assets() + "</html>")
    else:
        chunks = [f"# {title}", "纸间 · 英中对照阅读"]
        for p in paper["paragraphs"]:
            chunks.extend([("## " if p["kind"] == "heading" else "") + p["text"],
                           text_with_math(p, p.get("translation") or "[尚未翻译]", markdown=True)])
        content, media, extension = "\n\n".join(chunks), "text/markdown", "md"
    return Response(content, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(title[:100] + '.' + extension)}"})


app.include_router(page_view_router)
app.mount("/", StaticFiles(directory=db.ROOT / "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="127.0.0.1", port=int(os.environ.get("FOLIO_PORT", "8765")))
