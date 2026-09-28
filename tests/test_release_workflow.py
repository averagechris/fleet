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


def _job(name: str, next_name: str | None = None) -> str:
    start = WORKFLOW.index(f"  {name}:\n")
    end = WORKFLOW.index(f"  {next_name}:\n", start) if next_name else len(WORKFLOW)
    return WORKFLOW[start:end]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_build_only_contract_has_read_only_jobs(self):
        build = _job("build", "manual-publication")
        manual = _job("manual-publication")
        self.assertIn("manual publication required", manual)
        self.assertIn("averagechris/gander/blob/main/docs/release.md", manual)

        # Nested reusable-workflow permissions are validated statically, even
        # for skipped jobs. Inspect every job rather than only today's caller.
        jobs = re.findall(r"(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)",
                          WORKFLOW.split("jobs:\n", 1)[1])
        self.assertEqual({name for name, _ in jobs}, {"build", "manual-publication"})
        for name, job in jobs:
            self.assertRegex(job, r"(?m)^    permissions: \{contents: read\}$", name)
            self.assertNotIn("contents: write", job, name)

        for removed in ("publish_release", "github_app_id", "github_app_private_key",
                        "website_repository", "gh release", "gh api"):
            self.assertNotIn(removed, WORKFLOW)

    def test_manual_handoff_has_no_publication_side_effects(self):
        manual = _job("manual-publication")
        for forbidden in ("gh api", "gh release", "release upload", "release edit",
                          "create-github-app-token", "workflow/dispatches"):
            self.assertNotIn(forbidden, manual)
        self.assertNotIn("contents: write", manual)

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


if __name__ == "__main__":
    unittest.main()
