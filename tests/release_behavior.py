"""Network-free behavioral exercise of the generated release application."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run(*args, cwd, env=None, ok=True):
    result = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True)
    if ok and result.returncode:
        raise AssertionError(f"{args} failed\nstdout={result.stdout}\nstderr={result.stderr}")
    return result


def executable(path: Path, text: str):
    path.write_text(text)
    path.chmod(0o755)


class Fixture:
    def __init__(self, root: Path, release: Path):
        self.root = root
        root.mkdir(parents=True)
        self.release = release
        self.remote = root / "origin.git"
        self.primary = root / "primary"
        self.workspace = root / "isolated"
        self.primary.mkdir()
        run("git", "init", "--bare", str(self.remote), cwd=root)
        # The release runs in a genuine separate jj workspace. Its root has no
        # .git; jj git root resolves the shared backing store under primary/.jj.
        run("jj", "git", "init", cwd=self.primary)
        run("jj", "config", "set", "--repo", "user.name", "Release Test", cwd=self.primary)
        run("jj", "config", "set", "--repo", "user.email", "release@example.invalid", cwd=self.primary)
        self.git_dir = run("jj", "git", "root", cwd=self.primary).stdout.strip()
        run("git", f"--git-dir={self.git_dir}", "config", "user.name", "Release Test", cwd=self.primary)
        run("git", f"--git-dir={self.git_dir}", "config", "user.email", "release@example.invalid", cwd=self.primary)
        (self.primary / "package.json").write_text('{"version":"1.2.3"}\n')
        (self.primary / "CHANGELOG.md").write_text("# Changelog\n\n## Unreleased\n")
        run("jj", "describe", "-m", "initial", cwd=self.primary)
        run("jj", "bookmark", "set", "main", "-r", "@", cwd=self.primary)
        run("git", f"--git-dir={self.git_dir}", "remote", "add", "origin", str(self.remote), cwd=self.primary)
        run("git", f"--git-dir={self.git_dir}", "push", "origin", "main:main", cwd=self.primary)
        run("jj", "workspace", "add", "--name", "release-test", str(self.workspace), "-r", "main", cwd=self.primary)
        run("jj", "new", "main", cwd=self.workspace)
        assert not (self.workspace / ".git").exists()

        self.state = root / "state.json"
        self.collision = "prefix-game-v1.2.4-web.tar.gz"
        self.state.write_text(json.dumps({"uploads": [self.collision], "jobs": []}))
        self.log = root / "nix.log"; self.log.write_text("")
        self.artifact = root / "artifact"; self.artifact.mkdir()
        tools = root / "tools"; tools.mkdir()
        executable(tools / "nix", "#!" + shutil.which("sh") + """
set -eu
printf '%s\n' "$*" >> "$RELEASE_LOG"
case "$*" in
  *prepare-release*) python3 -c 'import json; p="package.json"; d=json.load(open(p)); d["version"]="1.2.4"; open(p,"w").write(json.dumps(d)+"\\n")' ;;
  *ci-*) [ "${FAIL_MODE:-}" != validation ] || exit 23 ;;
  'build .#release-artifact --no-link --print-out-paths')
    [ "${FAIL_MODE:-}" != artifact ] || exit 24
    printf payload > "$ART_DIR/game-v1.2.4-web.tar.gz"
    (cd "$ART_DIR" && sha256sum game-v1.2.4-web.tar.gz > game-v1.2.4-web.tar.gz.sha256)
    printf '%s\n' "$ART_DIR" ;;
esac
""")
        executable(tools / "srht", "#!" + sys.executable + """
import json,os,sys
p=os.environ['RELEASE_STATE']; s=json.load(open(p)); a=sys.argv[1:]
if a[:2] == ['auth','status']: raise SystemExit(0)
if 'artifact' in a and 'list' in a: print(json.dumps({'items':[{'filename':x} for x in s['uploads']]})); raise SystemExit(0)
if 'artifact' in a and 'upload' in a: s['uploads'].append(os.path.basename(a[-1])); json.dump(s,open(p,'w')); raise SystemExit(0)
if 'builds' in a and 'list' in a:
 tag=a[a.index('--tag')+1]; print(json.dumps({'items':[j for j in s['jobs'] if j['tag']==tag]})); raise SystemExit(0)
if 'builds' in a and 'submit' in a:
 tag=a[a.index('--tag')+1]; s['jobs'].append({'tag':tag,'status':'running'}); json.dump(s,open(p,'w'))
 if os.environ.get('FAIL_AFTER_SUBMIT') == '1': raise SystemExit(9)
 raise SystemExit(0)
raise SystemExit('unexpected srht arguments: '+repr(a))
""")
        self.env = os.environ | {"FLEET_RELEASE_NIX": str(tools / "nix"), "FLEET_RELEASE_SRHT": str(tools / "srht"), "RELEASE_STATE": str(self.state), "RELEASE_LOG": str(self.log), "ART_DIR": str(self.artifact)}

    def release_run(self, *extra, env=None, ok=True):
        return run(str(self.release), "--version", "1.2.4", *extra, cwd=self.workspace, env=env or self.env, ok=ok)

    def remote_refs(self):
        return run("git", "--git-dir", str(self.remote), "show-ref", cwd=self.workspace).stdout

    def assert_unpublished(self, initial):
        assert self.remote_refs() == initial
        assert run("git", "--git-dir", str(self.remote), "show-ref", "--verify", "refs/tags/v1.2.4", cwd=self.workspace, ok=False).returncode != 0


def scenario_check_is_non_mutating(root, release):
    f = Fixture(root, release)
    before_op = run("jj", "op", "log", "--no-graph", "-T", "self.id()", "-n", "1", cwd=f.workspace).stdout
    before_refs = run("git", f"--git-dir={f.git_dir}", "show-ref", cwd=f.workspace).stdout
    before_files = (f.workspace / "package.json").read_text()
    f.release_run("--check")
    assert before_op == run("jj", "op", "log", "--no-graph", "-T", "self.id()", "-n", "1", cwd=f.workspace).stdout
    assert before_refs == run("git", f"--git-dir={f.git_dir}", "show-ref", cwd=f.workspace).stdout
    assert before_files == (f.workspace / "package.json").read_text()


def scenario_prepublication_failures(root, release):
    for mode in ("validation", "artifact"):
        f = Fixture(root / mode, release); initial = f.remote_refs()
        result = f.release_run(env=f.env | {"FAIL_MODE": mode}, ok=False)
        assert result.returncode != 0 and "release aborted after version stamping" in result.stderr
        f.assert_unpublished(initial)
        log = f.log.read_text()
        assert "prepare-release" in log
        if mode == "validation": assert "build .#release-artifact" not in log
        else: assert "run .#ci-web" in log and "build .#release-artifact" in log


def scenario_dirty_and_stale_reject_before_prepare(root, release):
    dirty = Fixture(root / "dirty", release); (dirty.workspace / "dirty").write_text("x")
    assert dirty.release_run(ok=False).returncode != 0 and "prepare-release" not in dirty.log.read_text()
    stale = Fixture(root / "stale", release); run("jj", "bookmark", "set", "main", "--allow-backwards", "-r", "root()", cwd=stale.workspace)
    assert stale.release_run(ok=False).returncode != 0 and "prepare-release" not in stale.log.read_text()


def scenario_success_and_resume(root, release):
    f = Fixture(root, release)
    failure = f.release_run(env=f.env | {"FAIL_AFTER_SUBMIT": "1"}, ok=False)
    assert failure.returncode != 0 and "release aborted" not in failure.stderr
    first_refs = f.remote_refs()
    assert run("git", "--git-dir", str(f.remote), "cat-file", "-t", "refs/tags/v1.2.4", cwd=f.workspace).stdout.strip() == "tag"
    f.release_run()
    assert first_refs == f.remote_refs()
    state = json.loads(f.state.read_text())
    assert sorted(state["uploads"]) == sorted([f.collision, "game-v1.2.4-web.tar.gz", "game-v1.2.4-web.tar.gz.sha256"])
    assert len(state["jobs"]) == 1
    assert "run .#ci-web" in f.log.read_text()
    assert run("jj", "log", "-r", "@", "--no-graph", "-T", 'if(empty,"yes","no")', cwd=f.workspace).stdout == "yes"
    assert run("jj", "log", "-r", "@- & main", "--no-graph", "-T", "commit_id", cwd=f.workspace).stdout.strip()


def main():
    release = Path(os.environ["RELEASE_PROGRAM"])
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); home = root / "home"; home.mkdir(); os.environ["HOME"] = str(home)
        scenario_check_is_non_mutating(root / "check", release)
        scenario_prepublication_failures(root / "failures", release)
        scenario_dirty_and_stale_reject_before_prepare(root / "reject", release)
        scenario_success_and_resume(root / "resume", release)


if __name__ == "__main__":
    main()
