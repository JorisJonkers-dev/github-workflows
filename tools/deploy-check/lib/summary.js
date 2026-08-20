// The Deploy Preview comment body.
//
// The counts and image list here were previously re-derived with yq and jq in
// the action, separately from the scorecard that sits below them, so the
// header could describe a deployment the scorecard had not scored. Both now
// come from the same parsed documents.
import { renderScorecardMarkdown } from './scorecard.js'

export const COMMENT_MARKER = '<!-- deploy-preview-marker -->'

function countRoutes(deployment) {
  const workloads = deployment?.spec?.workloads ?? []
  return workloads.reduce((n, w) => n + (Array.isArray(w?.routes) ? w.routes.length : 0), 0)
}

function countGatusEndpoints(renderedManifests) {
  const fragment = renderedManifests.find((m) => /gatus-endpoint-fragment\.yaml$/.test(m.path))
  if (!fragment) return 0
  // The fragment lists endpoints under `endpoints:`; count list items rather
  // than counting the file, which is what the predecessor did (always 0 or 1).
  const matches = fragment.text.match(/^\s*-\s+name:/gm)
  return matches ? matches.length : 0
}

export function renderPreviewSummary({ deployment, contract, result, contextRef, environments, images, renderedManifests = [], renderFailures = [] }) {
  const name = deployment?.metadata?.name ?? 'unknown'
  const workloads = deployment?.spec?.workloads ?? []
  const lines = [
    COMMENT_MARKER,
    `## Deploy Preview — ${name}`,
    '',
    `**Environments:** ${environments.join(', ')}`,
    `**Context ref:** \`${contextRef}\``,
    `**Workloads:** ${workloads.length} | **Routes:** ${countRoutes(deployment)} | **Gatus endpoints:** ${countGatusEndpoints(renderedManifests)}`,
    '',
  ]

  if (renderFailures.length > 0) {
    lines.push('### ⚠️ Renders failed', '', 'No manifests were produced, so checks that inspect rendered output report `not_applicable` rather than `pass`.', '')
    for (const f of renderFailures) lines.push(`- \`${f.env}/${f.fragment}\`: ${String(f.detail).split('\n')[0]}`)
    lines.push('')
  }

  const digests = contract?.spec?.imageDigests ?? images ?? {}
  if (Object.keys(digests).length > 0) {
    lines.push('### Image refs', '')
    for (const [key, value] of Object.entries(digests)) lines.push(`  - \`${key}\`: \`${value}\``)
    lines.push('')
  }

  lines.push(renderScorecardMarkdown(result))

  const notApplicable = Object.entries(result.checks).filter(([, v]) => v.status === 'not_applicable')
  if (notApplicable.length > 0) {
    lines.push('', `> ➖ ${notApplicable.length} check(s) not applicable: ${notApplicable.map(([k]) => k).join(', ')}`)
  }
  lines.push('', '---', '_Updated by deploy-check on push to this PR._')
  return lines.join('\n')
}
