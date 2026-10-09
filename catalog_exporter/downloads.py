"""Optional public-media archive. No embedded-video platform ripping."""

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from .export import preferred_mp4
from .http import FetchError
from .util import digest, read_json, slug, write_csv, write_json


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_media(client, output, products, source, *, mode="all", media_hosts=(), max_mb=100):
    output = Path(output).resolve()
    allowed = {urlsplit(source).hostname, "cdn.shopify.com", *media_hosts}
    ledger_path = output / "download_ledger.json"
    ledger = read_json(ledger_path) if ledger_path.exists() else {}
    rows = []
    for product in products:
        assets = []
        if mode in ("images", "all"):
            assets.extend(("image", image["url"]) for image in product["images"])
        if mode in ("videos", "all"):
            for media in product["media"]:
                best = preferred_mp4(media)
                if best:
                    assets.append(("video", best["url"]))
        for kind, url in dict.fromkeys(assets):
            key = digest(url)
            extension = Path(urlsplit(url).path).suffix.lower()
            if extension not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".mp4"):
                extension = ".mp4" if kind == "video" else ".image"
            relative = f"downloads/{kind}/{slug(product['handle'])[:80]}-{key[:16]}{extension}"
            row = {"handle": product["handle"], "kind": kind, "url": url, "path": relative, "status": "", "bytes": ""}
            if urlsplit(url).hostname not in allowed:
                row["status"] = "host_not_allowed"
            else:
                previous = ledger.get(key, {})
                previous_path = previous.get("path", "")
                # Never trust a ledger-supplied absolute path or traversal.
                trusted = previous_path.startswith("downloads/") and ".." not in Path(previous_path).parts
                path = output / previous_path if trusted else output / relative
                if not path.resolve().is_relative_to(output) or not (output / relative).resolve().is_relative_to(output):
                    row["status"] = "unsafe_local_path"
                    rows.append(row)
                    continue
                if trusted and path.is_file() and path.stat().st_size == previous.get("bytes") and file_hash(path) == previous.get("sha256"):
                    row.update({"status": "cached", "path": previous_path, "bytes": previous["bytes"]})
                else:
                    try:
                        size = client.download(url, output / relative, max_mb * 1024 * 1024)
                        row.update({"status": "downloaded", "bytes": size})
                        ledger[key] = {"path": relative, "bytes": size, "sha256": file_hash(output / relative)}
                        write_json(ledger_path, ledger)
                    except FetchError as exc:
                        row["status"] = f"failed: {exc}"
            rows.append(row)
    write_csv(output / "downloads.csv", ["handle", "kind", "url", "path", "status", "bytes"], rows)
    return {"downloaded": sum(r["status"] == "downloaded" for r in rows),
            "cached": sum(r["status"] == "cached" for r in rows),
            "failed_or_skipped": sum(r["status"] not in ("cached", "downloaded") for r in rows)}
