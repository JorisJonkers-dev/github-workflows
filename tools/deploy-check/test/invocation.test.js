// Locks in the exact CLI contract by running the pipeline against a stub
// toolkit that records its arguments. Every defect these tests describe was
// shipped at least once, and each was only discoverable at runtime before.
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, chmodSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { runPreview } from '../lib/preview.js'

const DEPLOYMENT = `apiVersion: deployment.jorisjonkers.dev/v2
kind: Deployment
metadata:
  name: svc
spec:
  schemaVersion: 0.16.0
  namespace: demo
  workloads:
    - name: api
      health:
        path: /health
        port: 8080
      rollbackTargetRetention:
        minimumDays: 90
        acknowledged: true
`

/** A stub deploy-config-schema that appends its argv to a log and writes the
 *  file named by --output, so the pipeline sees plausible results. */
function stubToolkit(dir, { failRender = false } = {}) {
  const log = path.join(dir, 'argv.log')
  const bin = path.join(dir, 'stub-toolkit.js')
  writeFileSync(
    bin,
    `#!/usr/bin/env node
const fs = require('node:fs'); const path = require('node:path')
const argv = process.argv.slice(2)
fs.appendFileSync(${JSON.stringify(log)}, JSON.stringify(argv) + '\\n')
const failRender = ${failRender ? 'true' : 'false'}
if (argv[0] === 'render' && failRender) {
  process.stderr.write(JSON.stringify({ valid: false, diagnostics: [{ code: 'E_COMMAND', message: 'stub failure' }] }))
  process.exit(1)
}
const i = argv.indexOf('--output'); const o = argv.indexOf('--out')
const target = i !== -1 ? argv[i + 1] : (o !== -1 ? argv[o + 1] : null)
if (target) {
  if (argv[0] === 'artifact' && argv[1] === 'emit-apply-bundle') {
    fs.mkdirSync(target, { recursive: true })
    fs.writeFileSync(path.join(target, 'kustomization.yaml'), 'resources: []\\n')
  } else if (argv[0] === 'artifact' && argv[1] === 'emit-contract') {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, 'spec:\\n  contextRef: ghcr.io/x/ctx@sha256:' + 'a'.repeat(64) + '\\n  imageDigests:\\n    api: ghcr.io/x/api@sha256:' + 'b'.repeat(64) + '\\n')
  } else {
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, 'kind: KubernetesWorkloadFragment\\nmanifests: []\\n')
  }
}
`,
  )
  chmodSync(bin, 0o755)
  return { bin: process.execPath, wrap: bin, log }
}

function fixture(opts = {}) {
  const root = mkdtempSync(path.join(tmpdir(), 'dc-'))
  mkdirSync(path.join(root, 'platform'), { recursive: true })
  writeFileSync(path.join(root, 'platform', 'deployment.yml'), DEPLOYMENT)
  writeFileSync(path.join(root, 'platform', 'images.lock.json'), JSON.stringify({ api: 'ghcr.io/x/api@sha256:' + 'b'.repeat(64) }))
  const ctx = path.join(root, 'ctx', 'context', 'public')
  mkdirSync(ctx, { recursive: true })
  writeFileSync(path.join(ctx, 'cluster-context-public.yml'), 'spec:\n  schemaVersion: 0.20.0\n')
  const stub = stubToolkit(root, opts)
  return { root, stub, ctx: path.join(root, 'ctx') }
}

function invoke(f, over = {}) {
  return runPreview({
    bin: f.stub.wrap,
    deployDir: 'platform',
    environments: ['production'],
    images: 'platform/images.lock.json',
    contextRef: 'ghcr.io/x/ctx@sha256:' + 'a'.repeat(64),
    contextDir: 'ctx',
    artifactName: 'svc',
    outDir: 'out',
    cwd: f.root,
    ...over,
  })
}

const calls = (f) => readFileSync(f.stub.log, 'utf8').trim().split('\n').map((l) => JSON.parse(l))

test('render --output names a file, never a directory', () => {
  const f = fixture()
  invoke(f)
  const renders = calls(f).filter((a) => a[0] === 'render')
  assert.equal(renders.length, 5)
  for (const a of renders) {
    const out = a[a.indexOf('--output') + 1]
    assert.ok(out.endsWith('.yaml'), `--output ${out} must be a file; a directory exits EISDIR`)
    assert.ok(existsSync(out), `${out} should have been written`)
  }
})

test('each fragment renders to its own file', () => {
  const f = fixture()
  invoke(f)
  const outs = calls(f).filter((a) => a[0] === 'render').map((a) => a[a.indexOf('--output') + 1])
  assert.equal(new Set(outs).size, 5, 'five fragments must not share one output path')
})

test('no unpublished artifact subcommand is ever invoked', () => {
  const f = fixture()
  invoke(f)
  const subs = calls(f).filter((a) => a[0] === 'artifact').map((a) => a[1])
  for (const s of subs) {
    assert.ok(['emit-apply-bundle', 'emit-contract', 'emit-kustomization-health'].includes(s), `artifact ${s} is not published`)
  }
  assert.ok(!subs.includes('leak-scan'))
  assert.ok(!subs.includes('validate-raw-manifests'))
})

test('emit-contract passes --deployment and --context and no --schema-version', () => {
  const f = fixture()
  invoke(f)
  const call = calls(f).find((a) => a[0] === 'artifact' && a[1] === 'emit-contract')
  assert.ok(call, 'emit-contract was never called')
  assert.ok(call.includes('--deployment'), '--deployment is required')
  assert.ok(call.includes('--context'), '--context is required')
  assert.ok(!call.includes('--schema-version'), '--schema-version is not a flag on emit-contract')
})

test('the apply bundle is produced from the rendered manifests', () => {
  const f = fixture()
  invoke(f)
  const call = calls(f).find((a) => a[0] === 'artifact' && a[1] === 'emit-apply-bundle')
  assert.ok(call, 'emit-apply-bundle was never called')
  assert.match(call[call.indexOf('--manifests') + 1], /out.manifests.production$/)
})

test('a failed render is reported and blocks the contract', () => {
  const f = fixture({ failRender: true })
  const { renderFailures, contract, result } = invoke(f)
  assert.equal(renderFailures.length, 5)
  assert.equal(contract, null)
  // Nothing rendered, so the secret check must not claim to have inspected anything.
  assert.equal(result.checks.no_raw_secrets.status, 'not_applicable')
  assert.ok(!calls(f).some((a) => a[0] === 'artifact' && a[1] === 'emit-contract'))
})

test('a stale out/ directory cannot satisfy the next run', () => {
  const f = fixture()
  const stale = path.join(f.root, 'out', 'manifests', 'production', 'leftover.yaml')
  mkdirSync(path.dirname(stale), { recursive: true })
  writeFileSync(stale, 'kind: Secret\n')
  const { result } = invoke(f)
  assert.equal(result.checks.no_raw_secrets.status, 'pass', 'the stale Secret should have been cleared before rendering')
  assert.ok(!existsSync(stale))
})

test('a missing deployment.yml fails with a named error', () => {
  const f = fixture()
  assert.throws(() => invoke(f, { deployDir: 'nope' }), /E_DEPLOYMENT_MISSING/)
})

test('a context directory with no cluster context fails with a named error', () => {
  const f = fixture()
  mkdirSync(path.join(f.root, 'empty'), { recursive: true })
  assert.throws(() => invoke(f, { contextDir: 'empty' }), /E_CONTEXT_FILE_MISSING/)
})

// A pulled context package nests cluster-context-public.yml under
// context/public/. --context-dir makes the toolkit look for the file directly
// inside the directory it is given, without searching, so passing the package
// root fails with ENOENT once the layout is nested -- which is exactly what CI
// pulls. Every render must therefore receive a --context-path that exists.
test('the toolkit is given a context file that exists, not a package root', () => {
  const f = fixture()
  invoke(f)
  for (const a of calls(f).filter((c) => c[0] === 'render')) {
    const i = a.indexOf('--context-path')
    assert.notEqual(i, -1, 'render must pass --context-path')
    const file = a[i + 1]
    assert.ok(existsSync(file), `--context-path ${file} does not exist`)
    assert.match(file, /cluster-context-public\.yml$/)
    assert.ok(!a.includes('--context-dir'), '--context-dir does not search nested layouts')
  }
})

test('a nested context layout resolves to the nested file', () => {
  const f = fixture()
  const { contextPath } = invoke(f)
  assert.match(contextPath, /ctx.context.public.cluster-context-public\.yml$/)
})
