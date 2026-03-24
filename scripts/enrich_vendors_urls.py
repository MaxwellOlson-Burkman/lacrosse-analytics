"""Enrich vendors.csv with conference and team URLs.

Features:
- Fill `conference_url` from data/conference_urls.yaml.
- Probe candidate team URLs derived from data/team_url_hints.yaml.
- Preserve existing non-empty URLs by default (idempotent behavior).
- Emit an audit CSV for unresolved mappings or failed probes.

YAML shape:
  data/conference_urls.yaml
    conferences:
      "Big Ten": "https://bigten.org/sports/mens-lacrosse"

  data/team_url_hints.yaml
    by_org_id:
      "593943": { base_host: "goduke.com" }  # or { url: "https://..." }
    by_team_name:
      "Duke": { base_host: "goduke.com" }    # or { url: "https://..." }
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.seed_vendors_csv import FIELDNAMES

DEFAULT_VENDORS = PROJECT_ROOT / "data" / "vendors.csv"
DEFAULT_CONFERENCE_YAML = PROJECT_ROOT / "data" / "conference_urls.yaml"
DEFAULT_TEAM_HINTS_YAML = PROJECT_ROOT / "data" / "team_url_hints.yaml"
DEFAULT_AUDIT = PROJECT_ROOT / "data" / "audit" / "vendors_url_enrichment_failures.csv"

USER_AGENT = "Mozilla/5.0 (compatible; MSDIE/1.0; +https://example.local)"


@dataclass
class ProbeResult:
    ok: bool
    final_url: str
    detail: str


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        return {}
    return loaded


def _normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.netloc:
        return ""
    path = re.sub(r"/+$", "", parsed.path or "")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), path, "", "", ""))


def _host_only(host: str) -> str:
    host = (host or "").strip().lower()
    if not host:
        return ""
    host = host.replace("https://", "").replace("http://", "").split("/")[0]
    return host


def _candidate_urls_from_hint(hint: dict) -> list[str]:
    candidates: list[str] = []
    explicit = _normalize_url(str(hint.get("url", "")))
    if explicit:
        candidates.append(explicit)

    host = _host_only(str(hint.get("base_host", "")))
    if host:
        hosts = [host]
        if not host.startswith("www."):
            hosts.append(f"www.{host}")
        paths = [
            "/sports/mens-lacrosse",
            "/sports/mlax",
            "/sports/m-lacrosse",
            "/sports/men-s-lacrosse",
        ]
        for h in hosts:
            for p in paths:
                candidates.append(f"https://{h}{p}")
    # de-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        norm = _normalize_url(c)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _lacrosse_page_heuristic(url: str, html: str) -> bool:
    text = (html or "").lower()
    u = (url or "").lower()
    token_hits = any(t in text or t in u for t in ["mens-lacrosse", "men's lacrosse", "mlax"])
    lacrosse_hit = "lacrosse" in text or "lacrosse" in u
    context_hits = sum(
        1 for t in ["roster", "schedule", "stats", "box score", "sidearm", "prestosports"] if t in text
    )
    return bool((token_hits and context_hits >= 1) or (lacrosse_hit and context_hits >= 2))


def _probe_candidate(url: str, timeout_seconds: int = 15) -> ProbeResult:
    try:
        resp = requests.get(
            url,
            timeout=timeout_seconds,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        return ProbeResult(False, "", f"request_error:{type(exc).__name__}")

    content_type = (resp.headers.get("Content-Type") or "").lower()
    if resp.status_code != 200:
        return ProbeResult(False, "", f"http_{resp.status_code}")
    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        return ProbeResult(False, "", f"non_html:{content_type}")
    if not _lacrosse_page_heuristic(resp.url, resp.text):
        return ProbeResult(False, "", "heuristic_reject")
    return ProbeResult(True, _normalize_url(resp.url), "ok")


def _conference_template(rows: list[dict[str, str]]) -> str:
    conferences = sorted({(r.get("conference") or "").strip() for r in rows if (r.get("conference") or "").strip()})
    lines = ["conferences:"]
    for conf in conferences:
        lines.append(f'  "{conf}": ""')
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich vendors URLs from YAML mappings and URL probes")
    parser.add_argument("--vendors", default=str(DEFAULT_VENDORS), help="Path to vendors.csv")
    parser.add_argument(
        "--conference-yaml",
        default=str(DEFAULT_CONFERENCE_YAML),
        help="Path to conference_urls.yaml",
    )
    parser.add_argument(
        "--team-hints-yaml",
        default=str(DEFAULT_TEAM_HINTS_YAML),
        help="Path to team_url_hints.yaml (optional)",
    )
    parser.add_argument("--out", default="", help="Output vendors CSV (default: in-place)")
    parser.add_argument("--audit-out", default=str(DEFAULT_AUDIT), help="Audit CSV output path")
    parser.add_argument("--division", choices=["D1", "D2"], default="", help="Optional division filter")
    parser.add_argument("--dry-run", action="store_true", help="Do not write vendors CSV")
    parser.add_argument("--skip-conference", action="store_true", help="Skip conference URL filling")
    parser.add_argument("--force-conference", action="store_true", help="Overwrite existing conference_url values")
    parser.add_argument("--force-team", action="store_true", help="Re-probe and overwrite existing team_url values")
    parser.add_argument("--sleep-seconds", type=float, default=0.15, help="Delay between team probes")
    parser.add_argument(
        "--print-conference-template",
        action="store_true",
        help="Print conference YAML skeleton from vendors.csv and exit",
    )
    args = parser.parse_args()

    vendors_path = Path(args.vendors)
    if not vendors_path.exists():
        raise FileNotFoundError(f"vendors.csv not found: {vendors_path}")

    out_path = Path(args.out) if args.out else vendors_path
    audit_path = Path(args.audit_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)

    with vendors_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    if args.print_conference_template:
        print(_conference_template(rows))
        return

    conf_yaml = _read_yaml(Path(args.conference_yaml))
    conf_map = conf_yaml.get("conferences", {})
    if not isinstance(conf_map, dict):
        conf_map = {}

    hint_yaml = _read_yaml(Path(args.team_hints_yaml))
    by_org_id = hint_yaml.get("by_org_id", {}) if isinstance(hint_yaml.get("by_org_id", {}), dict) else {}
    by_team_name = (
        hint_yaml.get("by_team_name", {}) if isinstance(hint_yaml.get("by_team_name", {}), dict) else {}
    )

    audit_rows: list[dict[str, str]] = []
    conference_updates = 0
    team_updates = 0
    probes_attempted = 0

    for idx, row in enumerate(rows):
        division = (row.get("division") or "").strip().upper()
        if args.division and division != args.division:
            continue

        conference = (row.get("conference") or "").strip()
        org_id = (row.get("org_id") or "").strip()
        team_name = (row.get("team_name") or "").strip()

        if not args.skip_conference:
            current_conf = (row.get("conference_url") or "").strip()
            mapped_conf = _normalize_url(str(conf_map.get(conference, "")))
            if mapped_conf and (args.force_conference or not current_conf):
                row["conference_url"] = mapped_conf
                conference_updates += 1
            elif not mapped_conf:
                audit_rows.append(
                    {
                        "org_id": org_id,
                        "team_name": team_name,
                        "conference": conference,
                        "division": division,
                        "reason": "missing_conference_mapping",
                        "detail": f"conference {conference!r} not found in conference YAML",
                        "candidates_tried": "",
                    }
                )

        current_team = (row.get("team_url") or "").strip()
        if current_team and not args.force_team:
            continue

        hint = {}
        if org_id and str(org_id) in by_org_id and isinstance(by_org_id[str(org_id)], dict):
            hint = dict(by_org_id[str(org_id)])
        elif team_name in by_team_name and isinstance(by_team_name[team_name], dict):
            hint = dict(by_team_name[team_name])

        candidates = _candidate_urls_from_hint(hint) if hint else []
        if not candidates:
            audit_rows.append(
                {
                    "org_id": org_id,
                    "team_name": team_name,
                    "conference": conference,
                    "division": division,
                    "reason": "no_team_hints",
                    "detail": "no by_org_id/by_team_name hint found",
                    "candidates_tried": "",
                }
            )
            continue

        chosen = ""
        last_detail = "all_candidates_failed"
        for cidx, candidate in enumerate(candidates):
            result = _probe_candidate(candidate)
            probes_attempted += 1
            if result.ok:
                chosen = result.final_url or candidate
                last_detail = "ok"
                break
            last_detail = result.detail
            if args.sleep_seconds > 0 and cidx < len(candidates) - 1:
                time.sleep(args.sleep_seconds)

        if chosen:
            row["team_url"] = chosen
            team_updates += 1
        else:
            preview = "|".join(candidates[:8])
            audit_rows.append(
                {
                    "org_id": org_id,
                    "team_name": team_name,
                    "conference": conference,
                    "division": division,
                    "reason": "team_probe_failed",
                    "detail": last_detail,
                    "candidates_tried": preview,
                }
            )

        if args.sleep_seconds > 0 and idx < len(rows) - 1:
            time.sleep(args.sleep_seconds)

    if not args.dry_run:
        with out_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

    audit_fields = ["org_id", "team_name", "conference", "division", "reason", "detail", "candidates_tried"]
    with audit_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=audit_fields)
        writer.writeheader()
        writer.writerows(audit_rows)

    print(f"vendors_in: {vendors_path}")
    print(f"vendors_out: {out_path}")
    print(f"dry_run: {args.dry_run}")
    print(f"conference_updates: {conference_updates}")
    print(f"team_updates: {team_updates}")
    print(f"probes_attempted: {probes_attempted}")
    print(f"audit_rows: {len(audit_rows)}")
    print(f"audit_out: {audit_path}")


if __name__ == "__main__":
    main()

