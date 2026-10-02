# 0011: Private networking as an optional flag

**Status:** Accepted

## Context
Production deployments handling member data should not expose Foundry or Storage publicly. The demo
uses AI Search Free and Static Web Apps Free, neither of which supports private endpoints, and the
cost of a VNet-integrated Container Apps environment isn't justified for a demo.

## Decision
`infra/modules/network.bicep` is gated by `enablePrivateNetworking` (default `false`). When enabled it
creates a VNet, integrates the Container Apps environment, adds private endpoints and private DNS
zones for Foundry and Storage, and sets `publicNetworkAccess: Disabled`. CI builds it with the flag
on so it stays valid.

## Consequences
The production path is visible and tested for compilation. A production deployment would also move
AI Search to Basic or higher with a private endpoint and serve the web app from a tier that supports
private networking.
