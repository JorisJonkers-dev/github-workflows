#!/usr/bin/env bash
set -euo pipefail

readonly HEY_API_OPENAPI_TS_VERSION="0.99.0"
readonly TYPESCRIPT_VERSION="6.0.3"
readonly ZOD_VERSION="4.4.3"

die() {
  echo "::error::$*" >&2
  exit 1
}

required_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    die "$name is required"
  fi
}

reject_unsafe_string() {
  local name="$1"
  local value="$2"

  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    die "$name must not contain newlines"
  fi
  if [[ "$value" == *\"* || "$value" == *\\* ]]; then
    die "$name contains unsupported characters"
  fi
}

validate_identifier() {
  local name="$1"
  local value="$2"
  local pattern="$3"

  if [[ ! "$value" =~ $pattern ]]; then
    die "$name has an invalid value: $value"
  fi
}

maven_publish_failed_because_version_exists() {
  local log_path="$1"

  grep -Eiq '(Received status code 409|HTTP 409|409 Conflict)' "$log_path"
}

run_maven_publish_task() {
  local project_dir="$1"
  local task_name="$2"
  local log_path

  log_path="$(mktemp "${RUNNER_TEMP:-/tmp}/maven-publish.XXXXXX.log")"
  set +e
  gradle --no-daemon -p "$project_dir" "$task_name" > >(tee "$log_path") 2> >(tee -a "$log_path" >&2)
  local status=$?
  set -e

  if [[ "$status" -eq 0 ]]; then
    return 0
  fi

  if maven_publish_failed_because_version_exists "$log_path"; then
    echo "::notice::Maven package for $task_name already exists; treating HTTP 409 as an idempotent publish success."
    return 0
  fi

  return "$status"
}

npm_package_version_exists() {
  local package_name="$1"
  local version="$2"
  local registry="$3"

  npm view "${package_name}@${version}" version --registry "$registry" >/dev/null 2>&1
}

derive_jvm_package_base() {
  local maven_group="$1"
  local api_name="$2"
  local api_slug package_base segment

  api_slug="$(printf '%s' "$api_name" | tr '[:upper:]' '[:lower:]')"
  api_slug="${api_slug%-api}"
  api_slug="$(printf '%s' "$api_slug" | sed -E 's/[^a-z0-9]+/./g; s/^\.+//; s/\.+$//; s/\.+/./g')"

  if [[ -z "$api_slug" ]]; then
    die "api-name must produce a non-empty JVM package segment"
  fi

  package_base="${maven_group}.${api_slug}.client"
  IFS='.' read -r -a segments <<< "$package_base"
  for segment in "${segments[@]}"; do
    if [[ ! "$segment" =~ ^[a-z_][a-z0-9_]*$ ]]; then
      die "Derived JVM package segment is invalid: $segment"
    fi
  done

  printf '%s\n' "$package_base"
}

configure_npm_auth() {
  local registry="$1"
  local package_name="$2"
  local npmrc_path=".npmrc"
  local registry_host scope

  if [[ -z "${NODE_AUTH_TOKEN:-}" ]]; then
    return 0
  fi

  registry="${registry%/}"
  registry_host="${registry#http://}"
  registry_host="${registry_host#https://}"
  scope="${package_name%%/*}"

  touch "$npmrc_path"
  if [[ "$scope" == @* ]] && ! grep -qF "${scope}:registry=${registry}" "$npmrc_path"; then
    printf '%s:registry=%s\n' "$scope" "$registry" >> "$npmrc_path"
  fi
  if ! grep -qF "//${registry_host}/:_authToken=" "$npmrc_path"; then
    printf '//%s/:_authToken=%s\n' "$registry_host" "\${NODE_AUTH_TOKEN}" >> "$npmrc_path"
  fi
}

write_settings_gradle() {
  local output="$1"

  cat > "$output" <<'GRADLE'
pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
        maven {
            name = "JorisJonkersDevOpenApiClientGradle"
            url = uri("https://maven.pkg.github.com/JorisJonkers-dev/openapi-client-gradle")
            credentials {
                username =
                    providers
                        .gradleProperty("gpr.user")
                        .orElse(providers.environmentVariable("GITHUB_ACTOR"))
                        .orNull
                password =
                    providers
                        .gradleProperty("gpr.token")
                        .orElse(providers.environmentVariable("GITHUB_TOKEN"))
                        .orNull
            }
        }
    }
    resolutionStrategy {
        eachPlugin {
            if (requested.id.id == "dev.jorisjonkers.openapi-client") {
                useModule("dev.jorisjonkers:openapi-client-gradle:${requested.version}")
            }
        }
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
        maven("https://repo.spring.io/milestone")
    }
}

rootProject.name = "api-clients"

include(":java", ":kotlin")
GRADLE
}

write_root_build_gradle() {
  local output="$1"
  local maven_group="$2"
  local version="$3"

  cat > "$output" <<GRADLE
allprojects {
    group = "$maven_group"
    version = "$version"
}
GRADLE
}

write_jvm_build_gradle() {
  local output="$1"
  local language="$2"
  local artifact_id="$3"
  local plugin_version="$4"
  local spec_path="$5"
  local package_base="$6"

  cat > "$output" <<GRADLE
import org.gradle.api.publish.maven.MavenPublication
import org.gradle.external.javadoc.StandardJavadocDocletOptions

plugins {
    id("dev.jorisjonkers.openapi-client") version "$plugin_version"
    \`maven-publish\`
}

openApiClient {
GRADLE

  if [[ "$language" == "kotlin" ]]; then
    cat >> "$output" <<GRADLE
    useKotlinSpringRestClient()
GRADLE
  else
    cat >> "$output" <<GRADLE
    generatorName.set("java")
    library.set("restclient")
GRADLE
  fi

  cat >> "$output" <<GRADLE
    specPath.set("$spec_path")
    apiPackage.set("$package_base.api")
    modelPackage.set("$package_base.model")
    packageName.set("$package_base")
}

java {
    withSourcesJar()
    withJavadocJar()
}

tasks.withType<Javadoc>().configureEach {
    options {
        (this as StandardJavadocDocletOptions).addStringOption("Xdoclint:none", "-quiet")
    }
}

publishing {
    publications {
        create<MavenPublication>("mavenJava") {
            from(components["java"])
            groupId = project.group.toString()
            artifactId = "$artifact_id"
            version = project.version.toString()
        }
    }
    repositories {
        maven {
            name = "GitHubPackages"
            val repository =
                providers
                    .environmentVariable("GITHUB_REPOSITORY")
                    .orElse("JorisJonkers-dev/api-clients")
                    .get()
            url = uri("https://maven.pkg.github.com/\$repository")
            credentials {
                username =
                    providers
                        .gradleProperty("gpr.user")
                        .orElse(providers.environmentVariable("GITHUB_ACTOR"))
                        .orNull
                password =
                    providers
                        .gradleProperty("gpr.token")
                        .orElse(providers.environmentVariable("GITHUB_TOKEN"))
                        .orNull
            }
        }
    }

// Gradle 9 strict validation: sourcesJar/javadocJar package the generated OpenAPI sources,
// so they must declare an explicit dependency on the generate task.
tasks.matching { it.name == "sourcesJar" || it.name == "javadocJar" }.configureEach {
    dependsOn("generate")
}
}
GRADLE
}

write_typescript_package() {
  local package_dir="$1"
  local package_name="$2"
  local version="$3"
  local registry="$4"

  (
    cd "$package_dir"
    NODE_PACKAGE_NAME="$package_name" \
      NODE_PACKAGE_VERSION="$version" \
      NODE_REGISTRY_URL="${registry%/}" \
      NODE_HEY_API_OPENAPI_TS_VERSION="$HEY_API_OPENAPI_TS_VERSION" \
      NODE_TYPESCRIPT_VERSION="$TYPESCRIPT_VERSION" \
      NODE_ZOD_VERSION="$ZOD_VERSION" \
      node <<'NODE'
const fs = require('node:fs')

const pkg = {
  name: process.env.NODE_PACKAGE_NAME,
  version: process.env.NODE_PACKAGE_VERSION,
  type: 'module',
  files: ['dist'],
  exports: {
    '.': {
      types: './dist/index.d.ts',
      import: './dist/index.js',
    },
  },
  // Link the package to its (public) source repo so GitHub Packages inherits PUBLIC visibility
  // and consuming repos can install it; without this it defaults to private -> cross-repo 403.
  repository: {
    type: 'git',
    url: `git+https://github.com/${process.env.GITHUB_REPOSITORY || 'JorisJonkers-dev/api-clients'}.git`,
  },
  publishConfig: {
    registry: process.env.NODE_REGISTRY_URL,
    access: 'public',
  },
  scripts: {
    generate: 'openapi-ts -f openapi-ts.config.ts',
    build: 'tsc -p tsconfig.json',
  },
  dependencies: {
    zod: process.env.NODE_ZOD_VERSION,
  },
  devDependencies: {
    '@hey-api/openapi-ts': process.env.NODE_HEY_API_OPENAPI_TS_VERSION,
    typescript: process.env.NODE_TYPESCRIPT_VERSION,
  },
}

fs.writeFileSync('package.json', `${JSON.stringify(pkg, null, 2)}\n`)
NODE
  )
}

write_typescript_config() {
  local output="$1"
  local spec_path="$2"

  cat > "$output" <<TS
import { defineConfig } from '@hey-api/openapi-ts'

export default defineConfig({
  input: '$spec_path',
  output: {
    path: 'src/generated',
  },
  plugins: [
    '@hey-api/client-fetch',
    '@hey-api/typescript',
    'zod',
    {
      name: '@hey-api/sdk',
      validator: true,
    },
  ],
})
TS
}

write_tsconfig() {
  local output="$1"

  cat > "$output" <<'JSON'
{
  "compilerOptions": {
    "declaration": true,
    "declarationMap": true,
    "emitDeclarationOnly": false,
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "noEmitOnError": true,
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "target": "ES2022",
    "verbatimModuleSyntax": true
  },
  "include": ["src/**/*.ts"]
}
JSON
}

main() {
  required_env INPUT_MODE
  required_env INPUT_SPEC_PATH
  required_env INPUT_API_NAME
  required_env INPUT_VERSION
  required_env INPUT_TS_PACKAGE
  required_env INPUT_MAVEN_GROUP
  required_env INPUT_JAVA_ARTIFACT
  required_env INPUT_KOTLIN_ARTIFACT
  required_env INPUT_OPENAPI_CLIENT_GRADLE_VERSION
  required_env INPUT_NPM_REGISTRY_URL
  required_env GITHUB_WORKSPACE
  required_env RUNNER_TEMP

  local mode="$INPUT_MODE"
  local spec_path="$INPUT_SPEC_PATH"
  local api_name="$INPUT_API_NAME"
  local version="${INPUT_VERSION#v}"
  local ts_package="$INPUT_TS_PACKAGE"
  local maven_group="$INPUT_MAVEN_GROUP"
  local java_artifact="$INPUT_JAVA_ARTIFACT"
  local kotlin_artifact="$INPUT_KOTLIN_ARTIFACT"
  local plugin_version="$INPUT_OPENAPI_CLIENT_GRADLE_VERSION"
  local npm_registry_url="${INPUT_NPM_REGISTRY_URL%/}"
  local workspace_real source_spec source_spec_real clients_root jvm_dir ts_dir spec_dir
  local spec_extension spec_file_name jvm_spec_path ts_spec_path package_base

  version="${version#V}"

  case "$mode" in
    dry-run|publish)
      ;;
    *)
      die "mode must be dry-run or publish"
      ;;
  esac

  [[ -n "$version" ]] || die "version is empty after normalization"
  [[ "$spec_path" != /* ]] || die "spec-path must be relative to GITHUB_WORKSPACE"

  reject_unsafe_string "api-name" "$api_name"
  reject_unsafe_string "version" "$version"
  reject_unsafe_string "maven-group" "$maven_group"
  reject_unsafe_string "java-artifact" "$java_artifact"
  reject_unsafe_string "kotlin-artifact" "$kotlin_artifact"
  reject_unsafe_string "openapi-client-gradle-version" "$plugin_version"

  validate_identifier "maven-group" "$maven_group" '^[A-Za-z0-9_.-]+$'
  validate_identifier "java-artifact" "$java_artifact" '^[A-Za-z0-9_.-]+$'
  validate_identifier "kotlin-artifact" "$kotlin_artifact" '^[A-Za-z0-9_.-]+$'
  validate_identifier "openapi-client-gradle-version" "$plugin_version" '^[A-Za-z0-9_.+-]+$'
  validate_identifier "ts-package" "$ts_package" '^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$'

  workspace_real="$(realpath "$GITHUB_WORKSPACE")"
  source_spec_real="$(realpath -m "$workspace_real/$spec_path")"
  case "$source_spec_real" in
    "$workspace_real"/*)
      ;;
    *)
      die "spec-path must stay within GITHUB_WORKSPACE"
      ;;
  esac

  if [[ ! -f "$source_spec_real" ]]; then
    die "OpenAPI spec file does not exist: $source_spec_real"
  fi

  package_base="$(derive_jvm_package_base "$maven_group" "$api_name")"
  clients_root="$RUNNER_TEMP/api-clients"
  jvm_dir="$clients_root/jvm"
  ts_dir="$clients_root/typescript"
  spec_dir="$clients_root/spec"

  spec_extension="${source_spec_real##*.}"
  if [[ "$spec_extension" == "$source_spec_real" ]]; then
    spec_extension="json"
  else
    spec_extension="$(printf '%s' "$spec_extension" | tr '[:upper:]' '[:lower:]')"
  fi
  spec_file_name="openapi.${spec_extension}"
  source_spec="$spec_dir/$spec_file_name"
  jvm_spec_path="../spec/$spec_file_name"
  ts_spec_path="../spec/$spec_file_name"

  rm -rf "$jvm_dir"
  mkdir -p "$jvm_dir/java" "$jvm_dir/kotlin" "$spec_dir"
  if [[ -d "$ts_dir" ]]; then
    find "$ts_dir" -mindepth 1 -maxdepth 1 ! -name .npmrc -exec rm -rf {} +
  else
    mkdir -p "$ts_dir"
  fi
  mkdir -p "$ts_dir/src"
  cp "$source_spec_real" "$source_spec"

  write_settings_gradle "$jvm_dir/settings.gradle.kts"
  write_root_build_gradle "$jvm_dir/build.gradle.kts" "$maven_group" "$version"
  write_jvm_build_gradle "$jvm_dir/java/build.gradle.kts" "java" "$java_artifact" "$plugin_version" "$jvm_spec_path" "$package_base"
  write_jvm_build_gradle "$jvm_dir/kotlin/build.gradle.kts" "kotlin" "$kotlin_artifact" "$plugin_version" "$jvm_spec_path" "$package_base"

  (
    cd "$ts_dir"
    configure_npm_auth "$npm_registry_url" "$ts_package"
  )
  write_typescript_package "$ts_dir" "$ts_package" "$version" "$npm_registry_url"
  write_typescript_config "$ts_dir/openapi-ts.config.ts" "$ts_spec_path"
  write_tsconfig "$ts_dir/tsconfig.json"
  printf "export * from './generated/index'\n" > "$ts_dir/src/index.ts"

  command -v gradle >/dev/null 2>&1 || die "gradle is required on PATH"
  command -v npm >/dev/null 2>&1 || die "npm is required on PATH"
  command -v node >/dev/null 2>&1 || die "node is required on PATH"

  gradle --no-daemon -p "$jvm_dir" :java:build :kotlin:build

  (
    cd "$ts_dir"
    npm install --no-audit --no-fund
    npm run generate
    npm run build
  )

  case "$mode" in
    dry-run)
      gradle --no-daemon -p "$jvm_dir" :java:publishToMavenLocal :kotlin:publishToMavenLocal
      (
        cd "$ts_dir"
        npm pack --dry-run
      )
      ;;
    publish)
      if [[ -z "${GITHUB_TOKEN:-}" ]]; then
        die "GITHUB_TOKEN is required for Maven publishing"
      fi
      if [[ -z "${NODE_AUTH_TOKEN:-}" ]]; then
        die "NODE_AUTH_TOKEN is required for npm publishing"
      fi
      run_maven_publish_task "$jvm_dir" :java:publish
      run_maven_publish_task "$jvm_dir" :kotlin:publish
      (
        cd "$ts_dir"
        if npm_package_version_exists "$ts_package" "$version" "$npm_registry_url"; then
          echo "::notice::npm package ${ts_package}@${version} already exists; skipping publish."
        else
          npm publish --access public --provenance
        fi
      )
      ;;
  esac
}

main "$@"
