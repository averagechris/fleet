"""Contract tests for the shared release orchestrator.

These deliberately inspect the generated shell template boundary. Consumer
flakes evaluate this file into a writeShellApplication, so ordering in this
template is the stable fleet-wide behavior (and can be checked without network
access, credentials, or a real home directory).
"""

from pathlib import Path
import unittest


SOURCE = (Path(__file__).parents[1] / "nix" / "fleet-apps.nix").read_text()


def _position(fragment: str) -> int:
    position = SOURCE.find(fragment)
    assert position >= 0, f"missing release contract fragment: {fragment}"
    return position


class ReleaseSafetyContract(unittest.TestCase):
    def test_help_and_check_are_a_small_safe_interface(self) -> None:
        self.assertIn("release --version X.Y.Z [--check]", SOURCE)
        self.assertIn("--check) check_only=1", SOURCE)
        self.assertIn("[[ $check_only -eq 0 ]] || exit 0", SOURCE)
        release = SOURCE[_position("mkSourcehutRelease = {") : _position("mkGithubRelease = {")]
        for footgun in ("--skip-validate", "--skip-tag", "--skip-artifact"):
            self.assertNotIn(footgun, release)

    def test_every_rejection_precedes_preparation(self) -> None:
        prepare = _position('nix run .#prepare-release --')
        for rejection in ("release requires a jj Git-backed repository", "working-copy commit @ is not empty", "stale/diverged checkout", "existing tag %s is not an exact resumable release", "srht auth status"):
            self.assertLess(_position(rejection), prepare)

    def test_prepared_validation_and_artifact_precede_atomic_publication(self) -> None:
        positions = [_position(fragment) for fragment in ('nix run .#prepare-release --', "(${validateScript}) < /dev/null", "nix build .#release-artifact", 'sha256sum -c', 'push --atomic --force-with-lease=', "srht git artifact upload")]
        self.assertEqual(positions, sorted(positions))

    def test_failed_validation_or_build_cannot_publish_refs(self) -> None:
        release = SOURCE[_position("mkSourcehutRelease = {") : _position("mkGithubRelease = {")]
        self.assertEqual(release.count("push --atomic"), 1)
        self.assertLess(release.index("(${validateScript}) < /dev/null"), release.index("push --atomic"))
        self.assertLess(release.index("nix build .#release-artifact"), release.index("push --atomic"))
        self.assertIn("set -euo pipefail", release)

    def test_rust_presets_append_additional_release_validation_apps(self) -> None:
        self.assertIn("releaseValidationApps ? []", SOURCE)
        self.assertIn('ciApps = ["ci-fmt" "ci-clippy" "ci-test"] ++ releaseValidationApps;', SOURCE)
        self.assertIn('validation apps: %s', SOURCE)


if __name__ == "__main__":
    unittest.main()
