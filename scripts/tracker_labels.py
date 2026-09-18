#!/usr/bin/env python3
"""GitHub issue-label helpers for the shared fleet tracker."""

from __future__ import annotations

import json
import pathlib
import re
import subprocess

UMBRELLA_TRACKER_URL = "https://github.com/averagechris/fleet/issues"
UMBRELLA_TRACKER = "averagechris/fleet"
REPO_LABEL_COLOR = "268bd2"


def repo_label(name: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
        raise ValueError(f"invalid repo label name source: {name!r}")
    return f"repo:{name}"


def label_names_from_json(text: str) -> set[str]:
    payload = json.loads(text or "[]")
    if isinstance(payload, dict):
        for key in ("items", "results", "labels", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
    if not isinstance(payload, list):
        raise ValueError("GitHub labels JSON was not a list or known envelope")
    return {
        item if isinstance(item, str) else item["name"]
        for item in payload
        if isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("name"), str))
    }


def list_tracker_labels(*, gh_bin: str = "gh", tracker: str = UMBRELLA_TRACKER) -> set[str]:
    result = subprocess.run(
        [gh_bin, "label", "list", "--repo", tracker, "--limit", "1000", "--json", "name"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "gh label list failed")
    return label_names_from_json(result.stdout)


def ensure_repo_label(
    name: str,
    *,
    gh_bin: str = "gh",
    tracker: str = UMBRELLA_TRACKER,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Ensure repo:<name> exists. Returns (created, label)."""
    label = repo_label(name)
    if label in list_tracker_labels(gh_bin=gh_bin, tracker=tracker):
        return False, label
    if dry_run:
        return False, label
    result = subprocess.run(
        [gh_bin, "label", "create", label, "--repo", tracker, "--color", REPO_LABEL_COLOR,
         "--description", f"Issues for the {name} fleet project"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        if label in list_tracker_labels(gh_bin=gh_bin, tracker=tracker):
            return False, label
        raise RuntimeError(result.stderr.strip() or "gh label create failed")
    return True, label


def load_label_fixture(path: pathlib.Path) -> set[str]:
    return label_names_from_json(path.read_text())
