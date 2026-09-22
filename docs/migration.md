# Migration from the website repository

Fleet management moved here from `averagechris/averagechris.srht.site` at
source commit `19416e3fc0c0415e39104476565ec0c375bce69b`. This is a clean
extraction rather than filtered history.

The GitHub repository was renamed in place from `averagechris/projects`, so its
migrated issues and their SourceHut provenance remain intact. New tracker links
and label automation target <https://github.com/averagechris/fleet/issues>.

SourceHut remains the compatible default. The gander preset explicitly opts in
to the GitHub transport and reusable workflow; other consumers are unchanged.
The workflow drafts until its configured asset set is complete. Website
dispatch requires a repository-scoped GitHub App and otherwise warns so the
hourly/manual backstop remains visible.

Runner-built historical v0.8.2 bytes cannot reproduce the SourceHut artifact
because a build path leaked into it. A separate backfill copies the exact
existing assets; future releases use gander's build-path remapping fix.
