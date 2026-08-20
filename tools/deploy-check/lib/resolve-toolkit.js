// Find or install the pinned deploy-config-schema executable.
//
// The toolkit compares its own package version against the cluster context's
// spec.schemaVersion with strict string equality, so the version is not a
// preference -- an off-by-a-patch install fails with
// E_SCHEMA_VERSION_MISMATCH. It is therefore always taken from the caller and
// never defaulted to "latest".
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import os from 'node:os'

const REGISTRY = 'https://npm.pkg.github.com'
const PACKAGE = '@jorisjonkers-dev/deploy-config-schema'

export class ToolkitResolutionError extends Error {}

/**
 * @param {object} opts
 * @param {string} opts.schemaVersion  exact version to install
 * @param {string} [opts.cacheDir]     where to install; defaults under the OS temp dir
 * @param {string} [opts.token]        GitHub token for npm.pkg.github.com
 * @returns {string} path to the deploy-config-schema executable
 */
export function resolveToolkit({ schemaVersion, cacheDir, token }) {
  if (!schemaVersion) throw new ToolkitResolutionError('E_SCHEMA_VERSION_REQUIRED: pass the exact toolkit version')

  const root = cacheDir ?? path.join(os.tmpdir(), `deploy-check-toolkit-${schemaVersion}`)
  const bin = path.join(root, 'node_modules', '.bin', 'deploy-config-schema')
  if (existsSync(bin)) return bin

  mkdirSync(root, { recursive: true })
  const npmrc = path.join(root, '.npmrc')
  const lines = [`@jorisjonkers-dev:registry=${REGISTRY}`]
  const authToken = token ?? process.env.NODE_AUTH_TOKEN ?? process.env.GITHUB_TOKEN
  if (authToken) lines.push(`//npm.pkg.github.com/:_authToken=${authToken}`)
  writeFileSync(npmrc, `${lines.join('\n')}\n`)
  writeFileSync(path.join(root, 'package.json'), JSON.stringify({ name: 'deploy-check-toolkit-cache', private: true }, null, 2))

  const res = spawnSync(
    'npm',
    ['install', '--userconfig', npmrc, '--no-audit', '--no-fund', '--save-exact', `${PACKAGE}@${schemaVersion}`],
    { cwd: root, encoding: 'utf8' },
  )
  if (res.status !== 0) {
    throw new ToolkitResolutionError(
      `E_TOOLKIT_INSTALL_FAILED: could not install ${PACKAGE}@${schemaVersion}\n${res.stderr ?? ''}`,
    )
  }
  if (!existsSync(bin)) {
    throw new ToolkitResolutionError(`E_TOOLKIT_BIN_MISSING: installed ${PACKAGE}@${schemaVersion} but ${bin} is absent`)
  }
  return bin
}

/** Verify the installed toolkit reports the version that was requested. */
export function assertToolkitVersion(bin, expected) {
  const pkg = path.join(bin, '..', '..', '@jorisjonkers-dev', 'deploy-config-schema', 'package.json')
  if (!existsSync(pkg)) return
  const actual = JSON.parse(readFileSync(pkg, 'utf8')).version
  if (actual !== expected) {
    throw new ToolkitResolutionError(`E_SCHEMA_VERSION_MISMATCH: installed ${actual}, expected ${expected}`)
  }
}
