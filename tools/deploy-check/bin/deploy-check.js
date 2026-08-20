#!/usr/bin/env node
// deploy-check -- render a service repo's deployment and score its readiness.
//
// Replaces platform/render-local.sh, which every service repo carried its own
// 400-line copy of. The same code path backs the deploy-preview action, so a
// local run and a CI run cannot disagree.
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { runPreview } from '../lib/preview.js'
import { renderScorecardMarkdown, CHECKS } from '../lib/scorecard.js'
import { renderPreviewSummary } from '../lib/summary.js'
import { resolveToolkit } from '../lib/resolve-toolkit.js'

const USAGE = `Usage:
  deploy-check preview [options]

Options:
  --deploy-dir <dir>        directory holding deployment.yml (default: platform)
  --schema-version <ver>    exact deploy-config-schema version (required unless --bin)
  --context-ref <ref>       digest-pinned OCI context ref (required)
  --context-dir <dir>       local context package directory (local development)
  --context-path <file>     cluster-context-public.yml (with --context-ref, for CI)
  --images <file>           images.lock.json (default: <deploy-dir>/images.lock.json)
  --artifact-name <name>    contract artifact name (default: deployment metadata.name)
  --environments <list>     comma-separated (default: production)
  --out <dir>               output directory (default: out)
  --bin <path>              use an already-installed deploy-config-schema
  --provenance-verified     record npm provenance as verified
  --json                    emit the scorecard as JSON on stdout
  --markdown-out <file>     also write the scorecard markdown to a file
  -h, --help

Exit codes: 0 all checks pass or are not_applicable; 1 a check failed or a
render failed.`

function parseArgs(argv) {
  const opts = { deployDir: 'platform', environments: ['production'], out: 'out' }
  let i = 0
  const need = (flag) => {
    const v = argv[++i]
    if (v === undefined) throw new Error(`${flag} requires a value`)
    return v
  }
  for (; i < argv.length; i++) {
    const a = argv[i]
    switch (a) {
      case '--deploy-dir': opts.deployDir = need(a); break
      case '--schema-version': opts.schemaVersion = need(a); break
      case '--context-ref': opts.contextRef = need(a); break
      case '--context-dir': opts.contextDir = need(a); break
      case '--context-path': opts.contextPath = need(a); break
      case '--images': opts.images = need(a); break
      case '--artifact-name': opts.artifactName = need(a); break
      case '--environments': opts.environments = need(a).split(',').map((s) => s.trim()).filter(Boolean); break
      case '--out': opts.out = need(a); break
      case '--bin': opts.bin = need(a); break
      case '--provenance-verified': opts.provenanceVerified = true; break
      case '--json': opts.json = true; break
      case '--markdown-out': opts.markdownOut = need(a); break
      case '-h': case '--help': opts.help = true; break
      default: throw new Error(`unknown option: ${a}`)
    }
  }
  return opts
}

function fail(msg) {
  process.stderr.write(`ERROR: ${msg}\n`)
  process.exit(1)
}

async function main() {
  const [command, ...rest] = process.argv.slice(2)
  if (!command || command === '-h' || command === '--help') {
    process.stdout.write(`${USAGE}\n`)
    process.exit(command ? 0 : 1)
  }
  if (command !== 'preview') fail(`unknown command: ${command}\n\n${USAGE}`)

  let opts
  try { opts = parseArgs(rest) } catch (err) { fail(`${err.message}\n\n${USAGE}`) }
  if (opts.help) { process.stdout.write(`${USAGE}\n`); process.exit(0) }

  if (!opts.contextRef) fail('--context-ref is required (it is recorded in the artifact contract)')
  if (!opts.contextDir && !opts.contextPath) fail('supply --context-dir, or --context-path alongside --context-ref')
  if (!opts.images) opts.images = path.join(opts.deployDir, 'images.lock.json')

  let bin = opts.bin
  if (!bin) {
    if (!opts.schemaVersion) fail('--schema-version is required unless --bin is given')
    try { bin = resolveToolkit({ schemaVersion: opts.schemaVersion }) } catch (err) { fail(err.message) }
  }

  if (!opts.artifactName) {
    // Default to the deployment's own name rather than a placeholder, so the
    // contract is attributable without every caller repeating it.
    try {
      const YAML = (await import('yaml')).default
      const { readFileSync } = await import('node:fs')
      opts.artifactName = YAML.parse(readFileSync(path.join(opts.deployDir, 'deployment.yml'), 'utf8'))?.metadata?.name
    } catch { /* validated in runPreview */ }
  }
  if (!opts.artifactName) fail(`--artifact-name is required (deployment.yml declares no metadata.name)`)

  let outcome
  try {
    outcome = runPreview({
      bin,
      deployDir: opts.deployDir,
      environments: opts.environments,
      images: opts.images,
      contextRef: opts.contextRef,
      contextDir: opts.contextDir,
      contextPath: opts.contextPath,
      artifactName: opts.artifactName,
      outDir: opts.out,
      provenanceVerified: opts.provenanceVerified,
    })
  } catch (err) {
    fail(err.stderr ? `${err.message}\n${err.stderr}` : err.message)
  }

  const { result, renderFailures, contractError, contract, deployment } = outcome
  const markdown = renderScorecardMarkdown(result, opts.artifactName)
  const summary = renderPreviewSummary({
    deployment,
    contract,
    result,
    contextRef: opts.contextRef,
    environments: opts.environments,
    renderedManifests: outcome.renderedManifests ?? [],
    renderFailures,
  })

  mkdirSync(opts.out, { recursive: true })
  writeFileSync(path.join(opts.out, 'scorecard.json'), `${JSON.stringify(
    Object.fromEntries(CHECKS.map((c) => [c, result.checks[c].status])), null, 2)}\n`)
  writeFileSync(path.join(opts.out, 'scorecard-detail.json'), `${JSON.stringify(result, null, 2)}\n`)
  writeFileSync(path.join(opts.out, 'scorecard.md'), `${markdown}\n`)
  writeFileSync(path.join(opts.out, 'deploy-preview-summary.md'), `${summary}\n`)
  if (opts.markdownOut) {
    mkdirSync(path.dirname(opts.markdownOut), { recursive: true })
    writeFileSync(opts.markdownOut, `${summary}\n`)
  }

  if (opts.json) process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
  else process.stdout.write(`${markdown}\n`)

  for (const f of renderFailures) {
    process.stderr.write(`::error::render ${f.fragment} (${f.env}) failed: ${f.detail}\n`)
  }
  if (contractError) process.stderr.write(`::error::emit-contract failed: ${contractError.stderr ?? contractError.message}\n`)

  if (renderFailures.length > 0) fail(`E_RENDER_FAILED: ${renderFailures.map((f) => `${f.env}/${f.fragment}`).join(', ')}`)
  if (contractError) fail('E_EMIT_CONTRACT_FAILED')
  if (result.overall !== 'pass') fail(`scorecard has ${result.failed.length} failing check(s): ${result.failed.join(', ')}`)
  process.stderr.write('all scorecard checks passed\n')
}

main().catch((err) => fail(err.stack ?? String(err)))
