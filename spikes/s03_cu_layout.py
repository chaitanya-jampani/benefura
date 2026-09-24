"""Spike 3: Content Understanding prebuilt-layout on 5-page image-only PDFs at 200 vs 300 DPI."""

from __future__ import annotations

import argparse
import difflib
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from _common import approx_tokens, check, credential, require, settings, timer, write_result

CHUNK_PAGES = 5
MAX_CHUNK_BYTES = 8 * 1024 * 1024


def cu_client() -> Any:
    from azure.ai.contentunderstanding import ContentUnderstandingClient

    return ContentUnderstandingClient(endpoint=require(settings().ai_services_endpoint, "AI_SERVICES_ENDPOINT"),
                                      credential=credential())


def rasterize(pdf: Path, dpi: int, first: int, last: int, workdir: Path) -> list[Path]:
    if not shutil.which("pdftoppm"):
        raise SystemExit("pdftoppm not found (brew install poppler), or pass --pages-dir")
    prefix = workdir / f"p{dpi}"
    subprocess.run(  # noqa: S603
        ["pdftoppm", "-r", str(dpi), "-gray", "-jpeg", "-jpegopt", "quality=85", "-f", str(first), "-l", str(last),
         str(pdf), str(prefix)],
        check=True,
    )
    return sorted(workdir.glob(f"p{dpi}-*.jpg"))


def images_to_pdf(images: list[Path], dpi: int) -> bytes:
    """Image-only PDF (no text layer), as the browser's pdf-lib chunker produces."""
    from PIL import Image

    frames = [Image.open(p).convert("L") for p in images]
    buf = io.BytesIO()
    frames[0].save(buf, "PDF", resolution=float(dpi), save_all=True, append_images=frames[1:])
    return buf.getvalue()


def digits(text: str) -> str:
    return "".join(re.findall(r"\d", text))


def digit_accuracy(expected: str, actual: str) -> float | None:
    exp = digits(expected)
    if not exp:
        return None
    return round(difflib.SequenceMatcher(a=exp, b=digits(actual), autojunk=False).ratio(), 4)


def analyze_layout(client: Any, pdf_bytes: bytes) -> tuple[list[dict[str, Any]], float, str]:
    with timer() as t:
        poller = client.begin_analyze_binary("prebuilt-layout", pdf_bytes, content_type="application/pdf")
        result = poller.result()
    operation_id = poller.operation_id
    pages: list[dict[str, Any]] = []
    for content in result.contents:
        markdown = getattr(content, "markdown", "") or ""
        doc_pages = getattr(content, "pages", None) or []
        if doc_pages and all(getattr(p, "spans", None) for p in doc_pages):
            for p in doc_pages:
                text = "".join(markdown[s.offset : s.offset + s.length] for s in p.spans)
                pages.append({"page": p.page_number, "markdown": text})
        else:
            pages.append({"page": getattr(content, "start_page_number", 0), "markdown": markdown})
    client.delete_result(operation_id)  # retention: delete right after reading
    return pages, t["ms"], operation_id


def update_defaults(client: Any) -> dict[str, Any]:
    s = settings()
    defaults = client.update_defaults(
        model_deployments={"gpt-4.1": s.chat_model, "gpt-4.1-mini": s.nano_model, "text-embedding-3-large": s.embedding_model}
        # M0-verify: the model keys CU expects (completion/mini/embedding) for gpt-5 deployments.
    )
    return dict(defaults)


def receipt_round_trip(client: Any, receipt: Path) -> dict[str, Any]:
    analyzer_id = "benefuraReceiptSpike"
    definition = {
        "baseAnalyzerId": "prebuilt-document",
        "description": "Spike 3 throwaway receipt analyzer",
        "config": {"estimateFieldSourceAndConfidence": True, "returnDetails": True},
        "fieldSchema": {
            "fields": {
                "providerName": {"type": "string", "method": "extract"},
                "total": {"type": "number", "method": "extract"},
                "currency": {"type": "string", "method": "extract"},
                "serviceDate": {"type": "date", "method": "extract"},
            }
        },
        "models": {"completion": settings().chat_model},
    }
    with timer() as create_t:
        client.begin_create_analyzer(analyzer_id, definition, allow_replace=True).result()
    try:
        data = receipt.read_bytes()
        ctype = "application/pdf" if receipt.suffix.lower() == ".pdf" else f"image/{receipt.suffix.lower().lstrip('.').replace('jpg', 'jpeg')}"
        with timer() as t:
            poller = client.begin_analyze_binary(analyzer_id, data, content_type=ctype)
            result = poller.result()
        fields = {}
        for content in result.contents:
            for name, field in (getattr(content, "fields", None) or {}).items():
                fields[name] = {"confidence": getattr(field, "confidence", None), "hasSource": bool(getattr(field, "source", None))}
        client.delete_result(poller.operation_id)
        return {"createMs": create_t["ms"], "analyzeMs": t["ms"], "fields": fields}
    finally:
        client.delete_analyzer(analyzer_id)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--pages-dir", type=Path, help="Pre-rendered page images (sorted by name)")
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--expected", type=Path, help="golden pages.json [{page, markdown}]")
    parser.add_argument("--dpi", type=int, nargs="+", default=[200, 300])
    parser.add_argument("--update-defaults", action="store_true")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    if not args.pdf and not args.pages_dir:
        parser.error("--pdf or --pages-dir is required")

    expected = {}
    if args.expected:
        expected = {p["page"]: p["markdown"] for p in json.loads(args.expected.read_text())}

    client = cu_client()
    results: dict[str, bool] = {}
    data: dict[str, Any] = {"chunkPages": CHUNK_PAGES, "runs": []}

    if args.update_defaults:
        data["defaults"] = update_defaults(client)
        check(results, "CU defaults PATCH", True)

    first, last = args.first_page, args.first_page + CHUNK_PAGES - 1
    with tempfile.TemporaryDirectory() as tmp:
        for dpi in args.dpi:
            workdir = Path(tmp) / str(dpi)
            workdir.mkdir()
            images = rasterize(args.pdf, dpi, first, last, workdir) if args.pdf else sorted(args.pages_dir.iterdir())[:CHUNK_PAGES]
            pdf_bytes = images_to_pdf(images, dpi)
            pages, ms, op = analyze_layout(client, pdf_bytes)
            per_page = []
            for i, page in enumerate(pages):
                number = first + i if args.pdf else page["page"]
                acc = digit_accuracy(expected.get(number, ""), page["markdown"]) if expected else None
                per_page.append({"page": number, "tokens": approx_tokens(page["markdown"]), "digitAccuracy": acc})
            accs = [p["digitAccuracy"] for p in per_page if p["digitAccuracy"] is not None]
            run = {
                "dpi": dpi,
                "pdfBytes": len(pdf_bytes),
                "bytesPerPage": len(pdf_bytes) // max(1, len(images)),
                "analyzeMs": ms,
                "resultDeleted": op,
                "tokensPerPage": round(sum(p["tokens"] for p in per_page) / max(1, len(per_page))),
                "minDigitAccuracy": min(accs) if accs else None,
                "pages": per_page,
            }
            data["runs"].append(run)
            print(json.dumps({k: v for k, v in run.items() if k != "pages"}))
            check(results, f"{dpi} DPI chunk under 8 MB", len(pdf_bytes) <= MAX_CHUNK_BYTES, f"{len(pdf_bytes)} bytes")
            check(results, f"{dpi} DPI analyze under 60 s", ms < 60_000, f"{ms} ms")

    if args.receipt:
        data["receipt"] = receipt_round_trip(client, args.receipt)
        check(results, "custom receipt analyzer returns fields", bool(data["receipt"]["fields"]))

    good = [r for r in data["runs"] if r["minDigitAccuracy"] is None or r["minDigitAccuracy"] >= 0.995]
    data["recommendedDpi"] = min((r["dpi"] for r in good), default=max(args.dpi))
    data["checks"] = results
    write_result("s03", data, all(results.values()))


if __name__ == "__main__":
    main()
