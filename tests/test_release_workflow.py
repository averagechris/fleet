import glob
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


WORKFLOW = (Path(__file__).parents[1] / ".github/workflows/release.yml").read_text()


def _step(name: str) -> str:
    """Return one named publish step without snapshotting the whole workflow."""
    match = re.search(
        rf"(?ms)^      - name: {re.escape(name)}\n.*?(?=^      - name: |\Z)",
        WORKFLOW,
    )
    if match is None:
        raise AssertionError(f"missing workflow step: {name}")
    return match.group()


class ReleaseWorkflowTests(unittest.TestCase):
    def test_build_upload_and_merged_download_are_flat_and_complete(self):
        """Exercise the runner handoff, including upload-artifact's common-root rule."""
        build = _step("Build and verify artifact")
        script = build.split("        run: |\n", 1)[1].split("      - uses:", 1)[0]
        script = "\n".join(line[10:] for line in script.splitlines()) + "\n"
        upload = WORKFLOW.split("uses: actions/upload-artifact@", 1)[1]
        path = upload.split("          path: ", 1)[1].split("\n          if-no-files-found:", 1)[0]
        paths = ([line.strip() for line in path.splitlines()[1:]] if path.startswith("|")
                 else [path.strip()])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            merged = root / "assets"
            merged.mkdir()
            platforms = ("aarch64-darwin", "x86_64-linux")
            for platform in platforms:
                # Faithful per-runner archive fixture: tarball, sidecar and tag identity.
                runner = root / platform
                runner.mkdir()
                output = root / f"nix-{platform}"
                output.mkdir()
                name = f"gander-v0.8.3-{platform}.tar.gz"
                payload = f"fixture archive for {platform}\n".encode()
                (output / name).write_bytes(payload)
                (output / f"{name}.sha256").write_text(
                    f"{hashlib.sha256(payload).hexdigest()}  {name}\n"
                )
                (runner / f"release-identity-{platform}").write_text(
                    "07d282b8d769d1de1729a26b16ef93ca8636fb70\n"
                    "eebbd2924740d70282604b161c66a697f7031780\n"
                )
                bin_dir = runner / "bin"
                bin_dir.mkdir()
                nix = bin_dir / "nix"
                nix.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
                nix.chmod(0o755)
                subprocess.run(
                    ["bash", "-e", "-c", script], cwd=runner, check=True,
                    env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}",
                                      "PLATFORM": platform, "TAG": "v0.8.3",
                                      "GITHUB_REPOSITORY": "averagechris/gander"},
                    capture_output=True,
                )

                # upload-artifact stores paths relative to their least common search root.
                # A directory path uses that directory as root; a file/glob uses its parent.
                patterns = [p.replace("${{ matrix.platform.name }}", platform) for p in paths]
                bases = [runner / (p if p.endswith("/") else os.path.dirname(p))
                         for p in patterns]
                common = Path(os.path.commonpath(bases))
                for pattern in patterns:
                    for match in glob.glob(str(runner / pattern)):
                        for source in ([Path(match)] if Path(match).is_file()
                                       else Path(match).rglob("*")):
                            if source.is_file():
                                destination = merged / source.relative_to(common)
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(source, destination)

            expected = {f"gander-v0.8.3-{p}.tar.gz{ext}"
                        for p in platforms for ext in ("", ".sha256")}
            expected |= {f"release-identity-{p}" for p in platforms}
            self.assertEqual({p.relative_to(merged).as_posix() for p in merged.rglob("*")
                              if p.is_file()}, expected)
            for platform in platforms:
                name = f"gander-v0.8.3-{platform}.tar.gz"
                digest = hashlib.sha256((merged / name).read_bytes()).hexdigest()
                self.assertEqual((merged / f"{name}.sha256").read_text(),
                                 f"{digest}  {name}\n")
                self.assertEqual((merged / f"release-identity-{platform}").read_text(),
                                 "07d282b8d769d1de1729a26b16ef93ca8636fb70\n"
                                 "eebbd2924740d70282604b161c66a697f7031780\n")

    def test_website_token_is_pinned_and_narrowly_scoped(self):
        self.assertIn(
            "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
            WORKFLOW,
        )
        self.assertIn("owner: averagechris", WORKFLOW)
        self.assertIn("repositories: averagechris.github.io", WORKFLOW)
        self.assertIn("permission-actions: write", WORKFLOW)
        self.assertIn("permission-contents: read", WORKFLOW)
        self.assertNotIn("skip-token-revoke:", WORKFLOW)

    def test_website_token_is_not_hand_rolled_or_passed_in_payload_arguments(self):
        self.assertNotIn("openssl", WORKFLOW)
        self.assertNotIn("access_tokens", WORKFLOW)
        dispatch = WORKFLOW.split("- name: Dispatch website refresh", 1)[1]
        self.assertNotIn("Authorization: Bearer", dispatch)
        self.assertIn("gh api --method POST", WORKFLOW)
        self.assertIn("--input -", WORKFLOW)

    def test_dispatch_retains_trust_checks_and_payload_identity(self):
        trust = _step("Verify website dispatch trust")
        for expected in (
            "untrusted reusable-workflow caller repository",
            "untrusted reusable-workflow caller ref",
            "caller SHA is not the release tag commit",
            "untrusted website dispatch target repository",
            "GitHub App credentials absent; website dispatch skipped",
        ):
            self.assertIn(expected, trust)
        self.assertIn("project:$project,tag:$tag,sha:$sha", _step("Dispatch website refresh"))

    def test_trust_checks_gate_token_mint_and_dispatch(self):
        trust = _step("Verify website dispatch trust")
        mint = _step("Mint website dispatch token")
        dispatch = _step("Dispatch website refresh")
        enabled = "steps.website-dispatch.outputs.enabled == 'true'"

        for step in (mint, dispatch):
            self.assertIn(f"if: ${{{{ {enabled} }}}}", step)
            self.assertNotIn("always()", step)

        enabled_output = "echo 'enabled=true' >> \"$GITHUB_OUTPUT\""
        self.assertEqual(trust.count(enabled_output), 1)
        enabled_position = trust.index(enabled_output)
        for check in (
            '[[ "$GITHUB_REPOSITORY" == averagechris/gander ]]',
            '[[ "$GITHUB_REF" == "refs/tags/$TAG" ]]',
            '[[ -n "$commit" && "$commit" == "$SHA" ]]',
            '[[ "$TARGET" == averagechris/averagechris.github.io ]]',
            '[[ -z "$APP_ID" || "$HAS_APP_KEY" != true ]]',
        ):
            self.assertLess(trust.index(check), enabled_position)

    def test_private_key_is_only_revealed_to_pinned_action_after_trust(self):
        trust = _step("Verify website dispatch trust")
        mint = _step("Mint website dispatch token")
        action = "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1"

        self.assertIn(action, mint)
        self.assertIn("private-key: ${{ secrets.github_app_private_key }}", mint)
        self.assertNotIn(action, trust)
        self.assertNotIn("private-key:", trust)
        self.assertEqual(WORKFLOW.count("secrets.github_app_private_key"), 2)
        self.assertEqual(
            WORKFLOW.count("${{ secrets.github_app_private_key }}"),
            1,
        )
        self.assertLess(WORKFLOW.index("id: website-dispatch"), WORKFLOW.index(action))
        self.assertLess(
            WORKFLOW.index("echo 'enabled=true' >> \"$GITHUB_OUTPUT\""),
            WORKFLOW.index(action),
        )


if __name__ == "__main__":
    unittest.main()
