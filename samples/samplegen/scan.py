"""Image-only ("scanned") variant of a PDF: every page rasterized with slight skew and sensor noise."""

from __future__ import annotations

import io
import random

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

DPI = 150
SEED = 20260615


def scanned_pdf(pdf: bytes, title: str) -> bytes:
    src = pdfium.PdfDocument(pdf)
    rng = random.Random(SEED)
    out = io.BytesIO()
    first = src[0]
    w, h = first.get_size()
    first.close()
    c = Canvas(out, pagesize=(w, h), invariant=1)
    c.setTitle(title)
    c.setCreator("Benefura samples generator (scan simulation)")
    for i in range(len(src)):
        page = src[i]
        pw, ph = page.get_size()
        img = page.render(scale=DPI / 72, grayscale=True).to_pil().convert("L")
        page.close()

        angle = rng.uniform(-0.8, 0.8)
        shift = (rng.randint(-6, 6), rng.randint(-6, 6))
        img = img.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255, translate=shift)
        img = img.filter(ImageFilter.GaussianBlur(radius=0.35))

        arr = np.asarray(img, dtype=np.float32)
        noise_rng = np.random.default_rng(SEED + i)
        arr = arr * 0.94 + 8  # paper is never pure white, ink never pure black
        arr += noise_rng.normal(0.0, 5.0, size=arr.shape)
        # Uneven scanner lighting.
        cols = np.linspace(0, np.pi, arr.shape[1], dtype=np.float32)
        arr -= (np.sin(cols) * 4.0)[None, :]
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

        jpeg = io.BytesIO()
        img.save(jpeg, format="JPEG", quality=62, optimize=True)
        jpeg.seek(0)
        c.setPageSize((pw, ph))
        c.drawImage(ImageReader(jpeg), 0, 0, width=pw, height=ph)
        c.showPage()
    src.close()
    c.save()
    return out.getvalue()
