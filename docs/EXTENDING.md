# Architecture and custom adapters

The project separates extraction, normalization and serialization:

```text
public feed / public HTML
        |
        v
HttpClient (robots, limits, retries, cache)
        |
        v
adapter ---> raw responses
        |
        v
canonical products + collections + coverage + failures
        |
        v
CSV tables + draft Shopify CSV + bundle + report
```

- `http.py`: GET-only network transport; no credentials, private-network access, browser login or anti-bot bypass.
- `adapters.py`: pagination, Shopify endpoint strategy, bounded sitemap discovery and JSON-LD parsing.
- `model.py`: canonical mapping, description cleaning and separate media enrichment.
- `export.py`: table output, draft import dependency validation and local bundle checks.
- `downloads.py`: explicitly requested public image/MP4 archive with a resume ledger.
- `cli.py`: options, source/output guard, offline conversion, JSON results and optional ZIP.

## Add an adapter

Implement a function with the same return shape as `jsonld_export`: `products`, `collections`, `memberships`, `raw`, `coverage` and `errors`. Normalize products into the documented bundle schema. Record exact source price units, unknown fields and pagination termination. Add the adapter as an explicit CLI choice; automatic guessing can mistake a login page for an empty catalog.

Add synthetic tests for duplicate pages, price units, missing prices, missing media, option uniqueness and pagination limits. Unsupported fields should stay in raw responses if available, and should not silently become invented canonical fields. Extend the schema, field selection roots and documentation together when exposing a new canonical field.

This initial version deliberately has no dynamic plugin loader. Agents can inspect and modify the adapter boundary in a checkout; the CLI does not download or execute a third-party parser. HTML selectors, browser-rendered extraction, ProductGroup variants and authorized Storefront/Admin APIs are possible future adapters, not implemented capabilities.
