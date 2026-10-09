# Field coverage and source semantics

Canonical schema: `schemas/bundle.schema.json`. Fields can be selected for `products.csv` using dot paths; collections, variants and media remain separate relational tables.

| Data | Shopify source | Handling |
| --- | --- | --- |
| Handle, title, HTML description, vendor, type, tags | Public `products.json` | Preserved; canonical HTML cleaned; raw response retained |
| Variants, options, SKU, barcode | Public feed when exposed | Every returned variant exported; missing values remain blank |
| Price, compare-at price | Catalog feed | Decimal major-unit strings; no currency conversion |
| Ajax price | Public product `.js` | Not merged into prices; this endpoint uses integer minor units |
| Still-image URLs and order | Catalog feed | One source-image record per returned image; separate image CSV rows |
| Variant image association | Variant image/ID | Mapped where present; unknown association is not guessed |
| Video, external video, 3D sources | Product `.js` media | Preserved in media manifest; not imported by native product CSV |
| Video duration | Product `.js` | Milliseconds as supplied; unknown if not exposed |
| Video quality | Exposed sources | All retained; highest exposed MP4 height chosen for optional download |
| Collections and memberships | Public collection feeds | Paginated separately; many-to-many CSV; source scan status retained |
| Weight | Public variant grams | Positive grams enter draft CSV; zero/missing stays blank and is flagged |
| Availability | Public flag | Retained independently from inventory; quantities always unknown |
| Currency | Often absent from Shopify feed | Unknown; confirm source and destination store currency manually |
| Private metafields, cost, stock locations, draft products | Not public | Not recovered or fabricated |

## Native Shopify CSV choices

Modern column names are used. Shopify documents compatibility with older header names; this exporter does not need a legacy-header mode. Reference: [Shopify product CSV fields](https://help.shopify.com/en/manual/products/import-export/using-csv), checked 2026-10-09.

- Every product starts with a title row; every variant carries its option names and values.
- Additional still images use image-only rows with the same handle. They do not create variants.
- `Status=draft` and `Published on online store=false` apply to the product's first row.
- Missing/invalid prices or ambiguous option combinations withhold the native CSV. The canonical bundle and review CSVs remain available.
- Inventory quantity/tracker columns are omitted because public availability is not reliable inventory data. New draft products still need the destination's inventory setup.
- `manual` fulfillment and `deny` overselling are explicit draft-import defaults, not recovered source settings. Tax/shipping flags are copied when available; blank values use Shopify's import defaults and must be reviewed.
- Collection assignments are not squeezed into a single native `Collection` column. Preserve the separate relationship file and apply it during a dedicated import step.
- Image alt text falls back to the product title only in the native CSV if the source alt is absent. The canonical image field keeps the absence visible.
- The Shopify import CSV contains machine data, so it does not add spreadsheet-formula escaping to identifiers or titles. Human-review CSVs do. Use a safe text viewer for untrusted import files.

## Coverage states

`coverage.catalog.status`: `exhausted`, `incomplete`, `offline_snapshot` or `structured_data_only`. Exhausted means only that the requested public feed produced an empty final page. Repeated pages, network errors and maximum-page limits are explicit incomplete reasons. Source changes during a run can still make public pagination inconsistent; this is not a transactional backup.

`media_scan_status`: `ok`, `failed`, `not_requested`, `structured_data_only`. A successful media response with zero videos is different from a failed request. JSON-LD reports only structured-data coverage; it does not scan the full rendered gallery.

Saved snapshots from different runs can disagree. Compare capture time and scan scope before combining them; a video-only snapshot and a full-gallery snapshot are not interchangeable evidence of complete media ordering. Cached responses can also come from earlier request times, so use a fresh output or bypass cache when a contemporaneous review is needed.

Network exports include `raw/request_metadata.json` with response fetch timestamps and cache-hit flags. The bundle's `created_at` is its serialization time, not a claim that every source response was fetched then. Offline feed snapshots without request metadata have unknown capture times.

Collection records retain `metadata_status`, `membership_status` and observed `source_members`. The selected-product membership CSV may be smaller than `source_members` when handle filters or a sample limit are used.

The Ajax Product API has a documented 250-variant response limit. The exporter preserves the catalog-feed variants and records a warning at that boundary during media scanning. The public catalog feed may also omit data without announcing it. High-variant catalogs need an authorized Admin export/API workflow to prove completeness. References: [Ajax API limitations](https://shopify.dev/docs/api/ajax), [Product API](https://shopify.dev/docs/api/ajax/reference/product).
