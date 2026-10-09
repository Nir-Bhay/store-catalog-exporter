"""Heuristic release privacy check. No project/customer identifiers are embedded."""

import argparse
import re
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Check source files for accidental private exports and common secret markers.")
    parser.add_argument("--tracked", action="store_true", help="Check all Git-tracked files, including accidentally tracked ignored files.")
    parser.add_argument("--forbid", action="append", default=[], help="Additional private string that must not occur in public files.")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    excluded = {".git", ".venv", "venv", "__pycache__", "exports", "private-validation", "dist", "build"}
    if args.tracked:
        result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
        files = [root / name for name in result.stdout.decode().split("\0") if name]
    else:
        files = [p for p in root.rglob("*") if p.is_file() and not excluded.intersection(p.relative_to(root).parts)
                 and not any(part.endswith(".egg-info") for part in p.relative_to(root).parts)]
    patterns = [re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
                re.compile(r"(?:shpat|shpca|shpss)_[A-Za-z0-9]{16,}"),
                re.compile(r"AKIA[A-Z0-9]{16}"),
                re.compile(r"C:[/\\]Users[/\\][A-Za-z0-9_.-]+", re.I)]
    email_pattern = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
    findings = []
    for path in files:
        relative = path.relative_to(root)
        if any(part in ("exports", "private-validation", ".cache", "downloads") for part in relative.parts) or path.name.startswith(".env"):
            findings.append(f"{relative}: generated/private path is tracked")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError):
            findings.append(f"{relative}: binary/unreadable file needs manual review")
            continue
        real_email = any(not match.group().endswith((".example", ".invalid", ".test")) for match in email_pattern.finditer(text))
        if real_email or any(pattern.search(text) for pattern in patterns) or any(value and value.casefold() in text.casefold() for value in args.forbid):
            findings.append(f"{relative}: possible private value; inspect locally")
    if findings:
        print("\n".join(findings))
        return 1
    print(f"Checked {len(files)} source files: no configured private markers detected. Manual review is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
