from __future__ import annotations

import pytest
from pydantic import ValidationError

from knowledge.ingest.chunk import Chunk
from knowledge.ingest.sources import (
    EXCERPT_MAX_CHUNKS,
    EXCERPT_MAX_TOKENS,
    Manifest,
    Source,
    apply_licence,
    bounded_excerpt,
    load_manifest,
    select_sources,
)
from knowledge.ingest.tokens import count_tokens


def make_source(**overrides) -> Source:
    data = {
        "id": "test-source",
        "url": "https://example.org/page",
        "region": "CA",
        "title": "Test page",
        "publisher": "Tests",
        "licence": "Synthetic",
        "licence_note": "Synthetic test content only.",
        "attribution": "Synthetic fixture.",
        "reproduction": "verbatim",
        "index": True,
    }
    data.update(overrides)
    return Source.model_validate(data)


def chunks(n: int, sentences: int = 80) -> list[Chunk]:
    text = " ".join(f"Sentence number {i} explains a rule about claims and coverage." for i in range(sentences))
    return [Chunk(i, "Section", text, count_tokens(text)) for i in range(n)]


def test_real_manifest_is_valid_and_has_both_regions():
    manifest = load_manifest()
    regions = {s.region for s in manifest.sources}
    assert regions == {"CA", "AU"}
    indexable, link_only = select_sources(manifest)
    assert len(indexable) >= 15
    assert all(s.licence_note and s.attribution for s in manifest.sources)


def test_manifest_policy_link_only_sources():
    by_id = {s.id: s for s in load_manifest().sources}
    for source_id in (
        "bc-medical-services-plan",
        "clhia-guide-supplementary-health-insurance",
        "services-australia-medicare-safety-nets",
    ):
        assert by_id[source_id].index is False, source_id


def test_manifest_policy_ontario_is_verbatim_and_privatehealth_is_cc_by_au():
    manifest = load_manifest()
    for s in manifest.sources:
        if "ontario.ca" in str(s.url):
            assert s.reproduction == "verbatim"
        if "privatehealth.gov.au" in str(s.url):
            assert s.licence == "CC BY 3.0 AU"
            assert str(s.licence_url) == "https://creativecommons.org/licenses/by/3.0/au/"
            assert "Creative Commons" in s.attribution


def test_select_sources_filters_and_rejects_unknown_ids():
    manifest = Manifest(
        sources=[make_source(id="aaa-one"), make_source(id="bbb-two", url="https://example.org/2", index=False)]
    )
    indexable, link_only = select_sources(manifest)
    assert [s.id for s in indexable] == ["aaa-one"]
    assert [s.id for s in link_only] == ["bbb-two"]
    indexable, link_only = select_sources(manifest, ["bbb-two"])
    assert indexable == [] and [s.id for s in link_only] == ["bbb-two"]
    with pytest.raises(KeyError):
        select_sources(manifest, ["nope"])


@pytest.mark.parametrize(
    "overrides",
    [
        {"url": "http://example.org/insecure"},
        {"id": "Bad Id"},
        {"region": "US"},
        {"reproduction": "summary"},
        {"index": False, "fixture": "x.html"},
        {"unexpected": "field"},
    ],
)
def test_invalid_sources_rejected(overrides):
    with pytest.raises(ValidationError):
        make_source(**overrides)


def test_duplicate_ids_and_urls_rejected():
    with pytest.raises(ValidationError):
        Manifest(sources=[make_source(), make_source(url="https://example.org/other")])
    with pytest.raises(ValidationError):
        Manifest(sources=[make_source(), make_source(id="other-id")])


def test_link_only_sources_never_yield_chunks():
    assert apply_licence(make_source(index=False), chunks(3)) == []


def test_verbatim_sources_are_unchanged():
    original = chunks(30)
    assert apply_licence(make_source(reproduction="verbatim"), original) == original


def test_excerpt_sources_store_bounded_excerpts_only():
    original = chunks(EXCERPT_MAX_CHUNKS + 5)
    limited = apply_licence(make_source(reproduction="excerpt"), original)
    assert len(limited) == EXCERPT_MAX_CHUNKS
    for before, after in zip(original, limited, strict=False):
        assert after.tokens <= EXCERPT_MAX_TOKENS
        assert after.content.endswith("…")
        assert before.content.startswith(after.content.removesuffix(" …"))


def test_bounded_excerpt_keeps_short_text_and_cuts_at_sentence_or_line():
    assert bounded_excerpt("Short text.", 50) == "Short text."
    text = "First sentence here. Second sentence here.\n| a | b |\n| 1 | 2 |"
    cut = bounded_excerpt(text, 6)
    assert cut == "First sentence here. …"
    no_punctuation = " ".join(["word"] * 500)
    cut = bounded_excerpt(no_punctuation, 40)
    assert count_tokens(cut) <= 40 and cut.endswith("…")
