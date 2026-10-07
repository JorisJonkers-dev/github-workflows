"""The publish-fragment workflow and the scripts it runs.

The scripts decide nothing: they hand the deploy-kit command its arguments and
move bytes. So they are tested with a stand-in command that records its calls
(tests/fixtures/estate-delivery/deploy-kit), and stand-ins for oras and cosign.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions" / "publish-fragment"
WORKFLOW = ROOT / ".github" / "workflows" / "publish-fragment.yml"
STUB = ROOT / "tests" / "fixtures" / "estate-delivery" / "deploy-kit"
SHA = "0123456789abcdef0123456789abcdef01234567"


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class Pack(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        (self.dir / "deploy" / "env").mkdir(parents=True)
        (self.dir / "deploy" / "notes.project.yml").write_text("project: notes\n")
        (self.dir / "deploy" / "env" / "base.env").write_text("A=1\n")
        self.log = self.dir / "calls.log"

    def run_pack(self, **overrides):
        env = {
            **os.environ,
            "PROJECT_FILE": "deploy/notes.project.yml",
            "VERSION": "v1.4.0",
            "SOURCE_SHA": SHA,
            "REPOSITORY": "JorisJonkers-dev/notes",
            "OUT": "fragment",
            "DEPLOY_KIT_COMMAND": str(STUB),
            "STUB_LOG": str(self.log),
            "GITHUB_OUTPUT": str(self.dir / "output"),
            **overrides,
        }
        return subprocess.run(
            ["bash", str(ACTION / "pack.sh")], cwd=self.dir, env=env, capture_output=True, text=True
        )

    def test_validates_then_packs_the_release_and_writes_a_manifest(self):
        run = self.run_pack(VALIDATE_WITH="deploy/env")
        self.assertEqual(run.returncode, 0, run.stderr)

        calls = self.log.read_text().splitlines()
        project = self.dir.resolve() / "deploy" / "notes.project.yml"
        self.assertEqual(calls[0], f"validate {self.dir}/deploy/notes.project.yml {self.dir}/deploy/env")
        self.assertEqual(
            calls[1],
            f"publish {self.dir}/deploy/notes.project.yml --repository JorisJonkers-dev/notes "
            f"--source-sha {SHA} --version 1.4.0 --out {self.dir}/fragment",
        )
        self.assertTrue(project.exists())

        fragment = self.dir / "fragment"
        check = subprocess.run(
            ["sha256sum", "-c", "MANIFEST.sha256"], cwd=fragment, capture_output=True, text=True
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        manifest = (fragment / "MANIFEST.sha256").read_text()
        self.assertIn("./fragment.yml", manifest)
        self.assertNotIn("MANIFEST.sha256", manifest)

        self.assertEqual(
            (self.dir / "output").read_text(), "project=notes\nversion=1.4.0\ninputs-sha=abc123\n"
        )

    def test_the_images_lock_is_handed_to_the_command_only_when_there_is_one(self):
        run = self.run_pack()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("--images-lock", self.log.read_text())

        lock = self.dir / "built" / "images.lock.yml"
        lock.parent.mkdir()
        lock.write_text("kind: ImagesLock\n")
        self.log.write_text("")
        run = self.run_pack(IMAGES_LOCK="built/images.lock.yml")
        self.assertEqual(run.returncode, 0, run.stderr)
        publish = self.log.read_text().splitlines()[1]
        self.assertIn(f"--version 1.4.0 --images-lock {lock} --out {self.dir}/fragment", publish)

    def test_a_lock_that_is_named_and_not_there_packs_nothing(self):
        run = self.run_pack(IMAGES_LOCK="built/images.lock.yml")
        self.assertEqual(run.returncode, 1)
        self.assertIn("no images lock at built/images.lock.yml", run.stderr)
        self.assertEqual(self.log.read_text() if self.log.exists() else "", "")

    def test_a_refused_project_file_packs_nothing(self):
        run = self.run_pack(STUB_REFUSE_VALIDATE="1")
        self.assertEqual(run.returncode, 1)
        self.assertEqual(self.log.read_text().count("publish"), 0)
        self.assertFalse((self.dir / "fragment").exists())

    def test_what_is_not_a_release_is_refused_before_the_command_runs(self):
        cases = {
            "a branch name": {"VERSION": "main"},
            "a pre-release": {"VERSION": "v1.4.0-rc.1"},
            "a short commit": {"SOURCE_SHA": "0123abc"},
            "a missing project file": {"PROJECT_FILE": "deploy/other.project.yml"},
            "a path to read that is not there": {"VALIDATE_WITH": "deploy/gone"},
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                self.log.write_text("")
                run = self.run_pack(**overrides)
                self.assertEqual(run.returncode, 1)
                self.assertIn("publish-fragment:", run.stderr)
                self.assertEqual(self.log.read_text(), "")

    def test_a_command_that_packs_nothing_fails(self):
        run = self.run_pack(STUB_PACK_NOTHING="1")
        self.assertEqual(run.returncode, 1)
        self.assertIn("packed no fragment.yml", run.stderr)


class Push(unittest.TestCase):
    """push.sh against stand-ins for oras and cosign that record their calls."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.log = self.dir / "calls.log"
        self.fragment = self.dir / "fragment"
        self.fragment.mkdir()
        (self.fragment / "fragment.yml").write_text("spec:\n  project: notes\n  inputsSha: abc123\n")
        subprocess.run(
            "sha256sum ./fragment.yml > MANIFEST.sha256", shell=True, cwd=self.fragment, check=True
        )
        executable(
            self.bin / "oras",
            'echo "oras $*" >>"$CALLS"\n'
            'case "$1 $2" in\n'
            '  "manifest fetch")\n'
            '    if [ -n "${LATEST_ERROR:-}" ]; then echo "$LATEST_ERROR" >&2; exit 1; fi\n'
            '    if [ -z "${LATEST:-}" ]; then echo "Error: ghcr.io/x:latest: not found" >&2; exit 1; fi\n'
            '    if [ "$LATEST" = unlabelled ]; then echo "{}"; exit 0; fi\n'
            '    printf \'{"annotations":{"org.opencontainers.image.version":"%s"}}\' "$LATEST" ;;\n'
            '  "push "*) printf \'%s\' "${DIGEST-sha256:feed}" ;;\n'
            '  "pull "*) cp -R "${PULLED:-$FRAGMENT}/." "$4" ;;\n'
            "esac\n",
        )
        executable(self.bin / "cosign", 'echo "cosign $*" >>"$CALLS"\n[ "$1" != "${COSIGN_FAILS:-}" ]\n')

    def run_push(self, **overrides):
        env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "CALLS": str(self.log),
            "FRAGMENT": str(self.fragment),
            "PROJECT": "notes",
            "VERSION": "1.4.0",
            "SOURCE_SHA": SHA,
            "OWNER": "JorisJonkers-dev",
            "SIGNED_REPOSITORY": "JorisJonkers-dev/notes",
            "GITHUB_OUTPUT": str(self.dir / "output"),
            **overrides,
        }
        return subprocess.run(["bash", str(ACTION / "push.sh")], env=env, capture_output=True, text=True)

    def calls(self, tool):
        return [line for line in self.log.read_text().splitlines() if line.startswith(tool + " ")]

    def test_a_first_publish_is_tagged_latest_signed_and_read_back(self):
        run = self.run_push()
        self.assertEqual(run.returncode, 0, run.stderr)

        push = next(c for c in self.calls("oras") if c.startswith("oras push"))
        self.assertIn("ghcr.io/jorisjonkers-dev/intent-notes:1.4.0,latest", push)
        self.assertIn("org.opencontainers.image.version=1.4.0", push)
        self.assertIn(f"org.opencontainers.image.revision={SHA}", push)
        self.assertIn("dev.jorisjonkers.inputs-sha=abc123", push)

        sign, verify = self.calls("cosign")
        self.assertEqual(sign, "cosign sign --yes ghcr.io/jorisjonkers-dev/intent-notes@sha256:feed")
        self.assertIn("verify ghcr.io/jorisjonkers-dev/intent-notes@sha256:feed", verify)
        self.assertIn("--certificate-oidc-issuer https://token.actions.githubusercontent.com", verify)
        self.assertIn(
            r"--certificate-identity-regexp ^https://github\.com/JorisJonkers-dev/github-workflows/"
            r"\.github/workflows/publish-fragment\.yml@",
            verify,
        )
        self.assertIn("--certificate-github-workflow-repository JorisJonkers-dev/notes", verify)

        self.assertEqual(
            (self.dir / "output").read_text(),
            "ref=ghcr.io/jorisjonkers-dev/intent-notes@sha256:feed\ndigest=sha256:feed\n",
        )

    def test_latest_only_moves_forward(self):
        for latest, expected in {"1.3.9": "1.4.0,latest", "1.4.0": "1.4.0,latest", "1.10.0": "1.4.0 ", "2.0.0": "1.4.0 "}.items():
            with self.subTest(latest):
                self.log.write_text("")
                run = self.run_push(LATEST=latest)
                self.assertEqual(run.returncode, 0, run.stderr)
                push = next(c for c in self.calls("oras") if c.startswith("oras push"))
                self.assertIn(f"intent-notes:{expected}", push + " ")

    def test_a_latest_that_cannot_be_read_is_not_taken_for_a_first_publish(self):
        cases = {
            "a registry that does not answer": {"LATEST_ERROR": "Error: dial tcp: i/o timeout"},
            "a registry that refuses the token": {"LATEST_ERROR": "Error: unauthorized: authentication required"},
            "a latest with no release on it": {"LATEST": "unlabelled"},
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                self.log.write_text("")
                run = self.run_push(**overrides)
                self.assertEqual(run.returncode, 1)
                self.assertIn("publish-fragment:", run.stderr)
                self.assertFalse([c for c in self.calls("oras") if c.startswith("oras push")])
                self.assertFalse(self.calls("cosign"))

    def test_what_is_not_a_release_or_a_name_is_never_pushed(self):
        for overrides in ({"VERSION": "1.4.0,latest"}, {"VERSION": "latest"}, {"PROJECT": "../other"}, {"PROJECT": "Notes"}):
            with self.subTest(str(overrides)):
                self.log.write_text("")
                run = self.run_push(**overrides)
                self.assertEqual(run.returncode, 1)
                self.assertEqual(self.log.read_text(), "")

    def test_nothing_is_reported_published_unless_it_reads_back_and_verifies(self):
        other = self.dir / "other"
        other.mkdir()
        (other / "fragment.yml").write_text("spec:\n  project: someone-else\n")
        subprocess.run("sha256sum ./fragment.yml > MANIFEST.sha256", shell=True, cwd=other, check=True)
        tampered = self.dir / "tampered"
        tampered.mkdir()
        (tampered / "fragment.yml").write_text("changed\n")
        (tampered / "MANIFEST.sha256").write_text((self.fragment / "MANIFEST.sha256").read_text())

        cases = {
            "a push that returns no digest": {"DIGEST": ""},
            "a signature that fails": {"COSIGN_FAILS": "sign"},
            "a pulled package that is another fragment": {"PULLED": str(other)},
            "a pulled file that does not match its manifest": {"PULLED": str(tampered)},
            "a signature that does not verify": {"COSIGN_FAILS": "verify"},
        }
        for name, overrides in cases.items():
            with self.subTest(name):
                output = self.dir / "output"
                output.unlink(missing_ok=True)
                run = self.run_push(**overrides)
                self.assertNotEqual(run.returncode, 0)
                self.assertFalse(output.exists() and output.read_text())


class Workflow(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_no_caller_input_is_spliced_into_a_script(self):
        # An input reaches a script through `env`, never by expansion inside `run`,
        # where a caller's value would be run as shell.
        in_run = False
        for line in self.text.splitlines():
            stripped = line.strip()
            if re.match(r"^(- )?run: ", stripped) or stripped == "run: |":
                in_run = True
                indent = len(line) - len(line.lstrip())
                if "${{" in stripped:
                    self.fail(f"expression in a run line: {stripped}")
                continue
            if in_run:
                if stripped and len(line) - len(line.lstrip()) <= indent:
                    in_run = False
                elif "${{" in line:
                    self.fail(f"expression inside a script: {stripped}")

    def test_it_holds_no_permission_until_the_job_asks(self):
        self.assertIn("\npermissions: {}\n", self.text)
        job = self.text.split("jobs:\n", 1)[1]
        self.assertIn("contents: read", job)
        self.assertIn("packages: write", job)
        self.assertIn("id-token: write", job)
        self.assertNotIn("contents: write", job)

    def test_every_third_party_action_is_pinned_to_a_commit(self):
        for uses in re.findall(r"uses: (\S+)", self.text):
            self.assertRegex(uses, r"@[0-9a-f]{40}$", uses)

    def test_the_lock_is_fetched_outside_the_checkout_and_only_when_named(self):
        self.assertIn("if: ${{ inputs.images-lock-artifact != '' }}", self.text)
        self.assertIn("path: ${{ runner.temp }}/images-lock", self.text)
        self.assertIn(
            "IMAGES_LOCK: ${{ inputs.images-lock-artifact != '' && "
            "format('{0}/images-lock/images.lock.yml', runner.temp) || '' }}",
            self.text,
        )

    def test_fragments_to_validate_beside_are_pulled_outside_the_checkout_and_only_when_named(self):
        self.assertIn("if: ${{ inputs.validate-with-fragments != '' }}", self.text)
        self.assertIn("OUT: ${{ runner.temp }}/beside", self.text)
        self.assertIn('ref="ghcr.io/${owner}/intent-${project}:latest"', self.text)
        # Only a fragment publish-fragment signed is read, and by digest.
        step = self.text.split("- name: Pull the fragments the project file is validated beside", 1)[1].split("- name:", 1)[0]
        self.assertIn('cosign verify "${ref%:latest}@${digest}"', step)
        self.assertIn('oras pull "${ref%:latest}@${digest}"', step)
        self.assertLess(step.index("cosign verify"), step.index("oras pull"))
        # The signing run must come from a repository of this owner, not merely
        # call the same public workflow.
        self.assertIn(".optional.githubWorkflowRepository", step)
        self.assertIn('startswith($owner + "/")', step)
        self.assertLess(step.index("githubWorkflowRepository"), step.index("oras pull"))
        self.assertLess(
            self.text.index("sigstore/cosign-installer@"),
            self.text.index("- name: Pull the fragments the project file is validated beside"),
        )
        self.assertIn(
            "VALIDATE_WITH: ${{ inputs.validate-with }} ${{ steps.beside.outputs.paths }}",
            self.text,
        )
        # oras is set up once, before the first step that needs it.
        self.assertEqual(self.text.count("oras-project/setup-oras@"), 1)
        self.assertLess(
            self.text.index("oras-project/setup-oras@"),
            self.text.index("- name: Pull the fragments the project file is validated beside"),
        )

    def test_composition_is_started_with_the_dispatch_app_only(self):
        self.assertIn("app-id: ${{ vars.ESTATE_DISPATCH_APP_ID }}", self.text)
        self.assertIn("repositories: estate", self.text)
        self.assertIn('gh workflow run compose.yml --repo "$ESTATE" --ref main', self.text)


if __name__ == "__main__":
    unittest.main()
