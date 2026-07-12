# Changelog

## Unreleased

### Features

* enforce zero-warning reusable CI gates for Node, Nix, Python, and JVM workflows
* fail Gradle warnings and deprecations while making JVM lint static-only by default

## [0.12.7](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.12.6...v0.12.7) (2026-07-12)


### Bug Fixes

* **leak-scan:** replace --files-at-commit with gitleaks git --log-opts for pr-diff mode ([#75](https://github.com/JorisJonkers-dev/github-workflows/issues/75)) ([4be901c](https://github.com/JorisJonkers-dev/github-workflows/commit/4be901c3d65af28583ed8f429b334326e0292ab0))

## [0.12.6](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.12.5...v0.12.6) (2026-07-12)


### Bug Fixes

* **deploy-artifact:** anchor kind=Secret grep to prevent SecretStore over-match ([#73](https://github.com/JorisJonkers-dev/github-workflows/issues/73)) ([8e57f62](https://github.com/JorisJonkers-dev/github-workflows/commit/8e57f6243ae6373a0031f6cdad0d4b9cdc57f22c))

## [0.12.5](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.12.4...v0.12.5) (2026-07-10)


### Bug Fixes

* **leak-scan:** replace --all with --log-opts=--all for gitleaks v8.x ([#67](https://github.com/JorisJonkers-dev/github-workflows/issues/67)) ([d993ef7](https://github.com/JorisJonkers-dev/github-workflows/commit/d993ef74677700593e4e11e770542c3257403e4f))

## [0.12.4](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.12.3...v0.12.4) (2026-07-10)


### Bug Fixes

* **leak-scan:** install gitleaks+trufflehog with SHA pins; fail-closed all-refs mode ([#63](https://github.com/JorisJonkers-dev/github-workflows/issues/63)) ([1ddf81e](https://github.com/JorisJonkers-dev/github-workflows/commit/1ddf81e6128055f44585c449aee1b445faf40f67))

## [0.12.3](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.12.2...v0.12.3) (2026-07-10)


### Bug Fixes

* **deploy-preview:** fail loud on render failures and anchor the raw-secret match ([#62](https://github.com/JorisJonkers-dev/github-workflows/issues/62)) ([36710bd](https://github.com/JorisJonkers-dev/github-workflows/commit/36710bd96fd04d182b9181df40b21dafab11014f))

## [0.12.2](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.12.1...v0.12.2) (2026-07-10)


### Bug Fixes

* **deploy-preview:** scorecard gate misjudges not_applicable, no_raw_secrets, and npm_signatures_verified ([#60](https://github.com/JorisJonkers-dev/github-workflows/issues/60)) ([77c8d5c](https://github.com/JorisJonkers-dev/github-workflows/commit/77c8d5cc86d6dbffc67c6345a81b7b37c724b0eb))

## [0.12.1](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.12.0...v0.12.1) (2026-07-10)


### Bug Fixes

* **deploy-artifact:** handle npm audit no-deps skip for GHPR scoped packages ([5039ff0](https://github.com/JorisJonkers-dev/github-workflows/commit/5039ff071bd7472c6544771949b634f3aec570aa))

## [0.12.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.11...v0.12.0) (2026-07-09)


### Features

* **leak-scan:** add exclude-paths input for pr-diff mode ([c8c23b5](https://github.com/JorisJonkers-dev/github-workflows/commit/c8c23b5e0e5a8ef62927d5febee04bf4be9013ef))

## [0.11.11](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.10...v0.11.11) (2026-07-09)


### Bug Fixes

* remove tag from attest subject-name ([#53](https://github.com/JorisJonkers-dev/github-workflows/issues/53)) ([597a48f](https://github.com/JorisJonkers-dev/github-workflows/commit/597a48fc51401264687bc9b32b16b11bda428d62))

## [0.11.10](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.9...v0.11.10) (2026-07-09)


### Bug Fixes

* add subject-name to attest-build-provenance step ([#51](https://github.com/JorisJonkers-dev/github-workflows/issues/51)) ([07eff62](https://github.com/JorisJonkers-dev/github-workflows/commit/07eff62df010b9a16afd7ea5310df1091da655cb))

## [0.11.9](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.8...v0.11.9) (2026-07-09)


### Bug Fixes

* use deploy- tag prefix and fix oras resolve for artifact digest ([#49](https://github.com/JorisJonkers-dev/github-workflows/issues/49)) ([ca4ace3](https://github.com/JorisJonkers-dev/github-workflows/commit/ca4ace3e455f019b32b4edcd9dc0ddcc759d94c5))

## [0.11.8](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.7...v0.11.8) (2026-07-09)


### Bug Fixes

* add GHCR auth and fix idempotency check in deploy-artifact publish ([#47](https://github.com/JorisJonkers-dev/github-workflows/issues/47)) ([1dcc8d0](https://github.com/JorisJonkers-dev/github-workflows/commit/1dcc8d03be3027ac095d9298c39f3e34e3b3d226))

## [0.11.7](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.6...v0.11.7) (2026-07-09)


### Bug Fixes

* remove kubeconform and kustomize validation for fragment output ([#45](https://github.com/JorisJonkers-dev/github-workflows/issues/45)) ([99dfaf6](https://github.com/JorisJonkers-dev/github-workflows/commit/99dfaf601ee57ea10fb03331ca036731fefeeea4))

## [0.11.6](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.5...v0.11.6) (2026-07-09)


### Bug Fixes

* pass per-fragment file path to --output in render loop ([#43](https://github.com/JorisJonkers-dev/github-workflows/issues/43)) ([a212a6e](https://github.com/JorisJonkers-dev/github-workflows/commit/a212a6eda85b00b1e9fef006520805ae0ab395b1))

## [0.11.5](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.4...v0.11.5) (2026-07-09)


### Bug Fixes

* skip deploy-config-schema validate for ClusterContext file ([#41](https://github.com/JorisJonkers-dev/github-workflows/issues/41)) ([97d6e32](https://github.com/JorisJonkers-dev/github-workflows/commit/97d6e32dd12f3318dac3727ad5fd67ed43d0f6d8))

## [0.11.4](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.3...v0.11.4) (2026-07-09)


### Bug Fixes

* skip npm audit signatures for packages from private registry (GHPR not supported) ([#39](https://github.com/JorisJonkers-dev/github-workflows/issues/39)) ([1ead5ca](https://github.com/JorisJonkers-dev/github-workflows/commit/1ead5ca059c4b4cd51a63aadb55a196508b1b024))

## [0.11.3](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.2...v0.11.3) (2026-07-09)


### Bug Fixes

* read schema version from package.json instead of non-existent --version flag ([#37](https://github.com/JorisJonkers-dev/github-workflows/issues/37)) ([0cc8c8a](https://github.com/JorisJonkers-dev/github-workflows/commit/0cc8c8a09fc457d0a6cc72c2f5861e2edaa9abe8))

## [0.11.2](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.1...v0.11.2) (2026-07-09)


### Bug Fixes

* correct invalid action SHA pins ([#35](https://github.com/JorisJonkers-dev/github-workflows/issues/35)) ([351fd23](https://github.com/JorisJonkers-dev/github-workflows/commit/351fd23e045364b3daad8300024f92f74f740033))

## [0.11.1](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.11.0...v0.11.1) (2026-07-08)


### Bug Fixes

* align deploy actions with the real deploy-config-schema 0.16.0 CLI ([#33](https://github.com/JorisJonkers-dev/github-workflows/issues/33)) ([c77dd90](https://github.com/JorisJonkers-dev/github-workflows/commit/c77dd90dd5a810be9291a0a31a07480410da24f2))

## [0.11.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.10.2...v0.11.0) (2026-07-08)


### Features

* deploy-artifact, leak-scan and deploy-validate reusable workflows ([#31](https://github.com/JorisJonkers-dev/github-workflows/issues/31)) ([2cbc78f](https://github.com/JorisJonkers-dev/github-workflows/commit/2cbc78fdd3bd6e522f54af65be9c391f65916a70))

## [0.10.2](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.10.1...v0.10.2) (2026-07-01)


### Bug Fixes

* **node-ci:** exclude vendored tree via config, not deletion ([#26](https://github.com/JorisJonkers-dev/github-workflows/issues/26)) ([bde2ec0](https://github.com/JorisJonkers-dev/github-workflows/commit/bde2ec0d720ec2f484c8c479bbfa31f5c9c4e075))

## [0.10.1](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.10.0...v0.10.1) (2026-07-01)


### Bug Fixes

* **node-ci:** remove vendored actions tree before quality checks ([#24](https://github.com/JorisJonkers-dev/github-workflows/issues/24)) ([15e0845](https://github.com/JorisJonkers-dev/github-workflows/commit/15e0845b8f6dc72948884b47b8efca406cf38e3f))

## [0.10.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.9.0...v0.10.0) (2026-07-01)


### Features

* enforce zero-warning reusable CI ([#22](https://github.com/JorisJonkers-dev/github-workflows/issues/22)) ([c8ac6ff](https://github.com/JorisJonkers-dev/github-workflows/commit/c8ac6ffc8d9645639c3c2eec79d532c9f27c1073))

## [0.9.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.8.1...v0.9.0) (2026-06-29)


### Features

* add deploy automation reusables ([#17](https://github.com/JorisJonkers-dev/github-workflows/issues/17)) ([e6b6073](https://github.com/JorisJonkers-dev/github-workflows/commit/e6b607326865b03a8db1518b485338d2f2f64943))
* **ci:** migrate reusable workflows to JorisJonkers-dev coordinates ([#3](https://github.com/JorisJonkers-dev/github-workflows/issues/3)) ([7d1b8f3](https://github.com/JorisJonkers-dev/github-workflows/commit/7d1b8f33c02bc8deed9b5f68cd5018365c61ac5d))
* **clients:** add shared publish-api-clients reusable workflow + api-client-publish action ([#9](https://github.com/JorisJonkers-dev/github-workflows/issues/9)) ([96a2b65](https://github.com/JorisJonkers-dev/github-workflows/commit/96a2b650adae8a0f3fcef39262a63bd1fd3e7ab1))


### Bug Fixes

* **clients:** declare sourcesJar/javadocJar dependency on generate ([#13](https://github.com/JorisJonkers-dev/github-workflows/issues/13)) ([f67200c](https://github.com/JorisJonkers-dev/github-workflows/commit/f67200c6f04eed1040097fc59f761dc13d15ee24))
* **clients:** link npm package to its repo for public visibility ([#15](https://github.com/JorisJonkers-dev/github-workflows/issues/15)) ([8bccf4d](https://github.com/JorisJonkers-dev/github-workflows/commit/8bccf4d5d7a34d3e972c3d5930ccb0e59b15553a))
* **clients:** use plain setup-node (no install in empty dir) ([#11](https://github.com/JorisJonkers-dev/github-workflows/issues/11)) ([bd9c029](https://github.com/JorisJonkers-dev/github-workflows/commit/bd9c0294ca7841fc74f3d043263522a44f8dbf91))
* make reusable publishes idempotent ([#19](https://github.com/JorisJonkers-dev/github-workflows/issues/19)) ([2189fbe](https://github.com/JorisJonkers-dev/github-workflows/commit/2189fbe8b8c17fde54b1e501de888246484b7fd2))
* **reusable:** check out github-workflows at github.job_workflow_sha ([#7](https://github.com/JorisJonkers-dev/github-workflows/issues/7)) ([78a598a](https://github.com/JorisJonkers-dev/github-workflows/commit/78a598a5a69acb28269bf84554789bcb3c108113))

## [0.8.1](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.8.0...v0.8.1) (2026-06-29)


### Bug Fixes

* make reusable publishes idempotent ([5f173b4](https://github.com/JorisJonkers-dev/github-workflows/commit/5f173b4189f38d9e683a4c51af2f0fc3a1e45f9d))

## [0.8.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.7.3...v0.8.0) (2026-06-29)


### Features

* add deploy automation reusables ([#17](https://github.com/JorisJonkers-dev/github-workflows/issues/17)) ([e6b6073](https://github.com/JorisJonkers-dev/github-workflows/commit/e6b607326865b03a8db1518b485338d2f2f64943))

## [0.7.3](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.7.2...v0.7.3) (2026-06-28)


### Bug Fixes

* **clients:** link npm package to its repo for public visibility ([#15](https://github.com/JorisJonkers-dev/github-workflows/issues/15)) ([8bccf4d](https://github.com/JorisJonkers-dev/github-workflows/commit/8bccf4d5d7a34d3e972c3d5930ccb0e59b15553a))

## [0.7.2](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.7.1...v0.7.2) (2026-06-28)


### Bug Fixes

* **clients:** declare sourcesJar/javadocJar dependency on generate ([#13](https://github.com/JorisJonkers-dev/github-workflows/issues/13)) ([f67200c](https://github.com/JorisJonkers-dev/github-workflows/commit/f67200c6f04eed1040097fc59f761dc13d15ee24))

## [0.7.1](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.7.0...v0.7.1) (2026-06-28)


### Bug Fixes

* **clients:** use plain setup-node (no install in empty dir) ([#11](https://github.com/JorisJonkers-dev/github-workflows/issues/11)) ([bd9c029](https://github.com/JorisJonkers-dev/github-workflows/commit/bd9c0294ca7841fc74f3d043263522a44f8dbf91))

## [0.7.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.6.1...v0.7.0) (2026-06-28)


### Features

* **clients:** add shared publish-api-clients reusable workflow + api-client-publish action ([#9](https://github.com/JorisJonkers-dev/github-workflows/issues/9)) ([96a2b65](https://github.com/JorisJonkers-dev/github-workflows/commit/96a2b650adae8a0f3fcef39262a63bd1fd3e7ab1))

## [0.6.1](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.6.0...v0.6.1) (2026-06-28)


### Bug Fixes

* **reusable:** check out github-workflows at github.job_workflow_sha ([#7](https://github.com/JorisJonkers-dev/github-workflows/issues/7)) ([78a598a](https://github.com/JorisJonkers-dev/github-workflows/commit/78a598a5a69acb28269bf84554789bcb3c108113))

## [0.6.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.5.0...v0.6.0) (2026-06-28)


### Features

* **ci:** migrate reusable workflows to JorisJonkers-dev coordinates ([#3](https://github.com/JorisJonkers-dev/github-workflows/issues/3)) ([7d1b8f3](https://github.com/JorisJonkers-dev/github-workflows/commit/7d1b8f33c02bc8deed9b5f68cd5018365c61ac5d))

## [0.5.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.4.0...v0.5.0) (2026-06-10)


### Features

* GitHub Packages npm auth + reusable deploy-config render-drift ([#13](https://github.com/JorisJonkers-dev/github-workflows/issues/13)) ([48578c4](https://github.com/JorisJonkers-dev/github-workflows/commit/48578c429faa5cc8298126cc97ecd65ddc3d63ba))

## [0.4.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.3.0...v0.4.0) (2026-06-10)


### Features

* reusable flux-render-validate workflow + composite action (spec 009) ([#11](https://github.com/JorisJonkers-dev/github-workflows/issues/11)) ([cae8c61](https://github.com/JorisJonkers-dev/github-workflows/commit/cae8c61ee10701ded2509aa38f4a664f3a58049a))
* reusable platform-config-validate workflow + action ([#10](https://github.com/JorisJonkers-dev/github-workflows/issues/10)) ([512033e](https://github.com/JorisJonkers-dev/github-workflows/commit/512033e7b4e2fc288243a42120ef2e112d7b98c1))

## [0.3.0](https://github.com/JorisJonkers-dev/github-workflows/compare/v0.2.0...v0.3.0) (2026-06-09)


### Features

* per-service CRaC sidecar topology + compose system-test action skeleton (round 3) ([#6](https://github.com/JorisJonkers-dev/github-workflows/issues/6)) ([76c09b0](https://github.com/JorisJonkers-dev/github-workflows/commit/76c09b0069c7a40d028d7e47a2cc5e041ffcdc9a))
* round 2 reusable workflows + migration guard ([#5](https://github.com/JorisJonkers-dev/github-workflows/issues/5)) ([7650398](https://github.com/JorisJonkers-dev/github-workflows/commit/7650398d37e9715be5643f2a606595fb9dc1584a))
* working compose system-test composite action (round 4) ([#7](https://github.com/JorisJonkers-dev/github-workflows/issues/7)) ([c7e593f](https://github.com/JorisJonkers-dev/github-workflows/commit/c7e593f9b5365cface340ae1eb1429f68e492c26))
