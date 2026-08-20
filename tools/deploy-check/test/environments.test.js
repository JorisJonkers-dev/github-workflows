import test from 'node:test'
import assert from 'node:assert/strict'
import { EnvironmentError, parseEnvironments } from '../lib/environments.js'

test('a single environment parses', () => {
  assert.deepEqual(parseEnvironments('production').environments, ['production'])
})

test('whitespace and carriage returns are stripped', () => {
  // The bash predecessor ran sed 's/\r$//' because a CRLF list reached it in
  // practice; the parser has to survive the same input.
  assert.deepEqual(parseEnvironments(' production , staging \r\n').environments, ['production', 'staging'])
})

test('duplicates are dropped and reported, not silently kept', () => {
  const { environments, duplicates } = parseEnvironments('production,staging,production')
  assert.deepEqual(environments, ['production', 'staging'])
  assert.deepEqual(duplicates, ['production'])
})

test('an invalid name is rejected by name', () => {
  for (const bad of ['Production', '-prod', 'prod_1', 'prod!', '_x']) {
    assert.throws(() => parseEnvironments(bad), /E_INVALID_ENV_NAME/, bad)
  }
})

test('a name may contain digits and internal hyphens', () => {
  assert.deepEqual(parseEnvironments('prod-1,staging2').environments, ['prod-1', 'staging2'])
})

test('an empty or comma-only list is an error, not an empty run', () => {
  for (const empty of ['', '   ', ',,', undefined, null]) {
    assert.throws(() => parseEnvironments(empty), /E_NO_VALID_ENVIRONMENTS/, JSON.stringify(empty))
  }
})

test('the error type is exported so callers can distinguish it', () => {
  try { parseEnvironments('') } catch (err) { assert.ok(err instanceof EnvironmentError) }
})
