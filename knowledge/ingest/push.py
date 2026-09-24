"""Azure AI Search push. Chunk ids are content-addressed, so only new chunks get embedded and vanished ones deleted."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from knowledge.ingest.sources import KNOWLEDGE_DIR

INDEX_SCHEMA_PATH = KNOWLEDGE_DIR / "index_schema.json"
UPLOAD_BATCH = 100
EXPECTED_FIELDS = {
    "id": "Edm.String",
    "content": "Edm.String",
    "title": "Edm.String",
    "url": "Edm.String",
    "region": "Edm.String",
    "publisher": "Edm.String",
    "license": "Edm.String",
    "attribution": "Edm.String",
    "section": "Edm.String",
    "topics": "Collection(Edm.String)",
    "categories": "Collection(Edm.String)",
    "contentVector": "Collection(Edm.Single)",
    "contentHash": "Edm.String",
    "lastFetched": "Edm.DateTimeOffset",
}
_KEY_SAFE = re.compile(r"^[A-Za-z0-9_\-=]{1,1024}$")


def load_index_schema(path: Path = INDEX_SCHEMA_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def validate_index_schema(schema: dict[str, Any], *, dimensions: int = 512) -> list[str]:
    problems: list[str] = []
    fields = {f["name"]: f for f in schema.get("fields", [])}
    if schema.get("name") != "public-knowledge-v1":
        problems.append("index name must be public-knowledge-v1")
    for name, typ in EXPECTED_FIELDS.items():
        if name not in fields:
            problems.append(f"missing field {name}")
        elif fields[name].get("type") != typ:
            problems.append(f"field {name} must be {typ}")
    extra = set(fields) - set(EXPECTED_FIELDS)
    if extra:
        problems.append(f"unexpected fields {sorted(extra)}")
    keys = [n for n, f in fields.items() if f.get("key")]
    if keys != ["id"]:
        problems.append("id must be the only key field")
    for name in ("region", "topics", "categories", "url"):
        if name in fields and not fields[name].get("filterable"):
            problems.append(f"{name} must be filterable")
    for name in ("topics", "categories", "region"):
        if name in fields and not fields[name].get("facetable"):
            problems.append(f"{name} must be facetable")
    for name in ("content", "title"):
        if name in fields and not fields[name].get("searchable"):
            problems.append(f"{name} must be searchable")
    vector = fields.get("contentVector", {})
    if vector.get("dimensions") != dimensions:
        problems.append(f"contentVector must have {dimensions} dimensions")
    profiles = {p["name"]: p for p in schema.get("vectorSearch", {}).get("profiles", [])}
    algorithms = {a["name"]: a for a in schema.get("vectorSearch", {}).get("algorithms", [])}
    profile = profiles.get(vector.get("vectorSearchProfile", ""))
    if not profile:
        problems.append("contentVector profile not defined")
    elif algorithms.get(profile.get("algorithm", ""), {}).get("kind") != "hnsw":
        problems.append("vector profile must use an hnsw algorithm")
    semantic = schema.get("semantic", {})
    configs = {c["name"]: c for c in semantic.get("configurations", [])}
    default = configs.get(semantic.get("defaultConfiguration", ""))
    if not default:
        problems.append("semantic defaultConfiguration missing")
    else:
        pf = default.get("prioritizedFields", {})
        referenced = [pf.get("titleField", {}).get("fieldName")]
        referenced += [f["fieldName"] for f in pf.get("prioritizedContentFields", [])]
        referenced += [f["fieldName"] for f in pf.get("prioritizedKeywordsFields", [])]
        if pf.get("titleField", {}).get("fieldName") != "title":
            problems.append("semantic title field must be title")
        if "content" not in [f["fieldName"] for f in pf.get("prioritizedContentFields", [])]:
            problems.append("semantic content fields must include content")
        if not pf.get("prioritizedKeywordsFields"):
            problems.append("semantic keywords fields missing")
        for name in referenced:
            if name not in fields:
                problems.append(f"semantic config references unknown field {name}")
    return problems


def build_search_index(schema: dict[str, Any]):
    from azure.search.documents.indexes.models import SearchIndex

    problems = validate_index_schema(schema)
    if problems:
        raise ValueError("invalid index schema: " + "; ".join(problems))
    return SearchIndex(schema)


def chunk_id(source_id: str, section: str, content: str) -> str:
    digest = hashlib.sha256(f"{section}\n{content}".encode()).hexdigest()[:20]
    key = f"{source_id}-{digest}"
    assert _KEY_SAFE.match(key), key
    return key


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


@dataclass
class IndexStats:
    document_count: int
    storage_size: int
    vector_index_size: int


@dataclass
class PushOutcome:
    uploaded: int = 0
    touched: int = 0
    deleted: int = 0
    failed: int = 0


class IndexStore(Protocol):
    name: str

    async def ensure_index(self, schema: dict[str, Any]) -> None: ...

    async def existing_ids(self, url: str) -> set[str]: ...

    async def upload(self, documents: Sequence[dict[str, Any]]) -> int: ...

    async def touch(self, ids: Sequence[str], last_fetched: str) -> int: ...

    async def delete(self, ids: Sequence[str]) -> int: ...

    async def statistics(self) -> IndexStats | None: ...

    async def close(self) -> None: ...


def _odata_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class AzureSearchStore:
    name = "azure-ai-search"

    def __init__(self, endpoint: str | None = None, index_name: str | None = None) -> None:
        from azure.search.documents.aio import SearchClient
        from azure.search.documents.indexes.aio import SearchIndexClient

        from app.config import get_settings
        from app.services.foundry import get_credential

        settings = get_settings()
        self._endpoint = endpoint or settings.search_endpoint
        self._index = index_name or settings.search_index
        if not self._endpoint:
            raise RuntimeError("SEARCH_ENDPOINT is not set")
        # The CI identity needs Search Service Contributor and Search Index Data Contributor.
        self._index_client = SearchIndexClient(self._endpoint, get_credential())
        self._search_client = SearchClient(self._endpoint, self._index, get_credential())

    async def ensure_index(self, schema: dict[str, Any]) -> None:
        index = build_search_index({**schema, "name": self._index})
        await self._index_client.create_or_update_index(index)

    async def existing_ids(self, url: str) -> set[str]:
        ids: set[str] = set()
        results = await self._search_client.search(
            search_text="*", filter=f"url eq {_odata_quote(url)}", select=["id"], top=1000
        )
        async for doc in results:
            ids.add(doc["id"])
        return ids

    async def _index_actions(self, method: str, documents: Sequence[dict[str, Any]]) -> int:
        ok = 0
        for start in range(0, len(documents), UPLOAD_BATCH):
            batch = list(documents[start : start + UPLOAD_BATCH])
            results = await getattr(self._search_client, method)(batch)
            ok += sum(1 for r in results if r.succeeded)
        return ok

    async def upload(self, documents: Sequence[dict[str, Any]]) -> int:
        return await self._index_actions("merge_or_upload_documents", documents)

    async def touch(self, ids: Sequence[str], last_fetched: str) -> int:
        return await self._index_actions("merge_documents", [{"id": i, "lastFetched": last_fetched} for i in ids])

    async def delete(self, ids: Sequence[str]) -> int:
        return await self._index_actions("delete_documents", [{"id": i} for i in ids])

    async def statistics(self) -> IndexStats | None:
        stats = await self._index_client.get_index_statistics(self._index)
        return IndexStats(stats.document_count, stats.storage_size, stats.vector_index_size)

    async def close(self) -> None:
        await self._search_client.close()
        await self._index_client.close()


class MemoryIndexStore:
    name = "memory"

    def __init__(self) -> None:
        self.schema: dict[str, Any] | None = None
        self.docs: dict[str, dict[str, Any]] = {}

    async def ensure_index(self, schema: dict[str, Any]) -> None:
        build_search_index(schema)  # same validation as the live path
        self.schema = schema

    async def existing_ids(self, url: str) -> set[str]:
        return {i for i, d in self.docs.items() if d.get("url") == url}

    async def upload(self, documents: Sequence[dict[str, Any]]) -> int:
        allowed = set(EXPECTED_FIELDS)
        for doc in documents:
            unknown = set(doc) - allowed
            if unknown:
                raise ValueError(f"document has fields not in the index: {sorted(unknown)}")
            self.docs[doc["id"]] = {**self.docs.get(doc["id"], {}), **doc}
        return len(documents)

    async def touch(self, ids: Sequence[str], last_fetched: str) -> int:
        n = 0
        for i in ids:
            if i in self.docs:
                self.docs[i]["lastFetched"] = last_fetched
                n += 1
        return n

    async def delete(self, ids: Sequence[str]) -> int:
        return sum(1 for i in ids if self.docs.pop(i, None) is not None)

    async def statistics(self) -> IndexStats | None:
        size = sum(len(json.dumps({k: v for k, v in d.items() if k != "contentVector"})) for d in self.docs.values())
        vectors = sum(len(d.get("contentVector", [])) * 4 for d in self.docs.values())
        return IndexStats(len(self.docs), size, vectors)

    async def close(self) -> None:
        return None
