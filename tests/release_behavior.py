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
        canonical_urls = (
            "git@github.com:averagechris/web-game-fixture.git",
            "ssh://git@github.com/averagechris/web-game-fixture",
            "https://github.com/averagechris/web-game-fixture.git",
            "https://github.com/averagechris/web-game-fixture",
        )
        if os.environ.get("RELEASE_BACKEND") == "github":
            run("git", f"--git-dir={self.git_dir}", "remote", "set-url", "origin", canonical_urls[0], cwd=self.primary)
        run("jj", "workspace", "add", "--name", "release-test", str(self.workspace), "-r", "main", cwd=self.primary)
        run("jj", "new", "main", cwd=self.workspace)
        assert not (self.workspace / ".git").exists()

        self.state = root / "state.json"
        self.collision = "prefix-game-v1.2.4-web.tar.gz"
        self.state.write_text(json.dumps({"uploads": [self.collision], "jobs": []}))
        self.log = root / "nix.log"; self.log.write_text("")
        self.srht_log = root / "srht.log"; self.srht_log.write_text("")
        self.validation_observation = root / "validation-observation.json"
        self.artifact = root / "artifact"; self.artifact.mkdir()
        tools = root / "tools"; tools.mkdir()
        # Positive tests use an explicit transport wrapper: the release sees
        # canonical effective URLs, while all later origin operations are
        # delegated to the isolated bare fixture. Production has no override.
        self.transport_release = tools / "release-transport"
        executable(self.transport_release, "#!" + shutil.which("bash") + """
set -euo pipefail
git() {
  case "$*" in
    *' remote get-url --all origin') printf '%s\n' "$TEST_ORIGIN_FETCH_URL"; return ;;
    *' remote get-url --push --all origin') printf '%s\n' "$TEST_ORIGIN_PUSH_URL"; return ;;
  esac
  local args=("$@") i
  for i in "${!args[@]}"; do [[ "${args[$i]}" == origin ]] && args[$i]="$TEST_REMOTE"; done
  "$REAL_GIT" "${args[@]}"
}
export -f git
exec "$RELEASE_REAL" "$@"
""")
        executable(tools / "nix", "#!" + shutil.which("sh") + """
set -eu
printf '%s\n' "$*" >> "$RELEASE_LOG"
case "$*" in
  *prepare-release*) python3 -c 'import json; p="package.json"; d=json.load(open(p)); d["version"]="1.2.4"; open(p,"w").write(json.dumps(d)+"\\n")' ;;
  *ci-*)
    [ "${FAIL_MODE:-}" != validation ] || exit 23
    if echo "$*" | grep -q 'ci-web'; then
      python3 - "$RELEASE_VALIDATION_OBSERVATION" <<'PY'
import json, os, subprocess, sys

def output(*args):
    return subprocess.check_output(args, text=True).strip()

commit = output("jj", "log", "-r", "@", "--no-graph", "-T", "commit_id")
git_dir = output("jj", "git", "root")
tree = output(os.environ["REAL_GIT"], f"--git-dir={git_dir}", "rev-parse", f"{commit}^{{tree}}")
parents = output("jj", "log", "-r", "@", "--no-graph", "-T", 'parents.map(|p| p.commit_id()).join(" ")')
change = output("jj", "log", "-r", "@", "--no-graph", "-T", "change_id")
with open(sys.argv[1], "w") as record:
    json.dump({"commit": commit, "tree": tree, "parents": parents, "change": change}, record, sort_keys=True)
    record.write("\\n")
PY
      case "${GATE_MODE:-}" in
        edit) printf 'validation mutation\n' >> CHANGELOG.md ;;
        move) jj new ;;
        fail) exit 25 ;;
      esac
    fi ;;
  'build .#release-artifact --no-link --print-out-paths')
    [ "${FAIL_MODE:-}" != artifact ] || exit 24
    printf payload > "$ART_DIR/game-v1.2.4-web.tar.gz"
    (cd "$ART_DIR" && sha256sum game-v1.2.4-web.tar.gz > game-v1.2.4-web.tar.gz.sha256)
    printf '%s\n' "$ART_DIR" ;;
esac
""")
        executable(tools / "srht", "#!" + sys.executable + """
import json,os,sys
p_log=os.environ['SRHT_LOG']; open(p_log,'a').write(' '.join(sys.argv[1:])+'\\n')
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
        self.env = os.environ | {"FLEET_RELEASE_NIX": str(tools / "nix"), "FLEET_RELEASE_SRHT": str(tools / "srht"), "RELEASE_STATE": str(self.state), "RELEASE_LOG": str(self.log), "SRHT_LOG": str(self.srht_log), "RELEASE_VALIDATION_OBSERVATION": str(self.validation_observation), "ART_DIR": str(self.artifact), "RELEASE_REAL": str(self.release), "REAL_GIT": shutil.which("git"), "TEST_REMOTE": str(self.remote), "TEST_ORIGIN_FETCH_URL": canonical_urls[0], "TEST_ORIGIN_PUSH_URL": canonical_urls[0]}

    def set_origin_urls(self, fetch: str, push_urls: tuple[str, ...]):
        run("git", f"--git-dir={self.git_dir}", "remote", "set-url", "origin", fetch, cwd=self.workspace)
        run("git", f"--git-dir={self.git_dir}", "config", "--unset-all", "remote.origin.pushurl", cwd=self.workspace, ok=False)
        for url in push_urls:
            run("git", f"--git-dir={self.git_dir}", "config", "--add", "remote.origin.pushurl", url, cwd=self.workspace)

    def release_run(self, *extra, env=None, ok=True):
        program = self.transport_release if os.environ.get("RELEASE_BACKEND") == "github" else self.release
        return run(str(program), "--version", "1.2.4", *extra, cwd=self.workspace, env=env or self.env, ok=ok)

    def release_run_real(self, *extra, ok=True):
        return run(str(self.release), "--version", "1.2.4", *extra, cwd=self.workspace, env=self.env, ok=ok)

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


def scenario_github_check_is_preflight_only(root, release):
    f = Fixture(root, release)
    result = f.release_run("--check")
    assert f.log.read_text() == "", "GitHub --check invoked preparation, validation, or artifact build"
    assert f.srht_log.read_text() == "", "GitHub --check invoked srht"
    output = result.stdout + result.stderr
    assert "ref/version preflight" in output
    assert "stops before validation and artifact work" in output
    assert "release readiness" not in output


def scenario_github_origin_guard(root, release):
    for name, fetch, pushes, rewrite in (
        ("sourcehut", "git@git.sr.ht:~averagechris/web-game-fixture", ("git@git.sr.ht:~averagechris/web-game-fixture",), None),
        ("wrong-repo", "https://github.com/averagechris/other.git", ("https://github.com/averagechris/other.git",), None),
        ("fetch-rewrite", "https://github.com/averagechris/web-game-fixture", (), "insteadOf"),
        ("push-rewrite", "https://github.com/averagechris/web-game-fixture", (), "pushInsteadOf"),
        ("mixed-push", "https://github.com/averagechris/web-game-fixture", ("https://github.com/averagechris/web-game-fixture", "ssh://git@git.sr.ht/~averagechris/web-game-fixture"), None),
    ):
        f = Fixture(root / name, release)
        f.set_origin_urls(fetch, pushes)
        if rewrite:
            run("git", f"--git-dir={f.git_dir}", "config", "--add",
                f"url.ssh://git@git.sr.ht/~averagechris/web-game-fixture.{rewrite}", fetch,
                cwd=f.workspace)
        before = run("git", f"--git-dir={f.git_dir}", "show-ref", cwd=f.workspace).stdout
        result = f.release_run_real("--check", ok=False)
        assert result.returncode and "origin URL safety check failed" in result.stderr
        assert f.log.read_text() == ""
        assert before == run("git", f"--git-dir={f.git_dir}", "show-ref", cwd=f.workspace).stdout

    for name, fetch, pushes in (
        ("ssh", "git@github.com:averagechris/web-game-fixture.git", ("ssh://git@github.com/averagechris/web-game-fixture",)),
        ("https", "https://github.com/averagechris/web-game-fixture.git", ("https://github.com/averagechris/web-game-fixture",)),
    ):
        f = Fixture(root / name, release)
        result = f.release_run("--check", env=f.env | {
            "TEST_ORIGIN_FETCH_URL": fetch,
            "TEST_ORIGIN_PUSH_URL": "\n".join(pushes),
        })
        assert "ref/version preflight" in result.stdout


def scenario_github_validation_freezes_prepared_tree(root, release):
    for mode in ("edit", "move", "fail"):
        f = Fixture(root / mode, release)
        initial = f.remote_refs()
        result = f.release_run(env=f.env | {"GATE_MODE": mode}, ok=False)
        assert result.returncode != 0
        f.assert_unpublished(initial)
        assert run("git", f"--git-dir={f.git_dir}", "show-ref", "--verify",
                   "refs/tags/v1.2.4", cwd=f.workspace, ok=False).returncode != 0
        descriptions = run("jj", "log", "-r", "all()", "--no-graph", "-T", "description",
                           cwd=f.workspace).stdout
        assert "chore: release v1.2.4" not in descriptions
        if mode in ("edit", "move"):
            assert "changed the prepared release commit or tracked tree" in result.stderr, (
                mode, result.stdout, result.stderr
            )


def scenario_github_success_uses_prepared_tree(root, release):
    f = Fixture(root, release)
    f.release_run()
    observation = json.loads(f.validation_observation.read_text())
    assert observation["commit"] and observation["tree"]
    prepared = run("git", f"--git-dir={f.git_dir}", "show", f"{observation['commit']}:package.json", cwd=f.workspace).stdout
    assert json.loads(prepared)["version"] == "1.2.4"
    refs = f.remote_refs()
    assert "refs/heads/main" in refs and "refs/tags/v1.2.4" in refs
    tagged_commit = run("git", "--git-dir", str(f.remote), "rev-parse", "refs/tags/v1.2.4^{}",
                        cwd=f.workspace).stdout.strip()
    tagged_tree = run("git", "--git-dir", str(f.remote), "rev-parse", f"{tagged_commit}^{{tree}}",
                      cwd=f.workspace).stdout.strip()
    assert tagged_tree == observation["tree"]
    tagged_parents = run("git", "--git-dir", str(f.remote), "rev-list", "--parents", "-n", "1", tagged_commit,
                         cwd=f.workspace).stdout.split()[1:]
    assert " ".join(tagged_parents) == observation["parents"]
    tagged_change = run("jj", "log", "-r", tagged_commit, "--no-graph", "-T", "change_id",
                        cwd=f.workspace).stdout.strip()
    assert tagged_change == observation["change"]
    tagged = run("git", "--git-dir", str(f.remote), "show", "v1.2.4:package.json",
                 cwd=f.workspace).stdout
    assert json.loads(tagged)["version"] == "1.2.4"
    assert "run .#ci-web" in f.log.read_text()


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
        if os.environ.get("RELEASE_BACKEND") == "github":
            scenario_github_check_is_preflight_only(root / "github-check", release)
            scenario_github_origin_guard(root / "github-origins", release)
            scenario_github_validation_freezes_prepared_tree(root / "github-freeze", release)
            scenario_github_success_uses_prepared_tree(root / "github-success", release)
            return
        scenario_check_is_non_mutating(root / "check", release)
        scenario_prepublication_failures(root / "failures", release)
        scenario_dirty_and_stale_reject_before_prepare(root / "reject", release)
        scenario_success_and_resume(root / "resume", release)


if __name__ == "__main__":
    main()
