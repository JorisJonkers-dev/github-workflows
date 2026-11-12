# Setup Packages Proxy

A composite action that routes a job's package downloads through the packages
proxy at `packages.jorisjonkers.dev`: Go, Python, npm/pnpm/yarn, Cargo and
Gradle.

## Usage

Callers (typically mise or task repositories) add one step:

```yaml
- uses: JorisJonkers-dev/github-workflows/actions/setup-package-proxy@<ref>
  with:
    token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
```

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `token` | `''` | The proxy token, typically `secrets.PACKAGES_PROXY_TOKEN`. |
| `require-package-proxy` | `'false'` | `'true'` fails the job when the token is empty. Otherwise an empty token only warns. |
| `go` | `'true'` | Export `GOPROXY` and `GOPRIVATE`. |
| `python` | `'true'` | Export `PIP_INDEX_URL` and `UV_DEFAULT_INDEX`. |
| `node` | `'true'` | Write the npm user config (see below). |
| `cargo` | `'true'` | Write the Cargo config and export its token (see below). |
| `gradle` | `'true'` | Write retry and timeout properties to the Gradle user home (see below). |

Pass `'false'` to skip any of `go`, `python`, `node`, `cargo` or `gradle`.

## Empty token

With no token the action writes nothing and warns, so the job installs
packages directly, as before the proxy existed. A pull request from a fork
never receives secrets, so it always gets the warning and no failure. Set
`require-package-proxy: 'true'` to make a missing token fail the job.

## What it does

With a token set, the action

1. Masks the token before anything else, and the `Bearer` form used for Cargo.
2. Writes `~/.netrc` (mode 600) with a
   `machine packages.jorisjonkers.dev login packages password <token>` entry.
   Go, pip and uv all read that file, so the token never sits in an
   environment variable or a URL.
3. Appends, through `$GITHUB_ENV`:
   - `GOPROXY=https://packages.jorisjonkers.dev/go` (no `,direct`: first-party
     modules under `github.com/JorisJonkers-dev/*` stay direct through
     `GOPRIVATE`) and `GOPRIVATE=github.com/JorisJonkers-dev/*`.
   - `PIP_INDEX_URL=https://packages.jorisjonkers.dev/pypi/simple/` and
     `UV_DEFAULT_INDEX=https://packages.jorisjonkers.dev/pypi/simple/`, plus
     `PIP_RETRIES=9`, `UV_HTTP_RETRIES=8` and `UV_HTTP_TIMEOUT=60`.
   - `CARGO_REGISTRIES_JORISJONKERS_PACKAGES_TOKEN=Bearer <token>`, masked.
4. Writes the npm user config, `$HOME/.npmrc` (mode
   600), between `# BEGIN packages-proxy` and `# END packages-proxy` markers.
   The block sets `registry=https://packages.jorisjonkers.dev/npm/` and the
   token for that registry, keeps `@jorisjonkers-dev` on
   `https://npm.pkg.github.com`, and sets `fetch-retries=9`,
   `fetch-retry-factor=2`, `fetch-retry-mintimeout=2000` and
   `fetch-retry-maxtimeout=30000`. A previous block is replaced on each run.
5. Writes `${CARGO_HOME:-$HOME/.cargo}/config.toml` (mode 600) between the same
   markers. Crates.io is replaced by the `jorisjonkers-packages` sparse
   registry, `sparse+https://packages.jorisjonkers.dev/cargo/`. The token
   travels only in the environment variable above, never in the config file.
6. Writes `${GRADLE_USER_HOME:-$HOME/.gradle}/gradle.properties` between the
   same markers: 60s connection and socket timeouts, 8 repository tentatives
   with a 1s doubling backoff, and 8 network retries with the same backoff.
   Everything else in the file is preserved.
7. Puts `bin/proxy-retry` on `$GITHUB_PATH` for Go, which has no retry of its
   own.

Re-runs and token rotations replace the marked blocks instead of stacking them.

## Retries through a proxy restart

The proxy can be unreachable for a minute or two: connection refused, resets,
a certificate that does not validate yet, or a 404 while its routes load. Each
manager is given a bounded retry budget instead of failing the job on the first
refused connection.

| Manager | Settings | Rides out |
| --- | --- | --- |
| npm, pnpm | `fetch-retries=9`, factor 2, 2s to 30s | up to ~180s (delays are randomised, about half expected) |
| pip | `PIP_RETRIES=9` | ~127s |
| uv | `UV_HTTP_RETRIES=8`, `UV_HTTP_TIMEOUT=60` | 8 retries, 60s per request |
| Gradle | 8 tentatives, 1s doubling backoff, 60s timeouts | ~127s |
| Go | `proxy-retry go mod download` | ~91s |

Every budget is bounded: a manager gives up and the job fails rather than
retrying forever. npm, pnpm, pip and Gradle do not retry a real 404, so a 404
while the proxy's routes load still fails the step; re-run the job for that.
Go goes through `proxy-retry`, which retries any non-zero exit, so it rides out
a 404 too:

```yaml
- run: proxy-retry go mod download
```

`proxy-retry <command> [args...]` runs the command up to `PROXY_RETRY_ATTEMPTS`
times in total (default 8), sleeping `PROXY_RETRY_INITIAL_DELAY` seconds
(default 1) and doubling, capped at `PROXY_RETRY_MAX_DELAY` (default 30). It
prints each retry to stderr and returns the command's last exit code.

Sources: [npm config](https://docs.npmjs.com/cli/v11/using-npm/config),
[pnpm settings](https://pnpm.io/settings),
[uv environment](https://docs.astral.sh/uv/reference/environment/),
[pip](https://pip.pypa.io/en/stable/cli/pip/), and Gradle's
`ErrorHandlingModuleComponentRepository.java`,
`NetworkOperationBackOffAndRetry.java` and
`JavaSystemPropertiesHttpTimeoutSettings.java` for the property names.

## Container builds

The Dockerfile side stays token-free. Pass the netrc as a BuildKit secret and
mount it where the tooling expects it:

```dockerfile
ARG GOPROXY
RUN --mount=type=secret,id=packages-netrc,target=/root/.netrc,required=false \
    go mod download
```

Declare the index build args a Dockerfile's installers need (`GOPROXY`,
`PIP_INDEX_URL`, `UV_DEFAULT_INDEX`). Never pass the token itself as a build
arg.
