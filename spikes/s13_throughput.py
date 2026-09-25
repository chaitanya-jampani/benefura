"""Spike 13: measure booklet chunk wall time through the API and project a 60-page booklet at concurrency 4."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from _common import check, write_result

CHUNK = 5
OVERLAP = 1
CONCURRENCY = 4


def chunk_ranges(pages: int) -> list[tuple[int, int]]:
    out, start = [], 1
    while start <= pages:
        end = min(pages, start + CHUNK - 1)
        out.append((start, end))
        if end == pages:
            break
        start = end + 1 - OVERLAP
    return out


def split_pdf(pdf: Path, first: int, last: int) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf))
    writer = PdfWriter()
    for i in range(first - 1, last):
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


async def post_chunk(client: httpx.AsyncClient, api: str, pdf: Path, rng: tuple[int, int], doc_id: str,
                     sem: asyncio.Semaphore) -> dict[str, Any]:
    body = split_pdf(pdf, *rng)
    headers = {}
    if os.environ.get("EVALS_SHARED_SECRET"):
        headers = {"x-benefura-budget-namespace": "evals", "x-benefura-evals-key": os.environ["EVALS_SHARED_SECRET"]}
    async with sem:
        start = time.perf_counter()
        resp = await client.post(
            f"{api.rstrip('/')}/api/plan/analyze-chunk",
            data={"documentId": doc_id, "region": "CA", "pages": ",".join(str(p) for p in range(rng[0], rng[1] + 1))},
            files={"file": ("chunk.pdf", body, "application/pdf")},
            headers=headers,
        )
        seconds = time.perf_counter() - start
    out: dict[str, Any] = {"pages": list(rng), "status": resp.status_code, "seconds": round(seconds, 1), "bytes": len(body),
                           "traceId": resp.headers.get("x-benefura-trace-id")}
    if resp.status_code == 200:
        usage = resp.json().get("usage", {})
        out |= {"inputTokens": usage.get("inputTokens", 0), "outputTokens": usage.get("outputTokens", 0),
                "cuPages": usage.get("cuPages", 0), "estimatedCostUsd": usage.get("estimatedCostUsd", 0)}
    else:
        out["error"] = resp.text[:300]
    return out


def project(p95_seconds: float, tokens_per_page: float, tpm_k: int, pages: int = 60) -> dict[str, Any]:
    chunks = len(chunk_ranges(pages))
    # Extractor and Verifier each read the pages, plus ~30% for instructions and output.
    llm_tokens = pages * tokens_per_page * 2 * 1.3
    return {
        "pages": pages,
        "chunks": chunks,
        "wallMinutesAtConcurrency4": round(math.ceil(chunks / CONCURRENCY) * p95_seconds / 60, 1),
        "llmTokens": round(llm_tokens),
        "minutesOfTpmAtCapacity": round(llm_tokens / (tpm_k * 1000), 2),
    }


async def main_async(args: argparse.Namespace) -> None:
    measured: list[dict[str, Any]] = []
    if args.pdf:
        from pypdf import PdfReader

        total = len(PdfReader(str(args.pdf)).pages)
        ranges = chunk_ranges(total)[: args.chunks]
        sem = asyncio.Semaphore(CONCURRENCY)
        doc_id = f"spike13-{uuid.uuid4().hex[:8]}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(260)) as client:
            measured = await asyncio.gather(*(post_chunk(client, args.api_url, args.pdf, r, doc_id, sem) for r in ranges))
        for m in measured:
            print(json.dumps(m))

    ok = [m for m in measured if m["status"] == 200]
    seconds = sorted(m["seconds"] for m in ok)
    p95 = seconds[max(0, math.ceil(0.95 * len(seconds)) - 1)] if seconds else args.assume_chunk_seconds
    tokens_per_page = args.tokens_per_page
    if args.s03_result:
        runs = json.loads(args.s03_result.read_text())["runs"]
        tokens_per_page = max(r["tokensPerPage"] for r in runs)
    elif ok:
        pages_seen = sum(m["pages"][1] - m["pages"][0] + 1 for m in ok)
        tokens_per_page = sum(m["inputTokens"] for m in ok) / max(1, pages_seen) / 2  # two LLM passes see each page

    projection = project(p95, tokens_per_page, args.tpm_k)
    results: dict[str, bool] = {}
    if ok:
        check(results, "all chunks succeeded", len(ok) == len(measured))
        check(results, "p95 chunk <= 90 s target", p95 <= 90, f"{p95} s")
        check(results, "max chunk < 240 s ingress limit", max(seconds) < 240, f"{max(seconds)} s")
    check(results, "60 pages < 4 min at concurrency 4", projection["wallMinutesAtConcurrency4"] < 4,
          f"{projection['wallMinutesAtConcurrency4']} min")
    print(json.dumps(projection, indent=2))
    write_result("s13", {"measured": measured, "p95ChunkSeconds": p95, "medianChunkSeconds": statistics.median(seconds) if seconds else None,
                         "tokensPerPage": round(tokens_per_page), "tpmK": args.tpm_k, "projection": projection,
                         "checks": results}, all(results.values()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default=os.environ.get("API_URL", ""))
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--chunks", type=int, default=4)
    parser.add_argument("--tpm-k", type=int, default=200)
    parser.add_argument("--tokens-per-page", type=float, default=900.0)
    parser.add_argument("--assume-chunk-seconds", type=float, default=90.0)
    parser.add_argument("--s03-result", type=Path)
    args = parser.parse_args()
    if args.pdf and not args.api_url:
        parser.error("--api-url (or API_URL) is required with --pdf")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
