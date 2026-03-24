"""Seed data/vendors.csv from processed team stats.

This script builds a deterministic, idempotent vendor registry baseline for MSDIE
Step 1. It deduplicates programs by team name + division, keeps latest-year org
snapshots, and initializes vendor metadata defaults.

When --merge-existing is used, verified URL/vendor metadata is preserved for
matching program keys (division + team name).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_INPUT_CANDIDATES = [
    PROJECT_ROOT / "data" / "processed" / "team" / "team_stats_with_sos_full_synced.csv",
    PROJECT_ROOT / "data" / "processed" / "team" / "team_stats_with_sos_full.csv",
    PROJECT_ROOT / "data" / "processed" / "team" / "team_stats_with_sos.csv",
]
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "vendors.csv"

FIELDNAMES = [
    "org_id",
    "team_name",
    "conference",
    "division",
    "vendor",
    "conference_url",
    "team_url",
    "vendor_confidence",
    "last_verified_utc",
    "notes",
]


def _choose_input_path(explicit_input: str | None) -> Path:
    if explicit_input:
        path = Path(explicit_input)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        return path
    for candidate in DEFAULT_INPUT_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No default input file found in data/processed/team/")


def _normalize_division(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"1", "d1", "division 1", "division i"}:
        return "D1"
    if raw in {"2", "d2", "division 2", "division ii"}:
        return "D2"
    return raw.upper() if raw else ""


def _safe_int(value: str | None) -> int:
    try:
        return int(float((value or "").strip()))
    except ValueError:
        return -1


def _program_key(division: str, team_name: str) -> str:
    return f"{division}:{team_name.strip().lower()}"


def _dedupe_rows(rows: Iterable[dict[str, str]], target_divisions: set[str]) -> list[dict[str, str]]:
    by_key: dict[str, dict[str, str]] = {}
    for row in rows:
        division = _normalize_division(row.get("division"))
        if division not in target_divisions:
            continue

        org_id = (row.get("org_id") or "").strip()
        team_name = (row.get("team_name") or "").strip()
        conference = (row.get("conference") or "").strip()
        year = _safe_int(row.get("academic_year"))

        if not team_name:
            continue

        # org_id changes across seasons in some source files, so program-level
        # dedupe should be team-based and keep the latest-year org_id snapshot.
        key = _program_key(division, team_name)
        current = by_key.get(key)
        if current is None or year > _safe_int(current.get("academic_year")):
            by_key[key] = {
                "org_id": org_id,
                "team_name": team_name,
                "conference": conference,
                "division": division,
                "academic_year": str(year) if year >= 0 else "",
            }

    output_rows: list[dict[str, str]] = []
    for item in by_key.values():
        output_rows.append(
            {
                "org_id": item["org_id"],
                "team_name": item["team_name"],
                "conference": item["conference"],
                "division": item["division"],
                "vendor": "unknown",
                "conference_url": "",
                "team_url": "",
                "vendor_confidence": "low",
                "last_verified_utc": "",
                "notes": "",
            }
        )

    output_rows.sort(
        key=lambda r: (
            r["division"].lower(),
            r["conference"].lower(),
            r["team_name"].lower(),
            r["org_id"],
        )
    )
    return output_rows


def _merge_existing(seeded_rows: list[dict[str, str]], existing_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_key: dict[str, dict[str, str]] = {}
    for row in existing_rows:
        division = _normalize_division(row.get("division"))
        team_name = (row.get("team_name") or "").strip()
        if not division or not team_name:
            continue
        by_key[_program_key(division, team_name)] = row

    merged: list[dict[str, str]] = []
    preserve_fields = [
        "vendor",
        "conference_url",
        "team_url",
        "vendor_confidence",
        "last_verified_utc",
        "notes",
    ]
    for row in seeded_rows:
        key = _program_key(row["division"], row["team_name"])
        existing = by_key.get(key)
        if existing:
            for field in preserve_fields:
                val = (existing.get(field) or "").strip()
                if val:
                    row[field] = val
        merged.append(row)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed data/vendors.csv from processed team stats")
    parser.add_argument(
        "--input",
        default=None,
        help="Optional input CSV path (defaults to first existing processed team stats file)",
    )
    parser.add_argument(
        "--out",
        default=str(DEFAULT_OUTPUT),
        help="Output CSV path (default: data/vendors.csv)",
    )
    parser.add_argument(
        "--division",
        default="ALL",
        choices=["D1", "D2", "ALL"],
        help="Division scope to seed (default: ALL)",
    )
    parser.add_argument(
        "--merge-existing",
        default=str(DEFAULT_OUTPUT),
        help="Optional existing vendors.csv path to preserve known metadata (default: data/vendors.csv)",
    )
    args = parser.parse_args()

    input_path = _choose_input_path(args.input)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))

    target_divisions = {"D1", "D2"} if args.division == "ALL" else {args.division}
    seeded_rows = _dedupe_rows(rows, target_divisions=target_divisions)
    existing_path = Path(args.merge_existing) if args.merge_existing else None
    if existing_path and existing_path.exists():
        with existing_path.open("r", encoding="utf-8", newline="") as fh:
            existing_rows = list(csv.DictReader(fh))
        seeded_rows = _merge_existing(seeded_rows=seeded_rows, existing_rows=existing_rows)

    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(seeded_rows)

    print(f"Input: {input_path}")
    print(f"Output: {out_path}")
    print(f"Division: {args.division}")
    print(f"Merged existing metadata: {'yes' if existing_path and existing_path.exists() else 'no'}")
    print(f"Rows written: {len(seeded_rows):,}")


if __name__ == "__main__":
    main()
