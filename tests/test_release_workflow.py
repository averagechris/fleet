from pathlib import Path
import re
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
