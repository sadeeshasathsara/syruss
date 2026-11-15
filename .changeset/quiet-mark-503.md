---
www: patch
---

- **The app shows which version it is, under the sidebar** ("Syruss 0.9.0"). The release job gives each build its version from the changesets, so production shows its own release, the test environment its test release and dev its dev release (for example `0.16.0-dev.1`). Only the app's own number is shown: no list of services. A build made by hand shows nothing.
