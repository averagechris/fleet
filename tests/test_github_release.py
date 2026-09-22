"""Executable contracts for the GitHub registry projection."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "registry_projection", ROOT / "scripts/registry_projection.py"
)
assert SPEC and SPEC.loader
projection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(projection)


class GithubProjectionContract(unittest.TestCase):
    def test_old_sourcehut_rows_render_unchanged(self) -> None:
        config = {"repos": [{
            "name": "old", "pages_subdir": "old", "srht_repo": "old",
            "artifact_prefix": "old-v", "binaries": ["old"],
        }]}
        rendered = projection.render(projection.project(config))
        self.assertIn('srht_repo = "old"', rendered)
        self.assertNotIn("provider =", rendered)
        self.assertNotIn("github_repo =", rendered)

    def test_github_row_round_trips_transport_fields(self) -> None:
        config = {"repos": [{
            "name": "gander", "pages_subdir": "gander",
            "artifact_prefix": "gander-v", "binaries": ["gander"],
            "provider": "github", "github_repo": "averagechris/gander",
        }]}
        rows = projection.project(config)
        self.assertEqual(rows[0]["provider"], "github")
        self.assertEqual(rows[0]["github_repo"], "averagechris/gander")
        rendered = projection.render(rows)
        self.assertIn('provider = "github"', rendered)
        self.assertIn('github_repo = "averagechris/gander"', rendered)

    def test_github_row_requires_valid_repository(self) -> None:
        base = {
            "name": "gander", "pages_subdir": "gander",
            "artifact_prefix": "gander-v", "provider": "github",
        }
        with self.assertRaisesRegex(ValueError, "github_repo"):
            projection.project({"repos": [base]})
        with self.assertRaisesRegex(ValueError, "owner/repository"):
            projection.project({"repos": [base | {"github_repo": "gander"}]})


if __name__ == "__main__":
    unittest.main()
