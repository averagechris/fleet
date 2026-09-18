from __future__ import annotations

import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import new_project


FLOATING_INVOCATION = "nix run 'git+https://git.sr.ht/~averagechris/srht'"
APPROVED_INVOCATION = "nix run --inputs-from . fleet#srht --"


class SrhtChannelTests(unittest.TestCase):
    def test_shared_and_template_release_files_reject_floating_srht(self) -> None:
        release_files = [
            ROOT / "nix/fleet-apps.nix",
            ROOT / "templates/rust-cli/flake.nix",
            ROOT / "templates/rust-cli/builds/release-linux-x86_64.yml",
        ]
        for path in release_files:
            with self.subTest(path=path):
                self.assertNotIn(FLOATING_INVOCATION, path.read_text())
        self.assertIn(APPROVED_INVOCATION, release_files[-1].read_text())

    def test_generated_project_uses_fleet_approved_srht(self) -> None:
        args = types.SimpleNamespace(
            description="Demo CLI",
            artifact_prefix="demo",
            binary="demo",
            pages_subdir="demo",
            srht_repo="demo",
            name="demo",
            version="0.1.0",
        )
        flake = new_project.flake_nix(args)
        manifest = new_project.release_manifest(args)
        self.assertNotIn("srht.url", flake)
        self.assertIn("srhtPackage = fleet.packages.${system}.srht;", flake)
        self.assertIn("fleet.packages.${system}.srht", flake)
        self.assertNotIn(FLOATING_INVOCATION, manifest)
        self.assertIn(APPROVED_INVOCATION, manifest)


if __name__ == "__main__":
    unittest.main()
