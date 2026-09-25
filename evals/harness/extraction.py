"""Posts pre-redacted, image-only chunk PDFs to ``/api/plan/analyze-chunk``, as the browser uploads them."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from evals.harness.client import BenefuraClient
from evals.harness.datasets import REPO_ROOT

JSON = dict[str, Any]
_CHUNK = re.compile(r"chunk-(\d+)-pages-(\d+)-(\d+)\.pdf$")


def chunk_files(directory: Path) -> list[tuple[Path, list[int]]]:
    found = []
    for path in sorted(directory.glob("chunk-*.pdf")):
        m = _CHUNK.search(path.name)
        if m:
            found.append((int(m.group(1)), path, list(range(int(m.group(2)), int(m.group(3)) + 1))))
    return [(path, pages) for _, path, pages in sorted(found)]


async def run_document(client: BenefuraClient, doc: JSON) -> JSON | None:
    directory = REPO_ROOT / doc["redacted_chunks"]
    chunks = chunk_files(directory) if directory.exists() else []
    if not chunks:
        return None
    document_id = uuid.uuid4().hex
    responses: list[JSON] = []
    errors: list[str] = []
    for path, pages in chunks:
        res = await client.analyze_chunk(document_id, doc["region"], pages, path.name, path.read_bytes())
        if res.status_code == 200:
            responses.append(res.json())
        else:
            errors.append(f"{path.name}:http_{res.status_code}")
    return {"doc": doc["doc"], "chunks": responses, "errors": errors}
