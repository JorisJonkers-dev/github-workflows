// Thin, typed wrapper over the deploy-config-schema CLI.
//
// Every call here was previously spelled out in bash in two places, and both
// got the argument shapes wrong in ways that only surfaced at runtime:
//   - `render --output <dir>` exits EISDIR; --output names a FILE.
//   - `artifact leak-scan` and `artifact validate-raw-manifests` are not
//     published subcommands; the toolkit exposes only emit-apply-bundle,
//     emit-contract and emit-kustomization-health.
//   - `artifact emit-contract` requires --deployment and --context, and takes
//     no --schema-version flag.
// Keeping the invocations in one place means a wrong flag is a test failure
// here rather than a CI failure in seven repositories.
import { spawnSync } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

export const FRAGMENTS = [
  'kubernetes-workload-fragment',
  'traefik-route-fragment',
  'gatus-endpoint-fragment',
  'edge-catalog-fragment',
  'image-metadata-fragment',
]

/** Subcommands `deploy-config-schema artifact` actually publishes. */
export const PUBLISHED_ARTIFACT_SUBCOMMANDS = ['emit-apply-bundle', 'emit-contract', 'emit-kustomization-health']

export class ToolkitError extends Error {
  constructor(message, { code, stderr, args } = {}) {
    super(message)
    this.name = 'ToolkitError'
    this.code = code
    this.stderr = stderr
    this.args = args
  }
}

function run(bin, args, { cwd } = {}) {
  const res = spawnSync(bin, args, { cwd, encoding: 'utf8' })
  if (res.error) throw new ToolkitError(`could not execute ${bin}: ${res.error.message}`, { args })
  return { status: res.status ?? 1, stdout: res.stdout ?? '', stderr: res.stderr ?? '' }
}

/**
 * Context is supplied either as a local directory (local development) or as a
 * digest-pinned ref plus the file pulled from it (CI). Both forms are valid;
 * mixing them is not.
 */
export function contextArgs({ contextDir, contextRef, contextPath }) {
  if (contextDir) return ['--context-dir', contextDir]
  if (contextRef && contextPath) return ['--context', contextRef, '--context-path', contextPath]
  throw new ToolkitError('supply either contextDir, or both contextRef and contextPath')
}

export function renderFragment(bin, { fragment, deployDir, env, images, outFile, context, cwd }) {
  mkdirSync(path.dirname(outFile), { recursive: true })
  const args = [
    'render', fragment, deployDir,
    '--env', env,
    ...contextArgs(context),
    '--images', images,
    // A directory here exits EISDIR: --output names the file to write.
    '--output', outFile,
  ]
  const { status, stdout, stderr } = run(bin, args, { cwd })
  if (status !== 0) {
    throw new ToolkitError(`render ${fragment} (${env}) failed`, { code: status, stderr: stderr || stdout, args })
  }
  return outFile
}

export function emitApplyBundle(bin, { manifestsDir, outDir, cwd }) {
  const args = ['artifact', 'emit-apply-bundle', '--manifests', manifestsDir, '--out', outDir]
  const { status, stdout, stderr } = run(bin, args, { cwd })
  if (status !== 0) throw new ToolkitError('emit-apply-bundle failed', { code: status, stderr: stderr || stdout, args })
  return outDir
}

export function emitKustomizationHealth(bin, { deploymentYml, env, images, outFile, cwd }) {
  mkdirSync(path.dirname(outFile), { recursive: true })
  const args = [
    'artifact', 'emit-kustomization-health',
    '--deployment', deploymentYml,
    '--env', env,
    '--image-digests', images,
    '--out', outFile,
  ]
  const { status, stdout, stderr } = run(bin, args, { cwd })
  if (status !== 0) throw new ToolkitError(`emit-kustomization-health (${env}) failed`, { code: status, stderr: stderr || stdout, args })
  return outFile
}

export function emitContract(bin, { artifactName, environments, images, contextRef, deploymentYml, contextFile, provenanceVerified, outFile, outputRoot, cwd }) {
  mkdirSync(path.dirname(outFile), { recursive: true })
  const args = [
    'artifact', 'emit-contract',
    '--artifact-name', artifactName,
    '--environments', environments.join(','),
    '--images', images,
    '--context-ref', contextRef,
    // Both required, and both were missing from the per-repo script.
    '--deployment', deploymentYml,
    '--context', contextFile,
    '--out', outFile,
  ]
  if (provenanceVerified !== undefined) {
    // The contract field is a boolean: either provenance was verified or it was
    // not. 'not_applicable' distinguishes "could not be evaluated" from "failed
    // verification" for the scorecard only, and records as not verified here.
    // The toolkit happens to coerce an unknown string the same way; saying it
    // explicitly means the recorded value does not depend on that.
    args.push('--provenance-verified', provenanceVerified === true ? 'true' : 'false')
  }
  if (outputRoot) args.push('--output-root', outputRoot)
  const { status, stdout, stderr } = run(bin, args, { cwd })
  if (status !== 0) throw new ToolkitError('emit-contract failed', { code: status, stderr: stderr || stdout, args })
  return outFile
}
