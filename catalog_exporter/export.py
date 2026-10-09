"""Bundle serialization, Shopify draft CSV and independent validation."""

import csv
from decimal import Decimal
from pathlib import Path

from . import __version__
from .util import ExportError, amount, path_value, read_json, utc_now, write_csv, write_json

DEFAULT_FIELDS = ["handle", "title", "url", "vendor", "product_type", "description_html", "tags",
                  "currency", "options", "variants", "images", "media", "collection_handles", "media_scan_status"]
SHOPIFY_HEADERS = ["URL handle", "Title", "Description", "Vendor", "Type", "Tags",
                   "Published on online store", "Status", "Option1 name", "Option1 value",
                   "Option2 name", "Option2 value", "Option3 name", "Option3 value", "SKU", "Barcodes",
                   "Price", "Compare-at price", "Charge tax", "Requires shipping", "Fulfillment service",
                   "Continue selling when out of stock", "Weight value (grams)", "Weight unit for display",
                   "Product image URL", "Image position", "Image alt text", "Variant image URL"]


def prepare_output(output, source, adapter=None):
    output = Path(output).resolve()
    marker = output / ".catalog-exporter.json"
    if output.exists() and any(output.iterdir()):
        if not marker.is_file() or read_json(marker).get("source") != source:
            raise ExportError("Output is nonempty and is not an export for this source. Choose a new output folder.")
        previous_adapter = read_json(marker).get("adapter")
        if adapter and previous_adapter and adapter != previous_adapter:
            raise ExportError("Output belongs to another adapter. Choose a new output folder.")
    output.mkdir(parents=True, exist_ok=True)
    write_json(marker, {"tool": "store-catalog-exporter", "source": source, "adapter": adapter, "version": __version__})
    return output


def validate_products(products):
    errors, warnings, seen = [], [], set()
    currencies = set()
    for product in products:
        handle = product.get("handle") or ""
        prefix = handle or "<missing handle>"
        if not handle or handle in seen or any(c.isspace() for c in handle):
            errors.append(f"{prefix}: missing, duplicate or whitespace-containing handle.")
        seen.add(handle)
        if not product.get("title"):
            errors.append(f"{prefix}: missing title.")
        if product.get("currency"):
            currencies.add(product["currency"])
        provenance_currencies = product.get("provenance", {}).get("currencies", [])
        currencies.update(provenance_currencies)
        options = product.get("options", [])
        if not 1 <= len(options) <= 3 or any(not option.get("name") for option in options):
            errors.append(f"{prefix}: requires one to three named options.")
        variants = product.get("variants", [])
        if not variants:
            errors.append(f"{prefix}: no priced variants; an aggregate lowPrice is insufficient.")
        combinations = set()
        for variant in variants:
            values = tuple(variant.get("options", []))
            if len(values) != len(options) or any(value is None or value == "" for value in values):
                errors.append(f"{prefix}: variant option values are incomplete.")
            if values in combinations:
                errors.append(f"{prefix}: duplicate variant option combination.")
            combinations.add(values)
            if amount(variant.get("price")) is None:
                errors.append(f"{prefix}: variant price is missing or invalid; refusing a default zero price.")
            if amount(variant.get("price")) is not None and Decimal(amount(variant.get("price"))) == 0:
                warnings.append(f"{prefix}: source variant price is zero; review before selling.")
        if not product.get("images"):
            warnings.append(f"{prefix}: no public product images.")
        if any(amount(v.get("grams")) is None or Decimal(amount(v.get("grams"))) == 0 for v in variants):
            warnings.append(f"{prefix}: missing/zero shipping weight; no shipping weight is guessed.")
    if len(currencies) > 1:
        errors.append("Multiple currencies are present. Shopify CSV prices require one target currency; no conversion is performed.")
    if not products:
        errors.append("No products were extracted.")
    return {"errors": list(dict.fromkeys(errors)), "warnings": list(dict.fromkeys(warnings))}


def shopify_rows(products):
    """One row per variant, then extra image-only rows; no fake variants for images."""
    for product in products:
        images = product["images"]
        for index, variant in enumerate(product["variants"]):
            row = {key: "" for key in SHOPIFY_HEADERS}
            row["URL handle"] = product["handle"]
            if index == 0:
                row.update({"Title": product["title"], "Description": product["description_html"],
                            "Vendor": product["vendor"], "Type": product["product_type"],
                            "Tags": ", ".join(product["tags"]), "Published on online store": "false", "Status": "draft"})
                if images:
                    row.update({"Product image URL": images[0]["url"], "Image position": 1,
                                "Image alt text": images[0]["alt"] or product["title"]})
            for i, option in enumerate(product["options"]):
                row[f"Option{i + 1} name"] = option["name"]
                row[f"Option{i + 1} value"] = variant["options"][i]
            row.update({"SKU": variant["sku"], "Barcodes": variant["barcode"], "Price": variant["price"],
                        "Compare-at price": variant["compare_at_price"] or "",
                        "Charge tax": str(variant["taxable"]).lower() if variant["taxable"] is not None else "",
                        "Requires shipping": str(variant["requires_shipping"]).lower() if variant["requires_shipping"] is not None else "",
                        "Fulfillment service": "manual", "Continue selling when out of stock": "deny",
                        "Variant image URL": variant["image_url"]})
            grams = amount(variant.get("grams"))
            if grams is not None and Decimal(grams) > 0:
                row["Weight value (grams)"] = grams
                row["Weight unit for display"] = "g"
            yield row
        for index, image in enumerate(images[1:], 2):
            row = {key: "" for key in SHOPIFY_HEADERS}
            row.update({"URL handle": product["handle"], "Product image URL": image["url"],
                        "Image position": index, "Image alt text": image["alt"] or product["title"]})
            yield row


def preferred_mp4(media):
    sources = [source for source in media.get("sources", [])
               if source.get("format") == "mp4" or source.get("mime_type") == "video/mp4"]
    return max(sources, key=lambda source: source.get("height") or 0, default=None)


def save_bundle(output, source, adapter, result, config, *, http_stats=None):
    output = Path(output)
    products = result["products"]
    validation = validate_products(products)
    bundle = {"schema_version": "1.0", "tool_version": __version__, "created_at": utc_now(),
              "source": source, "adapter": adapter, "products": products,
              "collections": result["collections"], "memberships": result["memberships"],
              "coverage": result["coverage"], "extraction_errors": result["errors"]}
    write_json(output / "bundle.json", bundle)
    for name, raw in result["raw"].items():
        write_json(output / "raw" / (name + ".json"), raw)
    columns = config.get("csv_columns") or {field: field for field in config.get("fields", DEFAULT_FIELDS)}
    write_csv(output / "products.csv", list(columns),
              ({label: path_value(product, path) for label, path in columns.items()} for product in products))
    variant_headers = ["handle", "id", "sku", "barcode", "options", "price", "compare_at_price", "available",
                       "grams", "requires_shipping", "taxable", "image_url", "inventory_quantity"]
    write_csv(output / "variants.csv", variant_headers,
              ({"handle": p["handle"], **{key: v.get(key) for key in variant_headers[1:]}}
               for p in products for v in p["variants"]))
    image_headers = ["handle", "url", "position", "alt", "width", "height"]
    write_csv(output / "images.csv", image_headers,
              ({"handle": p["handle"], **{key: im.get(key) for key in image_headers[1:]}}
               for p in products for im in p["images"]))
    media_rows, video_rows, scan_rows = [], [], []
    media_headers = ["handle", "kind", "position", "alt", "url", "preview_url", "format", "height", "width",
                     "mime_type", "duration_ms", "preferred_mp4"]
    for product in products:
        videos = [m for m in product["media"] if m["kind"] in ("video", "external_video")]
        scan_rows.append({"handle": product["handle"], "status": product["media_scan_status"], "videos_found": len(videos)})
        for media in product["media"]:
            best = preferred_mp4(media)
            for media_source in media["sources"] or [{"url": media["url"]}]:
                row = {"handle": product["handle"], "kind": media["kind"], "position": media["position"],
                       "alt": media["alt"], "url": media_source.get("url") or "", "preview_url": media["preview_url"],
                       "duration_ms": media.get("duration_ms"), "preferred_mp4": bool(best and best == media_source),
                       **{key: media_source.get(key) for key in ("format", "height", "width", "mime_type")}}
                media_rows.append(row)
                if media in videos:
                    video_rows.append(row)
    write_csv(output / "media.csv", media_headers, media_rows)
    write_csv(output / "videos.csv", media_headers, video_rows)
    write_csv(output / "media_scans.csv", ["handle", "status", "videos_found"], scan_rows)
    collection_headers = ["handle", "title", "url", "description_html", "image_url", "metadata_status",
                          "membership_status", "source_members"]
    write_csv(output / "collections.csv", collection_headers, result["collections"])
    write_csv(output / "collection_memberships.csv", ["product_handle", "collection_handle"], result["memberships"])
    write_csv(output / "errors.csv", ["stage", "handle", "message"], result["errors"])
    shopify_path = output / "shopify_products.csv"
    row_count = 0
    if not validation["errors"]:
        rows = list(shopify_rows(products))
        row_count = len(rows)
        # This machine-import file preserves text exactly. Human CSVs are formula-safe.
        write_csv(shopify_path, SHOPIFY_HEADERS, rows, spreadsheet_safe=False)
    elif shopify_path.exists():
        shopify_path.unlink()  # Remove only our own stale machine-import artifact.
    known_incomplete = bool(result["errors"]) or result["coverage"].get("selection_limited", False)
    catalog_status = result["coverage"]["catalog"]["status"]
    report = {"schema_version": "1.0", "products": len(products),
              "variants": sum(len(p["variants"]) for p in products),
              "images": sum(len(p["images"]) for p in products),
              "products_with_video": sum(any(m["kind"] in ("video", "external_video") for m in p["media"]) for p in products),
              "media_scan_failures": sum(p["media_scan_status"] == "failed" for p in products),
              "collections": len(result["collections"]), "memberships": len(result["memberships"]),
              "shopify_csv_created": not validation["errors"], "shopify_csv_rows": row_count,
              "raw_files": ["raw/" + name + ".json" for name in sorted(result["raw"])],
              "product_csv_columns": columns,
              "import_validation": validation, "extraction_errors": result["errors"],
              "coverage": result["coverage"],
              "public_feed_exhausted": catalog_status == "exhausted",
              "known_incomplete": known_incomplete or catalog_status in ("incomplete", "failed"),
              "full_store_backup": False, "http": http_stats or {},
              "limitations": ["Only publicly exposed catalog data was collected.",
                              "No inventory quantities, costs, private metafields, orders or customers were inferred.",
                              "Videos and many-to-many collection assignments require a separate import step.",
                              "A valid draft CSV does not confirm destination-store import or production readiness."]}
    write_json(output / "report.json", report)
    return report


def validate_bundle(path):
    bundle = read_json(path)
    if bundle.get("schema_version") != "1.0" or not isinstance(bundle.get("products"), list):
        raise ExportError("Unsupported canonical bundle schema.")
    validation = validate_products(bundle["products"])
    handles = {p["handle"] for p in bundle["products"]}
    collections = {c["handle"] for c in bundle.get("collections", [])}
    for relation in bundle.get("memberships", []):
        if relation["product_handle"] not in handles or relation["collection_handle"] not in collections:
            validation["errors"].append("Collection membership references a missing product or collection.")
    csv_path = Path(path).parent / "shopify_products.csv"
    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != SHOPIFY_HEADERS:
                validation["errors"].append("Unexpected Shopify CSV columns.")
            rows = list(reader)
        expected = list(shopify_rows(bundle["products"])) if not validation["errors"] else []
        if len(rows) != len(expected):
            validation["errors"].append("Shopify CSV row count differs from canonical bundle.")
        else:
            for row, reference in zip(rows, expected):
                if row != {key: "" if value is None else str(value) for key, value in reference.items()}:
                    validation["errors"].append("Shopify CSV content differs from canonical bundle.")
                    break
    elif not validation["errors"]:
        validation["errors"].append("Expected draft Shopify CSV is missing.")
    return {"valid": not validation["errors"], **validation,
            "coverage": bundle.get("coverage"), "extraction_errors": bundle.get("extraction_errors", []),
            "scope": "local bundle and CSV consistency; not a destination import test"}
