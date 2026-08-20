import test from 'node:test'
import assert from 'node:assert/strict'
import { CHECKS, computeScorecard, renderScorecardMarkdown, usesLatestTag } from '../lib/scorecard.js'

const workload = (over = {}) => ({
  name: 'api',
  health: { path: '/health', port: 8080 },
  rollbackTargetRetention: { minimumDays: 90, acknowledged: true },
  ...over,
})
const deployment = (workloads = [workload()], over = {}) => ({
  apiVersion: 'deployment.jorisjonkers.dev/v2',
  metadata: { name: 'svc' },
  spec: { schemaVersion: '0.16.0', workloads, ...over },
})
const contract = (over = {}) => ({
  spec: {
    contextRef: 'ghcr.io/x/ctx@sha256:' + 'a'.repeat(64),
    imageDigests: { api: 'ghcr.io/x/api@sha256:' + 'b'.repeat(64) },
    ...over,
  },
})
const rendered = (text = 'kind: Deployment\n') => [{ path: 'out/manifests/production/w.yaml', text }]

const statusOf = (res, check) => res.checks[check].status

test('a clean deployment passes every applicable check', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered() })
  assert.equal(res.overall, 'pass')
  assert.deepEqual(res.failed, [])
  for (const c of CHECKS) assert.notEqual(statusOf(res, c), 'fail', c)
})

test('every check reports one of the three known statuses', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered() })
  for (const c of CHECKS) {
    assert.ok(['pass', 'fail', 'not_applicable'].includes(statusOf(res, c)), `${c} -> ${statusOf(res, c)}`)
  }
})

// The bug this whole package exists to prevent: with nothing rendered, the
// predecessor grepped an empty directory, found no Secret, and reported pass --
// so a run that produced no manifests at all looked ready to deploy.
test('no_raw_secrets is not_applicable when nothing was rendered, never pass', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: [] })
  assert.equal(statusOf(res, 'no_raw_secrets'), 'not_applicable')
  assert.match(res.checks.no_raw_secrets.reason, /nothing was rendered/)
})

test('no_raw_secrets passes on real manifests with no Secret', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered() })
  assert.equal(statusOf(res, 'no_raw_secrets'), 'pass')
})

test('no_raw_secrets fails on a whole-line kind: Secret and names the file', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered('apiVersion: v1\nkind: Secret\n') })
  assert.equal(statusOf(res, 'no_raw_secrets'), 'fail')
  assert.match(res.checks.no_raw_secrets.reason, /out\/manifests\/production\/w\.yaml/)
})

test('no_raw_secrets does not trip on VaultStaticSecret', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered('kind: VaultStaticSecret\n') })
  assert.equal(statusOf(res, 'no_raw_secrets'), 'pass')
})

test('health_declared names every workload missing a path', () => {
  const res = computeScorecard(
    deployment([workload(), workload({ name: 'worker', health: undefined })]),
    contract(),
    { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'health_declared'), 'fail')
  assert.match(res.checks.health_declared.reason, /worker/)
  assert.doesNotMatch(res.checks.health_declared.reason, /\bapi\b/)
})

test('health_declared is not_applicable when there are no workloads', () => {
  const res = computeScorecard(deployment([]), contract(), { renderedManifests: rendered() })
  assert.equal(statusOf(res, 'health_declared'), 'not_applicable')
})

// The predecessor counted `true` across all workloads and took the maximum
// minimumDays, so one compliant workload vouched for the rest.
test('rollback retention is judged per workload, not any-of', () => {
  const res = computeScorecard(
    deployment([workload(), workload({ name: 'worker', rollbackTargetRetention: undefined })]),
    contract(),
    { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'rollback_retention_acknowledged'), 'fail')
  assert.match(res.checks.rollback_retention_acknowledged.reason, /worker/)
})

test('rollback retention fails below the 90-day floor', () => {
  const res = computeScorecard(
    deployment([workload({ rollbackTargetRetention: { minimumDays: 89, acknowledged: true } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'rollback_retention_acknowledged'), 'fail')
  assert.match(res.checks.rollback_retention_acknowledged.reason, /89 days/)
})

test('rollback retention fails when days are met but not acknowledged', () => {
  const res = computeScorecard(
    deployment([workload({ rollbackTargetRetention: { minimumDays: 120, acknowledged: false } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'rollback_retention_acknowledged'), 'fail')
  assert.match(res.checks.rollback_retention_acknowledged.reason, /not acknowledged/)
})

test('routes are not_applicable when none are declared', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered() })
  assert.equal(statusOf(res, 'route_owner_authmode_declared'), 'not_applicable')
})

test('a route inherits authMode from routeDefaults', () => {
  const res = computeScorecard(
    deployment([workload({ routes: [{ host: 'a.example', owner: 'api' }], routeDefaults: { authMode: 'forward-auth' } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'route_owner_authmode_declared'), 'pass')
})

test('a route without owner or any authMode fails and names the host', () => {
  const res = computeScorecard(
    deployment([workload({ routes: [{ host: 'a.example' }] })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'route_owner_authmode_declared'), 'fail')
  assert.match(res.checks.route_owner_authmode_declared.reason, /a\.example/)
  assert.match(res.checks.route_owner_authmode_declared.reason, /owner/)
})

test('context_pinned requires a digest, not a tag', () => {
  const tagged = computeScorecard(deployment(), contract({ contextRef: 'ghcr.io/x/ctx:v1' }), { renderedManifests: rendered() })
  assert.equal(statusOf(tagged, 'context_pinned'), 'fail')
})

test('context_pinned fails rather than throwing when there is no contract', () => {
  const res = computeScorecard(deployment(), null, { renderedManifests: rendered() })
  assert.equal(statusOf(res, 'context_pinned'), 'fail')
  assert.equal(res.overall, 'fail')
})

test('stateful workloads must declare a migrationPolicy', () => {
  const res = computeScorecard(
    deployment([workload({ name: 'db', stateful: true })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'stateful_policy_declared'), 'fail')
  assert.match(res.checks.stateful_policy_declared.reason, /db/)
})

test('rawManifests without a guard report explains that no guard exists', () => {
  const res = computeScorecard(
    deployment([workload({ rawManifests: { enabled: true } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'raw_manifests_guarded'), 'fail')
  assert.match(res.checks.raw_manifests_guarded.reason, /validate-raw-manifests/)
})

// A package from GitHub Packages cannot be audited by npm audit signatures at
// all. Reporting that as a failed verification would block every publish, so
// "could not be evaluated" is a third state.
test('an unauditable registry is not_applicable, not a failure', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered(), provenanceVerified: 'not_applicable' })
  assert.equal(statusOf(res, 'npm_signatures_verified'), 'not_applicable')
  assert.equal(res.overall, 'pass')
})

test('a genuine verification failure is still a failure', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered(), provenanceVerified: false })
  assert.equal(statusOf(res, 'npm_signatures_verified'), 'fail')
  assert.equal(res.overall, 'fail')
})

test('npm_signatures_verified is not_applicable unless provenance was evaluated', () => {
  const preview = computeScorecard(deployment(), contract(), { renderedManifests: rendered() })
  assert.equal(statusOf(preview, 'npm_signatures_verified'), 'not_applicable')
  const publishFail = computeScorecard(deployment(), contract(), { renderedManifests: rendered(), provenanceVerified: false })
  assert.equal(statusOf(publishFail, 'npm_signatures_verified'), 'fail')
  const publishOk = computeScorecard(deployment(), contract(), { renderedManifests: rendered(), provenanceVerified: true })
  assert.equal(statusOf(publishOk, 'npm_signatures_verified'), 'pass')
})

test('schema_pinned fails on a missing or non-numeric version', () => {
  for (const v of [undefined, '', 'latest']) {
    const d = deployment(); d.spec.schemaVersion = v
    assert.equal(statusOf(computeScorecard(d, contract(), { renderedManifests: rendered() }), 'schema_pinned'), 'fail', String(v))
  }
})

test('usesLatestTag matches a whole tag only', () => {
  assert.equal(usesLatestTag('ghcr.io/x/a:latest'), true)
  assert.equal(usesLatestTag('ghcr.io/x/a:latest-alpine'), false)
  assert.equal(usesLatestTag('ghcr.io/x/a:v1'), false)
  assert.equal(usesLatestTag('ghcr.io/x/a@sha256:' + 'c'.repeat(64)), false)
  // a port in the registry host is not a tag
  assert.equal(usesLatestTag('registry:5000/x/a'), false)
  assert.equal(usesLatestTag(undefined), false)
})

test('no_latest_images fails and names the offending image', () => {
  const res = computeScorecard(deployment(), contract({ imageDigests: { api: 'ghcr.io/x/api:latest' } }), { renderedManifests: rendered() })
  assert.equal(statusOf(res, 'no_latest_images'), 'fail')
  assert.match(res.checks.no_latest_images.reason, /api/)
})

test('the markdown table renders one row per check plus a reason column', () => {
  const res = computeScorecard(deployment(), contract(), { renderedManifests: rendered() })
  const md = renderScorecardMarkdown(res, 'svc')
  for (const c of CHECKS) assert.match(md, new RegExp(c))
  assert.match(md, /\| Detail \|/)
})

// A queue consumer has no port to probe. Requiring a path from every workload
// made the check unsatisfiable for knowledge-ingest-worker, which has no ports
// and no probes in its cluster manifest.
test('a workload may be exempted with health.mandatory false', () => {
  const res = computeScorecard(
    deployment([workload(), workload({ name: 'worker', health: { mandatory: false } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'health_declared'), 'pass')
  assert.match(res.checks.health_declared.reason, /worker/)
  assert.match(res.checks.health_declared.reason, /mandatory/)
})

test('an exemption is reported even when every workload is exempt', () => {
  const res = computeScorecard(
    deployment([workload({ name: 'w1', health: { mandatory: false } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'health_declared'), 'pass')
  assert.match(res.checks.health_declared.reason, /w1/)
})

test('omitting health entirely is still a failure, and the message names the escape hatch', () => {
  const res = computeScorecard(
    deployment([workload({ name: 'worker', health: undefined })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'health_declared'), 'fail')
  assert.match(res.checks.health_declared.reason, /worker/)
  assert.match(res.checks.health_declared.reason, /mandatory: false/)
})

test('mandatory false does not excuse a workload that claims a path', () => {
  // A declared path is a promise the probe works; mandatory:false alongside it
  // is contradictory, so the path wins and the workload is not exempt.
  const res = computeScorecard(
    deployment([workload({ name: 'w', health: { mandatory: false, path: '/health', port: 8080 } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'health_declared'), 'pass')
  assert.equal(res.checks.health_declared.reason, undefined)
})

test('a partial health block without a path is not an exemption', () => {
  const res = computeScorecard(
    deployment([workload({ name: 'w', health: { port: 8080 } })]),
    contract(), { renderedManifests: rendered() },
  )
  assert.equal(statusOf(res, 'health_declared'), 'fail')
})
