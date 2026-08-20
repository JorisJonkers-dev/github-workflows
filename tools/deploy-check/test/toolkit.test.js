import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { FRAGMENTS, PUBLISHED_ARTIFACT_SUBCOMMANDS, ToolkitError, contextArgs } from '../lib/toolkit.js'
import { findClusterContext } from '../lib/preview.js'

test('all five fragments are rendered', () => {
  assert.deepEqual(FRAGMENTS, [
    'kubernetes-workload-fragment',
    'traefik-route-fragment',
    'gatus-endpoint-fragment',
    'edge-catalog-fragment',
    'image-metadata-fragment',
  ])
})

// artifact leak-scan and artifact validate-raw-manifests were called by the
// predecessor scripts and exit E_USAGE: they are not published.
test('the published artifact subcommand set is exactly the three that exist', () => {
  assert.deepEqual([...PUBLISHED_ARTIFACT_SUBCOMMANDS].sort(), [
    'emit-apply-bundle',
    'emit-contract',
    'emit-kustomization-health',
  ])
  for (const absent of ['leak-scan', 'validate-raw-manifests']) {
    assert.ok(!PUBLISHED_ARTIFACT_SUBCOMMANDS.includes(absent), absent)
  }
})

test('contextArgs uses --context-dir for a local package', () => {
  assert.deepEqual(contextArgs({ contextDir: '/ctx' }), ['--context-dir', '/ctx'])
})

test('contextArgs uses --context with --context-path for a pinned ref', () => {
  assert.deepEqual(
    contextArgs({ contextRef: 'ghcr.io/x@sha256:abc', contextPath: '/c/ctx.yml' }),
    ['--context', 'ghcr.io/x@sha256:abc', '--context-path', '/c/ctx.yml'],
  )
})

test('contextArgs rejects a ref without the file it came from', () => {
  assert.throws(() => contextArgs({ contextRef: 'ghcr.io/x@sha256:abc' }), ToolkitError)
  assert.throws(() => contextArgs({}), ToolkitError)
})

test('findClusterContext prefers the canonical context/public location', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'ctx-'))
  mkdirSync(path.join(root, 'a'), { recursive: true })
  mkdirSync(path.join(root, 'context', 'public'), { recursive: true })
  writeFileSync(path.join(root, 'a', 'cluster-context-public.yml'), 'x: 1\n')
  writeFileSync(path.join(root, 'context', 'public', 'cluster-context-public.yml'), 'x: 2\n')
  assert.match(findClusterContext(root), /context.public.cluster-context-public\.yml$/)
})

test('findClusterContext falls back to any match', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'ctx-'))
  mkdirSync(path.join(root, 'deep', 'nested'), { recursive: true })
  writeFileSync(path.join(root, 'deep', 'nested', 'cluster-context-public.yml'), 'x: 1\n')
  assert.match(findClusterContext(root), /deep.nested/)
})

test('findClusterContext returns null when absent, rather than throwing', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'ctx-'))
  assert.equal(findClusterContext(root), null)
})
