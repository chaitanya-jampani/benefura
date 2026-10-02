# 0006: Regions and data residency

**Status:** Accepted for the demo. Quota and Search creation pending M0 spikes 8 and 9.

## Context
The demo needs gpt-5-mini, gpt-5-nano, text-embedding-3-small, Content Understanding, Language,
Content Safety and AI Search with the free semantic ranker, on one keyless AIServices account at
minimal cost. As of September 2026, Microsoft Learn marks East US 2 as high demand and blocks new
AI Search services there. The semantic ranker on the Free tier is region-gated; East US 2 and
Canada Central both qualify.

## Decision
- All Foundry and AI services, Container Apps, Storage and monitoring in **East US 2**, with Global
  Standard model deployments.
- AI Search: reuse an existing Free service if the subscription has one (only one is allowed per
  subscription), otherwise create it in **Canada Central**. It holds public content only;
  cross-region calls use RBAC.

## Production residency
For real members, deploy per market: Canada Central for Canada and Australia East for Australia,
with Data Zone or regional deployments where the models are offered, Content Understanding and
Language in the same geography, private endpoints ([ADR 0011](0011-private-networking-flag.md)), and
App Insights in-region. Global Standard deployments may process data in any Azure region, so they
would not be used for member content.
