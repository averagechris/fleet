"""Executable contracts for the GitHub registry projection."""

import importlib.util
from pathlib import Path
import tomllib
import unittest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "registry_projection", ROOT / "scripts/registry_projection.py"
)
assert SPEC and SPEC.loader
projection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(projection)


class GithubProjectionContract(unittest.TestCase):
    def test_two_sourcehut_rows_project_without_new_fields(self) -> None:
        config = tomllib.loads((ROOT / "fleet.toml").read_text())
        rows = [row for row in projection.project(config) if row.get("provider", "sourcehut") == "sourcehut"]
        self.assertEqual(len(rows), 2)
        rendered = projection.render(rows)
        self.assertNotIn("provider =", rendered)
        self.assertNotIn("github_repo =", rendered)
        self.assertNotIn("expected_platforms =", rendered)
        self.assertNotIn("sourcehut_through =", rendered)

    def test_github_row_round_trips_transport_fields(self) -> None:
        config = {"repos": [{
            "name": "gander", "pages_subdir": "gander",
            "artifact_prefix": "gander-v", "binaries": ["gander"],
            "provider": "github", "github_repo": "averagechris/gander",
            "expected_platforms": ["aarch64-darwin", "x86_64-linux"],
        }]}
        rows = projection.project(config)
        self.assertEqual(rows[0]["provider"], "github")
        self.assertEqual(rows[0]["github_repo"], "averagechris/gander")
        self.assertEqual(rows[0]["expected_platforms"], ["aarch64-darwin", "x86_64-linux"])
        rendered = projection.render(rows)
        self.assertIn('provider = "github"', rendered)
        self.assertIn('github_repo = "averagechris/gander"', rendered)
        self.assertIn('expected_platforms = ["aarch64-darwin", "x86_64-linux"]', rendered)

    def test_github_row_round_trips_sourcehut_history_boundary(self) -> None:
        row = {
            "name": "gander", "pages_subdir": "gander", "srht_repo": "gander",
            "artifact_prefix": "gander-v", "provider": "github",
            "github_repo": "averagechris/gander", "expected_platforms": ["x86_64-linux"],
            "sourcehut_through": "v0.8.1",
        }
        projected = projection.project({"repos": [row]})[0]
        self.assertEqual(projected["sourcehut_through"], "v0.8.1")
        self.assertIn('sourcehut_through = "v0.8.1"', projection.render([projected]))

    def test_sourcehut_history_boundary_is_optional_and_github_only(self) -> None:
        base = {
            "name": "gander", "pages_subdir": "gander", "artifact_prefix": "gander-v",
            "provider": "github", "github_repo": "averagechris/gander",
            "expected_platforms": ["x86_64-linux"],
        }
        self.assertNotIn("sourcehut_through", projection.project({"repos": [base]})[0])
        with self.assertRaisesRegex(ValueError, "requires srht_repo"):
            projection.project({"repos": [base | {"sourcehut_through": "v0.8.1"}]})
        for boundary in ("0.8", "v0.8.1-rc.1", "v0.8.1+build.1", "v00.8.1"):
            with self.subTest(boundary=boundary), self.assertRaisesRegex(ValueError, "v-prefixed semver"):
                projection.project({"repos": [base | {"srht_repo": "gander", "sourcehut_through": boundary}]})
        sourcehut = base | {"provider": "sourcehut", "srht_repo": "gander", "sourcehut_through": "v0.8.1"}
        sourcehut.pop("github_repo")
        sourcehut.pop("expected_platforms")
        with self.assertRaisesRegex(ValueError, "only valid for the github provider"):
            projection.project({"repos": [sourcehut]})

    def test_github_row_requires_valid_repository(self) -> None:
        base = {
            "name": "gander", "pages_subdir": "gander",
            "artifact_prefix": "gander-v", "provider": "github",
        }
        with self.assertRaisesRegex(ValueError, "github_repo"):
            projection.project({"repos": [base]})
        with self.assertRaisesRegex(ValueError, "owner/repository"):
            projection.project({"repos": [base | {"github_repo": "gander", "expected_platforms": ["x86_64-linux"]}]})

    def test_github_row_requires_valid_expected_platforms(self) -> None:
        base = {"name": "gander", "pages_subdir": "gander", "artifact_prefix": "gander-v",
                "provider": "github", "github_repo": "averagechris/gander"}
        for value, message in (([], "nonempty"), (["x86_64-linux", "x86_64-linux"], "duplicates"),
                               (["m68k-amiga"], "unknown")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, message):
                projection.project({"repos": [base | {"expected_platforms": value}]})


if __name__ == "__main__":
    unittest.main()
