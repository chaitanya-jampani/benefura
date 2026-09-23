from __future__ import annotations

import copy

import pytest

from knowledge.ingest.push import (
    EXPECTED_FIELDS,
    MemoryIndexStore,
    build_search_index,
    chunk_id,
    load_index_schema,
    validate_index_schema,
)


def test_committed_schema_is_valid():
    assert validate_index_schema(load_index_schema()) == []


def test_schema_builds_sdk_search_index():
    index = build_search_index(load_index_schema())
    assert index.name == "public-knowledge-v1"
    fields = {f.name: f for f in index.fields}
    assert set(fields) == set(EXPECTED_FIELDS)
    assert fields["id"].key is True
    assert fields["region"].filterable is True
    assert fields["topics"].facetable is True and fields["topics"].filterable is True
    vector = fields["contentVector"]
    assert vector.vector_search_dimensions == 512
    assert vector.vector_search_profile_name == "hnsw-512"
    profile = index.vector_search.profiles[0]
    algorithm = next(a for a in index.vector_search.algorithms if a.name == profile.algorithm_configuration_name)
    assert algorithm.kind == "hnsw"
    semantic = index.semantic_search.configurations[0].prioritized_fields
    assert semantic.title_field.field_name == "title"
    assert [f.field_name for f in semantic.content_fields][0] == "content"
    assert {f.field_name for f in semantic.keywords_fields} == {"topics", "categories"}
    assert [f["name"] for f in index.as_dict()["fields"]] == list(EXPECTED_FIELDS)


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (lambda s: s["fields"].pop(1), "missing field content"),
        (lambda s: s["fields"][11].update(dimensions=1536), "512 dimensions"),
        (lambda s: s["fields"][4].update(filterable=False), "region must be filterable"),
        (lambda s: s["fields"][9].update(facetable=False), "topics must be facetable"),
        (lambda s: s["fields"][11].update(vectorSearchProfile="missing"), "profile not defined"),
        (lambda s: s["semantic"].update(defaultConfiguration="nope"), "semantic defaultConfiguration missing"),
        (lambda s: s["fields"].append({"name": "audience", "type": "Edm.String"}), "unexpected fields"),
        (lambda s: s["fields"][3].update(key=True), "only key field"),
    ],
)
def test_schema_validation_catches_problems(mutate, problem):
    schema = copy.deepcopy(load_index_schema())
    mutate(schema)
    problems = validate_index_schema(schema)
    assert any(problem in p for p in problems), problems
    with pytest.raises(ValueError):
        build_search_index(schema)


def test_chunk_ids_are_content_addressed_and_key_safe():
    a = chunk_id("phio-waiting-periods", "Waiting periods", "text")
    assert a == chunk_id("phio-waiting-periods", "Waiting periods", "text")
    assert a != chunk_id("phio-waiting-periods", "Waiting periods", "text changed")
    assert a != chunk_id("phio-waiting-periods", "Other section", "text")
    assert a.startswith("phio-waiting-periods-") and len(a) == len("phio-waiting-periods-") + 20


async def test_memory_store_rejects_fields_not_in_the_index():
    store = MemoryIndexStore()
    await store.ensure_index(load_index_schema())
    with pytest.raises(ValueError):
        await store.upload([{"id": "x", "content": "c", "audience": "members"}])
