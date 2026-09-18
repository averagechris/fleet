#!/usr/bin/env python3
"""Report fleet conformance to the standard release interface.

Checks each repo in fleet.toml for: repo presence, dirty working copy,
version vs latest tag, release manifest placement, CHANGELOG, upstream
remote, unmerged maint workspaces, and (with --nix) required flake apps.

Usage:
    fleet_status.py [--nix] [--repo NAME ...]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import platform
import subprocess
import tomllib

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def run(args: list[str], cwd: str | pathlib.Path | None = None) -> str | None:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def latest_tag(repo: pathlib.Path) -> str | None:
    out = run(["git", "tag", "--list", "v[0-9]*", "--sort=-v:refname"], cwd=repo)
    if not out:
        return None
    return out.splitlines()[0]


def nix_system() -> str:
    machine = {"arm64": "aarch64", "x86_64": "x86_64"}.get(platform.machine(), platform.machine())
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system(), platform.system().lower())
    return f"{machine}-{system}"


def repo_ecosystem(entry: dict) -> str:
    """Return repo ecosystem; omitted means rust for backward compatibility."""
    return entry.get("ecosystem", "rust")


def required_apps(entry: dict, config: dict) -> list[str]:
    fleet = config["fleet"]
    ecosystem = repo_ecosystem(entry)
    ecosystems = fleet.get("ecosystems", {})
    extras = ecosystems.get(ecosystem, {}).get("required_apps", [])
    return [*fleet["required_apps"], *extras]


def read_version(version_file: pathlib.Path) -> str | None:
    if version_file.suffix == ".json":
        manifest = json.loads(version_file.read_text())
        version = manifest.get("version")
    else:
        manifest = tomllib.loads(version_file.read_text())
        package = manifest.get("package") or manifest.get("workspace", {}).get("package") or {}
        version = package.get("version")
    return version if isinstance(version, str) else None


def check_repo(entry: dict, config: dict, use_nix: bool) -> list[tuple[str, str]]:
    """Return list of (level, message) where level is ok|warn|fail."""
    results: list[tuple[str, str]] = []
    repo = pathlib.Path(entry["local"])
    if not repo.is_dir():
        return [("fail", f"missing local checkout at {repo}")]

    # dirty working copy?
    wc = run(["jj", "log", "-r", "@", "--no-graph", "-T", 'if(empty, "clean", "dirty")'], cwd=repo)
    if wc == "dirty":
        results.append(("warn", "working copy has uncommitted changes"))
    elif wc is None:
        results.append(("warn", "could not read jj working copy state"))

    # unmerged maint workspace?
    workspaces = run(["jj", "workspace", "list"], cwd=repo) or ""
    if any(line.startswith("maint:") for line in workspaces.splitlines()):
        results.append(("warn", "maint workspace exists (unmerged maintenance work?)"))

    # version vs tag
    version_file = repo / entry["version_file"]
    version = None
    if version_file.exists():
        version = read_version(version_file)
        if version is None:
            results.append(("fail", f"no package version in {entry['version_file']}"))
    else:
        results.append(("fail", f"version file {entry['version_file']} missing"))
    tag = latest_tag(repo)
    if version and tag:
        if f"v{version}" == tag:
            results.append(("ok", f"version {version} == latest tag {tag}"))
        else:
            results.append(("warn", f"version {version} != latest tag {tag} (unreleased work?)"))
    elif version:
        results.append(("warn", f"version {version} but no v* tags found"))

    # release manifest placement
    if (repo / ".builds" / "release-linux-x86_64.yml").exists():
        results.append(("fail", "release manifest in .builds/ (auto-runs on every push); move to builds/"))
    elif (repo / "builds" / "release-linux-x86_64.yml").exists():
        results.append(("ok", "release manifest in builds/ (explicit submit only)"))
    else:
        results.append(("warn", "no builds/release-linux-x86_64.yml"))

    # CHANGELOG
    changelog = repo / "CHANGELOG.md"
    if not changelog.exists():
        results.append(("fail", "CHANGELOG.md missing"))
    elif "## Unreleased" not in changelog.read_text():
        results.append(("warn", "CHANGELOG.md has no '## Unreleased' section"))

    # upstream remote
    expected_upstream = entry.get("upstream")
    remotes = run(["git", "remote", "get-url", "upstream"], cwd=repo)
    if expected_upstream and not remotes:
        results.append(("warn", f"upstream remote not configured (expected {expected_upstream})"))
    elif expected_upstream and remotes and expected_upstream.removesuffix(".git") not in remotes.removesuffix(".git"):
        results.append(("warn", f"upstream remote {remotes} != fleet.toml {expected_upstream}"))

    # required flake apps
    if use_nix:
        out = run(["nix", "flake", "show", "--json", "--quiet"], cwd=repo)
        if out is None:
            results.append(("fail", "nix flake show failed"))
        else:
            apps = set(json.loads(out).get("apps", {}).get(nix_system(), {}))
            missing = [app for app in required_apps(entry, config) if app not in apps]
            if missing:
                results.append(("fail", f"missing flake apps: {', '.join(missing)}"))
            else:
                results.append(("ok", f"all required flake apps present ({repo_ecosystem(entry)})"))
            advisory_missing = [app for app in config["fleet"].get("advisory_apps", []) if app not in apps]
            if advisory_missing:
                results.append(("warn", f"missing advisory flake apps: {', '.join(advisory_missing)} (pending fleet-lib lock bump)"))

    return results


def check_host() -> list[tuple[str, str]]:
    """Host-level health checks shared by every fleet repo."""
    results: list[tuple[str, str]] = []
    if run(["sccache", "--version"]) is None:
        results.append(("warn", "sccache not on PATH (RUSTC_WRAPPER builds will fail)"))
        return results
    if run(["sccache", "cc", "--version"]) is None:
        results.append(
            (
                "fail",
                "sccache cannot drive the C compiler (poisoned server daemon?); "
                "run `sccache --stop-server` and re-check",
            )
        )
    else:
        results.append(("ok", "sccache compiles C (server healthy)"))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nix", action="store_true", help="also verify required flake apps (slower)")
    parser.add_argument("--repo", action="append", help="limit to specific repo name(s)")
    args = parser.parse_args()

    registry = pathlib.Path(os.environ.get("FLEET_REGISTRY", pathlib.Path(__file__).resolve().parent.parent / "fleet.toml"))
    config = tomllib.loads(registry.read_text())

    color = {"ok": GREEN, "warn": YELLOW, "fail": RED}
    worst = 0
    print("host")
    for level, message in check_host():
        print(f"  {color[level]}{level:4}{RESET}  {message}")
        worst = max(worst, {"ok": 0, "warn": 1, "fail": 2}[level])
    for entry in config["repos"]:
        if args.repo and entry["name"] not in args.repo:
            continue
        print(f"\n{entry['name']}  ({entry['local']})")
        for level, message in check_repo(entry, config, args.nix):
            print(f"  {color[level]}{level:4}{RESET}  {message}")
            worst = max(worst, {"ok": 0, "warn": 1, "fail": 2}[level])
        for quirk in entry.get("quirks", []):
            print(f"  note  {quirk}")
    raise SystemExit(0 if worst < 2 else 1)


if __name__ == "__main__":
    main()
