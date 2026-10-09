"""Small, explicit serialization and URL helpers."""

import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit


class ExportError(Exception):
    """An actionable export error safe to print at the command line."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".writing-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ExportError(f"Could not read JSON file {Path(path).name}: {exc}") from exc


def safe_cell(value):
    """Protect human-review CSVs from spreadsheet formula injection."""
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    value = str(value)
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")):
        return "'" + value
    return value


def write_csv(path, headers, rows, *, spreadsheet_safe=True):
    import io
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="raise", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        if spreadsheet_safe:
            row = {k: safe_cell(v) for k, v in row.items()}
        writer.writerow(row)
    # BOM helps Excel preserve Unicode. Python csv readers should use utf-8-sig.
    atomic_text(path, "\ufeff" + stream.getvalue())


def source_url(value):
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ExportError("Source must be an absolute HTTP(S) URL.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ExportError("Source URL must not contain credentials, a query, or a fragment.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def public_link(value, base):
    if not isinstance(value, str) or not value.strip():
        return ""
    url = urljoin(base.rstrip("/") + "/", value)
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return ""
    return url


def slug(value):
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value)).strip("-").lower() or "product"


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def amount(value, *, minor_units=False):
    """Never convert through binary float; Ajax money uses integer minor units."""
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            return None
        if minor_units:
            number /= Decimal(100)
        return format(number, "f")
    except InvalidOperation:
        return None


def path_value(record, path):
    """Dot paths, including list indexes: variants.0.price."""
    current = record
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current
