# Security and data handling

The CLI performs public HTTP(S) GET requests. It does not accept store passwords, Admin API tokens, browser cookies or checkout credentials. Private/loopback/reserved network addresses are blocked in the public CLI, including redirect destinations. The internal `allow_private` constructor option exists for offline localhost transport tests and is not a CLI feature.

Use this as a local developer tool, not an internet-facing URL-fetch service. DNS checks do not pin resolved addresses; they are not a complete defense against DNS rebinding in a hostile server deployment.

robots.txt is checked per origin. Denied/unavailable robots responses stop access; missing robots.txt (404/410) is treated as no published rule. Rate limits, timeouts, response-size limits and retry bounds are enforced. Access-control and anti-bot bypass are outside the project's scope.

Scraped HTML, JSON-LD and CSV text are untrusted. Canonical descriptions remove script/style/embed blocks and event attributes. Raw snapshots preserve original source responses. Human-review CSV cells are protected against common spreadsheet formula markers, while the native machine-import CSV preserves exact source text for identifiers. Use a safe viewer or disable formula execution when inspecting untrusted machine-import data.

Exported media links can include source-specific URL parameters. Exports, caches and logs are Git-ignored but can contain source content; inspect them before sharing. No generated export should be committed to the public repository. The privacy checker is a release aid, not a complete secrets or personal-data detector.

For a vulnerability, use GitHub's **Report a vulnerability** option in the repository's Security tab. Private vulnerability reporting is enabled. Do not post credentials, exploit payloads involving a real store, or private exports in a public issue.
