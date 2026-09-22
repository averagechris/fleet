# Fleet agent guide

This repository owns the reusable release library, operational registry, fleet
audits, project bootstrapper, and templates. The public tracker is
<https://github.com/averagechris/fleet/issues>; project issues use
`repo:<fleet-name>` labels.

Release publication is transitional: artifacts and the website refresh still
use the pinned `srht` input and SourceHut manifests. Do not submit builds,
publish tags, upload artifacts, or publish Pages unless explicitly requested.

Use jj for version control. Before handing off changes, run Python tests,
`nix flake show`, and the smallest relevant `nix flake check` targets. Keep
`lib.fleet.core`, `lib.fleet.presets.rust`, and `lib.mkFleetApps` compatible.

`fleet.toml` is the full operational registry. The website consumes only the
projection emitted by `nix run .#site-registry`; never copy operational fields
into the website.

For every release backend, `release --check` is only a nonmutating ref/version
preflight. It does not run prepared-tree validation or build artifacts. A real
release runs those gates before publication; GitHub CI also rechecks the tag.
