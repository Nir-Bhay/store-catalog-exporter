"""Stable CLI for developers and agent subprocesses; JSON on stdout."""

import argparse
import json
import sys
import zipfile
from pathlib import Path

from . import __version__
from .adapters import jsonld_export, select_products, shopify_export
from .downloads import download_media
from .export import DEFAULT_FIELDS, prepare_output, save_bundle, validate_bundle
from .http import HttpClient
from .model import attach_media, normalize_shopify
from .util import ExportError, read_json, source_url, write_json


def load_config(path):
    config = read_json(path) if path else {}
    if not isinstance(config, dict):
        raise ExportError("Config must be a JSON object.")
    allowed = {"fields", "csv_columns", "include_handles", "exclude_handles", "exclude_tags", "overrides"}
    if set(config) - allowed:
        raise ExportError("Unknown config keys: " + ", ".join(sorted(set(config) - allowed)))
    for key in ("fields", "include_handles", "exclude_handles", "exclude_tags"):
        value = config.get(key)
        if value is not None and (not isinstance(value, list) or any(not isinstance(v, str) for v in value)):
            raise ExportError(f"{key} must be an array of strings.")
    if "fields" in config and not config["fields"]:
        raise ExportError("fields cannot be empty.")
    columns = config.get("csv_columns")
    if columns is not None and (not isinstance(columns, dict) or not columns or
                                any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in columns.items())):
        raise ExportError("csv_columns must map nonempty CSV labels to dot paths.")
    known = set(DEFAULT_FIELDS) | {"source_id", "provenance"}
    for path_value in (list(columns.values()) if columns else config.get("fields", [])):
        if path_value.split(".")[0] not in known:
            raise ExportError(f"Unknown field root: {path_value.split('.')[0]}")
    overrides = config.get("overrides", {})
    if not isinstance(overrides, dict) or set(overrides) - {"vendor"} or any(not isinstance(v, str) for v in overrides.values()):
        raise ExportError("Only an explicit string vendor override is supported.")
    return config


def parser():
    root = argparse.ArgumentParser(description="Export public product data to review CSVs and a Shopify draft-import bundle.")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Read a public Shopify feed or JSON-LD product pages.")
    export.add_argument("source", help="Public store root; locale prefixes such as /en are supported.")
    export.add_argument("--adapter", choices=("shopify", "jsonld"), default="shopify")
    export.add_argument("--urls", type=Path, help="JSON-LD adapter: UTF-8 file of same-host page URLs, one per line.")
    export.add_argument("--no-media", action="store_true", help="Skip Shopify product.js enrichment; video absence remains unknown.")
    export.add_argument("--no-collections", action="store_true")
    export.add_argument("--max-pages", type=int, default=1000)
    export.add_argument("--delay", type=float, default=0.6, help="Minimum seconds between requests per origin.")
    export.add_argument("--timeout", type=float, default=30)
    export.add_argument("--retries", type=int, default=3)
    export.add_argument("--cache-hours", type=float, default=24, help="0 bypasses cached responses.")
    export.add_argument("--download", choices=("none", "images", "videos", "all"), default="none")
    export.add_argument("--media-host", action="append", default=[], help="Additional hostname allowed for media downloads.")
    export.add_argument("--max-media-mb", type=int, default=100)
    convert = commands.add_parser("convert", help="Offline: rebuild exports from a product feed snapshot or canonical bundle.")
    convert.add_argument("input", type=Path)
    convert.add_argument("--source", help="Required for feed snapshots; canonical bundles carry their source.")
    convert.add_argument("--media-input", type=Path, help="Optional [{handle,payload: product.js JSON}] companion snapshot.")
    validate = commands.add_parser("validate", help="Verify local canonical data and generated Shopify CSV consistency.")
    validate.add_argument("bundle", type=Path)
    for sub in (export, convert):
        sub.add_argument("--output", type=Path, default=Path("exports/catalog"))
        sub.add_argument("--config", type=Path)
        sub.add_argument("--fields", help="Comma-separated dot paths for the human-review products.csv.")
        sub.add_argument("--max-products", type=int, help="Export a sample; explicitly recorded as a selection limit.")
        sub.add_argument("--zip", action="store_true", help="Create bundle.zip; cache and logs are excluded.")
        sub.add_argument("--quiet", action="store_true", help="Suppress progress on stderr.")
    return root


def from_snapshot(args, config):
    data = read_json(args.input)
    if isinstance(data, dict) and data.get("schema_version") == "1.0":
        source = source_url(args.source or data["source"])
        if source != source_url(data["source"]):
            raise ExportError("Do not change the source identity of a canonical bundle.")
        products, limited = select_products(data["products"], config, args.max_products)
        handles = {p["handle"] for p in products}
        result = {"products": products, "collections": data.get("collections", []),
                  "memberships": [m for m in data.get("memberships", []) if m["product_handle"] in handles],
                  "coverage": data["coverage"], "errors": data.get("extraction_errors", []), "raw": {}}
        result["coverage"]["selection_limited"] = result["coverage"].get("selection_limited", False) or limited
        return source, data["adapter"], result
    if not args.source:
        raise ExportError("Feed snapshot conversion needs --source; it is recorded as provenance.")
    source = source_url(args.source)
    raw = data.get("products") if isinstance(data, dict) else data
    if not isinstance(raw, list) or any(not isinstance(p, dict) or "handle" not in p for p in raw):
        raise ExportError("Input must be a Shopify products.json envelope, product array, or canonical bundle. product.js is not a catalog feed.")
    products, limited = select_products([normalize_shopify(p, source) for p in raw], config, args.max_products)
    errors, raw_media = [], []
    if args.media_input:
        raw_media = read_json(args.media_input)
        if not isinstance(raw_media, list):
            raise ExportError("Media snapshot must be an array of {handle,payload} records.")
        by_handle = {row["handle"]: row["payload"] for row in raw_media}
        for product in products:
            if product["handle"] in by_handle:
                try:
                    attach_media(product, by_handle[product["handle"]], source)
                except ValueError as exc:
                    product["media_scan_status"] = "failed"
                    errors.append({"stage": "media", "handle": product["handle"], "message": str(exc)})
    selected_handles = {p["handle"] for p in products}
    return source, "shopify", {"products": products, "collections": [], "memberships": [], "errors": errors,
                                "raw": {"products": [p for p in raw if p.get("handle") in selected_handles],
                                        "media": [r for r in raw_media if r.get("handle") in selected_handles]},
                                "coverage": {"catalog": {"status": "offline_snapshot", "reason": "Source completeness not verified."},
                                             "collections": {"status": "not_requested"}, "selection_limited": limited,
                                             "discovered_products": len(raw)}}


def zip_bundle(output):
    # Explicit allowlist prevents caches, logs or accidentally dropped-in credentials.
    names = {"bundle.json", "report.json", "products.csv", "variants.csv", "images.csv", "media.csv", "videos.csv",
             "media_scans.csv", "collections.csv", "collection_memberships.csv", "errors.csv", "shopify_products.csv",
             }
    report = read_json(output / "report.json")
    names.update(report.get("raw_files", []))
    downloads_path = output / "downloads.csv"
    if downloads_path.exists() and report.get("downloads"):
        import csv
        names.add("downloads.csv")
        with downloads_path.open(encoding="utf-8-sig", newline="") as stream:
            names.update(row["path"] for row in csv.DictReader(stream) if row["status"] in ("downloaded", "cached"))
    with zipfile.ZipFile(output / "bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            relative = path.relative_to(output)
            if path.is_file() and not path.is_symlink() and str(relative).replace("\\", "/") in names:
                archive.write(path, relative)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_bundle(args.bundle)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["valid"] else 2
        config = load_config(args.config)
        if args.fields:
            config.pop("csv_columns", None)
            config["fields"] = [field.strip() for field in args.fields.split(",") if field.strip()]
            if not config["fields"] or any(f.split(".")[0] not in set(DEFAULT_FIELDS) | {"source_id", "provenance"} for f in config["fields"]):
                raise ExportError("--fields contains an empty or unknown field selection.")
        if args.max_products is not None and args.max_products < 1:
            raise ExportError("--max-products must be positive.")
        client = None
        if args.command == "convert":
            source, adapter, result = from_snapshot(args, config)
            output = prepare_output(args.output, source, adapter)
        else:
            source = source_url(args.source)
            if args.max_pages < 1 or args.delay < 0 or args.timeout <= 0 or not 0 <= args.retries <= 10 or args.cache_hours < 0 or args.max_media_mb < 1:
                raise ExportError("Invalid request limits; use positive page/timeout/media limits and 0–10 retries.")
            if args.urls and args.adapter != "jsonld":
                raise ExportError("--urls is supported only by the jsonld adapter.")
            output = prepare_output(args.output, source, args.adapter)
            client = HttpClient(output / ".cache", delay=args.delay, timeout=args.timeout,
                                retries=args.retries, cache_hours=args.cache_hours)
            urls = [line.strip() for line in args.urls.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip() and not line.strip().startswith("#")] if args.urls else None
            adapter = args.adapter
            handler = shopify_export if adapter == "shopify" else jsonld_export
            progress = (lambda message: None) if args.quiet else (lambda message: print(message, file=sys.stderr))
            result = handler(client, source, config, media=not args.no_media, collections=not args.no_collections,
                             max_pages=args.max_pages, max_products=args.max_products, progress=progress,
                             **({"urls": urls} if adapter == "jsonld" else {}))
            result["raw"]["request_metadata"] = list(client.response_metadata.values())
        result["coverage"]["selection"] = {key: config.get(key, []) for key in ("include_handles", "exclude_handles", "exclude_tags")}
        result["coverage"]["selection"]["max_products"] = args.max_products
        report = save_bundle(output, source, adapter, result, config,
                             http_stats={"requests": client.requests, "cache_hits": client.cache_hits} if client else None)
        if client and args.download != "none":
            report["downloads"] = download_media(client, output, result["products"], source, mode=args.download,
                                                  media_hosts=args.media_host, max_mb=args.max_media_mb)
            if report["downloads"]["failed_or_skipped"]:
                report["known_incomplete"] = True
            write_json(output / "report.json", report)
        if args.zip:
            zip_bundle(output)
        print(json.dumps({"output": str(output), **report}, ensure_ascii=False, indent=2))
        return 2 if report["known_incomplete"] or not report["shopify_csv_created"] else 0
    except (ExportError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Successful HTTP responses remain cached; rerun the same command to resume.", file=sys.stderr)
        return 130
