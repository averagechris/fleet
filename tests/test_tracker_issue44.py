from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import fleet_tracker_audit  # noqa: E402
import new_project  # noqa: E402
import tracker_labels  # noqa: E402


class TrackerLabelTests(unittest.TestCase):
    def test_label_json_shapes(self) -> None:
        self.assertEqual(tracker_labels.label_names_from_json('[{"name":"repo:demo"}]'), {"repo:demo"})
        self.assertEqual(tracker_labels.label_names_from_json('{"items":[{"name":"repo:demo"}]}'), {"repo:demo"})
        self.assertEqual(tracker_labels.label_names_from_json('{"results":[{"name":"repo:demo"}]}'), {"repo:demo"})

    @mock.patch("subprocess.run")
    def test_ensure_label_idempotent(self, run: mock.Mock) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, '[{"name":"repo:demo"}]', "")
        created, label = tracker_labels.ensure_repo_label("demo", gh_bin="fake-gh")
        self.assertFalse(created)
        self.assertEqual(label, "repo:demo")
        self.assertEqual(run.call_count, 1)

    @mock.patch("subprocess.run")
    def test_ensure_label_creates_when_missing(self, run: mock.Mock) -> None:
        run.side_effect = [
            subprocess.CompletedProcess([], 0, "[]", ""),
            subprocess.CompletedProcess([], 0, '{"name":"repo:demo"}', ""),
        ]
        created, label = tracker_labels.ensure_repo_label("demo", gh_bin="fake-gh")
        self.assertTrue(created)
        self.assertEqual(label, "repo:demo")
        self.assertIn("create", run.call_args_list[1].args[0])


class NewProjectTrackerTests(unittest.TestCase):
    def test_generated_docs_include_tracker_and_label(self) -> None:
        args = new_project.parse_args(["demo-cli", "--no-tracker-label"])
        for content in (
            new_project.readme(args),
            new_project.agents_md(args),
            new_project.enrollment_manifest(args),
            new_project.docs_page(args, "overview.html"),
        ):
            self.assertIn(tracker_labels.UMBRELLA_TRACKER_URL, content)
            self.assertIn("repo:demo-cli", content)

    @mock.patch("new_project.command_exists", return_value=True)
    @mock.patch("new_project.ensure_repo_label", return_value=(True, "repo:demo-cli"))
    def test_new_project_ensures_label(self, ensure: mock.Mock, _exists: mock.Mock) -> None:
        args = new_project.parse_args(["demo-cli"])
        log = new_project.ActionLog(dry_run=False)
        new_project.ensure_tracker_label(args, log)
        ensure.assert_called_once_with("demo-cli", gh_bin="gh", dry_run=False)


class FleetTrackerAuditTests(unittest.TestCase):
    def test_audit_helpers_with_fixture_do_not_call_github(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "demo-cli"
            repo.mkdir()
            (repo / "README.md").write_text(
                f"{tracker_labels.UMBRELLA_TRACKER_URL}\nrepo:demo-cli\n"
            )
            labels = root / "labels.json"
            labels.write_text('[{"name":"repo:demo-cli"}]')
            with mock.patch.object(fleet_tracker_audit, "list_tracker_labels") as live:
                self.assertEqual(tracker_labels.load_label_fixture(labels), {"repo:demo-cli"})
                self.assertEqual(fleet_tracker_audit.documentation_mentions(repo, "demo-cli"), (True, True))
                live.assert_not_called()


if __name__ == "__main__":
    unittest.main()
