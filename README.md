# averagechris fleet

This repository owns the release conventions and operational registry for
projects maintained by [averagechris](https://github.com/averagechris), as well
as their shared public issue tracker.

Issues migrated from [`~averagechris/projects` on SourceHut](https://todo.sr.ht/~averagechris/projects) retain their original ticket details and history as provenance in each issue.

Stable Nix interfaces are `lib.fleet.core`, `lib.fleet.presets.rust`, and the
backward-compatible `lib.mkFleetApps`. Fleet operations are exposed as
`fleet-status`, `fleet-tracker-audit`, `new-project`, and `site-registry` apps.

SourceHut remains the default release transport. `fleet.presets.gander` is the
explicit GitHub pilot; its local release app atomically publishes the prepared
main commit and annotated tag, while `.github/workflows/release.yml` builds all
configured platforms and keeps the GitHub Release drafted until every tarball
and checksum is present and verified.

Historical note: runner-built v0.8.2 bytes cannot reproduce the SourceHut
artifact because its build path leaked into the archive. The backfill therefore
copies the exact existing assets in a separate operation. Future releases use
gander's build-path remapping fix.
