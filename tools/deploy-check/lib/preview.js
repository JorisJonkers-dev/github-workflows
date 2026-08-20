// Render a service repo's deployment and score it. One implementation, shared
// by the deploy-preview action and by developers running the check locally, so
// the two cannot report different answers about the same repository.
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs'
import path from 'node:path'
import YAML from 'yaml'
import { FRAGMENTS, ToolkitError, emitApplyBundle, emitContract, emitKustomizationHealth, renderFragment } from './toolkit.js'
import { computeScorecard } from './scorecard.js'

/** Locate cluster-context-public.yml in a pulled context package. */
export function findClusterContext(root) {
  const hits = []
  const walk = (dir) => {
    let entries
    try { entries = readdirSync(dir, { withFileTypes: true }) } catch { return }
    for (const e of entries) {
      const full = path.join(dir, e.name)
      if (e.isDirectory()) walk(full)
      else if (e.name === 'cluster-context-public.yml') hits.push(full)
    }
  }
  walk(root)
  if (hits.length === 0) return null
  hits.sort()
  // Prefer the canonical context/public/ location when several copies exist.
  return hits.find((h) => h.includes(`${path.sep}context${path.sep}public${path.sep}`)) ?? hits[0]
}

function readYaml(file) {
  return YAML.parse(readFileSync(file, 'utf8'))
}

function collectRendered(dir) {
  if (!existsSync(dir)) return []
  const out = []
  const walk = (d) => {
    for (const e of readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, e.name)
      if (e.isDirectory()) walk(full)
      else if (e.name.endsWith('.yaml') || e.name.endsWith('.yml')) {
        out.push({ path: full, text: readFileSync(full, 'utf8') })
      }
    }
  }
  walk(dir)
  return out
}

/**
 * @param {object} opts
 * @param {string} opts.bin           path to the deploy-config-schema executable
 * @param {string} opts.deployDir     e.g. 'platform'
 * @param {string[]} opts.environments
 * @param {string} opts.images        images.lock.json path
 * @param {string} opts.contextRef    digest-pinned OCI ref (recorded in the contract)
 * @param {string} [opts.contextDir]  local context package directory
 * @param {string} [opts.contextPath] cluster-context-public.yml, when not using contextDir
 * @param {string} opts.artifactName
 * @param {string} opts.outDir
 * @param {boolean} [opts.provenanceVerified]
 * @param {string} [opts.cwd]
 */
export function runPreview(opts) {
  const { bin, deployDir, environments, images, contextRef, artifactName, outDir, cwd, provenanceVerified } = opts
  const deploymentYml = path.join(deployDir, 'deployment.yml')
  const absDeployment = path.isAbsolute(deploymentYml) ? deploymentYml : path.join(cwd ?? process.cwd(), deploymentYml)
  if (!existsSync(absDeployment)) {
    throw new ToolkitError(`E_DEPLOYMENT_MISSING: ${deploymentYml} not found`)
  }

  // --context-dir makes the toolkit look for cluster-context-public.yml
  // directly inside that directory; it does not search. A pulled context
  // package nests the file under context/public/, so passing the package root
  // as --context-dir fails with ENOENT. Resolve to the concrete file and pass
  // it as --context-path instead, which works for either layout.
  let contextPath = opts.contextPath
  if (!contextPath) {
    const root = opts.contextDir
      ? (path.isAbsolute(opts.contextDir) ? opts.contextDir : path.join(cwd ?? process.cwd(), opts.contextDir))
      : null
    if (!root) throw new ToolkitError('supply contextPath, or contextDir to search')
    contextPath = findClusterContext(root)
    if (!contextPath) {
      throw new ToolkitError(`E_CONTEXT_FILE_MISSING: cluster-context-public.yml not found under ${opts.contextDir}`)
    }
  }
  const context = { contextRef, contextPath }

  // A stale tree would let a previous run's manifests satisfy this run's
  // checks, which is the failure mode the whole scorecard exists to catch.
  rmSync(path.join(cwd ?? process.cwd(), outDir), { recursive: true, force: true })

  const renderFailures = []
  for (const env of environments) {
    for (const fragment of FRAGMENTS) {
      const outFile = path.join(outDir, 'manifests', env, `${fragment}.yaml`)
      try {
        renderFragment(bin, { fragment, deployDir, env, images, outFile: path.join(cwd ?? process.cwd(), outFile), context, cwd })
      } catch (err) {
        renderFailures.push({ env, fragment, detail: err.stderr ?? err.message })
      }
    }
    if (renderFailures.length === 0) {
      emitKustomizationHealth(bin, {
        deploymentYml,
        env,
        images,
        outFile: path.join(cwd ?? process.cwd(), outDir, 'metadata', env, 'kustomization-health.yml'),
        cwd,
      })
      // Fragments wrap their payload in a schema document, which kustomize and
      // kubeconform both reject. The apply bundle is the applyable form, and it
      // ships the kustomization.yaml those tools need. Publishing it is opt-out
      // because only artifacts consumed through an OCIRepository need it.
      if (opts.applyBundle !== false) {
        emitApplyBundle(bin, {
          manifestsDir: path.join(outDir, 'manifests', env),
          outDir: path.join(outDir, 'apply', env),
          cwd,
        })
      }
    }
  }

  let contract = null
  let contractError = null
  if (renderFailures.length === 0) {
    try {
      const contractFile = path.join(outDir, 'artifact-contract.yaml')
      emitContract(bin, {
        artifactName,
        environments,
        images,
        contextRef,
        deploymentYml,
        contextFile: contextPath,
        provenanceVerified,
        outFile: path.join(cwd ?? process.cwd(), contractFile),
        outputRoot: outDir,
        cwd,
      })
      contract = readYaml(path.join(cwd ?? process.cwd(), contractFile))
    } catch (err) {
      contractError = err
    }
  }

  const deployment = readYaml(absDeployment)
  const guardFile = path.join(cwd ?? process.cwd(), outDir, 'raw-manifests-guard.json')
  const evidence = {
    renderedManifests: collectRendered(path.join(cwd ?? process.cwd(), outDir, 'manifests')),
    rawManifestsGuard: existsSync(guardFile) ? JSON.parse(readFileSync(guardFile, 'utf8')) : null,
    provenanceVerified,
  }

  const result = computeScorecard(deployment, contract, evidence)
  return { result, renderFailures, contractError, contract, deployment, contextPath, renderedManifests: evidence.renderedManifests }
}
