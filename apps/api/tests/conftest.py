from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = FIXTURES / "recorded"


def clear_caches() -> None:
    from app.budget import get_budget, get_table_client
    from app.config import get_settings
    from app.limits import reset_limits
    from app.pipelines.booklet_workflow import get_workflow_clients
    from app.services.content_understanding import get_cu_client
    from app.services.rest import get_http_client
    from app.services.search import get_search_client

    for cached in (
        get_settings,
        get_budget,
        get_table_client,
        get_workflow_clients,
        get_http_client,
        get_cu_client,
        get_search_client,
    ):
        clear = getattr(cached, "cache_clear", None)  # tests may monkeypatch a getter with a plain function
        if clear is not None:
            clear()
    reset_limits()


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts in fake mode with the small fixture booklets and fresh in-memory limits."""
    monkeypatch.setenv("AI_MODE", "fake")
    monkeypatch.setenv("SAMPLES_DIR", str(FIXTURES))
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("EVALS_SHARED_SECRET", "")
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def image_pdf(pages: int) -> bytes:
    """An image-only PDF, like the browser's rasterized chunks."""
    from PIL import Image

    images = [Image.new("L", (120, 160), color=255) for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def text_pdf() -> bytes:
    """A one-page PDF with a real text layer (must be refused)."""
    content = b"BT /F1 24 Tf 40 100 Td (SIN 046 454 286) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color=(255, 255, 255)).save(buffer, "PNG")
    return buffer.getvalue()
