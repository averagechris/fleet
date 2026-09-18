# example-cli

This static template is valid for `nix flake init -t ...#rust-cli`, but it
cannot rename files or merge with an existing directory. Prefer the smart
scaffold command when starting real projects:

```sh
nix run --accept-flake-config github:averagechris/fleet#new-project -- --description "A SourceHut CLI"
```

After using the raw template, replace `example-cli` in `Cargo.toml`,
`flake.nix`, and `src/main.rs`, then run:

```sh
cargo generate-lockfile
nix --accept-flake-config flake lock
```

The resulting project receives the approved `srht` CLI through its locked
`fleet` input. Release manifests use `nix run --inputs-from . fleet#srht`; do
not add a separate or floating srht input.
