# example-cli project guidance

Use `jj` for version-control actions in this repository.

## Development

- Enter the toolchain with `direnv allow` or `nix develop`.
- Nix formatting uses wrapped `alejandra -q`; run `nix fmt` or `nix fmt -- --check .`.
- Prefer local checks: `nix run .#static-checks` (fmt + clippy), `nix run .#ci-test`, `nix run .#ci-machete`, `nix run .#ci-sort`, `nix run .#ci-deny`, `nix run .#ci-audit`.
- `.builds/ci.yml` runs fmt, clippy, test, and the package build on every push.

## sccache

The host sets `RUSTC_WRAPPER=sccache` globally, and it must never be unset to "fix"
build failures. If builds fail with sccache connection or compiler errors, run
`sccache --stop-server` and retry; the supervised launchd agent restarts a healthy
server.

## Release workflow

This repo uses the standard averagechris fleet interface:

```sh
nix run .#static-checks
nix run .#release -- --version X.Y.Z --check
nix run .#release -- --version X.Y.Z --submit-linux-build
```

`release --check` is a nonmutating ref/version preflight only; it does not run
prepared-tree validation or build artifacts. The real release runs those gates
before atomic publication, and hosted CI rechecks the published ref.

The release command performs preparation, prepared-tree validation, artifact
verification, atomic ref publication, upload, and refresh in that order. Do not
run the lower-level release helpers as a routine release workflow.
After a post-publication failure, rerun the same command: exact matching release
state resumes idempotently, while mismatched tags or refs fail closed.

`.builds/ci.yml` runs automatically on every push. `builds/release-linux-x86_64.yml`
is explicit-submit only; do not move it to `.builds/`.
The flake passes `fleet.packages.${system}.srht` to the release preset, and the
static manifest runs `nix run --inputs-from . fleet#srht`. Keep that approved,
fleet-locked channel; never replace it with a floating srht URL.
