// The SC-11 readiness scorecard.
//
// This existed twice in bash -- once in actions/deploy-preview/run.sh and once
// in a per-repo platform/render-local.sh -- and the two drifted. The CI copy
// read deployment.yml with yq and the artifact contract for image and context
// facts; the local copy re-derived the same answers by grepping raw text, so
// the two disagreed about the same repository. Both are replaced by this one
// pure function.
//
// Every check returns exactly one of:
//   'pass'            the check ran and the condition holds
//   'fail'            the check ran and the condition does not hold
//   'not_applicable'  the check does not apply, or nothing was produced to
//                     inspect. Never report 'pass' for an absent input: an
//                     empty manifest tree is an absence of evidence, not
//                     evidence that no secret would have been rendered.
//
// Failures carry a reason so a red check names the workload or file at fault
// instead of leaving the reader to re-derive it.

export const CHECKS = [
  'schema_pinned',
  'context_pinned',
  'no_latest_images',
  'health_declared',
  'route_owner_authmode_declared',
  'rollback_retention_acknowledged',
  'no_raw_secrets',
  'stateful_policy_declared',
  'raw_manifests_guarded',
  'npm_signatures_verified',
]

const MINIMUM_ROLLBACK_DAYS = 90

const pass = () => ({ status: 'pass' })
const na = (reason) => ({ status: 'not_applicable', reason })
const fail = (reason) => ({ status: 'fail', reason })

function workloadsOf(deployment) {
  const w = deployment?.spec?.workloads
  return Array.isArray(w) ? w : []
}

function routesOf(workload) {
  const r = workload?.routes
  return Array.isArray(r) ? r : []
}

/** A tag is :latest only as a whole tag -- :latest-alpine is a different tag. */
export function usesLatestTag(ref) {
  if (typeof ref !== 'string') return false
  const withoutDigest = ref.split('@')[0]
  const lastColon = withoutDigest.lastIndexOf(':')
  if (lastColon === -1) return false
  // A colon inside the registry host:port is not a tag separator.
  if (withoutDigest.slice(lastColon).includes('/')) return false
  return withoutDigest.slice(lastColon + 1) === 'latest'
}

const rules = {
  schema_pinned(deployment) {
    const v = deployment?.spec?.schemaVersion
    return typeof v === 'string' && /^\d/.test(v)
      ? pass()
      : fail(`spec.schemaVersion is ${JSON.stringify(v ?? null)}; expected a version string`)
  },

  context_pinned(_deployment, contract) {
    const ref = contract?.spec?.contextRef
    if (typeof ref !== 'string' || ref === '') {
      return fail('artifact contract has no spec.contextRef')
    }
    return ref.includes('@sha256:')
      ? pass()
      : fail(`contextRef is not digest-pinned: ${ref}`)
  },

  no_latest_images(_deployment, contract) {
    const digests = contract?.spec?.imageDigests
    if (!digests || typeof digests !== 'object') {
      return na('artifact contract declares no imageDigests')
    }
    const offenders = Object.entries(digests)
      .filter(([, ref]) => usesLatestTag(ref))
      .map(([name]) => name)
    return offenders.length === 0
      ? pass()
      : fail(`floating :latest tag on ${offenders.join(', ')}`)
  },

  // Per workload, not any-of. The bash predecessor accepted a single
  // acknowledged workload on behalf of all of them for rollback retention;
  // health was already per-workload. Verified a no-op against every current
  // deployment.yml before tightening.
  // A queue consumer has no HTTP surface to probe, so demanding a health path
  // from every workload cannot be satisfied. `health: { mandatory: false }` is
  // an explicit, reviewable opt-out: the workload still has to say something
  // about its health, and the exemption shows up in the reason rather than
  // disappearing. Omitting `health` entirely is still a failure -- silence is
  // not a decision.
  health_declared(deployment) {
    const workloads = workloadsOf(deployment)
    if (workloads.length === 0) return na('deployment declares no workloads')
    const exempt = []
    const missing = []
    for (const w of workloads) {
      const name = w?.name ?? '<unnamed>'
      const health = w?.health
      if (health && health.mandatory === false && !health.path) {
        exempt.push(name)
      } else if (!health?.path) {
        missing.push(name)
      }
    }
    if (missing.length > 0) {
      return fail(
        `no health.path on ${missing.join(', ')}` +
          ' (declare health.mandatory: false to exempt a workload with no HTTP surface)',
      )
    }
    return exempt.length > 0
      ? { status: 'pass', reason: `exempt by health.mandatory: false: ${exempt.join(', ')}` }
      : pass()
  },

  route_owner_authmode_declared(deployment) {
    const workloads = workloadsOf(deployment)
    const pairs = workloads.flatMap((w) => routesOf(w).map((route) => ({ w, route })))
    if (pairs.length === 0) return na('deployment declares no routes')
    const problems = []
    for (const { w, route } of pairs) {
      const host = route?.host ?? '<no host>'
      if (!route?.owner) problems.push(`${host} has no owner`)
      const authMode = route?.authMode ?? w?.routeDefaults?.authMode
      if (!authMode) problems.push(`${host} has no authMode and the workload sets no routeDefaults.authMode`)
    }
    return problems.length === 0 ? pass() : fail(problems.join('; '))
  },

  rollback_retention_acknowledged(deployment) {
    const workloads = workloadsOf(deployment)
    if (workloads.length === 0) return na('deployment declares no workloads')
    const problems = []
    for (const w of workloads) {
      const name = w?.name ?? '<unnamed>'
      const retention = w?.rollbackTargetRetention
      if (!retention) {
        problems.push(`${name} declares no rollbackTargetRetention`)
        continue
      }
      if (retention.acknowledged !== true) problems.push(`${name} is not acknowledged`)
      const days = Number(retention.minimumDays ?? 0)
      if (!Number.isFinite(days) || days < MINIMUM_ROLLBACK_DAYS) {
        problems.push(`${name} keeps ${retention.minimumDays ?? 0} days, below ${MINIMUM_ROLLBACK_DAYS}`)
      }
    }
    return problems.length === 0 ? pass() : fail(problems.join('; '))
  },

  no_raw_secrets(_deployment, _contract, evidence) {
    const rendered = evidence?.renderedManifests ?? []
    if (rendered.length === 0) {
      return na('nothing was rendered, so no manifest was inspected')
    }
    const offenders = rendered.filter((m) => /^kind:[ \t]*Secret[ \t]*$/m.test(m.text ?? ''))
    return offenders.length === 0
      ? pass()
      : fail(`kind: Secret in ${offenders.map((m) => m.path).join(', ')}`)
  },

  stateful_policy_declared(deployment) {
    const stateful = workloadsOf(deployment).filter((w) => w?.stateful === true)
    if (stateful.length === 0) return na('deployment declares no stateful workloads')
    const missing = stateful.filter((w) => !w?.migrationPolicy).map((w) => w?.name ?? '<unnamed>')
    return missing.length === 0 ? pass() : fail(`no migrationPolicy on ${missing.join(', ')}`)
  },

  raw_manifests_guarded(deployment, _contract, evidence) {
    const enabled = workloadsOf(deployment).filter((w) => w?.rawManifests?.enabled === true)
    if (enabled.length === 0) return na('no workload enables rawManifests')
    // The toolkit publishes no artifact validate-raw-manifests subcommand, so
    // there is no guard to consult. Saying so beats an unexplained fail.
    if (!evidence?.rawManifestsGuard) {
      return fail(
        'rawManifests is enabled but no guard report exists: the toolkit publishes no ' +
          'artifact validate-raw-manifests subcommand, so forbidden kinds cannot be checked',
      )
    }
    const violations = evidence.rawManifestsGuard.violations ?? []
    return violations.length === 0 ? pass() : fail(`${violations.length} forbidden-kind violation(s)`)
  },

  npm_signatures_verified(_deployment, _contract, evidence) {
    if (evidence?.provenanceVerified === undefined) {
      return na('provenance is only verified when publishing an artifact')
    }
    return evidence.provenanceVerified ? pass() : fail('npm audit signatures did not verify the package')
  },
}

/**
 * Compute the scorecard. Pure: all filesystem reads happen in the caller and
 * arrive as `evidence`.
 *
 * @param {object}   deployment  parsed platform/deployment.yml
 * @param {object}   contract    parsed artifact-contract.yaml (may be null)
 * @param {object}   evidence    { renderedManifests: [{path,text}],
 *                                 rawManifestsGuard, provenanceVerified }
 * @returns {{checks: Record<string,{status:string,reason?:string}>, overall: string}}
 */
export function computeScorecard(deployment, contract, evidence = {}) {
  const checks = {}
  for (const name of CHECKS) {
    checks[name] = rules[name](deployment, contract, evidence)
  }
  const failed = CHECKS.filter((n) => checks[n].status === 'fail')
  return { checks, overall: failed.length === 0 ? 'pass' : 'fail', failed }
}

/** Markdown table for the sticky PR comment and for terminal output. */
export function renderScorecardMarkdown(result, title) {
  const icon = { pass: '✅', fail: '❌', not_applicable: '➖' }
  const lines = [`### SC-11 Readiness Scorecard${title ? ` — ${title}` : ''}`, '', '| | Check | Status | Detail |', '|---|---|---|---|']
  for (const name of CHECKS) {
    const { status, reason } = result.checks[name]
    lines.push(`| ${icon[status]} | ${name} | ${status} | ${reason ?? ''} |`)
  }
  lines.push('', '`pass` = ready · `fail` = blocks deployment · `not_applicable` = check does not apply')
  return lines.join('\n')
}
