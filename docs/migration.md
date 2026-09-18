# Migration from the website repository

Fleet management moved here from `averagechris/averagechris.srht.site` at
source commit `19416e3fc0c0415e39104476565ec0c375bce69b`. This is a clean
extraction rather than filtered history.

The GitHub repository was renamed in place from `averagechris/projects`, so its
migrated issues and their SourceHut provenance remain intact. New tracker links
and label automation target <https://github.com/averagechris/fleet/issues>.

Release transport has **not** moved in this extraction. The library continues
to upload tag artifacts through the pinned `srht` CLI and trigger the existing
SourceHut Pages refresh. That provider-specific path is transitional and should
only change in a separately tested migration.
