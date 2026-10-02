# 0005: Push-model ingestion

**Status:** Accepted

## Context
AI Search indexers with a skillset (Document Layout skill, embedding skill, integrated vectorizer)
would be the low-code option. On the Free tier the search service has no outbound managed identity,
and the embedding skill would need an API key, but local auth is disabled on the Foundry account.

## Decision
`knowledge/ingest` fetches sources, converts HTML to markdown and PDFs through Content Understanding
layout, chunks by heading (~800 tokens with overlap), enriches with gpt-5-nano tags, embeds, and
pushes documents with the `azure-search-documents` SDK using Entra ID. A weekly GitHub Actions run
keeps content fresh and the Free service active, and publishes an ingestion report.

## Consequences
More code to own, but keyless end to end and fully testable offline. On Basic tier with a managed
identity, switching to indexer + Document Layout skill + integrated vectorizer is a configuration
change: the index schema stays the same.
