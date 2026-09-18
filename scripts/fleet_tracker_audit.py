#!/usr/bin/env python3
"""One-shot audit for fleet repo GitHub tracker label conformance."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tomllib

from tracker_labels import UMBRELLA_TRACKER_URL, list_tracker_labels, load_label_fixture, repo_label


def documentation_mentions(repo: pathlib.Path, name: str) -> tuple[bool, bool]:
    tracker_ok = False
    label_ok = False
    for rel in ("README.md", "AGENTS.md", ".averagechris-project.toml"):
        path = repo / rel
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(errors="replace")
        tracker_ok = tracker_ok or UMBRELLA_TRACKER_URL in text
        label_ok = label_ok or repo_label(name) in text
    return tracker_ok, label_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", help="limit to specific fleet repo name(s)")
    parser.add_argument("--labels-json", type=pathlib.Path, help="fixture JSON from `gh label list --json name`; avoids network")
    parser.add_argument("--gh-bin", default="gh")
    args = parser.parse_args(argv)

    registry = pathlib.Path(os.environ.get("FLEET_REGISTRY", pathlib.Path(__file__).resolve().parent.parent / "fleet.toml"))
    config = tomllib.loads(registry.read_text())
    labels = load_label_fixture(args.labels_json) if args.labels_json else list_tracker_labels(gh_bin=args.gh_bin)
    worst = 0
    print(f"umbrella tracker: {UMBRELLA_TRACKER_URL}")
    for entry in config["repos"]:
        name = entry["name"]
        if args.repo and name not in args.repo:
            continue
        repo = pathlib.Path(entry["local"])
        expected = repo_label(name)
        print(f"\n{name} ({repo})")
        if not repo.is_dir():
            print("  fail  missing local checkout")
            worst = max(worst, 2)
            continue
        tracker_doc, label_doc = documentation_mentions(repo, name)
        for ok, message in (
            (tracker_doc, "documents umbrella tracker URL"),
            (label_doc, f"documents label {expected}"),
            (expected in labels, f"umbrella tracker has label {expected}"),
        ):
            print(f"  {'ok  ' if ok else 'fail'}  {message}")
            if not ok:
                worst = max(worst, 2)
        near = sorted(label for label in labels if label.startswith("repo:") and label.split(":", 1)[1] in {entry.get("srht_repo"), entry.get("pages_subdir")})
        if near and expected not in near:
            print(f"  warn  related repo:* labels do not match fleet name convention: {', '.join(near)}")
            worst = max(worst, 1)
    return 0 if worst < 2 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
