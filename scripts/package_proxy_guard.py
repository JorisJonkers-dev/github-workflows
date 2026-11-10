#!/usr/bin/env python3
"""Find package installs in a caller's checkout that bypass the packages proxy.

Every JorisJonkers-dev repository installs third-party packages through
packages.jorisjonkers.dev. This guard scans a checkout and reports eight rules:

- uncovered-install: a workflow step runs an installer (npm, pnpm, yarn, pip,
  uv, cargo, go, gradle, ./gradlew, mvn, ./mvnw, task install, mise run install,
  mise install) in a job that sets up the proxy
  with a token before it. A covering step is a setup-package-proxy, setup-java-gradle
  or setup-node step given its token, or a run step that writes the proxy netrc or
  the proxy index variables inline. Coverage is by step order, not by if:
  conditions, which the guard does not evaluate.
- reusable-without-token: a job that calls a shared workflow which installs
  packages (INSTALLING_WORKFLOWS) must pass the proxy token: secrets: inherit or
  PACKAGES_PROXY_TOKEN. Other reusable calls, such as add-to-project,
  repository-hygiene-guard and release, are not checked.
- dockerfile-netrc: a Dockerfile RUN installs go, pip or uv packages without the
  packages-netrc secret mount, or installs npm packages without the packages-npmrc
  mount. It also reports a RUN that uses an index build arg (GOPROXY,
  PIP_INDEX_URL, UV_DEFAULT_INDEX) its stage does not declare. A FROM starts a
  new stage, so ARG declarations do not carry across stages.
- npmrc-registry: a committed .npmrc sets registry= to anything but the proxy.
- gradle-repository: a Gradle build file names a third-party Maven repository
  URL directly. mavenCentral(), gradlePluginPortal() and google() are allowed,
  because the init script replaces them in CI.
- gradle-wrapper: a gradle/wrapper/gradle-wrapper.properties file whose
  distributionUrl is not on https://packages.jorisjonkers.dev/.
- helm-repository: a Flux HelmRepository whose spec.url is not on
  https://packages.jorisjonkers.dev/helm/.
- allowlist: an entry in .github/package-proxy-allow.txt has no reason.

OS packages (apt, apk) are out of scope. Installers are matched by text on run:
lines, so an installer inside a script file is not seen.

Allowlist lines are "<path glob> <reason>". A finding whose path matches an
entry is counted as allowlisted rather than reported.

    package_proxy_guard.py [--root DIR] [--allowlist PATH] [--report-only]

--report-only always exits 0. Without it, any reported finding exits 1.
Usage errors exit 2.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

SKIP_DIRS = frozenset({".git", ".github-workflows", "node_modules", "target", "build", ".gradle", "vendor"})
DEFAULT_ALLOWLIST = ".github/package-proxy-allow.txt"
PROXY_NPM = "https://packages.jorisjonkers.dev/npm/"
PROXY_GRADLE = "https://packages.jorisjonkers.dev/generic/gradle/distributions/"
PROXY_HELM = "https://packages.jorisjonkers.dev/helm/"
PROXY_HOSTS = frozenset({"packages.jorisjonkers.dev", "maven.pkg.github.com"})
GRADLE_NAMES = frozenset({"settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"})
SHARED_WORKFLOW_PREFIX = "JorisJonkers-dev/github-workflows/.github/workflows/"
TOKEN_SECRET = "PACKAGES_PROXY_TOKEN"
# The shared reusable workflows that install packages, so a caller must pass them the token.
# tests/test_package_proxy_guard.py derives this set from the workflows of this repository and
# fails when the two drift apart.
INSTALLING_WORKFLOWS = frozenset(
    {
        "node-ci.yml",
        "jvm-ci.yml",
        "python-ci.yml",
        "container-publish.yml",
        "publish-fragment.yml",
        "mutation-report.yml",
        "publish-api-clients.yml",
        "path-buckets.yml",
        "docker-image-ci.yml",
        "crac-train.yml",
    }
)

# Each installer: a pattern, the manager it belongs to, and the build arg the
# Dockerfile must declare for it (None when none is needed). The order matters:
# the first match names the command.
INSTALLERS = (
    (re.compile(r"\bnpm (install|i|ci)\b"), "node", None),
    (re.compile(r"\bpnpm (install|i)\b"), "node", None),
    (re.compile(r"\byarn install\b"), "node", None),
    (re.compile(r"\buv (sync|pip)\b"), "python", "UV_DEFAULT_INDEX"),
    (re.compile(r"\bpip3? install\b"), "python", "PIP_INDEX_URL"),
    (re.compile(r"\bcargo (build|fetch|install)\b"), "cargo", None),
    (re.compile(r"\bgo (mod (download|tidy)|build|test|install)\b"), "go", "GOPROXY"),
    (re.compile(r"\bgradle\b|\./gradlew\b|\./mvnw\b"), "jvm", None),
    (re.compile(r"\bmvn\b"), "jvm", None),
)

# Task runners that install packages through a taskfile or mise.toml the guard
# cannot read. Workflow run lines only: a Dockerfile RUN is not matched, and the
# reusable workflows' own derivation (match_installer) is unaffected.
TASK_RUNNER_INSTALLERS = (
    (re.compile(r"(?<![\w./-])task install(?![\w:./-])"), "task", None),
    (re.compile(r"(?<![\w./-])mise run install(?![\w:./-])"), "mise", None),
    (re.compile(r"(?<![\w./-])mise install(?![\w:./-])"), "mise", None),
)

# The Dockerfile secret mount each manager's installs need. Cargo and jvm have none.
MOUNTS = {
    "node": "id=packages-npmrc",
    "python": "id=packages-netrc",
    "go": "id=packages-netrc",
}

# A step that sets up the proxy with its token, as an action.
COVERING_USES = re.compile(
    r"^(?:JorisJonkers-dev/github-workflows|\./\.github-workflows|\.)/actions/"
    r"(?:setup-package-proxy|setup-java-gradle|setup-node)(?:@\S+)?$"
)
TOKEN_KEYS = ("token", "packages-proxy-token")
# A run step that sets up the proxy inline: the netrc, an index variable, or the npm registry.
INLINE_PROXY_RE = re.compile(
    r"machine packages\.jorisjonkers\.dev"
    r"|(?:PIP_INDEX_URL|UV_DEFAULT_INDEX|GOPROXY)=https://packages\.jorisjonkers\.dev"
    r"|registry=https://packages\.jorisjonkers\.dev/npm/"
)
URL_RE = re.compile(r"https?://[^\s\"')]+")
GRADLE_KEYWORD_RE = re.compile(r"maven|url|uri\(")
NPMRC_REGISTRY_RE = re.compile(r"^\s*registry\s*=\s*(\S+)")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    message: str


@dataclass(frozen=True)
class Installer:
    command: str
    manager: str
    index: str | None


@dataclass(frozen=True)
class Allow:
    pattern: str
    reason: str


def match_installer(text: str) -> Installer | None:
    """The first installer command in text, or None when it runs none."""
    for pattern, manager, index in INSTALLERS:
        match = pattern.search(text)
        if match:
            return Installer(match.group(0), manager, index)
    return None


def match_workflow_installer(text: str) -> Installer | None:
    """match_installer, plus task runners that install (task install, mise install)."""
    hit = match_installer(text)
    if hit:
        return hit
    for pattern, manager, index in TASK_RUNNER_INSTALLERS:
        match = pattern.search(text)
        if match:
            return Installer(match.group(0), manager, index)
    return None


def mapping_get(node: Node | None, key: str) -> Node | None:
    """The value under key in a YAML mapping node, or None."""
    if not isinstance(node, MappingNode):
        return None
    for key_node, value in node.value:
        if key_node.value == key:
            return value
    return None


def line_of(scalar: ScalarNode, index: int) -> int:
    """1-based file line of the index-th line of a YAML scalar's value."""
    first = scalar.start_mark.line + 1
    if scalar.style in ("|", ">"):
        return first + 1 + index
    return first + index


def step_covers(step: Node) -> bool:
    """True when a step sets up the proxy: an action given its token, or an inline setup."""
    run = mapping_get(step, "run")
    if isinstance(run, ScalarNode) and INLINE_PROXY_RE.search(run.value):
        return True
    uses = mapping_get(step, "uses")
    if not isinstance(uses, ScalarNode) or not COVERING_USES.match(uses.value):
        return False
    with_node = mapping_get(step, "with")
    for key in TOKEN_KEYS:
        value = mapping_get(with_node, key)
        if isinstance(value, ScalarNode) and value.value.strip():
            return True
    return False


def calls_installing_workflow(job: Node) -> bool:
    """True when a job calls a shared workflow in INSTALLING_WORKFLOWS."""
    uses = mapping_get(job, "uses")
    if not isinstance(uses, ScalarNode) or not uses.value.startswith(SHARED_WORKFLOW_PREFIX):
        return False
    return uses.value[len(SHARED_WORKFLOW_PREFIX):].split("@", 1)[0] in INSTALLING_WORKFLOWS


def passes_token(job: Node) -> bool:
    """True when a reusable-workflow call hands over the proxy token. Callers check the call first."""
    secrets = mapping_get(job, "secrets")
    if isinstance(secrets, ScalarNode):
        return secrets.value == "inherit"
    if isinstance(secrets, MappingNode):
        return any(key.value.upper() == TOKEN_SECRET for key, _ in secrets.value)
    return False


def scan_workflow(rel: str, text: str, findings: list[Finding]) -> None:
    try:
        document = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as error:
        findings.append(Finding(rel, 1, "unparsable", f"cannot parse the workflow YAML: {error.__class__.__name__}"))
        return
    jobs = mapping_get(document, "jobs")
    if jobs is None:
        return
    for _, job in jobs.value:
        uses = mapping_get(job, "uses")
        if uses is not None:
            if calls_installing_workflow(job) and not passes_token(job):
                findings.append(
                    Finding(
                        rel,
                        uses.start_mark.line + 1,
                        "reusable-without-token",
                        "reusable workflow call must pass the packages proxy token: "
                        "secrets: inherit, or PACKAGES_PROXY_TOKEN, from a JorisJonkers-dev/github-workflows call",
                    )
                )
            continue
        steps_node = mapping_get(job, "steps")
        steps = steps_node.value if isinstance(steps_node, SequenceNode) else []
        covering = [index for index, step in enumerate(steps) if step_covers(step)]
        first_cover = covering[0] if covering else None
        for index, step in enumerate(steps):
            run = mapping_get(step, "run")
            if not isinstance(run, ScalarNode):
                continue
            if first_cover is not None and first_cover < index:
                continue
            for line_index, line in enumerate(run.value.splitlines()):
                hit = match_workflow_installer(line)
                if hit:
                    findings.append(
                        Finding(
                            rel,
                            line_of(run, line_index),
                            "uncovered-install",
                            f"{hit.command} runs outside the packages proxy: set up "
                            "actions/setup-package-proxy with its token before it, or call a shared reusable workflow",
                        )
                    )


def dockerfile_instructions(text: str):
    """Yield (line, instruction) with backslash continuations joined."""
    start: int | None = None
    parts: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if start is None:
            if not stripped or stripped.startswith("#"):
                continue
            start = number
        if stripped.endswith("\\"):
            parts.append(stripped[:-1].rstrip())
            continue
        parts.append(stripped)
        yield start, " ".join(parts)
        start, parts = None, []
    if start is not None:
        yield start, " ".join(parts)


def scan_dockerfile(rel: str, text: str, findings: list[Finding]) -> None:
    declared: set[str] = set()
    for number, instruction in dockerfile_instructions(text):
        words = instruction.split()
        keyword = words[0].upper() if words else ""
        if keyword == "FROM":
            declared = set()
        elif keyword == "ARG" and len(words) > 1:
            declared.add(words[1].split("=")[0])
        elif keyword == "RUN":
            hit = match_installer(instruction)
            if hit is None:
                continue
            mount = MOUNTS.get(hit.manager)
            if mount is not None and mount not in instruction:
                findings.append(
                    Finding(
                        rel,
                        number,
                        "dockerfile-netrc",
                        f"RUN installs packages ({hit.command}) without the {mount.removeprefix('id=')} secret mount",
                    )
                )
            if hit.index is not None and hit.index not in declared:
                findings.append(
                    Finding(
                        rel,
                        number,
                        "dockerfile-netrc",
                        f"RUN installs packages ({hit.command}) but its stage does not declare ARG {hit.index}",
                    )
                )


def scan_npmrc(rel: str, text: str, findings: list[Finding]) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        match = NPMRC_REGISTRY_RE.match(line)
        if match and not match.group(1).startswith(PROXY_NPM):
            findings.append(
                Finding(rel, number, "npmrc-registry", f"registry must be {PROXY_NPM}, found {match.group(1)}")
            )


def scalar_value(node: Node | None) -> str | None:
    return node.value if isinstance(node, ScalarNode) else None


def scan_helm_repository(rel: str, text: str, findings: list[Finding]) -> None:
    """Scan a YAML file for Flux HelmRepository resources whose spec.url bypasses the proxy."""
    try:
        documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        return
    for document in documents:
        api_version = scalar_value(mapping_get(document, "apiVersion"))
        if not (api_version or "").startswith("source.toolkit.fluxcd.io/"):
            continue
        if scalar_value(mapping_get(document, "kind")) != "HelmRepository":
            continue
        spec = mapping_get(document, "spec")
        url_node = mapping_get(spec, "url")
        if not isinstance(url_node, ScalarNode):
            continue
        url = url_node.value
        if scalar_value(mapping_get(spec, "type")) == "oci":
            if url.startswith("oci://packages.jorisjonkers.dev/"):
                continue
            expected = "oci://packages.jorisjonkers.dev/"
        elif url.startswith(PROXY_HELM):
            continue
        else:
            expected = PROXY_HELM
        findings.append(
            Finding(rel, line_of(url_node, 0), "helm-repository", f"spec.url must be {expected}, found {url}")
        )


def scan_gradle_wrapper(rel: str, text: str, findings: list[Finding]) -> None:
    """Scan a gradle-wrapper.properties file for a non-proxy distributionUrl."""
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("distributionUrl="):
            url = line.split("=", 1)[1].strip()
            # Unescape the colon in the URL
            url = url.replace("\\:", ":")
            if not url.startswith(PROXY_GRADLE):
                findings.append(
                    Finding(
                        rel,
                        number,
                        "gradle-wrapper",
                        f"distributionUrl must be {PROXY_GRADLE}<version>-<type>.zip, found {url}",
                    )
                )


def scan_gradle(rel: str, text: str, findings: list[Finding]) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("//", "#", "*", "/*")):
            continue
        if not GRADLE_KEYWORD_RE.search(stripped):
            continue
        for url in URL_RE.findall(stripped):
            host = urlsplit(url).hostname or ""
            if host not in PROXY_HOSTS:
                findings.append(
                    Finding(
                        rel,
                        number,
                        "gradle-repository",
                        f"direct Maven repository {url} must be replaced by the packages proxy "
                        "(mavenCentral(), gradlePluginPortal() and google() are allowed)",
                    )
                )


def is_dockerfile(name: str) -> bool:
    return name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith((".Dockerfile", ".dockerfile"))


def scan_file(rel: str, path: Path, findings: list[Finding]) -> None:
    name = path.name
    if rel.startswith(".github/workflows/") and name.endswith((".yml", ".yaml")):
        scan_workflow(rel, path.read_text(encoding="utf-8", errors="replace"), findings)
    elif is_dockerfile(name):
        scan_dockerfile(rel, path.read_text(encoding="utf-8", errors="replace"), findings)
    elif name == ".npmrc":
        scan_npmrc(rel, path.read_text(encoding="utf-8", errors="replace"), findings)
    elif name in GRADLE_NAMES:
        scan_gradle(rel, path.read_text(encoding="utf-8", errors="replace"), findings)
    elif rel.endswith("gradle/wrapper/gradle-wrapper.properties"):
        scan_gradle_wrapper(rel, path.read_text(encoding="utf-8", errors="replace"), findings)
    elif not rel.startswith(".github/workflows/") and name.endswith((".yaml", ".yml")):
        scan_helm_repository(rel, path.read_text(encoding="utf-8", errors="replace"), findings)


def walk_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            yield Path(dirpath) / name


def load_allowlist(path: Path, rel: str) -> tuple[list[Allow], list[Finding]]:
    """Valid allowlist entries, and a finding for each entry without a reason."""
    if not path.is_file():
        return [], []
    entries: list[Allow] = []
    problems: list[Finding] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        pattern = parts[0]
        reason = parts[1].strip() if len(parts) > 1 else ""
        if reason:
            entries.append(Allow(pattern, reason))
        else:
            problems.append(Finding(rel, number, "allowlist", f"entry for {pattern} has no reason"))
    return entries, problems


def collect(root: Path, allowlist: str) -> tuple[list[Finding], int]:
    """Every reported finding under root, and how many the allowlist suppressed."""
    raw: list[Finding] = []
    for path in walk_files(root):
        rel = path.relative_to(root).as_posix()
        scan_file(rel, path, raw)
    entries, problems = load_allowlist(root / allowlist, allowlist)
    raw.extend(problems)
    kept: list[Finding] = []
    suppressed = 0
    for finding in raw:
        if finding.rule != "allowlist" and any(fnmatchcase(finding.path, entry.pattern) for entry in entries):
            suppressed += 1
        else:
            kept.append(finding)
    return sorted(kept, key=lambda f: (f.path, f.line)), suppressed


def run(root: Path, allowlist: str, report_only: bool, out=None) -> int:
    findings, suppressed = collect(root, allowlist)
    for finding in findings:
        print(f"{finding.path}:{finding.line}: [{finding.rule}] {finding.message}", file=out)
    counts = Counter(finding.rule for finding in findings)
    if counts:
        by_rule = " ".join(f"{rule}={counts[rule]}" for rule in sorted(counts))
        print(f"package-proxy-guard: by rule: {by_rule}", file=out)
    mode = "report" if report_only else "enforce"
    files = len({finding.path for finding in findings})
    print(
        f"package-proxy-guard: {len(findings)} finding(s) in {files} file(s), {suppressed} allowlisted, mode {mode}",
        file=out,
    )
    return 0 if report_only or not findings else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report package installs that bypass the packages proxy.")
    parser.add_argument("--root", default=".", help="Checkout to scan. Default: the current directory.")
    parser.add_argument("--allowlist", default=DEFAULT_ALLOWLIST, help="Allowlist path, relative to --root.")
    parser.add_argument("--report-only", action="store_true", help="Print findings and exit 0.")
    args = parser.parse_args(argv)
    return run(Path(args.root).resolve(), args.allowlist, args.report_only)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
