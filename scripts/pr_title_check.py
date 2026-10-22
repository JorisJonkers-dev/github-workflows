#!/usr/bin/env python3
"""Fail a pull request whose title or commits are not Conventional Commits, or that credits a coding agent.

release-please reads the squash-merged title, so a title it cannot parse is a change the
changelog never shows. The estate also carries no agent attribution anywhere (workspace
CLAUDE.md), so the pull request body and every commit message in the range are scanned for a
Co-Authored-By trailer naming a coding agent, a "generated with" banner naming one, or a link
back to an agent session. Ported from deploy-kit's scripts/check-pr-title.ts, so every
repository applies the same rules.

    pr_title_check.py --title "feat(ci): add a workflow" [--body TEXT] [--range BASE..HEAD]

The title and body fall back to PR_TITLE and PR_BODY in the environment, which is how the
reusable workflow passes them without quoting them through a shell.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess

# The Conventional Commits types release-please understands.
COMMIT_TYPES = ("feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert")

CONVENTIONAL = re.compile(rf"^({'|'.join(COMMIT_TYPES)})(\([a-z0-9._/-]+\))?!?: .+")
# Lines release-please and GitHub write themselves, which follow no type.
GENERATED = re.compile(r"^(Merge\s|Revert\s|chore\(main\): release\s|Release\s)")

# Brand terms only: Cody, Devin and Jules are names real people carry, so an agent known by a
# plain name is matched by its bot account instead.
AGENT_MARKERS = (
    "claude",
    "anthropic",
    "copilot",
    "chatgpt",
    "openai",
    "codex",
    "cursor",
    "windsurf",
    "aider",
    "codeium",
    "tabnine",
    "devin-ai-integration",
    "devin.ai",
    "google-labs-jules",
    "jules[bot]",
    "amazon-q-developer",
    "codewhisperer",
)

SESSION_HOSTS = (
    "claude.ai",
    "chatgpt.com",
    "chat.openai.com",
    "devin.ai",
    "cursor.sh",
    "windsurf.com",
    "windsurf.ai",
    "aider.chat",
    "jules.google.com",
)

CO_AUTHOR = re.compile(r"^co-authored-by:\s*(.+)$", re.IGNORECASE)
BANNER = re.compile(r"\bgenerated\s+(with|by|using)\b", re.IGNORECASE)
# A host matches only at a host boundary: after the scheme and any subdomains, and before `/`,
# `:`, whitespace or the end. Unanchored, `https://example.com/claude.ai/x` would match, and an
# unescaped dot would let `chatXopenai.com` match `chat.openai.com`.
SESSION_LINK = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*(?:" + "|".join(map(re.escape, SESSION_HOSTS)) + r")(?=[/:\s]|$)",
    re.IGNORECASE,
)


def failure_reason(subject: str) -> str | None:
    """Why a subject line is not a Conventional Commit, or None when it is one."""
    if GENERATED.match(subject) or CONVENTIONAL.match(subject):
        return None
    return (
        f'"{subject}" is not a Conventional Commit. Expected type(scope)!: subject, '
        f"e.g. feat(ci): add a workflow, with type one of {', '.join(COMMIT_TYPES)}."
    )


def agent_marker(text: str) -> str | None:
    lower = text.lower()
    return next((marker for marker in AGENT_MARKERS if marker in lower), None)


def attribution_findings(text: str) -> list[str]:
    """Every line of text that credits a coding agent, each with the reason."""
    findings = []
    for raw in text.splitlines():
        line = raw.strip()
        co_author = CO_AUTHOR.match(line)
        marker = agent_marker(co_author.group(1)) if co_author else None
        if marker:
            findings.append(f'a Co-Authored-By trailer names a coding agent ("{marker}"); credit a person or remove it: {line}')
            continue
        marker = agent_marker(line) if BANNER.search(line) else None
        if marker:
            findings.append(f'a "generated with" banner names a coding agent ("{marker}"); remove it: {line}')
            continue
        if SESSION_LINK.search(line):
            findings.append(f"a link back to a coding agent session; remove it: {line}")
    return findings


def commit_messages(commit_range: str) -> list[str]:
    """The full message of every commit in the range, newest first."""
    out = subprocess.run(
        ["git", "log", "--format=%B%x00", commit_range], check=True, capture_output=True, text=True
    ).stdout
    return [message.strip("\n") for message in out.split("\0") if message.strip("\n")]


def check(title: str, body: str, messages: list[str]) -> list[str]:
    """Every failure in the title, the body and the commit messages. Empty means clean."""
    failures = []
    if (why := failure_reason(title)) is not None:
        failures.append(f"pull request title: {why}")
    failures += [f"pull request body: {finding}" for finding in attribution_findings(body)]
    for message in messages:
        subject = message.splitlines()[0]
        if (why := failure_reason(subject)) is not None:
            failures.append(f'commit "{subject}": {why}')
        failures += [f'commit "{subject}": {finding}' for finding in attribution_findings(message)]
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a pull request's title, body and commits.")
    parser.add_argument("--title", default=os.environ.get("PR_TITLE", ""))
    parser.add_argument("--body", default=os.environ.get("PR_BODY", ""))
    parser.add_argument("--range", dest="commit_range", help="BASE..HEAD, the pull request's commits")
    args = parser.parse_args(argv)
    if not args.title:
        parser.error("no title: pass --title or set PR_TITLE")

    messages = commit_messages(args.commit_range) if args.commit_range else []
    failures = check(args.title, args.body, messages)
    for failure in failures:
        print(f"::error::{failure}")
    if failures:
        return 1
    print(f"title, body and {len(messages)} commit(s) are conventional and carry no agent attribution")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
