#!/usr/bin/env python3
"""Render or check the website-facing projection of the fleet registry."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import tomllib

FIELDS = ("name", "pages_subdir", "srht_repo", "artifact_prefix", "binaries", "provider", "github_repo")


def project(config: dict) -> list[dict]:
    rows = []
    for repo in config["repos"]:
        row = {field: repo[field] for field in FIELDS if field in repo}
        provider = row.get("provider", "sourcehut")
        required = ("name", "pages_subdir", "artifact_prefix")
        required += (("github_repo",) if provider == "github" else ("srht_repo",))
        missing = [field for field in required if field not in row]
        if missing:
            raise ValueError(f"{repo.get('name', '<unnamed>')}: missing projection fields: {', '.join(missing)}")
        if provider not in ("sourcehut", "github"):
            raise ValueError(f"{repo.get('name', '<unnamed>')}: unsupported provider: {provider}")
        if provider == "github" and (not isinstance(row["github_repo"], str) or row["github_repo"].count("/") != 1):
            raise ValueError(f"{repo.get('name', '<unnamed>')}: github_repo must be owner/repository")
        rows.append(row)
    return sorted(rows, key=lambda row: row["pages_subdir"])


def render(rows: list[dict]) -> str:
    lines = [
        "# Generated site-facing projection of averagechris/fleet/fleet.toml.",
        "# Regenerate with: nix run github:averagechris/fleet#site-registry -- --output fleet-site.toml",
        "# Operational fields intentionally do not belong in this repository.",
        "",
    ]
    for row in rows:
        lines.append("[[repos]]")
        for field in FIELDS:
            if field in row:
                lines.append(f"{field} = {json.dumps(row[field])}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=pathlib.Path)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--check", type=pathlib.Path)
    args = parser.parse_args(argv)
    root = pathlib.Path(__file__).resolve().parent.parent
    registry = args.registry or pathlib.Path(os.environ.get("FLEET_REGISTRY", root / "fleet.toml"))
    content = render(project(tomllib.loads(registry.read_text())))
    if args.check:
        if args.check.read_text() != content:
            raise SystemExit(f"{args.check} is stale; regenerate it with site-registry")
        print(f"ok: {args.check}")
    elif args.output:
        args.output.write_text(content)
        print(args.output)
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
