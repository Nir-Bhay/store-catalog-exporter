# Release and maintain this project

## Release checks

1. Run the test suite, offline sample conversion and bundle validation from `CONTRIBUTING.md`.
2. Run `python scripts/check_public_repo.py`. Inspect candidate files and confirm generated exports, caches, raw client data, credentials and local identity paths are absent.
3. Review `git status`, the complete tracked-file list and the staged diff. `.gitignore` does not remove files that are already tracked.
4. Keep commits focused and use a GitHub noreply email if you do not want a personal email in public commit metadata.
5. Push changes to a feature branch and open a pull request when practical. Review the CI run before merging.
6. Keep the repository description, topics, issue templates and security reporting settings current. The live repository uses the name `store-catalog-exporter` and links are set in `pyproject.toml`.

## Discovery and releases

Keep the README's search terms descriptive and factual: Shopify catalog export, products.json pagination, product image URLs, video export, collections and CSV migration. Link the sample command, limitations and machine contract near the beginning. Create releases with changelog notes and test evidence. A PyPI release can be a later step after confirming package-name availability and the release workflow; no PyPI package is claimed to exist today.

Documentation helps discovery and interpretation; it does not guarantee search-engine indexing or agent adoption. Avoid fabricated usage counts, benchmark claims, unsupported “any website” promises or SEO keyword stuffing.
