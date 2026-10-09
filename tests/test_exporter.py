import contextlib
import copy
import csv
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from catalog_exporter.adapters import JsonLdParser, jsonld_export, paginate, product_nodes, shopify_export
from catalog_exporter.cli import load_config, main, zip_bundle
from catalog_exporter.downloads import download_media
from catalog_exporter.export import prepare_output, save_bundle, shopify_rows, validate_bundle, validate_products
from catalog_exporter.http import FetchError, HttpClient
from catalog_exporter.model import attach_media, clean_description, normalize_shopify
from catalog_exporter.util import ExportError, amount, read_json, safe_cell, source_url

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "https://store.example"


class FakeClient:
    def __init__(self, responses):
        self.responses = responses

    def json(self, url):
        value = self.responses.get(url, FetchError("Not available.", 404))
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def text(self, url):
        return self.json(url)


def fixture_product():
    return copy.deepcopy(read_json(ROOT / "examples/sample-feed.json")["products"][0])


class ModelTests(unittest.TestCase):
    def test_decimal_and_ajax_units(self):
        self.assertEqual(amount("49.90"), "49.90")
        self.assertEqual(amount(4990, minor_units=True), "49.9")
        self.assertIsNone(amount("NaN"))
        self.assertIsNone(amount(-1))

    def test_media_merge_does_not_replace_feed_price(self):
        p = normalize_shopify(fixture_product(), SOURCE)
        media = read_json(ROOT / "examples/sample-media.json")[0]["payload"]
        attach_media(p, media, SOURCE)
        self.assertEqual(p["variants"][0]["price"], "49.90")
        self.assertEqual(p["media_scan_status"], "ok")
        self.assertEqual(p["media"][1]["position"], 2)
        self.assertEqual(len(p["media"][1]["sources"]), 3)
        self.assertEqual(p["images"][0]["alt"], "Woven throw in a room")

    def test_invalid_media_is_not_no_video(self):
        p = normalize_shopify(fixture_product(), SOURCE)
        with self.assertRaises(ValueError):
            attach_media(p, {}, SOURCE)
        self.assertEqual(p["media_scan_status"], "not_requested")

    def test_images_do_not_create_variants(self):
        p = normalize_shopify(fixture_product(), SOURCE)
        rows = list(shopify_rows([p]))
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(bool(r["Price"]) for r in rows), 2)
        self.assertEqual([r["Image position"] for r in rows if r["Product image URL"]], [1, 2])
        self.assertEqual(rows[0]["Status"], "draft")
        self.assertEqual(rows[0]["Published on online store"], "false")
        self.assertEqual(rows[0]["Barcodes"], "000000001234")
        self.assertNotIn("Inventory quantity", rows[0])

    def test_variant_image_association_via_image_variant_ids(self):
        raw = fixture_product()
        raw["variants"][0].pop("featured_image")
        raw["images"][0]["variant_ids"] = [2001]
        self.assertEqual(normalize_shopify(raw, SOURCE)["variants"][0]["image_url"], "https://cdn.example/throw-sand.jpg")

    def test_missing_price_duplicate_options_and_handles_block_csv(self):
        p = normalize_shopify(fixture_product(), SOURCE)
        p["variants"][0]["price"] = None
        p["variants"][1]["options"] = p["variants"][0]["options"]
        self.assertGreaterEqual(len(validate_products([p, p])["errors"]), 3)

    def test_script_and_event_handlers_removed(self):
        result = clean_description('<p onclick="bad()">Fine<script>bad()</script><a href="javascript:bad()">link</a></p>', SOURCE)
        self.assertEqual(result, "<p>Fine<a>link</a></p>")

    def test_review_csv_formula_protection(self):
        self.assertEqual(safe_cell("=HYPERLINK(\"bad\")"), "'=HYPERLINK(\"bad\")")
        self.assertEqual(safe_cell("  +cmd"), "'  +cmd")
        self.assertEqual(safe_cell("49.90"), "49.90")


class AdapterTests(unittest.TestCase):
    def test_more_than_250_and_short_pages_continue_until_empty(self):
        records = [{"id": i, "handle": f"p-{i}"} for i in range(1, 252)]
        client = FakeClient({SOURCE + "/products.json?limit=250&page=1": {"products": records[:250]},
                             SOURCE + "/products.json?limit=250&page=2": {"products": records[250:]},
                             SOURCE + "/products.json?limit=250&page=3": {"products": []}})
        items, coverage = paginate(client, SOURCE + "/products.json", "products", 10)
        self.assertEqual(len(items), 251)
        self.assertEqual(coverage["status"], "exhausted")
        self.assertEqual(coverage["pages"], 3)

    def test_repeated_page_is_incomplete_not_infinite(self):
        client = FakeClient({SOURCE + f"/products.json?limit=250&page={page}": {"products": [fixture_product()]} for page in (1, 2)})
        items, coverage = paginate(client, SOURCE + "/products.json", "products", 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(coverage["reason"], "repeated_page")

    def test_page_failure_preserves_partial_catalog(self):
        client = FakeClient({SOURCE + "/products.json?limit=250&page=1": {"products": [fixture_product()]}})
        products, coverage = paginate(client, SOURCE + "/products.json", "products", 10)
        self.assertEqual(len(products), 1)
        self.assertEqual(coverage["status"], "incomplete")

    def test_handle_selection_does_not_archive_excluded_product_records(self):
        raw = read_json(ROOT / "examples/sample-feed.json")["products"]
        client = FakeClient({SOURCE + "/products.json?limit=250&page=1": {"products": raw},
                             SOURCE + "/products.json?limit=250&page=2": {"products": []}})
        result = shopify_export(client, SOURCE, {"include_handles": ["ceramic-cup"]}, media=False, collections=False)
        self.assertEqual(result["coverage"]["discovered_products"], 2)
        self.assertEqual([p["handle"] for p in result["raw"]["products"]], ["ceramic-cup"])

    def test_collection_many_to_many_and_media_failure(self):
        responses = {SOURCE + "/products.json?limit=250&page=1": {"products": [fixture_product()]},
                     SOURCE + "/products.json?limit=250&page=2": {"products": []},
                     SOURCE + "/collections.json?limit=250&page=1": {"collections": [{"id": 1, "handle": "a", "title": "A"}, {"id": 2, "handle": "b", "title": "B"}]},
                     SOURCE + "/collections.json?limit=250&page=2": {"collections": []}}
        for handle in ("a", "b"):
            responses[SOURCE + f"/collections/{handle}.json"] = {"collection": {"body_html": "<p>Collection</p>"}}
            responses[SOURCE + f"/collections/{handle}/products.json?limit=250&page=1"] = {"products": [fixture_product()]}
            responses[SOURCE + f"/collections/{handle}/products.json?limit=250&page=2"] = {"products": []}
        result = shopify_export(FakeClient(responses), SOURCE, {})
        self.assertEqual(result["products"][0]["collection_handles"], ["a", "b"])
        self.assertEqual(len(result["memberships"]), 2)
        self.assertEqual(result["products"][0]["media_scan_status"], "failed")
        self.assertEqual(result["errors"][0]["stage"], "media")

    def test_jsonld_graph_and_exact_offer_price(self):
        node = {"@context": "https://schema.org", "@graph": [{"@type": "Product", "name": "A Cup", "image": "/cup.jpg",
                                                                 "offers": {"@type": "Offer", "price": "19.95", "priceCurrency": "USD"}}]}
        page = '<script type="application/ld+json">' + json.dumps(node) + "</script>"
        result = jsonld_export(FakeClient({SOURCE + "/products/cup": page}), SOURCE, {}, urls=[SOURCE + "/products/cup"])
        self.assertEqual(result["products"][0]["variants"][0]["price"], "19.95")
        self.assertEqual(result["products"][0]["images"][0]["url"], SOURCE + "/cup.jpg")
        self.assertEqual(result["coverage"]["catalog"]["status"], "structured_data_only")

    def test_aggregate_low_price_is_not_variant_price(self):
        node = {"@type": "Product", "name": "A", "offers": {"@type": "AggregateOffer", "lowPrice": "10"}}
        page = '<script type="application/ld+json">' + json.dumps(node) + "</script>"
        result = jsonld_export(FakeClient({SOURCE: page}), SOURCE, {}, urls=[SOURCE])
        self.assertTrue(validate_products(result["products"])["errors"])

    def test_sitemap_discovery_and_malformed_jsonld_report(self):
        pages = {SOURCE + "/sitemap.xml": '<sitemapindex><sitemap><loc>' + SOURCE + '/products.xml</loc></sitemap></sitemapindex>',
                 SOURCE + "/products.xml": '<urlset><url><loc>' + SOURCE + '/p/cup</loc></url></urlset>',
                 SOURCE + "/p/cup": '<script type="application/ld+json">bad json</script>'}
        result = jsonld_export(FakeClient(pages), SOURCE, {})
        self.assertEqual(result["coverage"]["catalog"]["pages_scanned"], 1)
        self.assertEqual(result["errors"][0]["stage"], "jsonld_parse")


class BundleTests(unittest.TestCase):
    def test_invalid_price_withholds_machine_csv_and_removes_stale_file(self):
        with tempfile.TemporaryDirectory() as directory:
            out = prepare_output(Path(directory) / "bundle", SOURCE)
            product = normalize_shopify(fixture_product(), SOURCE)
            result = {"products": [product], "collections": [], "memberships": [], "raw": {}, "errors": [],
                      "coverage": {"catalog": {"status": "offline_snapshot"}, "collections": {"status": "not_requested"}}}
            save_bundle(out, SOURCE, "shopify", result, {})
            self.assertTrue((out / "shopify_products.csv").exists())
            product["variants"][0]["price"] = None
            report = save_bundle(out, SOURCE, "shopify", result, {})
            self.assertFalse(report["shopify_csv_created"])
            self.assertFalse((out / "shopify_products.csv").exists())
            self.assertTrue((out / "bundle.json").exists())

    def test_unknown_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text('{"invent_stock": true}', encoding="utf-8")
            with self.assertRaises(ExportError):
                load_config(path)

    def test_offline_cli_roundtrip_and_zip_excludes_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "bundle"
            with contextlib.redirect_stdout(io.StringIO()):
                code = main(["convert", str(ROOT / "examples/sample-feed.json"), "--source", SOURCE,
                             "--media-input", str(ROOT / "examples/sample-media.json"), "--output", str(out), "--zip"])
            self.assertEqual(code, 0)
            self.assertTrue(validate_bundle(out / "bundle.json")["valid"])
            report = read_json(out / "report.json")
            self.assertEqual((report["products"], report["variants"], report["images"], report["products_with_video"]), (2, 3, 3, 1))
            self.assertFalse(report["public_feed_exhausted"])
            (out / ".env").write_text("SECRET=example", encoding="utf-8")
            zip_bundle(out)
            import zipfile
            with zipfile.ZipFile(out / "bundle.zip") as archive:
                self.assertNotIn(".env", archive.namelist())
                self.assertNotIn(".catalog-exporter.json", archive.namelist())

    def test_canonical_bundle_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a", Path(directory) / "b"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["convert", str(ROOT / "examples/sample-feed.json"), "--source", SOURCE, "--output", str(first)])
                code = main(["convert", str(first / "bundle.json"), "--output", str(second)])
            self.assertEqual(code, 0)
            self.assertEqual(read_json(first / "bundle.json")["products"], read_json(second / "bundle.json")["products"])
            self.assertTrue(validate_bundle(second / "bundle.json")["valid"])

    def test_tampered_csv_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "bundle"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["convert", str(ROOT / "examples/sample-feed.json"), "--source", SOURCE, "--output", str(out)])
            csv_path = out / "shopify_products.csv"
            text = csv_path.read_text(encoding="utf-8-sig").replace("49.90", "0.00")
            csv_path.write_text(text, encoding="utf-8-sig")
            self.assertFalse(validate_bundle(out / "bundle.json")["valid"])

    def test_custom_fields_and_vendor_override(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "bundle"
            with contextlib.redirect_stdout(io.StringIO()):
                main(["convert", str(ROOT / "examples/sample-feed.json"), "--source", SOURCE, "--output", str(out),
                      "--config", str(ROOT / "examples/config.json")])
            with (out / "products.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["First price"], "49.90")
            self.assertEqual(read_json(out / "bundle.json")["products"][0]["vendor"], "Your Brand")

    def test_output_guard_and_source_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "important.txt").write_text("preserve", encoding="utf-8")
            with self.assertRaises(ExportError):
                prepare_output(directory, SOURCE)
        with self.assertRaises(ExportError):
            source_url("https://user:password@store.example")


class Handler(BaseHTTPRequestHandler):
    hits = {}

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.hits[self.path] = self.hits.get(self.path, 0) + 1
        if self.path == "/robots.txt":
            body, status = b"User-agent: *\nDisallow: /blocked\n", 200
        elif self.path == "/products.json?limit=250&page=1":
            body, status = (ROOT / "examples/sample-feed.json").read_bytes(), 200
        elif self.path.startswith("/products/") and self.path.endswith(".js"):
            handle = self.path.split("/")[-1][:-3]
            data = next(row["payload"] for row in read_json(ROOT / "examples/sample-media.json") if row["handle"] == handle)
            body, status = json.dumps(data).encode(), 200
        elif self.path == "/collections.json?limit=250&page=1":
            body, status = b'{"collections":[{"id":9,"handle":"homewares","title":"Homewares"}]}', 200
        elif self.path == "/collections.json?limit=250&page=2":
            body, status = b'{"collections":[]}', 200
        elif self.path == "/collections/homewares.json":
            body, status = b'{"collection":{"body_html":"<p>Homewares</p>"}}', 200
        elif self.path == "/collections/homewares/products.json?limit=250&page=1":
            body, status = (ROOT / "examples/sample-feed.json").read_bytes(), 200
        elif self.path == "/retry" and self.hits[self.path] == 1:
            body, status = b"wait", 429
        elif self.path == "/malformed" and self.hits[self.path] == 1:
            body, status = b"not JSON", 200
        else:
            body, status = b'{"products": []}', 200
        self.send_response(status)
        if status == 429:
            self.send_header("Retry-After", "0")
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.hits = {}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_private_network_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FetchError):
                HttpClient(directory).json(self.url + "/products.json")

    def test_http_pipeline_to_valid_draft_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            client = HttpClient(Path(directory) / ".cache", allow_private=True, delay=0)
            result = shopify_export(client, self.url, {})
            self.assertEqual(len(result["products"]), 2)
            self.assertEqual(len(result["memberships"]), 2)
            self.assertEqual(result["products"][0]["media_scan_status"], "ok")
            output = prepare_output(Path(directory) / "export", self.url)
            report = save_bundle(output, self.url, "shopify", result, {})
            self.assertEqual(report["products_with_video"], 1)
            self.assertFalse(report["known_incomplete"])
            self.assertTrue(validate_bundle(output / "bundle.json")["valid"])

    def test_retry_cache_and_robots(self):
        with tempfile.TemporaryDirectory() as directory:
            client = HttpClient(directory, allow_private=True, delay=0, retries=1)
            self.assertEqual(client.json(self.url + "/retry"), {"products": []})
            self.assertEqual(Handler.hits["/retry"], 2)
            fetched_at = client.response_metadata[self.url + "/retry"]["fetched_at"]
            client.json(self.url + "/retry")
            self.assertEqual(client.cache_hits, 1)
            self.assertEqual(client.response_metadata[self.url + "/retry"]["fetched_at"], fetched_at)
            self.assertTrue(client.response_metadata[self.url + "/retry"]["cache_hit"])
            with self.assertRaises(FetchError):
                client.json(self.url + "/blocked")
            self.assertNotIn("/blocked", Handler.hits)

    def test_bounded_response(self):
        with tempfile.TemporaryDirectory() as directory:
            client = HttpClient(directory, allow_private=True, delay=0, max_bytes=4)
            with self.assertRaises(FetchError):
                client._request(self.url + "/too-large")

    def test_malformed_json_does_not_poison_resume_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            client = HttpClient(directory, allow_private=True, delay=0)
            with self.assertRaises(FetchError):
                client.json(self.url + "/malformed")
            self.assertEqual(client.json(self.url + "/malformed"), {"products": []})
            self.assertEqual(Handler.hits["/malformed"], 2)


class DownloadTests(unittest.TestCase):
    class Client:
        def __init__(self):
            self.calls = []

        def download(self, url, path, limit):
            self.calls.append(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-image")
            return path.stat().st_size

    def test_allowlist_resume_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            product = normalize_shopify(fixture_product(), SOURCE)
            client = self.Client()
            result = download_media(client, directory, [product], SOURCE)
            self.assertEqual(result["failed_or_skipped"], 2)
            self.assertEqual(client.calls, [])
            result = download_media(client, directory, [product], SOURCE, media_hosts=["cdn.example"])
            self.assertEqual(result["downloaded"], 2)
            result = download_media(client, directory, [product], SOURCE, media_hosts=["cdn.example"])
            self.assertEqual(result["cached"], 2)
            path = next(Path(directory, "downloads/image").iterdir())
            path.write_bytes(b"X" * len(b"synthetic-image"))
            result = download_media(client, directory, [product], SOURCE, media_hosts=["cdn.example"])
            self.assertEqual(result["downloaded"], 1)

    def test_highest_mp4_only_is_downloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            product = normalize_shopify(fixture_product(), SOURCE)
            attach_media(product, read_json(ROOT / "examples/sample-media.json")[0]["payload"], SOURCE)
            client = self.Client()
            result = download_media(client, directory, [product], SOURCE, mode="videos", media_hosts=["cdn.example"])
            self.assertEqual(result["downloaded"], 1)
            self.assertEqual(client.calls, ["https://cdn.example/weaving-1080.mp4"])


if __name__ == "__main__":
    unittest.main()
