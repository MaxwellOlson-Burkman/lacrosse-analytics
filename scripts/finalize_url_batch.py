"""Finalize a URL batch: update playbook, metrics history, manual queue, and next queue."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def _bucket_error(error: str) -> str:
    e = (error or "").lower()
    if "404" in e:
        return "http_404"
    if "wmt offense total row not found" in e or "team totals rows not found" in e:
        return "wmt_total_row_missing"
    if "no team_url" in e or "missing_team_url" in e:
        return "missing_team_url"
    return "other"


def _manual_required(category: str) -> bool:
    return category in {"missing_team_url", "no_stats_endpoint_found", "hard_404"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize URL-discovery batch outputs.")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--season", default="2025")
    parser.add_argument("--division", default="D2")
    parser.add_argument("--teams-in-batch", default="10")
    parser.add_argument("--batch-results", required=True)
    parser.add_argument("--pilot-out", required=True)
    parser.add_argument("--pilot-failures", required=True)
    parser.add_argument("--playbook", default=str(PROJECT_ROOT / "data" / "audit" / "URL_BY_URL_D2_2025_PLAYBOOK.csv"))
    parser.add_argument("--impact-out", required=True)
    parser.add_argument("--history", default=str(PROJECT_ROOT / "data" / "audit" / "batch_impact_history.csv"))
    parser.add_argument("--next-out", required=True)
    parser.add_argument("--manual-queue-out", default=str(PROJECT_ROOT / "data" / "audit" / "manual_url_input_required.csv"))
    parser.add_argument("--next-size", type=int, default=10)
    parser.add_argument("--skip-hard-blockers", action="store_true")
    args = parser.parse_args()

    batch_results = _read_csv(Path(args.batch_results))
    pilot_rows = _read_csv(Path(args.pilot_out))
    pilot_fails = _read_csv(Path(args.pilot_failures))
    playbook = _read_csv(Path(args.playbook))
    history = _read_csv(Path(args.history))

    result_by_team = {(r.get("team_name") or "").strip(): r for r in batch_results}

    # Update playbook with latest fixed/blocked outcomes.
    for row in playbook:
        team = (row.get("team_name") or "").strip()
        result = result_by_team.get(team)
        if not result:
            continue
        status = (result.get("status") or "").strip().lower()
        if status == "fixed":
            row["stats_page_url"] = result.get("stats_page_url", "")
            row["source_type"] = result.get("source_type", "")
            row["route_method"] = result.get("route_method", "")
            row["route_confidence"] = result.get("route_confidence", "")
            row["hint_note"] = result.get("validation_note", "")
            row["status_2025_d2"] = "ok"
            row["next_action"] = "none"
            row["recipe_notes"] = f"{args.batch_id} verified required fields"
            row["last_error"] = ""
        else:
            row["status_2025_d2"] = "needs_work"
            row["next_action"] = "discover_stats_url"
            row["recipe_notes"] = result.get("blocked_reason", "")

    _write_csv(Path(args.playbook), playbook)

    # Compute impact.
    before = len(batch_results)
    after = len(pilot_fails)
    reduced = before - after
    buckets: dict[str, int] = {}
    for fail in pilot_fails:
        key = _bucket_error((fail.get("error") or "").strip())
        buckets[key] = buckets.get(key, 0) + 1
    remaining = ",".join(f"{k}:{v}" for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]))

    impact_lines = [
        f"Batch: {args.batch_id}",
        f"rows_gained={len(pilot_rows)}",
        f"failures_before={before}",
        f"failures_after={after}",
        f"failures_reduced={reduced}",
        f"remaining_buckets={remaining}",
    ]
    impact_path = Path(args.impact_out)
    impact_path.parent.mkdir(parents=True, exist_ok=True)
    impact_path.write_text("\n".join(impact_lines) + "\n", encoding="utf-8")

    # Update history with manual-required count.
    manual_count = 0
    for r in batch_results:
        category = (r.get("blocked_category") or "").strip()
        if category and _manual_required(category):
            manual_count += 1
    history.append(
        {
            "batch_id": args.batch_id,
            "season": str(args.season),
            "division": str(args.division),
            "teams_in_batch": str(args.teams_in_batch),
            "rows_gained": str(len(pilot_rows)),
            "failures_before": str(before),
            "failures_after": str(after),
            "failures_reduced": str(reduced),
            "remaining_top_buckets": remaining,
            "blocked_manual_required": str(manual_count),
        }
    )
    _write_csv(Path(args.history), history)

    # Manual queue from latest playbook.
    manual_rows: list[dict[str, str]] = []
    for row in playbook:
        if (row.get("status_2025_d2") or "").strip() != "needs_work":
            continue
        reason = (row.get("recipe_notes") or row.get("last_error") or "").strip()
        lower = reason.lower()
        category = "other"
        if "missing_team_url" in lower:
            category = "missing_team_url"
        elif "404" in lower:
            category = "hard_404"
        elif "no_valid_candidate" in lower or "no_verified_stats_url" in lower:
            category = "no_stats_endpoint_found"
        if _manual_required(category):
            manual_rows.append(
                {
                    "team_name": row.get("team_name", ""),
                    "team_url": row.get("team_url", ""),
                    "blocked_category": category,
                    "blocked_reason": reason,
                    "suggested_field_to_fill": "team_url" if category == "missing_team_url" else "stats_page_url",
                }
            )
    _write_csv(Path(args.manual_queue_out), manual_rows)

    # Build next queue; optionally skip hard blockers.
    candidates: list[dict[str, str]] = []
    for row in playbook:
        if (row.get("status_2025_d2") or "").strip() != "needs_work":
            continue
        if args.skip_hard_blockers:
            text = ((row.get("recipe_notes") or "") + " " + (row.get("last_error") or "")).lower()
            if "missing_team_url" in text or "404" in text:
                continue
        candidates.append(row)
    next_rows = candidates[: max(1, int(args.next_size))]
    _write_csv(Path(args.next_out), next_rows)

    print(f"rows_gained: {len(pilot_rows)}")
    print(f"failures_before: {before}")
    print(f"failures_after: {after}")
    print(f"failures_reduced: {reduced}")
    print(f"blocked_manual_required: {manual_count}")
    print(f"impact_out: {impact_path}")
    print(f"next_out: {Path(args.next_out)}")
    print(f"manual_queue_out: {Path(args.manual_queue_out)}")


if __name__ == "__main__":
    main()
