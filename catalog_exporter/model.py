"""Canonical public product records, separate from source snapshots."""

import html
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from .util import amount, public_link, slug


class CleanDescription(HTMLParser):
    allowed = {"p", "div", "span", "br", "strong", "b", "em", "i", "ul", "ol", "li",
               "h1", "h2", "h3", "h4", "h5", "h6", "table", "tbody", "thead", "tr",
               "td", "th", "blockquote", "a", "img", "hr"}
    blocked = {"script", "style", "iframe", "object", "embed"}

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.result = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.depth += 1
        if self.depth or tag not in self.allowed:
            return
        keep = []
        for key, value in attrs:
            if key in ("href", "src") and tag in ("a", "img"):
                value = public_link(value, self.base)
                if value:
                    keep.append(f'{key}="{html.escape(value, quote=True)}"')
            elif key in ("alt", "title"):
                keep.append(f'{key}="{html.escape(value or "", quote=True)}"')
        self.result.append("<" + tag + (" " + " ".join(keep) if keep else "") + ">")

    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.depth = max(0, self.depth - 1)
            return
        if not self.depth and tag in self.allowed and tag not in ("img", "br", "hr"):
            self.result.append("</" + tag + ">")

    def handle_data(self, data):
        if not self.depth:
            self.result.append(html.escape(data))


def clean_description(value, base):
    parser = CleanDescription(base)
    parser.feed(str(value or ""))
    return "".join(parser.result)


def image_record(image, base, position):
    if isinstance(image, str):
        image = {"src": image}
    return {"id": str(image.get("id", "")), "url": public_link(image.get("src") or image.get("url") or image.get("contentUrl"), base),
            "position": image.get("position") or position, "alt": image.get("alt") or "",
            "width": image.get("width"), "height": image.get("height")}


def normalize_shopify(product, base):
    handle = product.get("handle") or ""
    raw_options = product.get("options", [])
    options = [{"name": o.get("name", ""), "position": o.get("position", i + 1)}
               if isinstance(o, dict) else {"name": str(o), "position": i + 1}
               for i, o in enumerate(raw_options)]
    variants = []
    for v in product.get("variants", []):
        values = [v.get(f"option{i + 1}") for i in range(len(options))]
        if not options and len(product.get("variants", [])) == 1:
            options = [{"name": "Title", "position": 1}]
            values = [v.get("option1") or "Default Title"]
        featured = v.get("featured_image") or {}
        variants.append({"id": str(v.get("id", "")), "sku": v.get("sku") or "",
                         "barcode": v.get("barcode") or "", "options": values,
                         "price": amount(v.get("price")), "compare_at_price": amount(v.get("compare_at_price")),
                         "available": v.get("available"), "grams": v.get("grams"),
                         "requires_shipping": v.get("requires_shipping"), "taxable": v.get("taxable"),
                         "image_url": public_link(featured.get("src"), base),
                         "image_id": str(v.get("image_id") or featured.get("id") or ""),
                         "inventory_quantity": None})
    images = [image_record(im, base, i + 1) for i, im in enumerate(product.get("images", []))]
    images = [im for im in images if im["url"]]
    images.sort(key=lambda im: im["position"])
    by_id = {im["id"]: im["url"] for im in images if im["id"]}
    by_variant = {str(variant_id): public_link(image.get("src"), base)
                  for image in product.get("images", []) if isinstance(image, dict)
                  for variant_id in image.get("variant_ids", [])}
    for variant in variants:
        if not variant["image_url"]:
            variant["image_url"] = by_id.get(variant["image_id"], "") or by_variant.get(variant["id"], "")
    tags = product.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {"handle": handle, "source_id": str(product.get("id", "")),
            "url": base + "/products/" + quote(handle, safe=""), "title": product.get("title") or "",
            "description_html": clean_description(product.get("body_html"), base),
            "vendor": product.get("vendor") or "", "product_type": product.get("product_type") or "",
            "tags": tags, "currency": None, "options": options, "variants": variants,
            "images": images, "media": [], "media_scan_status": "not_requested",
            "collection_handles": [], "provenance": {"adapter": "shopify", "price_units": "major_decimal"}}


def attach_media(product, payload, base):
    """Ajax media ordering is separate from the CSV still-image ordering."""
    if not isinstance(payload, dict) or not isinstance(payload.get("media"), list):
        raise ValueError("Public product.js response has no media list.")
    product["media_scan_status"] = "ok"
    image_meta = {}
    for i, m in enumerate(payload["media"]):
        if not isinstance(m, dict):
            raise ValueError("Malformed media item in public response.")
        kind = m.get("media_type", "unknown")
        preview = m.get("preview_image") or {}
        sources = []
        for s in m.get("sources", []):
            url = public_link(s.get("url"), base)
            if url:
                sources.append({"url": url, "format": s.get("format"), "mime_type": s.get("mime_type"),
                                "height": s.get("height"), "width": s.get("width")})
        url = public_link(m.get("src") or m.get("url"), base)
        if kind == "external_video" and not url:
            host = m.get("host")
            external_id = str(m.get("external_id") or "")
            if host == "youtube" and external_id:
                url = "https://www.youtube.com/watch?v=" + quote(external_id, safe="")
            elif host == "vimeo" and external_id:
                url = "https://vimeo.com/" + quote(external_id, safe="")
        product["media"].append({"id": str(m.get("id", "")), "kind": kind,
                                 "position": m.get("position") or i + 1, "alt": m.get("alt") or "",
                                 "url": url, "preview_url": public_link(preview.get("src"), base),
                                 "duration_ms": m.get("duration"), "sources": sources})
        if kind == "image":
            image_meta[str(m.get("id", ""))] = m
    for image in product["images"]:
        m = image_meta.get(image["id"])
        if m and m.get("alt"):
            image["alt"] = m["alt"]
    # Availability and price in product.js are intentionally not merged: minor units!


def normalize_jsonld(node, page_url, ordinal=0):
    title = node.get("name") or ""
    url = public_link(node.get("url"), page_url) or page_url
    handle = slug(urlsplit(url).path.rstrip("/").split("/")[-1] or title)
    if ordinal:
        handle += f"-{ordinal + 1}"
    brand = node.get("brand", {})
    brand = brand.get("name", "") if isinstance(brand, dict) else str(brand)
    image_values = node.get("image", [])
    if not isinstance(image_values, list):
        image_values = [image_values]
    images = [image_record(v, page_url, i + 1) for i, v in enumerate(image_values) if isinstance(v, (str, dict))]
    images = [im for im in images if im["url"]]
    offers = node.get("offers", [])
    if not isinstance(offers, list):
        offers = [offers]
    variants = []
    currencies = set()
    for i, offer in enumerate(offers):
        if not isinstance(offer, dict) or amount(offer.get("price")) is None:
            continue  # Aggregate lowPrice is not an actual purchasable variant price.
        currency = offer.get("priceCurrency")
        if currency:
            currencies.add(currency)
        availability = offer.get("availability", "")
        available = True if availability.endswith("/InStock") else False if availability.endswith("/OutOfStock") else None
        variants.append({"id": "", "sku": offer.get("sku") or node.get("sku") or "",
                         "barcode": node.get("gtin13") or node.get("gtin") or "",
                         "options": [str(i + 1)] if len(offers) > 1 else ["Default Title"],
                         "price": amount(offer.get("price")), "compare_at_price": None,
                         "available": available, "grams": None, "requires_shipping": None,
                         "taxable": None, "image_url": "", "image_id": "", "inventory_quantity": None})
    videos = node.get("video", [])
    if not isinstance(videos, list):
        videos = [videos]
    media = []
    for video in videos:
        if not isinstance(video, dict):
            continue
        content = public_link(video.get("contentUrl"), page_url)
        embed = public_link(video.get("embedUrl"), page_url)
        media.append({"id": "", "kind": "video" if content else "external_video", "position": len(media) + 1,
                      "alt": video.get("name") or "", "url": embed if not content else "",
                      "preview_url": public_link(video.get("thumbnailUrl"), page_url), "duration_ms": None,
                      "sources": [{"url": content, "format": "mp4" if urlsplit(content).path.lower().endswith(".mp4") else None,
                                   "mime_type": None, "height": None, "width": None}] if content else []})
    return {"handle": handle, "source_id": str(node.get("@id", "")), "url": url, "title": title,
            "description_html": clean_description(node.get("description"), page_url), "vendor": brand,
            "product_type": node.get("category") or "", "tags": [],
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "options": [{"name": "Offer" if len(offers) > 1 else "Title", "position": 1}],
            "variants": variants, "images": images, "media": media, "media_scan_status": "structured_data_only",
            "collection_handles": [], "provenance": {"adapter": "jsonld", "page_url": page_url,
                                                     "price_units": "major_decimal", "currencies": sorted(currencies)}}
