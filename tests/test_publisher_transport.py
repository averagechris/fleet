"""Run the actual workflow shell against isolated API state and artifact bytes."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
OBJECT = "07d282b8d769d1de1729a26b16ef93ca8636fb70"
COMMIT = "eebbd2924740d70282604b161c66a697f7031780"
PLATFORMS = json.dumps([{"name": p, "runner": "fixture"} for p in
                        ("aarch64-darwin", "x86_64-linux")])


def shell(source):
    text = source.split("      - name: Verify complete set and publish draft\n", 1)[1]
    text = text.split("      - name: Verify website dispatch trust\n", 1)[0]
    text = text.split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in text.splitlines()) + "\n"


class PublisherTransportTests(unittest.TestCase):
    def run_case(self, scenario):
        source = (ROOT / ".github/workflows/release.yml").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            assets = work / "assets"
            assets.mkdir()
            for platform in ("aarch64-darwin", "x86_64-linux"):
                name = f"gander-v0.8.3-{platform}.tar.gz"
                data = f"archive {platform}\n".encode()
                (assets / name).write_bytes(data)
                (assets / f"{name}.sha256").write_text(
                    f"{hashlib.sha256(data).hexdigest()}  {name}\n")
                (assets / f"release-identity-{platform}").write_text(
                    f"{OBJECT}\n{COMMIT}\n")
            expected = {p.name: p.read_bytes().hex() for p in assets.iterdir()
                        if not p.name.startswith("release-identity-")}
            state = {"calls": [], "exists": scenario != "absent", "draft": True,
                     "assets": {}, "asset_states": {}, "release_tag": "v0.8.3"}
            if scenario == "partial":
                state["assets"][next(iter(expected))] = next(iter(expected.values()))
            if scenario in ("published", "mismatch", "draft_mismatch", "incomplete"):
                state.update(draft=False, assets=expected.copy())
            if scenario == "draft_mismatch":
                state["draft"] = True
            if scenario in ("mismatch", "draft_mismatch"):
                first = next(iter(expected))
                state["assets"][first] = b"different bytes".hex()
            if scenario == "incomplete":
                state["assets"].pop(next(iter(expected)))
            if scenario == "open":
                state["assets"] = expected.copy()
                state["asset_states"][next(iter(expected))] = "open"
            if scenario == "empty_after_create":
                state.update(exists=False, empty_after_create=True)
            if scenario == "empty_after_upload":
                state["empty_after_upload"] = True
            if scenario in ("auth", "network"):
                state["api_error"] = scenario
            if scenario == "duplicate":
                state["duplicate"] = True
            state_path = work / "state.json"
            state_path.write_text(json.dumps(state))
            bin_dir = work / "bin"
            bin_dir.mkdir()
            transport = ROOT / "tests/publisher_transport.py"
            for tool in ("gh", "git", "curl", "find"):
                (bin_dir / tool).symlink_to(transport)
            run = subprocess.run(["bash", "-e", "-c", shell(source)], cwd=work,
                                 text=True, capture_output=True,
                                 env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                                                   "PUBLISHER_STATE": str(state_path),
                                                   "TAG": "v0.8.3", "PLATFORMS": PLATFORMS,
                                                   "GITHUB_REPOSITORY": "averagechris/gander",
                                                   "GH_TOKEN": "offline-only"})
            return run, json.loads(state_path.read_text()), expected

    def test_scenarios(self):
        for scenario in ("absent", "empty", "partial", "published", "mismatch",
                         "draft_mismatch", "incomplete", "open", "auth", "network",
                         "duplicate", "empty_after_create", "empty_after_upload"):
            with self.subTest(scenario=scenario):
                run, state, expected = self.run_case(scenario)
                calls = state["calls"]
                mutations = [c for c in calls if c[:2] in
                             (["gh", "release"],)]
                if scenario in ("mismatch", "draft_mismatch", "incomplete", "auth", "network", "duplicate"):
                    self.assertNotEqual(run.returncode, 0, run.stderr)
                    self.assertEqual(mutations, [], calls)
                elif scenario == "open":
                    self.assertNotEqual(run.returncode, 0, run.stderr)
                    self.assertIn("delete the incomplete asset(s) on GitHub and rerun", run.stderr)
                    self.assertIn(next(iter(expected)), run.stderr)
                    self.assertEqual(mutations, [], calls)
                    self.assertEqual(state["assets"], expected)
                    self.assertTrue(state["draft"])
                elif scenario == "empty_after_create":
                    self.assertNotEqual(run.returncode, 0, run.stderr)
                    self.assertIn("release not found after create", run.stderr)
                    self.assertEqual(len(mutations), 1)
                    self.assertEqual(mutations[0][:3], ["gh", "release", "create"])
                    self.assertFalse(any(c[:3] == ["gh", "release", "upload"] for c in calls))
                    self.assertFalse(any(c[:3] == ["gh", "release", "edit"] for c in calls))
                elif scenario == "empty_after_upload":
                    self.assertNotEqual(run.returncode, 0, run.stderr)
                    self.assertIn("release not found after upload", run.stderr)
                    self.assertGreater(state.get("upload_count", 0), 0)
                    self.assertFalse(any(c[:3] == ["gh", "release", "edit"] for c in calls))
                else:
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertEqual(state["assets"], expected)
                    self.assertFalse(state["draft"])
                    edits = [i for i, c in enumerate(calls) if c[:3] == ["gh", "release", "edit"]]
                    uploads = [i for i, c in enumerate(calls) if c[:3] == ["gh", "release", "upload"]]
                    if scenario == "published":
                        self.assertFalse(edits or uploads)
                    else:
                        self.assertEqual(len(edits), 1)
                        self.assertTrue(not uploads or max(uploads) < edits[0])
                        self.assertTrue(any(c[:2] == ["curl", "-fsSL"] for c in calls[:edits[0]]))
                    if scenario == "absent":
                        creates = [i for i, c in enumerate(calls) if c[:3] == ["gh", "release", "create"]]
                        self.assertEqual(len(creates), 1)
                        self.assertTrue(all(i > creates[0] for i in uploads))


if __name__ == "__main__":
    unittest.main()
