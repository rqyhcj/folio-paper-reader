"""Coordinate clustering keeps upstream bounds without blocking page previews."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import random
import threading

from fastapi.testclient import TestClient
import pymupdf as fitz
import pytest

from backend import figures, storage as db


@pytest.mark.parametrize("rectangles", [
    [(20, 20, 80, 80), (83, 20, 100, 80), (300, 300, 400, 400)],
    [(20, 20, 80, 80)] * 40 + [(83, 20, 100, 80)] * 30,
    [(20, 20, 80, 80), (83, 80, 83, 80), (86, 80, 86, 80), (89, 80, 140, 140)],
    [(20, 20, 20, 80), (20, 80, 80, 80), (85, 20, 90, 25), (-10, 0, 100, 100)],
    [(20, 20, 80, 80), (20, 30, 90, 80), (95, 30, 100, 35), (91, 37, 95, 40)],
])
def test_tuple_clusters_equal_upstream_neighborhood_rules(rectangles):
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        original = page.cluster_drawings(drawings=[{"rect": fitz.Rect(rect)} for rect in rectangles])
        expected = [tuple(rect) for rect in original]
        assert figures._cluster_rectangles(rectangles, tuple(page.rect)) == expected


def test_duplicate_dense_grid_and_separate_clusters_keep_identical_bounds():
    generator = random.Random(184)
    rectangles = []
    for offset in (30, 330):
        for _ in range(250):
            x, y = generator.randint(0, 100) + offset, generator.randint(20, 190)
            rectangles.extend([(x, y, x + generator.randint(0, 8), y + generator.randint(0, 8))] * 4)
    with fitz.open() as document:
        page = document.new_page(width=600, height=800)
        expected = [tuple(rect) for rect in page.cluster_drawings(drawings=[{"rect": fitz.Rect(r)} for r in rectangles])]
        assert figures._cluster_rectangles(rectangles, tuple(page.rect)) == expected


def test_coordinate_clustering_honors_cancellation():
    assert figures._cluster_rectangles([(30, 30, 400, 500)], (0, 0, 600, 800), lambda: True) == []


def test_page_preview_works_while_same_paper_vector_clustering_is_blocked(tmp_path, monkeypatch):
    api = importlib.import_module("backend.app")
    monkeypatch.setattr(db, "DATA", tmp_path / "preview-during-clustering")
    api.jobs.clear()
    api.figure_jobs.clear()
    started, release = threading.Event(), threading.Event()
    original = figures._cluster_rectangles

    def slow_cluster(rectangles, bounds, should_stop=None):
        started.set()
        while not release.wait(.01):
            if should_stop and should_stop():
                return []
        return original(rectangles, bounds, should_stop)

    monkeypatch.setattr(figures, "_cluster_rectangles", slow_cluster)
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((55, 75), "A Concurrent Local Reading Test", fontsize=20)
        page.insert_text((55, 145), "The original page stays available while figures are grouped.", fontsize=11)
        page.draw_rect(fitz.Rect(70, 200, 400, 420), color=(.2, .3, .4), width=2)
        raw = document.tobytes()
    try:
        with TestClient(api.app) as client:
            imported = client.post("/api/papers", files={"file": ("local.pdf", raw, "application/pdf")})
            assert imported.status_code == 200
            paper = imported.json()
            assert started.wait(2)
            assert paper["figure_status"] == "extracting"
            with ThreadPoolExecutor(max_workers=1) as pool:
                preview = pool.submit(client.get, f"/api/papers/{paper['id']}/pages/1.png")
                try:
                    response = preview.result(timeout=2)
                    assert response.status_code == 200
                    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
                    assert not release.is_set()
                finally:
                    release.set()
    finally:
        release.set()
