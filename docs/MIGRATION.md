# Export-to-import migration guide

The exporter writes local artifacts. It never deletes a destination catalog, imports to a store, changes payments, edits a theme or publishes products.

## Before importing

Back up the destination catalog and theme independently. Confirm ownership or reuse rights for source content. Run a sample export and check its prices, currency, options, media links and branding. Review source-provided weights if shipping rates depend on them; unknown weights cannot be calculated from selling price.

Read `report.json` and `errors.csv`. If public data is incomplete, decide whether a public export meets the task or an authorized Admin export is required. Do not infer success from a CSV being present. Keep the source media URLs reachable until Shopify has processed the imports.

## Draft product import

Import `shopify_products.csv` through Shopify's native Products import workflow into a test or reviewed destination. Start with a small sample. Preserve drafts, review import errors, and verify variant options and prices. Handle collisions matter: overwriting matching products can clear existing columns or recreate variant IDs. Use the file for creating reviewed drafts rather than blindly overwriting a live catalog.

Shopify fetches publicly reachable image URLs and hosts imported images. It does not read image files from your laptop through CSV. Confirm processing and image counts on the destination before retiring source URLs. A successful browser page load is not a guarantee that Shopify can retrieve the file.

## Videos and collections

Attach product videos through Shopify Admin or a separately authorized media-import script. Map the **destination product ID by handle**, then use the video manifest's preferred exposed MP4 or an approved external-video URL. Staged upload flows may be required for files. Wait until destination media are processed/ready. The manifest preserves original mixed-gallery positions, but native image CSV order alone cannot recreate mixed image/video ordering.

Create or reconcile collections from `collections.csv`. Apply all relationships from `collection_memberships.csv`; one product can belong to several collections. Public membership does not recover an automated collection's original rule conditions, so a manual collection recreation and an automated rule recreation are different tasks. Preserve existing destination collections until the replacement and its theme dependencies are reviewed.

## Storefront checks before publication

Imported products alone do not populate a homepage that references an old or empty collection. Reconnect featured collections, Shop links, category links, product links, search and filtering to the actual destination catalog. Confirm images are ready, variants select correctly and cart prices match. Keep theme redesign and payment configuration outside the product export workflow.

Only after destination content, inventory/shipping requirements, collection bindings and checkout behavior are reviewed should the store owner publish. Test payment mode and live payment mode are separate operational decisions; this exporter makes neither decision.

Official references: [Product CSV import](https://help.shopify.com/en/manual/products/import-export/import-products), [CSV columns and images](https://help.shopify.com/en/manual/products/import-export/using-csv), [Shopify product media](https://help.shopify.com/en/manual/products/product-media).
