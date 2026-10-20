"""The software version an image carries, and the images lock that records it.

container-publish stamps `org.opencontainers.image.version` with the version of
the software inside the image (software-version.sh). resolve-image-lock reads it
back from what was pushed and writes it into the release's images lock beside the
digest (resolve.sh). Both are tested through their scripts, with a stand-in for
`docker buildx imagetools` that answers from fixture files.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
STAMP = ROOT / "actions" / "container-publish" / "software-version.sh"
RESOLVE = ROOT / "actions" / "resolve-image-lock" / "resolve.sh"
ACTION = ROOT / "actions" / "resolve-image-lock" / "action.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "container-publish.yml"
DIGEST = "sha256:" + "a" * 64


class SoftwareVersion(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()

    def stamp(self, dockerfile: str, override: str = "", release: str = "v1.4.0"):
        (self.dir / "Dockerfile").write_text(dockerfile)
        output = self.dir / "output"
        summary = self.dir / "summary"
        output.write_text("")
        summary.write_text("")
        run = subprocess.run(
            ["bash", str(STAMP)],
            cwd=self.dir,
            env={
                **os.environ,
                "DOCKERFILE": "Dockerfile",
                "RELEASE": release,
                "OVERRIDE": override,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return output.read_text(), summary.read_text()

    def test_an_image_built_from_an_upstream_base_carries_the_base_tag(self):
        output, _ = self.stamp(f"FROM docker.io/n8nio/n8n:2.43.8@{DIGEST}\nUSER 1000\n")
        self.assertEqual(output, "version=2.43.8\n")

    def test_the_upstream_tag_is_kept_as_upstream_writes_it(self):
        output, _ = self.stamp(f"FROM linuxserver/radarr:6.3.0.10514-ls314@{DIGEST}\n")
        self.assertEqual(output, "version=6.3.0.10514-ls314\n")

    def test_the_last_stage_names_the_base_when_earlier_stages_only_feed_it(self):
        output, _ = self.stamp(
            f"FROM litestream/litestream:0.5.17@{DIGEST} AS litestream\n"
            f"FROM --platform=linux/amd64 ghcr.io/karakeep-app/karakeep:0.33.2@{DIGEST}\n"
            "COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/\n"
        )
        self.assertEqual(output, "version=0.33.2\n")

    def test_a_last_stage_built_from_an_earlier_stage_names_that_stage_s_base(self):
        output, _ = self.stamp(
            f"FROM ghcr.io/vectorize-io/hindsight-api:0.9.2@{DIGEST} AS base\n"
            "FROM base\n"
            "COPY entrypoint.sh /\n"
        )
        self.assertEqual(output, "version=0.9.2\n")

    def test_an_image_of_our_own_software_carries_its_release_version(self):
        output, _ = self.stamp(
            "FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS build\n"
            "RUN go build ./...\n"
            "FROM gcr.io/distroless/static-debian12:nonroot\n"
        )
        self.assertEqual(output, "version=1.4.0\n")

    def test_a_caller_names_the_version_itself(self):
        output, _ = self.stamp(f"FROM liquibase/liquibase:5.0.4@{DIGEST}\n", override="0.3.0")
        self.assertEqual(output, "version=0.3.0\n")

    def test_a_base_whose_tag_names_no_version_leaves_the_label_unset_and_says_so(self):
        output, summary = self.stamp(f"FROM ghcr.io/karakeep-app/karakeep-chrome:release@{DIGEST}\n")
        self.assertEqual(output, "version=\n")
        self.assertIn("ghcr.io/karakeep-app/karakeep-chrome:release", summary)
        self.assertIn("software-version", summary)


def executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class ResolveImageLock(unittest.TestCase):
    """resolve.sh against a docker stand-in answering from <ref>.manifest / <ref>.image files."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp()).resolve()
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.images = self.dir / "images"
        self.images.mkdir()
        executable(
            self.bin / "docker",
            # docker buildx imagetools inspect <ref> --format <format>
            'ref="$(printf %s "$4" | tr "/:@" "___")"\n'
            'case "$6" in *Manifest*) kind=manifest ;; *) kind=image ;; esac\n'
            'file="$IMAGES/${ref}.${kind}"\n'
            '[ -f "$file" ] || { echo "ERROR: $4: not found" >&2; exit 1; }\n'
            'cat "$file"\n',
        )

    def push(self, ref: str, digest: str = DIGEST, user: str = "1000:1000", labels: dict | None = None):
        """Make ref answer as a pushed multi-arch image."""
        name = ref.rsplit(":", 1)[0] if "@" not in ref else ref.split("@")[0]
        safe = lambda r: r.replace("/", "_").replace(":", "_").replace("@", "_")
        (self.images / f"{safe(ref)}.manifest").write_text(json.dumps({"digest": digest}))
        config = {"config": {"User": user, "Labels": labels or {}}}
        (self.images / f"{safe(name + '@' + digest)}.image").write_text(
            json.dumps({"linux/amd64": config, "linux/arm64": config})
        )

    def resolve(self, images: str, tag: str = "v1.4.0"):
        out = self.dir / "images.lock.yml"
        run = subprocess.run(
            ["bash", str(RESOLVE)],
            cwd=self.dir,
            env={
                **os.environ,
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "IMAGES": str(self.images),
                "LOCK_NAME": "arr",
                "IMAGE_LIST": images,
                "TAG": tag,
                "OWNER": "JorisJonkers-dev",
                "OUT": str(out),
                "GITHUB_STEP_SUMMARY": str(self.dir / "summary"),
            },
            capture_output=True,
            text=True,
        )
        return run, out

    def test_an_image_s_version_comes_from_its_label(self):
        self.push(
            "ghcr.io/jorisjonkers-dev/radarr:v1.4.0",
            labels={"org.opencontainers.image.version": "6.3.0.10514-ls314"},
        )
        run, out = self.resolve("radarr")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            yaml.safe_load(out.read_text()),
            {
                "apiVersion": "lock.jorisjonkers.dev/v1",
                "kind": "ImagesLock",
                "schemaVersion": "1.0.0",
                "name": "arr",
                "images": {
                    "radarr": {
                        "repository": "ghcr.io/jorisjonkers-dev/radarr",
                        "digest": DIGEST,
                        "uid": 1000,
                        "gid": 1000,
                        "version": "6.3.0.10514-ls314",
                    }
                },
            },
        )

    def test_the_lock_reads_as_today_s_inline_jobs_wrote_it_plus_the_version(self):
        self.push("ghcr.io/jorisjonkers-dev/n8n:v1.4.0", labels={"org.opencontainers.image.version": "2.43.8"})
        run, out = self.resolve("n8n")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(
            out.read_text(),
            "apiVersion: lock.jorisjonkers.dev/v1\n"
            "kind: ImagesLock\n"
            "schemaVersion: 1.0.0\n"
            "name: arr\n"
            "images:\n"
            "  n8n:\n"
            "    repository: ghcr.io/jorisjonkers-dev/n8n\n"
            f'    digest: "{DIGEST}"\n'
            "    uid: 1000\n"
            "    gid: 1000\n"
            '    version: "2.43.8"\n',
        )

    def test_an_image_without_a_label_takes_the_version_tag_it_is_named_by(self):
        self.push("docker.io/getmeili/meilisearch:v1.41.0")
        run, out = self.resolve("search=docker.io/getmeili/meilisearch:v1.41.0")
        self.assertEqual(run.returncode, 0, run.stderr)
        entry = yaml.safe_load(out.read_text())["images"]["search"]
        self.assertEqual(entry["repository"], "docker.io/getmeili/meilisearch")
        self.assertEqual(entry["version"], "v1.41.0")

    def test_the_release_tag_is_never_taken_for_the_software_s_version(self):
        self.push("ghcr.io/jorisjonkers-dev/radarr:v1.4.0")
        run, out = self.resolve("radarr")
        self.assertEqual(run.returncode, 1)
        self.assertFalse(out.exists())

    def test_an_image_with_neither_is_refused_naming_it_and_both_fixes(self):
        self.push("ghcr.io/jorisjonkers-dev/chrome:release")
        run, out = self.resolve("chrome=ghcr.io/jorisjonkers-dev/chrome:release")
        self.assertEqual(run.returncode, 1)
        self.assertIn("ghcr.io/jorisjonkers-dev/chrome:release", run.stdout + run.stderr)
        self.assertIn("org.opencontainers.image.version", run.stdout + run.stderr)
        self.assertIn("versioned tag", run.stdout + run.stderr)
        self.assertFalse(out.exists())

    def test_a_release_of_several_images_locks_each_in_the_order_named(self):
        for alias, version in (("sonarr", "4.0.19.2979-ls322"), ("radarr", "6.3.0.10514-ls314")):
            self.push(
                f"ghcr.io/jorisjonkers-dev/{alias}:v1.4.0",
                digest="sha256:" + alias[0] * 64,
                labels={"org.opencontainers.image.version": version},
            )
        run, out = self.resolve("sonarr\nradarr")
        self.assertEqual(run.returncode, 0, run.stderr)
        images = yaml.safe_load(out.read_text())["images"]
        self.assertEqual(list(images), ["sonarr", "radarr"])
        self.assertEqual(images["sonarr"]["version"], "4.0.19.2979-ls322")
        self.assertEqual(images["radarr"]["digest"], "sha256:" + "r" * 64)

    def test_one_unversioned_image_refuses_the_whole_release(self):
        self.push("ghcr.io/jorisjonkers-dev/sonarr:v1.4.0", labels={"org.opencontainers.image.version": "4.0.19"})
        self.push("ghcr.io/jorisjonkers-dev/radarr:v1.4.0")
        run, out = self.resolve("sonarr radarr")
        self.assertEqual(run.returncode, 1)
        self.assertIn("radarr", run.stdout + run.stderr)
        self.assertFalse(out.exists())

    def test_an_image_that_runs_as_a_name_is_refused(self):
        self.push(
            "ghcr.io/jorisjonkers-dev/n8n:v1.4.0",
            user="node",
            labels={"org.opencontainers.image.version": "2.43.8"},
        )
        run, out = self.resolve("n8n")
        self.assertEqual(run.returncode, 1)
        self.assertIn("not a numeric uid", run.stdout + run.stderr)
        self.assertFalse(out.exists())

    def test_an_image_that_was_not_pushed_is_refused(self):
        run, out = self.resolve("n8n")
        self.assertEqual(run.returncode, 1)
        self.assertIn("ghcr.io/jorisjonkers-dev/n8n:v1.4.0", run.stdout + run.stderr)
        self.assertFalse(out.exists())


class Wiring(unittest.TestCase):
    def test_the_shared_build_stamps_the_label_it_works_out(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = workflow["jobs"]["publish"]["steps"]
        build = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action"))
        self.assertIn(
            "format('org.opencontainers.image.version={0}', steps.software.outputs.version)",
            build["with"]["labels"],
        )
        self.assertIn("software-version", workflow[True]["workflow_call"]["inputs"])

    def test_the_lock_action_uploads_the_lock_it_resolved(self):
        action = yaml.safe_load(ACTION.read_text())
        self.assertEqual(set(action["inputs"]), {"name", "images", "tag", "artifact-name", "token"})
        self.assertIn("images-lock-artifact", action["outputs"])
        upload = next(s for s in action["runs"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact"))
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/resolve-image-lock/images.lock.yml")


if __name__ == "__main__":
    unittest.main()
