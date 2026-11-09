from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.attribution_guard import (
    DEFAULT_IDENTITIES,
    Commit,
    Policy,
    author_refusal,
    check,
    commits_in,
    identity_refusal,
    line_refusal,
    main,
    text_findings,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "attribution"
WORKFLOW = ROOT / ".github" / "workflows" / "repository-hygiene-guard.yml"
POLICY = Policy.load(DEFAULT_IDENTITIES)

OWNER = "J.W. Jonkers <info@jorisjonkers.dev>"
RENOVATE = "renovate[bot] <29139614+renovate[bot]@users.noreply.github.com>"


def refusal(identity: str) -> str | None:
    name, _, email = identity.rpartition(" <")
    return identity_refusal(name, email.rstrip(">"), POLICY)


def commit(message: str, author: str = OWNER, sha: str = "a" * 40) -> Commit:
    name, _, email = author.rpartition(" <")
    return Commit(sha, name, email.rstrip(">"), message)


class TheOwnersIdentities(unittest.TestCase):
    def test_every_owner_name_and_email_is_allowed(self) -> None:
        for name in ("J.W. Jonkers", "Joris Jonkers", "Joris Wouter Jonkers", "ExtraToast"):
            for email in (
                "info@jorisjonkers.dev",
                "joriswouterjonkers@gmail.com",
                "ExtraToast@users.noreply.github.com",
                "74975850+ExtraToast@users.noreply.github.com",
            ):
                self.assertIsNone(identity_refusal(name, email, POLICY), f"{name} <{email}>")

    def test_names_and_emails_compare_without_case(self) -> None:
        self.assertIsNone(refusal("joris wouter jonkers <INFO@JorisJonkers.dev>"))

    def test_an_agent_name_on_the_owners_email_is_refused(self) -> None:
        self.assertIn("is not one of the owner's names", refusal("Claude <info@jorisjonkers.dev>"))

    def test_an_owner_domain_agent_address_is_refused(self) -> None:
        self.assertIn('names an AI ("agent")', refusal("Agents Agent <agents@jorisjonkers.dev>"))
        self.assertIn('names an AI ("agent")', refusal("JorisJonkers Agent <agents@jorisjonkers.dev>"))

    def test_a_person_named_after_an_ai_is_refused(self) -> None:
        self.assertIn('names an AI ("claude")', refusal("Claude Opus 5 (1M context) <claude@example.com>"))
        self.assertIn('names an AI ("copilot")', refusal("Copilot <198982749+Copilot@users.noreply.github.com>"))
        self.assertIn('names an AI ("codex")', refusal("Someone <codex@example.com>"))


class OtherPeopleAndAutomation(unittest.TestCase):
    def test_an_external_contributor_is_allowed(self) -> None:
        for identity in ("Jules Verne <jules@example.com>", "Devin Smith <devin@student.utwente.nl>", "Pieter Pipeline <p@example.com>"):
            self.assertIsNone(refusal(identity), identity)

    def test_reviewed_automation_is_allowed(self) -> None:
        for identity in (
            "estate-composition <estate-composition@users.noreply.github.com>",
            "knowledge-ingest-worker <worker@knowledge.jorisjonkers.dev>",
            "basic-memory-vault <basicmemory@knowledge.local>",
            "flux <flux@example.com>",
            "fluxcdbot <fluxcdbot@users.noreply.github.com>",
        ):
            self.assertIsNone(refusal(identity), identity)

    def test_automation_under_another_address_is_not_automation(self) -> None:
        policy = Policy.load(self.identities("refuse"))
        self.assertIsNotNone(identity_refusal("knowledge-ingest-worker", "worker@example.com", policy))
        self.assertIsNone(identity_refusal("flux", "anything@example.com", policy))

    def test_refuse_mode_allows_only_the_owner_automation_and_bots(self) -> None:
        policy = Policy.load(self.identities("refuse"))
        self.assertIn("is not the owner, reviewed automation", identity_refusal("Jules Verne", "jules@example.com", policy))
        self.assertIsNone(identity_refusal("J.W. Jonkers", "info@jorisjonkers.dev", policy))
        self.assertIsNone(identity_refusal("renovate[bot]", "29139614+renovate[bot]@users.noreply.github.com", policy))

    def test_an_unknown_mode_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be \"allow\" or \"refuse\""):
            Policy.load(self.identities("maybe"))

    def identities(self, mode: str) -> Path:
        data = json.loads(DEFAULT_IDENTITIES.read_text(encoding="utf-8"))
        data["non_owner_humans"]["mode"] = mode
        path = Path(tempfile.mkdtemp()) / "identities.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


class GitHubAppBots(unittest.TestCase):
    def test_bots_that_are_not_agents_are_allowed_with_and_without_an_id(self) -> None:
        for identity in (
            RENOVATE,
            "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
            "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>",
            "jorisjonkers-dev-agents[bot] <290081373+jorisjonkers-dev-agents[bot]@users.noreply.github.com>",
            "personal-stack-agents[bot] <personal-stack-agents[bot]@users.noreply.github.com>",
            "blueshell-release[bot] <1+blueshell-release[bot]@users.noreply.github.com>",
            "estate-collector[bot] <337023833+estate-collector[bot]@users.noreply.github.com>",
            "pipeline-ops[bot] <1+pipeline-ops[bot]@users.noreply.github.com>",
        ):
            self.assertIsNone(refusal(identity), identity)

    def test_ai_agent_bots_are_refused(self) -> None:
        for slug, marker in (
            ("copilot-swe-agent", "copilot"),
            ("claude", "claude"),
            ("chatgpt-codex-connector", "chatgpt"),
            ("devin-ai-integration", "devin"),
            ("cursor", "cursor"),
            ("google-labs-jules", "jules"),
            ("gemini-code-assist", "gemini"),
            ("coderabbitai", "coderabbit"),
            ("gpt-helper", "gpt"),
        ):
            why = refusal(f"{slug}[bot] <1+{slug}[bot]@users.noreply.github.com>")
            self.assertIn(f'is an AI agent\'s bot ("{marker}")', why, slug)

    def test_a_bot_address_under_another_name_is_refused(self) -> None:
        why = refusal("Claude <29139614+renovate[bot]@users.noreply.github.com>")
        self.assertIn("must carry its own name, renovate[bot]", why)


class ProviderAddresses(unittest.TestCase):
    def test_provider_addresses_are_refused(self) -> None:
        for email in (
            "noreply@anthropic.com",
            "vibe@mistral.ai",
            "codex@openai.com",
            "bot@z.ai",
            "agent@mail.deepseek.com",
            "x@gemini.google.com",
            "x@opencode.ai",
            "hermes@nousresearch.com",
            "agent@cursor.com",
        ):
            self.assertIn("is an AI provider's address", refusal(f"Someone <{email}>"), email)

    def test_a_label_only_matches_a_whole_label(self) -> None:
        self.assertFalse(POLICY.is_provider_domain("cursors-r-us.example"))
        self.assertFalse(POLICY.is_provider_domain("buzz.ai"))


class ReadingALine(unittest.TestCase):
    def test_an_owner_co_author_trailer_is_allowed(self) -> None:
        self.assertIsNone(line_refusal(f"Co-authored-by: {OWNER}", POLICY))
        self.assertIsNone(line_refusal(f"Co-authored-by: {RENOVATE}", POLICY))

    def test_an_agent_co_author_trailer_is_refused(self) -> None:
        why = line_refusal("Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>", POLICY)
        self.assertIn("a Co-Authored-By trailer credits someone not allowed", why)
        self.assertIn("AI provider's address", why)

    def test_an_owner_domain_agent_co_author_is_refused(self) -> None:
        self.assertIsNotNone(line_refusal("Co-authored-by: JorisJonkers Agent <agents@jorisjonkers.dev>", POLICY))

    def test_a_trailer_without_an_email_is_refused(self) -> None:
        self.assertEqual(line_refusal("Co-authored-by: Claude", POLICY), "a Co-Authored-By trailer that is not Name <email>")

    def test_a_credit_to_an_ai_is_refused(self) -> None:
        for line, marker in (
            ("Generated by Mistral Vibe.", "mistral"),
            ("🤖 Generated with [Claude Code](https://claude.com/claude-code)", "claude"),
            ("Reviewed by mistral-large (runner)", "mistral"),
            ("ADR-0009 say so. Reviewed by deepseek-flash and mistral-large.", "deepseek"),
            ("Reviewed by Kimi K2.7 and Qwen 3.8 Flash (OpenCode, read-only) and by", "kimi"),
            ("- **Reviews:** four passes, written by GLM 5.3 through Vibe, each reviewed by Haiku.", "glm"),
            ("each reviewed by Haiku", "haiku"),
            ("Drafted with gpt-5 via the gateway", "gpt"),
            ("Generated with Claude, merged by Renovate", "claude"),
        ):
            self.assertEqual(line_refusal(line, POLICY), f'a credit to an AI ("{marker}")', line)

    def test_a_credit_to_a_person_or_tool_is_allowed(self) -> None:
        for line in (
            "oracle file is reviewed by hand and never written by CI, moves into 0088, and",
            "- Methods generated by the Kotlin compiler for functions with JvmStatic annotation are filtered out",
            "written by the pipeline",
            # Prose about the rule names a provider only after the clause the credit sits in.
            '- Refused: a `Co-Authored-By` trailer naming an agent, a "Generated with/by" line, an Anthropic noreply address.',
            '"Generated with" PR line, and nothing stopped a commit that carried them.',
            "Claude Code kept adding its trailer and its \"Generated with\" PR line",
        ):
            self.assertIsNone(line_refusal(line, POLICY), line)

    def test_a_line_leading_banner_naming_no_allowed_generator_is_refused(self) -> None:
        for line in ("Generated with a tool", "This PR was generated by our script", "> generated by something"):
            self.assertEqual(line_refusal(line, POLICY), 'a "generated with/by" banner', line)

    def test_a_renovate_or_release_please_banner_is_allowed(self) -> None:
        for line in (
            "This PR was generated by [Mend Renovate](https://mend.io/renovate/). View the [repository job log](https://developer.mend.io/github/JorisJonkers-dev/agent-kit).",
            "This PR was generated with [Release Please](https://github.com/googleapis/release-please).",
            "This PR has been generated by Dependabot",
        ):
            self.assertIsNone(line_refusal(line, POLICY), line)

    def test_a_trailer_whose_key_names_an_ai_is_refused(self) -> None:
        why = line_refusal("Claude-Session: https://claude.ai/code/session_01PPnwbKr1Ex9iXAtv6TThAU", POLICY)
        self.assertEqual(why, 'a trailer that names an AI ("claude")')

    def test_a_by_trailer_crediting_an_ai_is_refused(self) -> None:
        self.assertIn("a Reviewed-by trailer credits an AI", line_refusal("Reviewed-by: Claude Opus 5", POLICY))
        why = line_refusal("Signed-off-by: Claude <noreply@anthropic.com>", POLICY)
        self.assertIn("a Signed-off-by trailer credits an AI: <noreply@anthropic.com> is an AI provider's address", why)

    def test_a_by_trailer_crediting_a_person_is_allowed(self) -> None:
        self.assertIsNone(line_refusal(f"Signed-off-by: {OWNER}", POLICY))
        self.assertIsNone(line_refusal("Reviewed-by: Devin Smith", POLICY))
        self.assertIsNone(line_refusal("Release-As: 1.0.0", POLICY))

    def test_the_robot_emoji_is_refused(self) -> None:
        self.assertEqual(line_refusal("Done 🤖", POLICY), "the robot emoji")
        self.assertEqual(line_refusal("- [ ] 🤖 Pick PR", POLICY), "the robot emoji")

    def test_the_robot_emoji_in_renovates_checkbox_is_allowed(self) -> None:
        self.assertIsNone(line_refusal(" - [ ] <!-- manual job -->🤖 Pick PR", POLICY))
        self.assertIsNone(line_refusal("* [x] <!-- rebase-check -->🤖 Rebase", POLICY))

    def test_a_provider_address_anywhere_is_refused(self) -> None:
        why = line_refusal("Ping noreply@anthropic.com for this", POLICY)
        self.assertEqual(why, "an AI provider's address <noreply@anthropic.com>")

    def test_an_ordinary_address_is_allowed(self) -> None:
        self.assertIsNone(line_refusal("Reported by someone@example.com", POLICY))

    def test_a_claude_code_link_is_refused(self) -> None:
        for line in ("https://claude.ai/code/session_01ABC", "see claude.com/claude-code"):
            self.assertEqual(line_refusal(line, POLICY), "a Claude Code link", line)

    def test_naming_a_package_or_product_is_allowed(self) -> None:
        for line in (
            "| [@anthropic-ai/claude-code](https://redirect.github.com/anthropics/claude-code) | `2.1.272` → `2.1.316` |",
            "| [@openai/codex](https://redirect.github.com/openai/codex) | `0.154.0` → `0.166.0` |",
            "- Fixed a resumed claude.ai cloud session",
            "fix(critique): detect Vibe sign-in on Linux",
        ):
            self.assertIsNone(line_refusal(line, POLICY), line)


class PullRequestAuthors(unittest.TestCase):
    def test_ai_agent_authors_are_refused(self) -> None:
        for login, marker in (("claude", "claude"), ("Copilot", "copilot"), ("app/copilot-swe-agent", "copilot"), ("devin-ai-integration[bot]", "devin")):
            self.assertEqual(author_refusal(login, POLICY), f'{login} is an AI agent ("{marker}")', login)

    def test_the_owner_people_and_automation_are_allowed(self) -> None:
        for login in ("ExtraToast", "app/renovate", "renovate[bot]", "app/jorisjonkers-dev-agents", "devin-smith"):
            self.assertIsNone(author_refusal(login, POLICY), login)


class CheckingAPullRequest(unittest.TestCase):
    def test_findings_name_the_line(self) -> None:
        self.assertEqual(text_findings("ok\nDone 🤖\n", POLICY), ["line 2: the robot emoji: Done 🤖"])

    def test_a_clean_pull_request_has_no_failures(self) -> None:
        messages = [commit(f"fix: x\n\nCo-authored-by: {RENOVATE}"), commit("chore: y", author=RENOVATE)]
        self.assertEqual(check("fix: x", "Body.", messages, POLICY), [])

    def test_every_offence_is_reported_with_where_it_is(self) -> None:
        failures = check(
            "fix: x 🤖",
            "Generated with Codex",
            [commit("fix: y\n\nCo-Authored-By: Codex <codex@openai.com>", author="Codex <codex@openai.com>", sha="b" * 40)],
            POLICY,
        )
        self.assertEqual(len(failures), 4)
        self.assertTrue(failures[0].startswith("pull request title: line 1: the robot emoji"))
        self.assertTrue(failures[1].startswith('pull request body: line 1: a credit to an AI ("codex")'))
        self.assertTrue(failures[2].startswith(f'commit {"b" * 12} "fix: y": author Codex <codex@openai.com>'))
        self.assertTrue(failures[3].startswith(f'commit {"b" * 12} "fix: y": message line 3: a Co-Authored-By trailer'))

    def test_the_pull_request_author_is_checked(self) -> None:
        self.assertEqual(check("fix: x", "", [], POLICY, "claude"), ['pull request author: claude is an AI agent ("claude")'])

    def test_an_empty_message_is_labelled(self) -> None:
        self.assertEqual(commit("").label, f'commit {"a" * 12} ""')


class RealPullRequests(unittest.TestCase):
    """Bodies and a commit read from agent-kit pull requests on 2026-11-09."""

    def test_a_renovate_body_passes(self) -> None:
        for name in ("renovate-agent-kit-73.md", "renovate-agent-kit-212.md"):
            body = (FIXTURES / name).read_text(encoding="utf-8")
            self.assertEqual(check("chore(deps): update minor and patch dependencies", body, [], POLICY), [], name)

    def test_a_release_please_body_passes(self) -> None:
        body = (FIXTURES / "release-please-agent-kit-268.md").read_text(encoding="utf-8")
        bot = "jorisjonkers-dev-agents[bot] <290081373+jorisjonkers-dev-agents[bot]@users.noreply.github.com>"
        self.assertEqual(check("chore(main): release 6.0.0", body, [commit("chore(main): release 6.0.0", author=bot)], POLICY), [])

    def test_a_mistral_vibe_commit_fails_on_its_banner_and_its_trailer(self) -> None:
        message = (FIXTURES / "mistral-vibe-commit-agent-kit-391.txt").read_text(encoding="utf-8")
        failures = check("fix(model-gateway): list only the Vibe models Vibe accepts", "", [commit(message)], POLICY)
        self.assertEqual(len(failures), 2, failures)
        self.assertIn('a credit to an AI ("mistral"): Generated by Mistral Vibe.', failures[0])
        self.assertIn("<vibe@mistral.ai> is an AI provider's address", failures[1])


def git(repo: str, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=env).stdout


class ReadingCommits(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q", "-b", "main")
        self.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def commit(self, message: str, name: str = "J.W. Jonkers", email: str = "info@jorisjonkers.dev") -> str:
        env = {**self.env, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com"}
        git(self.repo, "commit", "-q", "--allow-empty", "-m", message, env=env)
        return git(self.repo, "rev-parse", "HEAD").strip()

    def test_reads_each_commits_author_and_full_message(self) -> None:
        base = self.commit("chore: base")
        self.commit("fix: one\n\nbody line\n\nCo-authored-by: Claude <noreply@anthropic.com>", name="Claude", email="noreply@anthropic.com")
        head = self.commit("fix: two")
        found = commits_in(f"{base}..{head}", self.repo)
        self.assertEqual([c.message.splitlines()[0] for c in found], ["fix: two", "fix: one"])
        self.assertEqual((found[1].author_name, found[1].author_email), ("Claude", "noreply@anthropic.com"))
        self.assertTrue(found[1].message.endswith("Co-authored-by: Claude <noreply@anthropic.com>"))

    def test_main_fails_with_an_error_per_offence(self) -> None:
        base = self.commit("chore: base")
        head = self.commit("fix: one\n\nGenerated by Mistral Vibe.", name="Mistral Vibe", email="vibe@mistral.ai")
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.dict(os.environ, {"PR_TITLE": "fix: one", "PR_BODY": ""}):
            cwd = os.getcwd()
            os.chdir(self.repo)
            try:
                code = main(["--range", f"{base}..{head}"])
            finally:
                os.chdir(cwd)
        self.assertEqual(code, 1)
        errors = [line for line in out.getvalue().splitlines() if line.startswith("::error::")]
        self.assertEqual(len(errors), 2, out.getvalue())
        self.assertIn("author Mistral Vibe <vibe@mistral.ai>", errors[0])
        self.assertIn("Generated by Mistral Vibe.", errors[1])

    def test_main_passes_a_clean_title_and_body_without_a_range(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(["--title", "fix: x", "--body", "Plain body."])
        self.assertEqual(code, 0)
        self.assertIn("0 commit(s) credit no AI", out.getvalue())

    def test_main_reads_another_identities_file_and_the_author(self) -> None:
        data = json.loads(DEFAULT_IDENTITIES.read_text(encoding="utf-8"))
        data["non_owner_humans"]["mode"] = "refuse"
        path = Path(self.repo) / "identities.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        body = "Co-authored-by: Ada Lovelace <ada@example.com>"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--title", "fix: x", "--body", body]), 0)
            self.assertEqual(main(["--title", "fix: x", "--body", body, "--identities", str(path)]), 1)
            self.assertEqual(main(["--title", "fix: x", "--pr-author", "claude"]), 1)


class TheWorkflow(unittest.TestCase):
    """The guard is wired into the hygiene workflow every consumer already calls."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_the_attribution_job_runs_the_script_from_the_pinned_checkout(self) -> None:
        self.assertIn("'attribution':", self.text)
        self.assertIn(".github-workflows/scripts/attribution_guard.py", self.text)

    def test_the_override_label_does_not_skip_it(self) -> None:
        job = self.text.split("'attribution':", 1)[1]
        self.assertNotIn("inputs.override-label", job)

    def test_it_reads_the_title_and_body_through_the_environment(self) -> None:
        self.assertIn("'PR_TITLE': '${{ github.event.pull_request.title }}'", self.text)
        self.assertIn("'PR_BODY': '${{ github.event.pull_request.body }}'", self.text)
        self.assertIn("'PR_AUTHOR': '${{ github.event.pull_request.user.login }}'", self.text)


if __name__ == "__main__":
    unittest.main()
