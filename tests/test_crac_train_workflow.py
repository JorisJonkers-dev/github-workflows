from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRAC_WORKFLOW = ROOT / ".github/workflows/crac-train.yml"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
PLATFORM_CONFIG_WORKFLOW = ROOT / ".github/workflows/platform-config-validate.yml"
NODE_CI_WORKFLOW = ROOT / ".github/workflows/node-ci.yml"
PYTHON_CI_WORKFLOW = ROOT / ".github/workflows/python-ci.yml"
NIX_CI_WORKFLOW = ROOT / ".github/workflows/nix-ci.yml"
DOCKER_IMAGE_CI_WORKFLOW = ROOT / ".github/workflows/docker-image-ci.yml"
CONTAINER_PUBLISH_WORKFLOW = ROOT / ".github/workflows/container-publish.yml"
GITOPS_CI_WORKFLOW = ROOT / ".github/workflows/gitops-ci.yml"
DEPLOY_BUNDLE_WORKFLOW = ROOT / ".github/workflows/deploy-bundle.yml"
DEPLOY_SOURCES_RENDER_WORKFLOW = ROOT / ".github/workflows/deploy-sources-render.yml"
ADD_TO_PROJECT_WORKFLOW = ROOT / ".github/workflows/add-to-project.yml"
HYGIENE_GUARD_WORKFLOW = ROOT / ".github/workflows/repository-hygiene-guard.yml"
API_CLIENT_PUBLISH_RUNNER = ROOT / "actions/api-client-publish/run.sh"
COMPOSE_ACTION = ROOT / "actions/compose-system-test-stack/action.yml"
COMPOSE_RUNNER = ROOT / "actions/compose-system-test-stack/run.sh"
COMPOSE_ROUTES_FIXTURE = ROOT / "actions/compose-system-test-stack/fixtures/routes.example.txt"
COMPOSE_STACK_FIXTURE = ROOT / "actions/compose-system-test-stack/fixtures/compose.stack.example.yml"
PLATFORM_CONFIG_ACTION = ROOT / "actions/platform-config-validate/action.yml"
PLATFORM_CONFIG_RUNNER = ROOT / "actions/platform-config-validate/run.sh"
PLATFORM_CONFIG_PLATFORM_FIXTURE = ROOT / "actions/platform-config-validate/fixtures/platform.example.yml"
PLATFORM_CONFIG_SERVICE_FIXTURE = ROOT / "actions/platform-config-validate/fixtures/service-intent.example.yml"
SETUP_NODE_ACTION = ROOT / "actions/setup-node/action.yml"
RENOVATE_CONFIG = ROOT / "renovate.json"
RELEASE_CONFIG = ROOT / "release-please-config.json"
README = ROOT / "README.md"


class CracTrainWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = CRAC_WORKFLOW.read_text(encoding="utf-8")
        cls.compose_action = COMPOSE_ACTION.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
        match = re.search(
            r"python3 - <<'PY' >> \"\$GITHUB_OUTPUT\"\n(?P<body>.*?)\n          PY",
            cls.workflow,
            re.DOTALL,
        )
        if match is None:
            raise AssertionError("Could not find embedded CRaC sidecar resolver.")
        cls.sidecar_resolver = textwrap.dedent(match.group("body"))

    def resolve_sidecars(self, sidecars_json: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory():
            env = os.environ.copy()
            env["SIDECARS_JSON"] = sidecars_json
            result = subprocess.run(
                [sys.executable, "-c", self.sidecar_resolver],
                env=env,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return result

    def test_missing_sidecars_defaults_to_round_two_topology(self) -> None:
        result = self.resolve_sidecars("null")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("postgres=true\n", result.stdout)
        self.assertIn("valkey=true\n", result.stdout)
        self.assertIn("rabbitmq=true\n", result.stdout)
        self.assertIn("list=postgres,valkey,rabbitmq\n", result.stdout)

    def test_sidecars_accepts_subset_list(self) -> None:
        result = self.resolve_sidecars('["postgres", "valkey"]')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("postgres=true\n", result.stdout)
        self.assertIn("valkey=true\n", result.stdout)
        self.assertIn("rabbitmq=false\n", result.stdout)
        self.assertIn("list=postgres,valkey\n", result.stdout)

    def test_sidecars_support_explicit_none_and_reject_mixed_none(self) -> None:
        result = self.resolve_sidecars('"none"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("postgres=false\n", result.stdout)
        self.assertIn("valkey=false\n", result.stdout)
        self.assertIn("rabbitmq=false\n", result.stdout)
        self.assertIn("list=none\n", result.stdout)

        empty_result = self.resolve_sidecars("[]")
        self.assertEqual(empty_result.returncode, 0, empty_result.stderr)
        self.assertIn("list=none\n", empty_result.stdout)

        mixed_result = self.resolve_sidecars('["none", "postgres"]')
        self.assertNotEqual(mixed_result.returncode, 0)
        self.assertIn("value 'none' cannot be combined with other sidecars", mixed_result.stderr)

    def test_only_supported_sidecar_names_are_accepted(self) -> None:
        result = self.resolve_sidecars('["postgres", "search"]')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported CRaC sidecar(s): search", result.stderr)

    def test_job_services_are_not_started_unconditionally(self) -> None:
        services_block = re.search(r"\n    services:\n", self.workflow)
        self.assertIsNone(services_block)
        self.assertIn("Start Postgres sidecar", self.workflow)
        self.assertIn("Start Valkey sidecar", self.workflow)
        self.assertIn("Start RabbitMQ sidecar", self.workflow)

    def test_training_env_vars_are_conditional(self) -> None:
        self.assertIn('if [ "$POSTGRES_ENABLED" = "true" ]; then', self.workflow)
        self.assertIn('if [ "$VALKEY_ENABLED" = "true" ]; then', self.workflow)
        self.assertIn('if [ "$RABBITMQ_ENABLED" = "true" ]; then', self.workflow)

    def test_readme_documents_sidecar_matrix_examples(self) -> None:
        self.assertIn('"sidecars": ["postgres", "valkey"]', self.readme)
        self.assertIn('"sidecars": "none"', self.readme)

    def test_compose_action_is_working_composite_action(self) -> None:
        expected_inputs = [
            "compose-files",
            "services",
            "wait-strategy",
            "diagnostics-command",
            "migration-check-command",
            "wait-timeout-seconds",
            "wait-interval-seconds",
        ]
        for expected_input in expected_inputs:
            self.assertIn(f"  {expected_input}:", self.compose_action)

        self.assertIn('run: bash "${{ github.action_path }}/run.sh"', self.compose_action)
        self.assertNotIn("placeholder-ack", self.compose_action)
        self.assertNotIn("design-first skeleton", self.compose_action)


class ComposeSystemTestStackActionTest(unittest.TestCase):
    def write_executable(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        path.chmod(0o755)

    def run_action(self, workdir: Path, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        bin_dir = workdir / "bin"
        bin_dir.mkdir(exist_ok=True)
        command_log = workdir / "commands.log"
        self.write_executable(
            bin_dir / "docker",
            """\
            #!/usr/bin/env bash
            printf 'docker' >> "$COMMAND_LOG"
            printf ' <%s>' "$@" >> "$COMMAND_LOG"
            printf '\\n' >> "$COMMAND_LOG"
            if [ "${DOCKER_FAIL_UP:-false}" = "true" ] && [ "$1" = "compose" ]; then
              for arg in "$@"; do
                if [ "$arg" = "up" ]; then
                  exit 23
                fi
              done
            fi
            exit 0
            """,
        )
        self.write_executable(
            bin_dir / "curl",
            """\
            #!/usr/bin/env bash
            printf 'curl' >> "$COMMAND_LOG"
            printf ' <%s>' "$@" >> "$COMMAND_LOG"
            printf '\\n' >> "$COMMAND_LOG"
            exit "${CURL_EXIT:-0}"
            """,
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                "COMMAND_LOG": str(command_log),
                "WORKING_DIRECTORY": str(workdir),
                "COMPOSE_FILES": "docker-compose.yml",
                "SERVICES": "",
                "PROJECT_NAME": "",
                "WAIT_STRATEGY": "none",
                "WAIT_COMMAND": "",
                "WAIT_ROUTES_FILE": "",
                "WAIT_TIMEOUT_SECONDS": "2",
                "WAIT_INTERVAL_SECONDS": "1",
                "MIGRATION_CHECK_COMMAND": "",
                "DIAGNOSTICS_COMMAND": "",
                "CLEANUP_COMMAND": "",
                "UP_ARGS": "--no-build --wait --timeout 300 -d",
                "DOWN_ON_COMPLETE": "true",
            }
        )
        env.update(env_overrides)

        return subprocess.run(
            [str(COMPOSE_RUNNER)],
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_runner_assembles_compose_files_project_and_services(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            (workdir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (workdir / "docker-compose.ci.yml").write_text("services: {}\n", encoding="utf-8")

            result = self.run_action(
                workdir,
                {
                    "COMPOSE_FILES": "docker-compose.yml\n# ignored\ndocker-compose.ci.yml\n",
                    "PROJECT_NAME": "systemtest",
                    "SERVICES": "api frontend db",
                    "DOWN_ON_COMPLETE": "false",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            log = (workdir / "commands.log").read_text(encoding="utf-8")
            self.assertIn(
                "docker <compose> <-p> <systemtest> <-f> <docker-compose.yml> <-f> <docker-compose.ci.yml> "
                "<up> <--no-build> <--wait> <--timeout> <300> <-d> <api> <frontend> <db>",
                log,
            )
            self.assertNotIn("<down>", log)

    def test_runner_waits_for_routes_and_runs_migration_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            (workdir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (workdir / "routes.txt").write_text(
                "# name url\napp-health http://example.invalid/health\nhttp://example.invalid/ready\n",
                encoding="utf-8",
            )

            result = self.run_action(
                workdir,
                {
                    "WAIT_STRATEGY": "routes",
                    "WAIT_ROUTES_FILE": "routes.txt",
                    "MIGRATION_CHECK_COMMAND": "printf 'migration-check\\n' >> \"$COMMAND_LOG\"",
                    "DOWN_ON_COMPLETE": "false",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            log = (workdir / "commands.log").read_text(encoding="utf-8")
            self.assertIn("curl <-fsS> <-L> <-k> <--max-time> <5> <-o> </dev/null> <http://example.invalid/health>", log)
            self.assertIn("curl <-fsS> <-L> <-k> <--max-time> <5> <-o> </dev/null> <http://example.invalid/ready>", log)
            self.assertIn("migration-check", log)

    def test_runner_adds_compose_wait_for_service_health_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            (workdir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

            result = self.run_action(
                workdir,
                {
                    "WAIT_STRATEGY": "services",
                    "UP_ARGS": "--no-build -d",
                    "DOWN_ON_COMPLETE": "false",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            log = (workdir / "commands.log").read_text(encoding="utf-8")
            self.assertIn("docker <compose> <-f> <docker-compose.yml> <up> <--no-build> <-d> <--wait>", log)

    def test_runner_dumps_diagnostics_and_cleans_up_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            (workdir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

            result = self.run_action(
                workdir,
                {
                    "MIGRATION_CHECK_COMMAND": "exit 17",
                    "DIAGNOSTICS_COMMAND": "printf 'custom-diagnostics\\n' >> \"$COMMAND_LOG\"",
                    "CLEANUP_COMMAND": "printf 'custom-cleanup\\n' >> \"$COMMAND_LOG\"",
                },
            )

            self.assertEqual(result.returncode, 17)
            log = (workdir / "commands.log").read_text(encoding="utf-8")
            self.assertIn("docker <compose> <-f> <docker-compose.yml> <ps> <-a>", log)
            self.assertIn("docker <compose> <-f> <docker-compose.yml> <logs> <--tail=200> <--no-color>", log)
            self.assertIn("docker <ps> <-a>", log)
            self.assertIn("docker <system> <df>", log)
            self.assertIn("custom-diagnostics", log)
            self.assertIn("custom-cleanup", log)
            self.assertIn("docker <compose> <-f> <docker-compose.yml> <down> <--remove-orphans>", log)

    def test_runner_validates_wait_strategy_and_required_route_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            (workdir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

            unsupported = self.run_action(workdir, {"WAIT_STRATEGY": "sleep"})
            self.assertNotEqual(unsupported.returncode, 0)
            self.assertIn("Unsupported wait-strategy: sleep", unsupported.stderr)

            missing_routes = self.run_action(workdir, {"WAIT_STRATEGY": "routes"})
            self.assertNotEqual(missing_routes.returncode, 0)
            self.assertIn("wait-routes-file is required", missing_routes.stderr)

    def test_compose_fixtures_are_generic_and_parameterized(self) -> None:
        routes = COMPOSE_ROUTES_FIXTURE.read_text(encoding="utf-8")
        compose = COMPOSE_STACK_FIXTURE.read_text(encoding="utf-8")
        forbidden = [
            "jorisjonkers",
            "blueshell",
            "127.0.0.1",
            "auth-api",
            "assistant-api",
            "vault.jorisjonkers",
        ]

        for value in forbidden:
            self.assertNotIn(value, routes)
            self.assertNotIn(value, compose)

        self.assertIn("${SYSTEM_TEST_APP_BASE_URL}", routes)
        self.assertIn("${SYSTEM_TEST_APP_IMAGE", compose)


class PlatformConfigValidateSurfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = PLATFORM_CONFIG_WORKFLOW.read_text(encoding="utf-8")
        cls.action = PLATFORM_CONFIG_ACTION.read_text(encoding="utf-8")
        cls.runner = PLATFORM_CONFIG_RUNNER.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
        cls.ci = CI_WORKFLOW.read_text(encoding="utf-8")

    def write_executable(self, path: Path, body: str) -> None:
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        path.chmod(0o755)

    def install_fake_npm(self, bin_dir: Path) -> None:
        self.write_executable(
            bin_dir / "npm",
            """\
            #!/usr/bin/env bash
            set -euo pipefail

            printf 'npm' >> "$COMMAND_LOG"
            printf ' <%s>' "$@" >> "$COMMAND_LOG"
            printf '\\n' >> "$COMMAND_LOG"

            case "${1:-}" in
              init)
                printf '{}\\n' > package.json
                ;;
              install)
                package_dir="node_modules/@jorisjonkers-dev/deploy-config-schema"
                mkdir -p "$package_dir"
                cat > "$package_dir/package.json" <<'JSON'
            {"bin":{"deploy-config-schema":"cli.js"}}
            JSON
                cat > "$package_dir/cli.js" <<'JS'
            #!/usr/bin/env node
            const fs = require("fs");
            const args = process.argv.slice(2);
            fs.appendFileSync(process.env.COMMAND_LOG, `cli ${args.map((arg) => `<${arg}>`).join(" ")}\\n`);
            if (args[0] === "validate" && process.env.CLI_VALIDATE_EXIT) {
              process.exit(Number(process.env.CLI_VALIDATE_EXIT));
            }
            if (args[0] === "render-tree" && process.env.CLI_DRIFT_EXIT) {
              process.exit(Number(process.env.CLI_DRIFT_EXIT));
            }
            JS
                chmod +x "$package_dir/cli.js"
                ;;
            esac
            """,
        )

    def run_platform_config_action(
        self,
        workdir: Path,
        env_overrides: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        bin_dir = workdir / "bin"
        runner_temp = workdir / "runner-temp"
        repo_dir = workdir / "repo"
        bin_dir.mkdir(exist_ok=True)
        runner_temp.mkdir(exist_ok=True)
        repo_dir.mkdir(exist_ok=True)

        command_log = workdir / "commands.log"
        self.install_fake_npm(bin_dir)

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}{os.pathsep}{env['PATH']}",
                "COMMAND_LOG": str(command_log),
                "RUNNER_TEMP": str(runner_temp),
                "CONFIG_PATHS": "platform/**/*.yaml\nplatform/**/*.yml",
                "SCHEMA_KIND": "auto",
                "PACKAGE_VERSION": "0.3.0",
                "DRIFT_CHECK": "false",
                "WORKING_DIRECTORY": str(repo_dir),
            }
        )
        env.update(env_overrides)

        return subprocess.run(
            [str(PLATFORM_CONFIG_RUNNER)],
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_reusable_workflow_exposes_expected_inputs_and_wraps_action(self) -> None:
        expected_inputs = [
            "config-paths",
            "schema-kind",
            "package-version",
            "drift-check",
            "working-directory",
        ]
        for expected_input in expected_inputs:
            self.assertIn(f"      {expected_input}:", self.workflow)
            self.assertIn(f"  {expected_input}:", self.action)

        self.assertIn("on:\n  workflow_call:", self.workflow)
        self.assertIn("uses: ./.github-workflows/actions/platform-config-validate", self.workflow)
        self.assertIn("repository: JorisJonkers-dev/github-workflows", self.workflow)
        self.assertIn("actions/setup-node@v6", self.action)
        self.assertIn('run: bash "${{ github.action_path }}/run.sh"', self.action)

    def test_action_supports_schema_kinds_package_pin_and_drift_check(self) -> None:
        self.assertIn("default: 0.3.0", self.action)
        self.assertIn("platform|deploy-config|service-intent|fleet-inventory|vault-dynamic-secrets|auto", self.runner)
        self.assertIn('"$cli_bin" validate "$schema_kind"', self.runner)
        self.assertIn('"$cli_bin" validate', self.runner)
        self.assertIn('"$cli_bin" render-tree "$drift_file" --check', self.runner)

    def test_runner_expands_globs_installs_package_and_runs_drift_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            repo_dir = workdir / "repo"
            repo_dir.mkdir()
            (repo_dir / "platform").mkdir()
            (repo_dir / "intents").mkdir()
            (repo_dir / "platform/app.yml").write_text("kind: platform\n", encoding="utf-8")
            (repo_dir / "platform/cluster.yaml").write_text("kind: platform\n", encoding="utf-8")
            (repo_dir / "intents/api.yaml").write_text("kind: service-intent\n", encoding="utf-8")

            result = self.run_platform_config_action(
                workdir,
                {
                    "WORKING_DIRECTORY": str(repo_dir),
                    "CONFIG_PATHS": "platform/*.yml\nplatform/*.yaml\nintents/*.yaml",
                    "SCHEMA_KIND": "platform",
                    "PACKAGE_VERSION": "9.8.7",
                    "DRIFT_CHECK": "true",
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            log = (workdir / "commands.log").read_text(encoding="utf-8")
            self.assertIn("npm <install> <--userconfig>", log)
            self.assertIn("<--no-audit> <--no-fund> <--save-exact> <@jorisjonkers-dev/deploy-config-schema@9.8.7>", log)
            self.assertIn(
                "cli <validate> <platform> <platform/app.yml> <platform/cluster.yaml> <intents/api.yaml>",
                log,
            )
            self.assertIn(
                "cli <render-tree> <platform/app.yml> <--check>",
                log,
            )

    def test_runner_fails_clearly_when_no_configs_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workdir = Path(temp)
            repo_dir = workdir / "repo"
            repo_dir.mkdir()

            result = self.run_platform_config_action(
                workdir,
                {
                    "WORKING_DIRECTORY": str(repo_dir),
                    "CONFIG_PATHS": "platform/**/*.yaml",
                },
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No config files matched config-paths", result.stdout)

    def test_generic_fixtures_and_readme_usage_are_present(self) -> None:
        platform_fixture = PLATFORM_CONFIG_PLATFORM_FIXTURE.read_text(encoding="utf-8")
        service_fixture = PLATFORM_CONFIG_SERVICE_FIXTURE.read_text(encoding="utf-8")

        self.assertIn("name: example-platform", platform_fixture)
        self.assertIn("api-service:", service_fixture)
        self.assertIn("platform-config-validate.yml", self.readme)
        self.assertIn("@jorisjonkers-dev/deploy-config-schema", self.readme)

    def test_ci_uses_official_actionlint_download_script(self) -> None:
        self.assertIn("download-actionlint.bash", self.ci)
        self.assertIn("raw.githubusercontent.com/rhysd/actionlint/main/scripts/download-actionlint.bash", self.ci)
        self.assertNotIn("rhysd/actionlint@v1", self.ci)


class MigrationReusableWorkflowSurfaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node_ci = NODE_CI_WORKFLOW.read_text(encoding="utf-8")
        cls.python_ci = PYTHON_CI_WORKFLOW.read_text(encoding="utf-8")
        cls.nix_ci = NIX_CI_WORKFLOW.read_text(encoding="utf-8")
        cls.docker_image_ci = DOCKER_IMAGE_CI_WORKFLOW.read_text(encoding="utf-8")
        cls.container_publish = CONTAINER_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
        cls.gitops_ci = GITOPS_CI_WORKFLOW.read_text(encoding="utf-8")
        cls.deploy_bundle = DEPLOY_BUNDLE_WORKFLOW.read_text(encoding="utf-8")
        cls.deploy_sources_render = DEPLOY_SOURCES_RENDER_WORKFLOW.read_text(encoding="utf-8")
        cls.add_to_project = ADD_TO_PROJECT_WORKFLOW.read_text(encoding="utf-8")
        cls.hygiene_guard = HYGIENE_GUARD_WORKFLOW.read_text(encoding="utf-8")
        cls.api_client_publish_runner = API_CLIENT_PUBLISH_RUNNER.read_text(encoding="utf-8")
        cls.setup_node = SETUP_NODE_ACTION.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")

    def test_reusable_workflows_are_documented_at_current_tag(self) -> None:
        for workflow in [
            "node-ci.yml",
            "deploy-bundle.yml",
            "deploy-sources-render.yml",
            "crac-train.yml",
        ]:
            self.assertTrue((ROOT / f".github/workflows/{workflow}").is_file())
            self.assertIn(
                f"JorisJonkers-dev/github-workflows/.github/workflows/{workflow}@v0.7.3",
                self.readme,
            )

    def test_node_ci_uses_setup_node_and_setup_node_supports_npm(self) -> None:
        for expected_input in [
            "node-version",
            "package-manager",
            "working-directory",
            "install-command",
            "lint-command",
            "typecheck-command",
            "test-command",
            "build-command",
            "package-check-command",
        ]:
            self.assertIn(f"      {expected_input}:", self.node_ci)

        self.assertIn("uses: ./.github-workflows/actions/setup-node", self.node_ci)
        self.assertIn("repository: JorisJonkers-dev/github-workflows", self.node_ci)
        self.assertIn("npm|pnpm|yarn)", self.setup_node)
        self.assertIn('INSTALL_COMMAND="npm ci"', self.setup_node)
        self.assertIn("default: '@jorisjonkers-dev'", self.setup_node)

    def test_language_and_build_reusables_expose_expected_commands(self) -> None:
        self.assertIn("default: python -m pip install -r requirements-dev.txt", self.python_ci)
        self.assertIn("default: ruff check .", self.python_ci)
        self.assertIn("default: python -m unittest discover", self.python_ci)
        self.assertIn("uses: cachix/install-nix-action@v31", self.nix_ci)
        self.assertIn("default: nix flake check --print-build-logs", self.nix_ci)
        self.assertIn("uses: docker/build-push-action@v7", self.docker_image_ci)
        self.assertIn("push: false", self.docker_image_ci)

    def test_container_publish_tags_version_and_sha_only(self) -> None:
        self.assertIn("packages: write", self.container_publish)
        self.assertIn("uses: docker/login-action@v4", self.container_publish)
        self.assertIn('printf \'%s:%s\\n\' "$image_ref" "$VERSION"', self.container_publish)
        self.assertIn('printf \'%s:sha-%s\\n\' "$image_ref" "$GITHUB_SHA"', self.container_publish)
        self.assertNotIn(":latest", self.container_publish)

    def test_deploy_v2_workflows_expose_bundle_render_and_project_surfaces(self) -> None:
        self.assertIn("'uses': './.github-workflows/actions/deploy-bundle'", self.deploy_bundle)
        self.assertIn("oras push --disable-path-validation", self.deploy_bundle)
        self.assertIn("application/vnd.jorisjonkers.deployment.bundle.v1+tar", self.deploy_bundle)
        self.assertIn("'uses': './.github-workflows/actions/deploy-sources-render'", self.deploy_sources_render)
        self.assertIn("'image-tags':", self.deploy_sources_render)
        self.assertIn("actions/create-github-app-token@v3", self.add_to_project)
        self.assertIn("actions/add-to-project@v2.0.0", self.add_to_project)
        self.assertIn(".specify/**", self.hygiene_guard)

    def test_api_client_maven_publish_is_idempotent_on_existing_versions(self) -> None:
        self.assertIn("maven_publish_failed_because_version_exists", self.api_client_publish_runner)
        self.assertIn("Received status code 409", self.api_client_publish_runner)
        self.assertIn("run_maven_publish_task \"$jvm_dir\" :java:publish", self.api_client_publish_runner)
        self.assertIn("run_maven_publish_task \"$jvm_dir\" :kotlin:publish", self.api_client_publish_runner)
        self.assertIn("npm_package_version_exists", self.api_client_publish_runner)
        self.assertIn("npm package ${ts_package}@${version} already exists", self.api_client_publish_runner)

    def test_gitops_ci_runs_required_and_optional_validation_steps(self) -> None:
        self.assertIn("uses: ./.github-workflows/actions/platform-config-validate", self.gitops_ci)
        self.assertIn("uses: ./.github-workflows/actions/flux-render-validate", self.gitops_ci)
        self.assertIn("uses: ./.github-workflows/actions/deploy-config-render-drift", self.gitops_ci)
        self.assertIn("inputs.deploy-config-command != '' && inputs.deploy-config-targets != ''", self.gitops_ci)
        self.assertIn("inputs.system-tests-command != ''", self.gitops_ci)

    def test_release_and_renovate_configs_match_migration_contract(self) -> None:
        renovate = json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))
        release = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))

        self.assertEqual(
            renovate,
            {
                "$schema": "https://docs.renovatebot.com/renovate-schema.json",
                "extends": ["github>JorisJonkers-dev/renovate-config"],
            },
        )
        self.assertEqual(release["bootstrap-sha"], "1eef0fe38c718b8263edb46b0bc4e2f1c6eb30a1")
        self.assertEqual(release["release-type"], "simple")


if __name__ == "__main__":
    unittest.main()
