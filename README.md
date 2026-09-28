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

## Manual GitHub Release publication

Call the reusable workflow with `publish_release: false` when GitHub Actions
should build but not publish a release. The run verifies the annotated tag,
builds every configured platform, verifies each checksum, and uploads one named
`release-<platform>` Actions artifact per platform. Its final summary reports
**Manual publication required** and links back to the run artifacts and these
instructions.

Download and combine the artifacts from the successful run. Before manually
creating or updating the GitHub Release, verify every `*.sha256` sidecar and
confirm each `release-identity-<platform>` file contains the expected annotated
tag object ID followed by its peeled commit ID. Publish only the tarballs and
checksum sidecars; the identity files are verification evidence, not release
assets. In this mode Actions does not call the Releases API, mint a GitHub App
token, or dispatch the website workflow.

Historical note: runner-built v0.8.2 bytes cannot reproduce the SourceHut
artifact because its build path leaked into the archive. The backfill therefore
copies the exact existing assets in a separate operation. Future releases use
gander's build-path remapping fix.
