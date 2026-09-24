"""Publish the prompt agents to Foundry, creating a version only when the definition hash changed.

Prints ``AGENT_VERSIONS`` JSON and also writes it to ``--out`` and, in GitHub Actions, ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol

from azure.core.exceptions import ResourceNotFoundError

from app.agents import knowledge, plan_claims
from app.agents.base import AgentSpec
from app.config import Settings, get_settings

HASH_KEY = "definition_sha256"
AGENTS: tuple[AgentSpec, ...] = (plan_claims.AGENT, knowledge.AGENT)


class AgentsOperations(Protocol):
    def get(self, agent_name: str, **kwargs: Any) -> Awaitable[Any]: ...

    def create_version(self, agent_name: str, *args: Any, **kwargs: Any) -> Awaitable[Any]: ...


def agent_by_key(key: str) -> AgentSpec:
    return next(a for a in AGENTS if a.key == key)


async def _latest(agents: AgentsOperations, name: str) -> Any | None:
    try:
        details = await agents.get(name)
    except ResourceNotFoundError:
        return None
    return details.versions.latest


async def publish(agents: AgentsOperations, settings: Settings, *, dry_run: bool = False) -> dict[str, str]:
    versions: dict[str, str] = {}
    for spec in AGENTS:
        digest = spec.definition_hash(settings)
        latest = await _latest(agents, spec.name)
        if latest is not None and (latest.metadata or {}).get(HASH_KEY) == digest:
            versions[spec.name] = str(latest.version)
            print(f"{spec.name}: unchanged at version {latest.version}", file=sys.stderr)
            continue
        if dry_run:
            versions[spec.name] = "pending"
            print(f"{spec.name}: definition changed (dry run, not published)", file=sys.stderr)
            continue
        created = await agents.create_version(
            spec.name,
            definition=spec.definition(settings),
            description=spec.description,
            metadata={HASH_KEY: digest, "publisher": "benefura-deploy"},
        )
        versions[spec.name] = str(created.version)
        print(f"{spec.name}: published version {created.version}", file=sys.stderr)
    return versions


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="Write AGENT_VERSIONS JSON to this file.")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without creating versions.")
    args = parser.parse_args(argv)

    from app.services.foundry import close_clients, get_project_client

    settings = get_settings()
    try:
        versions = await publish(get_project_client().agents, settings, dry_run=args.dry_run)
    finally:
        await close_clients()

    payload = json.dumps(versions, sort_keys=True, separators=(",", ":"))
    print(payload)
    if args.out:
        args.out.write_text(payload + "\n")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output and not args.dry_run:
        _append_line(Path(github_output), f"agent_versions={payload}")
    return 0


def _append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
