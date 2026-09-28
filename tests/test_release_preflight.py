import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
WORKFLOW = (ROOT / ".github/workflows/release.yml").read_text()


def preflight_shell():
    text = WORKFLOW.split("      - name: Verify release API access\n", 1)[1]
    text = text.split("\n  build:\n", 1)[0]
    text = text.split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in text.splitlines()) + "\n"


class ReleasePreflightTests(unittest.TestCase):
    def run_case(self, statuses):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state.json"
            state.write_text(json.dumps({"statuses": statuses, "calls": []}))
            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
p = Path(os.environ['PREFLIGHT_STATE'])
s = json.loads(p.read_text())
status = s['statuses'][min(len(s['calls']), len(s['statuses']) - 1)]
s['calls'].append(sys.argv[1:])
p.write_text(json.dumps(s))
print(f'HTTP/2.0 {status} fixture')
print('X-Accepted-GitHub-Permissions: contents=read')
print('X-GitHub-Request-Id: SAFE-FIXTURE')
print('Retry-After: 1')
print()
print('BODY MUST NOT BE LOGGED')
sys.exit(0 if status == 200 else 1)
""")
            gh.chmod(0o755)
            sleep = bin_dir / "sleep"
            sleep.write_text("#!/bin/sh\nexit 0\n")
            sleep.chmod(0o755)
            run = subprocess.run(
                ["bash", "-e", "-c", preflight_shell()], text=True,
                capture_output=True, env=os.environ | {
                    "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "PREFLIGHT_STATE": str(state),
                    "GITHUB_REPOSITORY": "averagechris/gander",
                    "GH_TOKEN": "offline-only",
                })
            return run, json.loads(state.read_text())

    def test_preflight_gates_build_and_preserves_publish_lookup(self):
        preflight = WORKFLOW.index("  preflight:")
        build = WORKFLOW.index("  build:")
        publish = WORKFLOW.index("  publish:")
        self.assertLess(preflight, build)
        self.assertLess(build, publish)
        self.assertIn("  build:\n    needs: preflight", WORKFLOW)
        self.assertIn("  publish:\n    needs: build", WORKFLOW)
        self.assertEqual(WORKFLOW.count('repos/$GITHUB_REPOSITORY/releases?per_page=100'), 2)
        self.assertIn("permissions: {contents: write}", WORKFLOW[preflight:build])

    def test_success_calls_exact_endpoint_once_without_logging_body(self):
        run, state = self.run_case([200])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(state["calls"], [["api", "--include", "--silent",
                                          "repos/averagechris/gander/releases?per_page=100"]])
        self.assertIn("preflight succeeded", run.stdout)
        self.assertNotIn("BODY MUST NOT BE LOGGED", run.stdout + run.stderr)

    def test_403_retries_once_then_fails_with_safe_headers(self):
        run, state = self.run_case([403, 403])
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(len(state["calls"]), 2)
        self.assertEqual(run.stderr.count("Retrying"), 1)
        self.assertIn("X-Accepted-GitHub-Permissions: contents=read", run.stderr)
        self.assertIn("X-GitHub-Request-Id: SAFE-FIXTURE", run.stderr)
        self.assertIn("Retry-After: 1", run.stderr)
        self.assertNotIn("BODY MUST NOT BE LOGGED", run.stdout + run.stderr)

    def test_other_error_fails_without_retry(self):
        run, state = self.run_case([500])
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(len(state["calls"]), 1)
        self.assertNotIn("Retrying", run.stderr)


if __name__ == "__main__":
    unittest.main()
