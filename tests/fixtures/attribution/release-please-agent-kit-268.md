:robot: I have created a release *beep* *boop*
---


## [6.0.0](https://github.com/JorisJonkers-dev/agent-kit/compare/v5.2.0...v6.0.0) (2026-11-09)


### ⚠ BREAKING CHANGES

* the renderer is now scripts/render_agent_kit.py, and the runtime package's build paths moved from runner-manifests/runtime/ to runner/manifests/runtime/.
* the tracked .claude/, .agents/ and .council/context/ copies are gone; run `uv run python render-agent-kit.py --write` after pulling. Source paths under skills/ moved to skills/workstation/ and templates/repo/ to skills/repo/ and agents/.
* the runtime-home package drops sdd/, the speckit-seed self-test and the sddSeed contract field.

### Features

* **build-tracker:** live ticket graph for every multi-ticket build ([#275](https://github.com/JorisJonkers-dev/agent-kit/issues/275)) ([0993045](https://github.com/JorisJonkers-dev/agent-kit/commit/0993045bc5ed0f817835c0fd91908dcf3ad104f6)), closes [#269](https://github.com/JorisJonkers-dev/agent-kit/issues/269)
* model gateway for every delegation ([#325](https://github.com/JorisJonkers-dev/agent-kit/issues/325)) ([e8cad85](https://github.com/JorisJonkers-dev/agent-kit/commit/e8cad85c613a362d2087c644a711663d77b68892))
* **opencode:** install and configure OpenCode on the workstation ([#313](https://github.com/JorisJonkers-dev/agent-kit/issues/313)) ([4421dd7](https://github.com/JorisJonkers-dev/agent-kit/commit/4421dd7368590ef465f1aaf5e27f34e8bddae1a0))
* **projects-layout:** one place for clones and worktrees, with a guard hook ([#376](https://github.com/JorisJonkers-dev/agent-kit/issues/376)) ([d154633](https://github.com/JorisJonkers-dev/agent-kit/commit/d154633cec5b3f1b0cb563f64a289fa9a8db356c))
* **registry:** add the Grimoire MCP server for the workstation ([#267](https://github.com/JorisJonkers-dev/agent-kit/issues/267)) ([a4ceb65](https://github.com/JorisJonkers-dev/agent-kit/commit/a4ceb65955cc2366664fedb140597306ba942fd5))


### Bug Fixes

* **critique:** detect Vibe sign-in on Linux ([#394](https://github.com/JorisJonkers-dev/agent-kit/issues/394)) ([ebc6d0b](https://github.com/JorisJonkers-dev/agent-kit/commit/ebc6d0b4e26182cb194ac9023949b4a858b52e19))
* **lsp:** install language servers that work on NixOS, and fix kotlin-lsp and jdtls ([#375](https://github.com/JorisJonkers-dev/agent-kit/issues/375)) ([d616478](https://github.com/JorisJonkers-dev/agent-kit/commit/d61647864e769807c91ce10252b9ffb3b4a61b2b))


### Code Refactoring

* drop Spec Kit, BMAD sync, spec-compare and claude-worker ([#274](https://github.com/JorisJonkers-dev/agent-kit/issues/274)) ([8a4fd73](https://github.com/JorisJonkers-dev/agent-kit/commit/8a4fd739e1b31d718c4288f0c93333669b9f16f5)), closes [#253](https://github.com/JorisJonkers-dev/agent-kit/issues/253)
* group the repository root into runner/, scripts/, provenance/ and docs/ ([#283](https://github.com/JorisJonkers-dev/agent-kit/issues/283)) ([f2b8870](https://github.com/JorisJonkers-dev/agent-kit/commit/f2b8870bb35a26e911eb52b66a2a99d394b7c0ad)), closes [#280](https://github.com/JorisJonkers-dev/agent-kit/issues/280) [#281](https://github.com/JorisJonkers-dev/agent-kit/issues/281) [#277](https://github.com/JorisJonkers-dev/agent-kit/issues/277)
* move skill sources under skills/ and stop tracking .claude, .agents and .council ([#282](https://github.com/JorisJonkers-dev/agent-kit/issues/282)) ([58e014a](https://github.com/JorisJonkers-dev/agent-kit/commit/58e014acba1e22278ee3aafb3bbf0840d5433862)), closes [#277](https://github.com/JorisJonkers-dev/agent-kit/issues/277)

---
This PR was generated with [Release Please](https://github.com/googleapis/release-please). See [documentation](https://github.com/googleapis/release-please#release-please).
