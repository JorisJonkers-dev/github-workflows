# Setup Packages Proxy

A composite action that routes a job's package downloads through the packages
proxy at `packages.jorisjonkers.dev`: Go, Python, npm/pnpm/yarn and Cargo.

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

Pass `'false'` to skip any of `go`, `python`, `node` or `cargo`.

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
     `UV_DEFAULT_INDEX=https://packages.jorisjonkers.dev/pypi/simple/`.
   - `CARGO_REGISTRIES_JORISJONKERS_PACKAGES_TOKEN=Bearer <token>`, masked.
4. Writes the npm user config, `$HOME/.npmrc` (mode
   600), between `# BEGIN packages-proxy` and `# END packages-proxy` markers.
   The block sets `registry=https://packages.jorisjonkers.dev/npm/` and the
   token for that registry, and keeps `@jorisjonkers-dev` on
   `https://npm.pkg.github.com`. A previous block is replaced on each run.
5. Writes `${CARGO_HOME:-$HOME/.cargo}/config.toml` (mode 600) between the same
   markers. Crates.io is replaced by the `jorisjonkers-packages` sparse
   registry, `sparse+https://packages.jorisjonkers.dev/cargo/`. The token
   travels only in the environment variable above, never in the config file.

Re-runs and token rotations replace the marked blocks instead of stacking them.

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
