// Environment list parsing.
//
// The bash callers each did this themselves: strip CR, trim, validate the name,
// drop duplicates. Two implementations of the same parsing is how a caller ends
// up rendering an environment the other would have rejected.
export class EnvironmentError extends Error {}

const VALID = /^[a-z0-9][a-z0-9-]*$/

/**
 * @param {string} raw comma-separated list
 * @returns {{environments: string[], duplicates: string[]}}
 */
export function parseEnvironments(raw) {
  const seen = new Set()
  const environments = []
  const duplicates = []
  for (const part of String(raw ?? '').replace(/\r/g, '').split(',')) {
    const env = part.trim()
    if (env === '') continue
    if (!VALID.test(env)) {
      throw new EnvironmentError(`E_INVALID_ENV_NAME: '${env}' must match ${VALID}`)
    }
    if (seen.has(env)) { duplicates.push(env); continue }
    seen.add(env)
    environments.push(env)
  }
  if (environments.length === 0) {
    throw new EnvironmentError('E_NO_VALID_ENVIRONMENTS: the environment list is empty')
  }
  return { environments, duplicates }
}
