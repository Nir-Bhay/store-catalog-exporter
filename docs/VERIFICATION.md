# Local release verification

Checked 2026-10-09 with Python 3.12 on Windows.

- 30 automated tests passed, including a complete public-feed-shaped HTTP pipeline against an ephemeral localhost fixture server.
- The documented fictional offline example produced 2 products, 3 variants, 3 images and 1 product with video. Its draft CSV passed local bundle consistency validation.
- A wheel built successfully with pip's isolated build environment. It installed into a separate target directory with no runtime dependencies. An isolated Python process imported that installed package and completed the offline CLI example.
- A separate private local regression snapshot was exercised without copying source data or source identities into this repository.
- The repository privacy check passed. Generated exports, downloaded media, caches, local installation smoke artifacts and package build directories are excluded from the public source artifact.

GitHub Actions passed on the initial public commit and the follow-up security documentation commit: all six Windows/Linux and Python 3.10/3.12/3.13 matrix jobs succeeded. These jobs use fictional offline fixtures. This is not a universal live-site compatibility test, destination-store import test or production checkout certification.

Repeat the commands in `CONTRIBUTING.md` after changes. A passing local check does not replace reviewing source coverage, destination currency, media access, collection dependencies and ownership of content before an actual migration.
