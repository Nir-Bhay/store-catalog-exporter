"""Two explicit extraction strategies. Public feeds are best-effort endpoints."""

import json
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

from .http import FetchError
from .model import attach_media, normalize_jsonld, normalize_shopify
from .util import ExportError, public_link


def paginate(client, endpoint, key, max_pages):
    items, seen = [], set()
    duplicate_count = 0
    for page in range(1, max_pages + 1):
        try:
            payload = client.json(f"{endpoint}?limit=250&page={page}")
            if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
                raise FetchError(f"Public endpoint has no {key} array.")
            batch = payload[key]
            if not batch:
                return items, {"status": "exhausted", "pages": page, "duplicates": duplicate_count}
            added = 0
            for item in batch:
                if not isinstance(item, dict):
                    raise FetchError("Malformed item in public feed.")
                identity = str(item.get("id") or item.get("handle") or "")
                if not identity:
                    raise FetchError("Feed item has neither an ID nor a handle.")
                if identity in seen:
                    duplicate_count += 1
                    continue
                seen.add(identity)
                items.append(item)
                added += 1
            if not added:
                return items, {"status": "incomplete", "pages": page, "reason": "repeated_page",
                               "duplicates": duplicate_count}
        except FetchError as exc:
            if not items:
                raise
            return items, {"status": "incomplete", "pages": page, "reason": str(exc),
                           "duplicates": duplicate_count}
    return items, {"status": "incomplete", "pages": max_pages, "reason": "max_pages_reached",
                   "duplicates": duplicate_count}


def select_products(products, config, max_products):
    include = set(config.get("include_handles", []))
    exclude = set(config.get("exclude_handles", []))
    exclude_tags = set(config.get("exclude_tags", []))
    selected = [p for p in products if (not include or p["handle"] in include)
                and p["handle"] not in exclude and not exclude_tags.intersection(p["tags"])]
    limited = max_products is not None and len(selected) > max_products
    if max_products is not None:
        selected = selected[:max_products]
    vendor = config.get("overrides", {}).get("vendor")
    if vendor is not None:
        for product in selected:
            product["vendor"] = vendor
    return selected, limited


def shopify_export(client, source, config, *, media=True, collections=True, max_pages=1000,
                   max_products=None, progress=lambda message: None):
    raw, coverage = paginate(client, source + "/products.json", "products", max_pages)
    products = [normalize_shopify(p, source) for p in raw]
    products, limited = select_products(products, config, max_products)
    raw_media, errors = [], []
    if media:
        for index, product in enumerate(products):
            try:
                payload = client.json(source + "/products/" + quote(product["handle"], safe="") + ".js")
                raw_media.append({"handle": product["handle"], "payload": payload})
                attach_media(product, payload, source)
                if len(payload.get("variants", [])) >= 250:
                    errors.append({"stage": "variant_coverage", "handle": product["handle"],
                                   "message": "Ajax response has at least 250 variants; truncation is possible. Catalog variants were preserved."})
            except (FetchError, ValueError) as exc:
                product["media"] = []
                product["media_scan_status"] = "failed"
                errors.append({"stage": "media", "handle": product["handle"], "message": str(exc)})
            progress(f"Media: {index + 1}/{len(products)}")
    raw_collections, normalized_collections, memberships = [], [], []
    collection_coverage = {"status": "not_requested"}
    if collections:
        try:
            raw_collections, collection_coverage = paginate(client, source + "/collections.json", "collections", max_pages)
        except FetchError as exc:
            collection_coverage = {"status": "failed", "reason": str(exc)}
            errors.append({"stage": "collections", "handle": "", "message": str(exc)})
        product_by_handle = {p["handle"]: p for p in products}
        for index, collection in enumerate(raw_collections):
            handle = collection.get("handle") or ""
            endpoint = source + "/collections/" + quote(handle, safe="")
            record = {"handle": handle, "title": collection.get("title") or "", "url": endpoint,
                      "description_html": collection.get("body_html") or "",
                      "image_url": public_link((collection.get("image") or {}).get("src"), source),
                      "metadata_status": "feed_only", "membership_status": "failed", "source_members": None}
            try:
                details = client.json(endpoint + ".json").get("collection")
                if not isinstance(details, dict):
                    raise FetchError("Collection metadata missing.")
                record.update({"description_html": details.get("body_html") or record["description_html"],
                               "image_url": public_link((details.get("image") or {}).get("src"), source) or record["image_url"],
                               "metadata_status": "ok"})
            except (FetchError, AttributeError) as exc:
                errors.append({"stage": "collection_metadata", "handle": handle, "message": str(exc)})
            try:
                members, member_coverage = paginate(client, endpoint + "/products.json", "products", max_pages)
                record["membership_status"] = member_coverage["status"]
                record["source_members"] = len(members)
                if member_coverage["status"] != "exhausted":
                    errors.append({"stage": "collection_membership", "handle": handle,
                                   "message": member_coverage.get("reason", "incomplete")})
                for member in members:
                    p = product_by_handle.get(member.get("handle"))
                    if p:
                        p["collection_handles"].append(handle)
                        memberships.append({"product_handle": p["handle"], "collection_handle": handle})
            except FetchError as exc:
                errors.append({"stage": "collection_membership", "handle": handle, "message": str(exc)})
            normalized_collections.append(record)
            progress(f"Collections: {index + 1}/{len(raw_collections)}")
    selected_handles = {p["handle"] for p in products}
    return {"products": products, "collections": normalized_collections, "memberships": memberships,
            "raw": {"products": [p for p in raw if p.get("handle") in selected_handles],
                    "media": raw_media, "collections": raw_collections},
            "coverage": {"catalog": coverage, "collections": collection_coverage,
                         "selection_limited": limited, "discovered_products": len(raw)}, "errors": errors}


class JsonLdParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.capture = False
        self.buffer = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type", "").lower().split(";")[0] == "application/ld+json":
            self.capture = True
            self.buffer = []

    def handle_endtag(self, tag):
        if tag == "script" and self.capture:
            self.scripts.append("".join(self.buffer))
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.buffer.append(data)


def product_nodes(value):
    if isinstance(value, list):
        for child in value:
            yield from product_nodes(child)
    elif isinstance(value, dict):
        kinds = value.get("@type", [])
        if isinstance(kinds, str):
            kinds = [kinds]
        if "Product" in kinds or "https://schema.org/Product" in kinds:
            yield value
        else:
            for child in value.values():
                if isinstance(child, (list, dict)):
                    yield from product_nodes(child)


def discover_sitemap(client, source, max_pages, *, max_sitemaps=100):
    """Bounded, same-origin sitemap traversal; no link spidering."""
    pending, visited, pages, seen_pages = [source + "/sitemap.xml"], set(), [], set()
    while pending and len(visited) < max_sitemaps:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        text = client.text(url)
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise FetchError("Unsupported XML declarations in sitemap.")
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise FetchError("Sitemap is not valid XML.") from exc
        is_index = root.tag.split("}")[-1] == "sitemapindex"
        for loc in root.iter():
            if loc.tag.split("}")[-1] != "loc":
                continue
            link = public_link(loc.text or "", source)
            if not link or urlsplit(link).netloc != urlsplit(source).netloc:
                continue
            if is_index:
                if link not in visited and link not in pending:
                    pending.append(link)
            elif link not in seen_pages:
                if len(pages) >= max_pages:
                    return pages, "limited"
                seen_pages.add(link)
                pages.append(link)
    return pages, "limited" if pending else "exhausted"


def jsonld_export(client, source, config, *, urls=None, max_pages=1000, max_products=None,
                  progress=lambda message: None, **unused):
    if urls is None:
        urls, discovery = discover_sitemap(client, source, max_pages)
    else:
        urls, discovery = list(dict.fromkeys(urls)), "explicit_url_list"
        if len(urls) > max_pages:
            urls, discovery = urls[:max_pages], "limited"
    products, raw, errors, scans, seen = [], [], [], [], set()
    stopped = False
    for index, url in enumerate(urls):
        if urlsplit(url).netloc != urlsplit(source).netloc or not public_link(url, source):
            raise ExportError("Every page URL must be HTTP(S) and on the source hostname.")
        try:
            parser = JsonLdParser()
            parser.feed(client.text(url))
            nodes = []
            malformed = 0
            for script in parser.scripts:
                try:
                    nodes.extend(product_nodes(json.loads(script)))
                except ValueError:
                    malformed += 1
            for ordinal, node in enumerate(nodes):
                product = normalize_jsonld(node, url, ordinal)
                key = (product["url"], product["title"])
                if key not in seen:
                    seen.add(key)
                    products.append(product)
                    raw.append({"handle": product["handle"], "url": url, "node": node})
            scans.append({"url": url, "status": "ok" if nodes else "no_product_jsonld", "products": len(nodes)})
            if malformed:
                errors.append({"stage": "jsonld_parse", "handle": "", "message": f"{malformed} malformed JSON-LD blocks at page {index + 1}."})
        except FetchError as exc:
            scans.append({"url": url, "status": "failed", "products": 0})
            errors.append({"stage": "page", "handle": "", "message": f"Page {index + 1}: {exc}"})
        progress(f"Pages: {index + 1}/{len(urls)}")
        if max_products is not None and len(products) >= max_products:
            stopped = index + 1 < len(urls)
            break
    products, limited = select_products(products, config, max_products)
    selected_handles = {p["handle"] for p in products}
    return {"products": products, "collections": [], "memberships": [],
            "raw": {"structured_data": [r for r in raw if r["handle"] in selected_handles], "page_scans": scans}, "errors": errors,
            "coverage": {"catalog": {"status": "structured_data_only", "discovery": discovery,
                                      "pages_scanned": len(scans), "candidate_pages": len(urls)},
                         "collections": {"status": "unsupported"}, "selection_limited": stopped or limited,
                         "discovered_products": len(raw)}}
