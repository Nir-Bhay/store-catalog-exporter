# Contributing

Use Python 3.10+ and clone the repository. No dependency installation is needed to run the source CLI or test suite.

```sh
python -m unittest discover -s tests -v
python -m catalog_exporter convert examples/sample-feed.json --source https://store.example --media-input examples/sample-media.json --output exports/demo --zip
python -m catalog_exporter validate exports/demo/bundle.json
python scripts/check_public_repo.py
```

Submit a focused pull request with the extraction case, resulting behavior and test evidence. Use synthetic HTML/JSON fixtures. Do not attach a client's catalog, domain, screenshots, personal details, credentials, source photographs or exported artifacts. Minimized fictional reproductions are easier to review and safe to redistribute.

Bug reports should include Python version, adapter, command with the source hostname replaced, sanitized error state and a minimal synthetic fixture. For an incomplete export, state whether the failure occurred during catalog pagination, product media enrichment, collection metadata, membership scanning or downloading.

Changes to canonical fields, CLI exit codes or output filenames must update the schemas, agent contract and documentation. Preserve unknown-field semantics, draft CSV output and original decimal prices. The project currently makes no destination-store writes.
