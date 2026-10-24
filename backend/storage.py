"""Local document storage and Windows current-user protected credentials."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("FOLIO_DATA_DIR", str(ROOT / "data"))).resolve()
DEFAULTS = {"provider": "custom", "endpoint": "", "model": "", "concurrency": 2}


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(raw: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("此版本的密钥保险箱需要 Windows。")
    source = ctypes.create_string_buffer(raw)
    inp = Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    out = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    func = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    func.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not func(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out)):
        raise RuntimeError("无法访问 Windows 密钥保险箱，请重新保存 API Key。")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def init_storage():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "papers").mkdir(exist_ok=True)
    with _db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS papers (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    for paper in list_papers(full=True):
        if paper.get("status") == "translating":
            paper["status"] = "paused"
            save_paper(paper)


def _db():
    return sqlite3.connect(DATA / "folio.sqlite3", timeout=10)


def settings_private() -> dict:
    config = dict(DEFAULTS)
    path = DATA / "settings.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        config.update({key: saved[key] for key in DEFAULTS if key in saved})
        if saved.get("protected_key"):
            config["api_key"] = _protect(base64.b64decode(saved["protected_key"]), True).decode("utf-8")
    config.setdefault("api_key", "")
    return config


def public_settings(config: dict) -> dict:
    from .providers import requires_key
    key = config.get("api_key", "")
    return {**{k: config[k] for k in DEFAULTS}, "has_key": bool(key), "key_hint": ("•••• " + key[-4:]) if key else "", "requires_key": requires_key(config)}


def save_settings(config: dict):
    saved = {k: config[k] for k in DEFAULTS}
    key = config.get("api_key", "")
    saved["protected_key"] = base64.b64encode(_protect(key.encode())).decode() if key else ""
    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DATA, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(saved, stream, ensure_ascii=False, indent=2)
    try:
        temporary.replace(DATA / "settings.json")
    finally:
        temporary.unlink(missing_ok=True)


def save_paper(paper: dict):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO papers (id,body) VALUES (?,?)", (paper["id"], json.dumps(paper, ensure_ascii=False)))


def get_paper(paper_id: str) -> dict | None:
    with _db() as con:
        row = con.execute("SELECT body FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return json.loads(row[0]) if row else None


def summarize(paper: dict) -> dict:
    result = {k: paper.get(k) for k in ("id", "title", "filename", "page_count", "paragraph_count", "translated_count", "progress", "created_at", "status", "is_demo")}
    result["source_format"] = paper.get("source_format", "pdf")
    return result


def list_papers(full=False) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT body FROM papers").fetchall()
    papers = sorted([json.loads(r[0]) for r in rows], key=lambda p: p["created_at"], reverse=True)
    return papers if full else [summarize(p) for p in papers]


def delete_paper(paper_id: str):
    with _db() as con:
        con.execute("DELETE FROM papers WHERE id = ?", (paper_id,))


def translation_paragraphs(paper: dict) -> list[dict]:
    """Skip source math, image labels and glyph runs without meaningful text."""
    labels = {pid for figure in paper.get("figures", []) for pid in figure.get("text_paragraph_ids", [])}
    return [p for p in paper["paragraphs"] if p.get("kind") != "equation" and p["id"] not in labels
            and any(char.isalnum() for char in p.get("text", ""))]


def refresh_progress(paper: dict) -> dict:
    paragraphs = translation_paragraphs(paper)
    done = sum(bool(p.get("translation")) for p in paragraphs)
    failed = sum(bool(p.get("error")) and not p.get("translation") for p in paragraphs)
    paper.update(paragraph_count=len(paper["paragraphs"]), translated_count=done,
                 progress={"total": len(paragraphs), "done": done, "failed": failed})
    return paper
