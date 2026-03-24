"""Apply vendor fingerprint rules to rows in data/vendors.csv.

This script classifies vendor as sidearm/presto/custom/unknown using URL and
optional HTML probe signals. It can run on a conference subset or all rows.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VENDORS_PATH = PROJECT_ROOT / "data" / "vendors.csv"

BIG_TEN_CONFERENCE_URL = "https://bigten.org/sports/mens-lacrosse"
BIG_TEN_TEAM_URLS = {
    "Johns Hopkins": "https://bluejays.com/sports/mens-lacrosse",
    "Maryland": "https://umterps.com/sports/mens-lacrosse",
    "Michigan": "https://mgoblue.com/sports/mens-lacrosse",
    "Ohio St.": "https://ohiostatebuckeyes.com/sports/mens-lacrosse",
    "Penn St.": "https://gopsusports.com/sports/mens-lacrosse",
    "Rutgers": "https://scarletknights.com/sports/mens-lacrosse",
}


def _fetch_html(url: str, timeout_seconds: int = 12) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 (MSDIE-VendorProbe/1.0)"})
    with urlopen(req, timeout=timeout_seconds) as resp:
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            return ""
        return resp.read(1_200_000).decode("utf-8", errors="ignore")


def _classify_vendor(url: str, html: str, used_probe: bool) -> tuple[str, str, str]:
    haystack = f"{url}\n{html}".lower()

    sidearm_signals = [
        "sidearm",
        "sidearmsports",
        "learfieldsidearm",
        "/services/responsive-stats.ashx",
    ]
    presto_signals = [
        "prestosports",
        "/gameday",
    ]

    has_sidearm = any(sig in haystack for sig in sidearm_signals)
    has_presto = any(sig in haystack for sig in presto_signals)

    if has_sidearm and has_presto:
        return "sidearm", "medium", "signals: sidearm+presto"
    if has_sidearm:
        return "sidearm", "high" if html else "medium", "signals: sidearm"
    if has_presto:
        return "presto", "high" if html else "medium", "signals: presto"
    if used_probe:
        return "unknown", "low", "signals: none (manual verify)"
    if url:
        return "custom", "low", "signals: url-only fallback"
    return "unknown", "low", "signals: missing url"


def _update_big_ten_urls(row: dict[str, str]) -> None:
    if (row.get("conference") or "").strip() != "Big Ten":
        return
    row["conference_url"] = BIG_TEN_CONFERENCE_URL
    team_name = (row.get("team_name") or "").strip()
    if team_name in BIG_TEN_TEAM_URLS:
        row["team_url"] = BIG_TEN_TEAM_URLS[team_name]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fingerprint vendors in data/vendors.csv")
    parser.add_argument(
        "--vendors",
        default=str(DEFAULT_VENDORS_PATH),
        help="Path to vendors.csv (default: data/vendors.csv)",
    )
    parser.add_argument(
        "--conference",
        default="",
        help="Only process rows for this conference (default: all conferences)",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Fetch team pages and classify using live HTML signals",
    )
    parser.add_argument(
        "--apply-known-overrides",
        action="store_true",
        help="Apply baked-in URL overrides for known pilot conferences (currently Big Ten)",
    )
    args = parser.parse_args()

    vendors_path = Path(args.vendors)
    if not vendors_path.exists():
        raise FileNotFoundError(f"vendors.csv not found: {vendors_path}")

    with vendors_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []

    now_utc = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    updated = 0
    for row in rows:
        conference = (row.get("conference") or "").strip()
        if args.conference and conference != args.conference:
            continue
        if args.apply_known_overrides:
            _update_big_ten_urls(row)
        url = (row.get("team_url") or "").strip()

        html = ""
        if args.probe and url:
            try:
                html = _fetch_html(url)
                row["notes"] = "live probe ok"
            except URLError as exc:
                row["notes"] = f"probe_error: {exc.reason}"
            except Exception as exc:  # pragma: no cover - defensive catch for one-off ops
                row["notes"] = f"probe_error: {type(exc).__name__}"

        vendor, confidence, signal_note = _classify_vendor(url=url, html=html, used_probe=args.probe)
        row["vendor"] = vendor
        row["vendor_confidence"] = confidence
        row["last_verified_utc"] = now_utc if args.probe else row.get("last_verified_utc", "")
        if row.get("notes", "").startswith("probe_error:"):
            pass
        elif args.probe:
            row["notes"] = f"live probe ok; {signal_note}"
        else:
            row["notes"] = signal_note
        updated += 1

    with vendors_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated rows: {updated}")
    print(f"Conference: {args.conference or 'ALL'}")
    print(f"Applied known overrides: {args.apply_known_overrides}")
    print(f"Probe mode: {args.probe}")
    print(f"Vendors file: {vendors_path}")


if __name__ == "__main__":
    main()
