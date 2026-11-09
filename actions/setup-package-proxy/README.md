# Setup Packages Proxy

A composite action that opts a job in to the packages proxy at
`packages.jorisjonkers.dev`. Without a token it does nothing, so behaviour
stays exactly as it is today.

## Usage

Callers (typically mise or task repositories) add one step:

```yaml
- uses: JorisJonkers-dev/github-workflows/actions/setup-package-proxy@<ref>
  with:
    token: ${{ secrets.PACKAGES_PROXY_TOKEN }}
```

`go` and `python` default to `true`; pass `'false'` to skip the Go or Python
exports.

## What it does

With a token set, the action

1. Masks the token as the very first thing it does.
2. Writes `~/.netrc` (mode 600) with a
   `machine packages.jorisjonkers.dev login packages password <token>` entry.
   Go (via `GOAUTH`'s netrc default), pip and uv all read that file, so the
   token never sits in an environment variable or a URL.
3. Appends, through `$GITHUB_ENV`:
   - `GOPROXY=https://packages.jorisjonkers.dev/go` (no `,direct`: first-party
     modules under `github.com/JorisJonkers-dev/*` stay direct through
     `GOPRIVATE`) and `GOPRIVATE=github.com/JorisJonkers-dev/*`.
   - `PIP_INDEX_URL=https://packages.jorisjonkers.dev/pypi/simple/` and
     `UV_DEFAULT_INDEX=https://packages.jorisjonkers.dev/pypi/simple/`.

## Container builds

The Dockerfile side stays token-free. Pass the netrc as a BuildKit secret and
mount it where the tooling expects it:

```dockerfile
RUN --mount=type=secret,id=packages-netrc,target=/root/.netrc,required=false \
    go mod download
```

Never pass the token as a build arg.
