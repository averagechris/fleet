# averagechris fleet

This repository owns the release conventions and operational registry for
projects maintained by [averagechris](https://github.com/averagechris), as well
as their shared public issue tracker.

Issues migrated from [`~averagechris/projects` on SourceHut](https://todo.sr.ht/~averagechris/projects) retain their original ticket details and history as provenance in each issue.

Stable Nix interfaces are `lib.fleet.core`, `lib.fleet.presets.rust`, and the
backward-compatible `lib.mkFleetApps`. Fleet operations are exposed as
`fleet-status`, `fleet-tracker-audit`, `new-project`, and `site-registry` apps.

Release artifacts and website refreshes still use SourceHut during the
transition; see [the migration note](docs/migration.md).
