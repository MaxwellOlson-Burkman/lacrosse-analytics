"""Dashboard views: index (three-panel), chat HTMX partials."""
from __future__ import annotations

import json
import re
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import redirect, render
import pandas as pd

from .rag_helpers import (
    chroma_index_exists,
    get_report_content,
    get_team_choices,
    get_team_index,
    load_rag_config,
)

MAX_CHAT_MESSAGES = 40
MAX_RECENT_STEMS = 10

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _cap_chat_messages(request):
    messages = request.session.get("chat_messages", [])
    if len(messages) > MAX_CHAT_MESSAGES:
        request.session["chat_messages"] = messages[-MAX_CHAT_MESSAGES:]


def _get_mode2_extra_context(question: str, request, config: dict) -> str:
    """Extension point: return extra context for Mode 2 (predictions/current-season). Stub returns empty."""
    q_lower = question.lower()
    if any(
        phrase in q_lower
        for phrase in (
            "predict", "prediction", "current season", "this season", "this year",
            "get current stats", "acquire current", "fetch current", "current data",
        )
    ):
        return "Note: Predictions and current-season data acquisition are coming soon. Answers below use only the existing team report index."
    return ""


def _run_rag_query(request, question: str, *, mode: int = 1):
    """Run RAG query, append user+assistant to session. mode: 1 = Chat, 2 = Team & stats (RAG+)."""
    config = load_rag_config()
    msgs = request.session.setdefault("chat_messages", [])
    msgs.append({"role": "user", "content": question})

    if not chroma_index_exists(config):
        msgs.append({
            "role": "assistant",
            "content": "RAG index is not available. Build it with: python scripts/build_rag_index.py",
            "sources": [],
        })
        _cap_chat_messages(request)
        request.session.modified = True
        return

    chroma_path = str(Path(settings.BASE_DIR) / config.get("chroma_persist_dir", "data/chroma"))

    try:
        from src.rag.chain import query
    except ImportError:
        msgs.append({"role": "assistant", "content": "RAG module not available.", "sources": []})
        _cap_chat_messages(request)
        request.session.modified = True
        return

    reports_dir = Path(settings.BASE_DIR) / config.get("reports_dir", "models/team_reports")
    team_aliases_path = Path(settings.BASE_DIR) / config.get("team_aliases_path", "config/team_aliases.yaml")

    extra_context = _get_mode2_extra_context(question, request, config) if mode == 2 else ""

    try:
        answer, docs = query(
            question,
            chroma_path,
            embedding_model=config.get("embedding_model", "nomic-embed-text"),
            llm_model=config.get("llm_model", "llama3.2"),
            collection_name=config.get("collection_name", "lacrosse_team_reports"),
            k=config.get("retriever_k", 5),
            return_sources=True,
            reports_dir=reports_dir,
            team_aliases_path=team_aliases_path,
            extra_context=extra_context or None,
        )
    except Exception as e:
        err_msg = str(e)
        if "tenant" in err_msg.lower() or "bindings" in err_msg.lower():
            err_msg = (
                "The RAG index could not be loaded (ChromaDB version/format issue). "
                "Try rebuilding: delete data/chroma and run python scripts/build_rag_index.py"
            )
        msgs.append({"role": "assistant", "content": f"Error: {err_msg}", "sources": []})
        _cap_chat_messages(request)
        request.session.modified = True
        return

    sources = []
    for d in docs:
        meta = getattr(d, "metadata", {}) or {}
        source = meta.get("source", "")
        snippet = (getattr(d, "page_content", "") or "")[:300]
        sources.append({"source": source, "snippet": snippet})

    msgs.append({"role": "assistant", "content": answer, "sources": sources})
    _cap_chat_messages(request)
    request.session.modified = True


def _humanize_stat_key(key: str) -> str:
    """Turn snake_case stat key into title-style label."""
    key = key.strip()
    if not key:
        return key
    # Known mappings
    known = {
        "win % (from schedule)": "Win % (from schedule)",
        "caused_turnovers_per_game": "Caused turnovers per game",
        "man_up_offense": "Man-up offense",
        "scoring_offense": "Scoring offense",
        "turnovers_per_game": "Turnovers per game",
        "offensive_efficiency": "Offensive efficiency",
        "saves_per_game": "Saves per game",
        "defensive_efficiency": "Defensive efficiency",
        "points_per_game": "Points per game",
        "scoring_defense": "Scoring defense",
        "clearing_percentage": "Clearing %",
        "ground_balls_per_game": "Ground balls per game",
        "possession_value_index": "Possession value index",
    }
    key_lower = key.lower().replace("_", " ")
    for k, v in known.items():
        if k.replace("_", " ") == key_lower or k == key.lower():
            return v
    return key.replace("_", " ").title()


def parse_team_report_tables(report_content: str):
    """Parse report text into summary, stat comparison, and SOS for table display.

    Adds semantic fields to stat comparison rows:
    - key: raw stat key from the report
    - delta: signed difference vs league avg (positive = above, negative = below)
    - abs_delta: absolute difference
    - direction: "above" | "below" | "equal"
    - good_bad: "good" | "bad" | "neutral" based on stat semantics.
    """
    if not report_content:
        return None
    lines = report_content.strip().splitlines()
    summary = []
    stat_comparison = []
    sos = []

    # Stat semantics: whether higher or lower values are better.
    stat_semantics = {
        "turnovers_per_game": "lower",
        "defensive_efficiency": "lower",
        "scoring_defense": "lower",
        # Generally higher is better for these:
        "caused_turnovers_per_game": "higher",
        "man_up_offense": "higher",
        "scoring_offense": "higher",
        "offensive_efficiency": "higher",
        "saves_per_game": "higher",
        "points_per_game": "higher",
        "clearing_percentage": "higher",
        "ground_balls_per_game": "higher",
        "possession_value_index": "higher",
        "win % (from schedule)": "higher",
    }

    phase = None  # "header" | "stat" | "sos"
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Team:"):
            summary.append(("Team", stripped.split(":", 1)[1].strip()))
        elif stripped.startswith("Season:"):
            summary.append(("Season", stripped.split(":", 1)[1].strip()))
        elif stripped.startswith("Record:"):
            summary.append(("Record", stripped.split(":", 1)[1].strip()))
        elif stripped.startswith("Winning %:"):
            raw = stripped.split(":", 1)[1].strip()
            try:
                raw = f"{float(raw) * 100:.1f}%"
            except (ValueError, TypeError):
                pass
            summary.append(("Winning %", raw))
        elif stripped.startswith("Predicted Winning %:"):
            raw = stripped.split(":", 1)[1].strip()
            try:
                raw = f"{float(raw) * 100:.1f}%"
            except (ValueError, TypeError):
                pass
            summary.append(("Predicted Winning %", raw))
        elif "Stat Comparison vs League Average" in stripped:
            phase = "stat"
            continue
        elif stripped.lower().startswith("strength of schedule"):
            phase = "sos"
            continue
        elif phase == "stat" and ":" in stripped:
            part = stripped.split(":", 1)
            key_raw = part[0].strip()
            rest = part[1].strip()
            value = ""
            vs_avg = ""
            m = re.match(r"^([\d.]+)\s*(\(.+\))?$", rest)
            if m:
                value = m.group(1)
                if m.group(2):
                    vs_avg = m.group(2).strip(" ()")
            else:
                value = rest
            # Parse vs_avg like "0.932 above average" / "0.932 below average"
            direction = "equal"
            delta = 0.0
            abs_delta = 0.0
            if vs_avg:
                text = vs_avg.strip()
                # grab leading numeric magnitude
                m2 = re.match(r"^([+-]?[0-9]*\.?[0-9]+)", text)
                if m2:
                    mag = float(m2.group(1))
                    lower = text.lower()
                    if "above" in lower:
                        direction = "above"
                        delta = mag
                        abs_delta = mag
                    elif "below" in lower:
                        direction = "below"
                        delta = -mag
                        abs_delta = mag
            # Determine good/bad based on semantics and direction
            semantics_key = key_raw
            sem = stat_semantics.get(semantics_key, "higher")
            if direction == "equal" or abs_delta == 0.0:
                good_bad = "neutral"
            elif sem == "higher":
                good_bad = "good" if direction == "above" else "bad"
            elif sem == "lower":
                good_bad = "good" if direction == "below" else "bad"
            else:
                good_bad = "neutral"

            label = _humanize_stat_key(key_raw)
            stat_comparison.append(
                {
                    "key": key_raw,
                    "stat": label,
                    "value": value,
                    "vs_average": vs_avg,
                    "delta": delta,
                    "abs_delta": abs_delta,
                    "direction": direction,
                    "good_bad": good_bad,
                }
            )
        elif phase == "sos" and ":" in stripped:
            key = stripped.split(":", 1)[0].strip()
            val = stripped.split(":", 1)[1].strip()
            sos.append((key, val))

    if not summary:
        return None
    return {"summary": summary, "stat_comparison": stat_comparison, "sos": sos}


def parse_team_report_kpis(report_content: str):
    """Parse a team report text into KPI dict and radar/chart data. Returns None on failure."""
    if not report_content:
        return None
    lines = report_content.strip().splitlines()
    kpis = {}

    for line in lines:
        line = line.strip()
        if line.startswith("Record:"):
            kpis["record"] = line.split(":", 1)[1].strip()
        elif line.startswith("Winning %:"):
            raw = line.split(":", 1)[1].strip()
            try:
                kpis["win_pct"] = f"{float(raw) * 100:.1f}%"
            except (ValueError, TypeError):
                kpis["win_pct"] = raw
        elif line.startswith("Predicted Winning %:"):
            raw = line.split(":", 1)[1].strip()
            try:
                kpis["predicted_win_pct"] = f"{float(raw) * 100:.1f}%"
            except (ValueError, TypeError):
                kpis["predicted_win_pct"] = raw
        elif line.startswith("Stats-based SOS rank:") or line.startswith("Model schedule difficulty rank:"):
            kpis["sos_rank"] = line.split(":", 1)[1].strip()

    if not kpis.get("record"):
        return None

    # Radar: subset of stats with fixed scales
    stat_keys_radar = {
        "scoring_offense": "Scoring Offense",
        "scoring_defense": "Scoring Defense",
        "clearing_percentage": "Clearing %",
        "ground_balls_per_game": "Ground Balls",
        "possession_value_index": "Possession Value",
    }
    scales = {
        "scoring_offense": (5, 20),
        "scoring_defense": (5, 20),
        "clearing_percentage": (0.6, 1.0),
        "ground_balls_per_game": (15, 50),
        "possession_value_index": (0.05, 0.25),
    }
    raw_stats = {}
    for line in lines:
        line_stripped = line.strip()
        for key in list(stat_keys_radar.keys()) + [
            "caused_turnovers_per_game", "man_up_offense", "turnovers_per_game",
            "offensive_efficiency", "saves_per_game", "defensive_efficiency",
            "points_per_game",
        ]:
            if line_stripped.startswith(f"{key}:") or (key == "win % (from schedule)" and line_stripped.lower().startswith("win % (from schedule):")):
                m = re.search(r":\s*([\d.]+)", line_stripped)
                if m:
                    raw_stats[key] = float(m.group(1))
                break
        else:
            if line_stripped.lower().startswith("win % (from schedule):"):
                m = re.search(r":\s*([\d.]+)", line_stripped)
                if m:
                    raw_stats["win % (from schedule)"] = float(m.group(1))

    radar_labels = []
    radar_values = []
    for key, label in stat_keys_radar.items():
        radar_labels.append(label)
        val = raw_stats.get(key)
        if val is not None:
            lo, hi = scales[key]
            normalized = max(0, min(100, (val - lo) / (hi - lo) * 100))
            radar_values.append(round(normalized, 1))
        else:
            radar_values.append(None)
    kpis["radar_labels"] = radar_labels
    kpis["radar_values"] = radar_values

    # All stats for bar/line charts: order matching report Stat Comparison section
    chart_order = [
        "win % (from schedule)", "caused_turnovers_per_game", "man_up_offense", "scoring_offense",
        "turnovers_per_game", "offensive_efficiency", "saves_per_game", "defensive_efficiency",
        "points_per_game", "scoring_defense", "clearing_percentage", "ground_balls_per_game",
        "possession_value_index",
    ]
    chart_labels = []
    chart_values = []
    for key in chart_order:
        val = raw_stats.get(key)
        if val is not None:
            chart_labels.append(_humanize_stat_key(key))
            chart_values.append(round(val, 3))
    # Fallback: add any keys found in raw_stats not in chart_order
    for key, val in raw_stats.items():
        if key in chart_order:
            continue
        chart_labels.append(_humanize_stat_key(key))
        chart_values.append(round(val, 3))
    kpis["chart_labels"] = chart_labels
    kpis["chart_values"] = chart_values

    return kpis


def _push_recent_stem(request, stem: str, label: str):
    """Append {stem, label} to session recent_stems, cap at MAX_RECENT_STEMS."""
    recent = request.session.get("recent_stems", [])
    recent = [r for r in recent if r.get("stem") != stem]
    recent.insert(0, {"stem": stem, "label": label})
    request.session["recent_stems"] = recent[:MAX_RECENT_STEMS]
    request.session.modified = True


def _label_for_stem(stem: str, team_choices):
    for label, value in team_choices:
        if value == stem:
            return label
    return stem


# ──────────────────────────────────────────────
# Views
# ──────────────────────────────────────────────

def favicon_view(request):
    return HttpResponse(status=204)


def index_view(request):
    if request.method == "POST":
        return redirect("dashboard:index")

    team_choices = get_team_choices()
    team_index = get_team_index()

    # Unified picker: division → year → team
    div_raw = request.GET.get("division", "1").strip()
    picker_division = int(div_raw) if div_raw in ("1", "2") else 1

    picker_years = sorted(
        {r["year"] for r in team_index if r["division"] == picker_division},
        reverse=True,
    )

    year_raw = request.GET.get("year", "").strip()
    picker_year = int(year_raw) if year_raw.isdigit() else (picker_years[0] if picker_years else None)

    picker_teams = [
        (r["name"], r["stem"])
        for r in team_index
        if r["division"] == picker_division and (picker_year is None or r["year"] == picker_year)
    ]

    # Team selection
    stem = request.GET.get("stem", "").strip()
    report_content = get_report_content(stem) if stem else None
    kpis = parse_team_report_kpis(report_content) if report_content else None
    report_tables = parse_team_report_tables(report_content) if report_content else None

    selected_stem_label = ""
    if stem:
        selected_stem_label = _label_for_stem(stem, team_choices)
        _push_recent_stem(request, stem, selected_stem_label)
        # Keep current selection in dropdown when switching year/division so it doesn't revert to "Select team"
        if not any(s == stem for _, s in picker_teams):
            picker_teams = list(picker_teams) + [(selected_stem_label, stem)]

    config = load_rag_config()
    chroma_ok = chroma_index_exists(config)
    chat_messages = request.session.get("chat_messages", [])
    recent_stems = request.session.get("recent_stems", [])

    # Chart data as JSON for Chart.js (radar + bar/line)
    radar_labels = json.dumps(kpis["radar_labels"]) if kpis else None
    radar_values = json.dumps(kpis["radar_values"]) if kpis else None
    chart_labels = json.dumps(kpis["chart_labels"]) if kpis else None
    chart_values = json.dumps(kpis["chart_values"]) if kpis else None

    return render(
        request,
        "dashboard/index.html",
        {
            "active_page": "index",
            "team_choices": team_choices,
            "selected_stem": stem,
            "selected_stem_label": selected_stem_label,
            "report_content": report_content,
            "report_tables": report_tables,
            "kpis": kpis,
            "radar_labels": radar_labels,
            "radar_values": radar_values,
            "chart_labels": chart_labels,
            "chart_values": chart_values,
            "chroma_ok": chroma_ok,
            "chat_messages": chat_messages,
            "recent_stems": recent_stems,
            "picker_division": picker_division,
            "picker_year": picker_year,
            "picker_years": picker_years,
            "picker_teams": picker_teams,
        },
    )


def chat_page_view(request):
    """Full-page chat (two-column layout; main content is chat only)."""
    return render(request, "dashboard/chat.html", {"active_page": "chat"})


def chat_partial(request):
    """HTMX endpoint: POST question; when index exists always run RAG. use_rag selects mode (2 = Team & stats + future predictions)."""
    if request.method != "POST":
        return HttpResponse(status=405)
    question = (request.POST.get("question") or "").strip()
    use_rag_raw = (request.POST.get("use_rag") or "1").strip().lower()
    mode = 2 if use_rag_raw in ("1", "true", "yes") else 1
    config = load_rag_config()
    chroma_ok = chroma_index_exists(config)

    if question and chroma_ok:
        _run_rag_query(request, question, mode=mode)
    elif question and not chroma_ok:
        msgs = request.session.setdefault("chat_messages", [])
        msgs.append({"role": "user", "content": question})
        msgs.append({
            "role": "assistant",
            "content": "RAG index is not available. Build it with: python scripts/build_rag_index.py",
            "sources": [],
        })
        _cap_chat_messages(request)
        request.session.modified = True

    chat_messages = request.session.get("chat_messages", [])
    return render(request, "dashboard/partials/chat_messages.html", {"chat_messages": chat_messages})


def rankings_view(request):
    """Simple per-year/division stat rankings from team_stats_with_sos.csv."""
    year_raw = (request.GET.get("year") or "").strip()
    div_raw = (request.GET.get("division") or "").strip()
    stat = (request.GET.get("stat") or "winning_percentage").strip()

    try:
        year = int(year_raw)
    except Exception:
        year = 2024
    division = 1 if div_raw == "1" else 2 if div_raw == "2" else 1

    # Load data: prefer synced file when present (same source as conference rankings)
    team_dir = Path(settings.BASE_DIR) / "data" / "processed" / "team"
    synced = team_dir / "team_stats_with_sos_full_synced.csv"
    data_path = synced if synced.exists() else team_dir / "team_stats_with_sos.csv"
    df = pd.read_csv(data_path)

    allowed_stats = {
        "winning_percentage": ("Winning %", True),
        "predicted_winning_percentage": ("Predicted Winning %", True),  # computed from reports later (placeholder)
        "points_per_game": ("Points / Game", True),
        "scoring_offense": ("Scoring Offense", True),
        "scoring_defense": ("Scoring Defense (lower better)", False),
        "clearing_percentage": ("Clearing %", True),
        "face_off_winning_percentage": ("Faceoff Win %", True),
        "ground_balls_per_game": ("Ground Balls / Game", True),
        "rpi": ("RPI", True),
        "opp_wp": ("Opponent Win % (SOS)", True),
    }
    label, higher_is_better = allowed_stats.get(stat, ("Winning %", True))
    if stat not in df.columns:
        stat = "winning_percentage"
        label, higher_is_better = allowed_stats[stat]

    subset = df[(df["academic_year"] == year) & (df["division"] == division)].copy()
    subset = subset[["team_name", stat]].dropna()
    subset = subset.sort_values(by=stat, ascending=not higher_is_better).reset_index(drop=True)
    subset["rank"] = subset.index + 1
    # Template expects "value" for the stat column
    top = subset.head(50).copy()
    top = top.rename(columns={stat: "value"})
    top = top.to_dict(orient="records")

    # Options for dropdowns
    years = sorted(df["academic_year"].dropna().astype(int).unique().tolist(), reverse=True)
    stats = [{"key": k, "label": v[0]} for k, v in allowed_stats.items() if (k == "predicted_winning_percentage" or k in df.columns)]

    return render(
        request,
        "dashboard/rankings.html",
        {
            "active_page": "rankings",
            "year": year,
            "division": str(division),
            "stat": stat,
            "stat_label": label,
            "rows": top,
            "years": years,
            "stats": stats,
        },
    )


def conference_rankings_view(request):
    """Conference strength rankings per year/division from conference_rankings.csv."""
    year_raw = (request.GET.get("year") or "").strip()
    div_raw = (request.GET.get("division") or "").strip()

    try:
        year = int(year_raw)
    except Exception:
        year = 2024
    division = 1 if div_raw == "1" else 2 if div_raw == "2" else 1

    conf_path = Path(settings.BASE_DIR) / "data" / "processed" / "team" / "conference_rankings.csv"
    if not conf_path.exists():
        return render(
            request,
            "dashboard/conference_rankings.html",
            {
                "active_page": "conference_rankings",
                "file_missing": True,
                "rows": [],
                "years": [],
                "year": year,
                "division": str(division),
            },
        )

    df = pd.read_csv(conf_path)
    subset = df[
        (df["academic_year"] == year) & (df["division"] == division)
    ].sort_values("conference_rank").copy()
    subset["conference_strength"] = subset["conference_strength"].apply(
        lambda x: f"{float(x):.3f}"
    )
    rows = subset[["conference_rank", "conference", "conference_strength", "team_count"]].to_dict(
        orient="records"
    )
    # Rename keys for template (conference_rank -> rank)
    for r in rows:
        r["rank"] = r.pop("conference_rank")

    years = sorted(
        df["academic_year"].dropna().astype(int).unique().tolist(),
        reverse=True,
    )

    return render(
        request,
        "dashboard/conference_rankings.html",
        {
            "active_page": "conference_rankings",
            "file_missing": False,
            "rows": rows,
            "years": years,
            "year": year,
            "division": str(division),
        },
    )


def clear_chat_partial(request):
    """HTMX endpoint: clear chat, return empty chat fragment."""
    if request.method != "POST":
        return HttpResponse(status=405)
    request.session["chat_messages"] = []
    request.session.modified = True
    return render(request, "dashboard/partials/chat_messages.html", {"chat_messages": []})


def compare_view(request):
    """Side-by-side team comparison. GET stem_a, stem_b."""
    team_choices = get_team_choices()

    stem_a = (request.GET.get("stem_a") or "").strip()
    stem_b = (request.GET.get("stem_b") or "").strip()

    def build_side(stem: str):
        if not stem:
            return None
        content = get_report_content(stem)
        label = _label_for_stem(stem, team_choices)
        if not content:
            return {"stem": stem, "label": label, "missing": True}
        tables = parse_team_report_tables(content)
        kpis = parse_team_report_kpis(content)
        return {
            "stem": stem,
            "label": label,
            "missing": False,
            "report_tables": tables,
            "kpis": kpis,
            "radar_labels": json.dumps(kpis["radar_labels"]) if kpis else None,
            "radar_values": json.dumps(kpis["radar_values"]) if kpis else None,
            "chart_labels": json.dumps(kpis["chart_labels"]) if kpis else None,
            "chart_values": json.dumps(kpis["chart_values"]) if kpis else None,
        }

    side_a = build_side(stem_a)
    side_b = build_side(stem_b)

    return render(
        request,
        "dashboard/compare.html",
        {
            "active_page": "compare",
            "team_choices": team_choices,
            "stem_a": stem_a,
            "stem_b": stem_b,
            "side_a": side_a,
            "side_b": side_b,
        },
    )


# ──────────────────────────────────────────────
# Tewaaraton Watch
# ──────────────────────────────────────────────

def tewaaraton_view(request):
    """Tewaaraton Trophy estimator: dual-model Top 10."""
    year_raw = (request.GET.get("year") or "").strip()
    div_raw = (request.GET.get("division") or "").strip()

    try:
        year = int(year_raw)
    except Exception:
        year = 2024
    division = int(div_raw) if div_raw in ("1", "2") else 1

    team_dir = Path(settings.BASE_DIR) / "data" / "processed" / "team"
    sos_path = team_dir / "team_stats_with_sos.csv"
    years: list[int] = []
    if sos_path.exists():
        df_sos = pd.read_csv(sos_path)
        years = sorted(df_sos["academic_year"].dropna().astype(int).unique().tolist(), reverse=True)

    rows = []
    try:
        from src.estimators import build_tewaaraton_rankings
        df = build_tewaaraton_rankings(year, division)
        if not df.empty:
            rows = df.to_dict(orient="records")
    except Exception:
        pass

    return render(request, "dashboard/tewaaraton.html", {
        "active_page": "tewaaraton",
        "year": year,
        "division": str(division),
        "years": years,
        "rows": rows,
    })


# ──────────────────────────────────────────────
# Player Leaderboards
# ──────────────────────────────────────────────

def leaderboards_view(request):
    """Position-based player leaderboards with tabs."""
    from dashboard.models import Player

    year_raw = (request.GET.get("year") or "").strip()
    div_raw = (request.GET.get("division") or "").strip()
    tab = (request.GET.get("tab") or "offense").strip().lower()

    try:
        year = int(year_raw)
    except Exception:
        year = 2024
    division = int(div_raw) if div_raw in ("1", "2") else 1

    years = list(
        Player.objects.values_list("academic_year", flat=True)
        .distinct()
        .order_by("-academic_year")
    )
    if not years:
        years = [2024]

    rows = []
    columns: list[dict] = []
    try:
        from src.rankers import PositionRanker
        ranker = PositionRanker()
        if tab == "defense":
            df = ranker.rank_defense(year, division)
            columns = [
                {"key": "rank", "label": "#"}, {"key": "player_name", "label": "Player"},
                {"key": "team_name", "label": "Team"}, {"key": "position", "label": "Pos"},
                {"key": "games_played", "label": "GP"}, {"key": "caused_turnovers", "label": "CT"},
                {"key": "ground_balls", "label": "GB"}, {"key": "ct_pg", "label": "CT/G"},
                {"key": "gb_pg", "label": "GB/G"}, {"key": "def_score", "label": "Score"},
            ]
        elif tab == "goalies":
            df = ranker.rank_goalies(year, division)
            columns = [
                {"key": "rank", "label": "#"}, {"key": "player_name", "label": "Player"},
                {"key": "team_name", "label": "Team"},
                {"key": "games_played", "label": "GP"}, {"key": "saves", "label": "Sv"},
                {"key": "goals_allowed", "label": "GA"}, {"key": "save_pct", "label": "Sv%"},
                {"key": "gaa", "label": "GAA"},
            ]
        elif tab == "faceoff":
            df = ranker.rank_faceoff(year, division)
            columns = [
                {"key": "rank", "label": "#"}, {"key": "player_name", "label": "Player"},
                {"key": "team_name", "label": "Team"}, {"key": "position", "label": "Pos"},
                {"key": "games_played", "label": "GP"}, {"key": "faceoffs_won", "label": "FOW"},
                {"key": "faceoffs_lost", "label": "FOL"}, {"key": "fo_total", "label": "Total"},
                {"key": "fo_pct", "label": "FO%"},
            ]
        else:
            df = ranker.rank_offense(year, division)
            columns = [
                {"key": "rank", "label": "#"}, {"key": "player_name", "label": "Player"},
                {"key": "team_name", "label": "Team"}, {"key": "position", "label": "Pos"},
                {"key": "games_played", "label": "GP"}, {"key": "points", "label": "Pts"},
                {"key": "goals", "label": "G"}, {"key": "assists", "label": "A"},
                {"key": "ppg", "label": "PPG"}, {"key": "gpg", "label": "GPG"},
            ]
        if not df.empty:
            rows = df.to_dict(orient="records")
    except Exception:
        pass

    tabs = [
        {"key": "offense", "label": "Offense"},
        {"key": "defense", "label": "Defense"},
        {"key": "goalies", "label": "Goalies"},
        {"key": "faceoff", "label": "Faceoff"},
    ]

    return render(request, "dashboard/leaderboards.html", {
        "active_page": "leaderboards",
        "year": year,
        "division": str(division),
        "years": years,
        "tab": tab,
        "tabs": tabs,
        "columns": columns,
        "rows": rows,
    })


# ──────────────────────────────────────────────
# Compare Players
# ──────────────────────────────────────────────

def compare_players_view(request):
    """Side-by-side player comparison with radar charts, scoped by year + division."""
    from dashboard.models import Player, SeasonTotals

    player_a_id = (request.GET.get("player_a") or "").strip()
    player_b_id = (request.GET.get("player_b") or "").strip()

    # Year + division filter — shrinks search space from 2000 to ~hundreds per season
    year_raw = (request.GET.get("year") or "").strip()
    div_raw = (request.GET.get("division") or "").strip()
    division = int(div_raw) if div_raw in ("1", "2") else 1

    # Build years list from actual player data (only years that have players)
    available_years = list(
        Player.objects.values_list("academic_year", flat=True)
        .distinct()
        .order_by("-academic_year")
    )
    if not available_years:
        available_years = [2024]

    try:
        year = int(year_raw)
        if year not in available_years:
            year = available_years[0]
    except (ValueError, TypeError):
        year = available_years[0]

    # Filter players to the selected (year, division) only, cap at 500
    filtered_players = list(
        Player.objects.filter(academic_year=year, division=division)
        .values_list("id", "name", "team_name", "academic_year", "division")
        .order_by("name")[:500]
    )
    player_choices = [
        (f"{name} – {team} ({yr} D{div})", pid)
        for pid, name, team, yr, div in filtered_players
    ]

    radar_labels = json.dumps(["Goals/G", "Assists/G", "Points/G", "GB/G", "Shooting %"])

    def _build_side(pid_str: str):
        if not pid_str:
            return None
        try:
            pid = int(pid_str)
            player = Player.objects.get(id=pid)
        except (ValueError, Player.DoesNotExist):
            return None
        st = player.season_totals.first()
        if st is None:
            return {"player": player, "label": str(player), "missing": True}

        gp = max(st.games_played, 1)
        gpg = round(st.goals / gp, 2) if st.goals else 0
        apg = round(st.assists / gp, 2) if st.assists else 0
        ppg = round(st.points / gp, 2) if st.points else 0
        gbpg = round(st.ground_balls / gp, 2) if st.ground_balls else 0
        sh_pct = round(st.goals / max(st.shots, 1) * 100, 1) if st.shots else 0

        # Normalize to 0-100 for radar
        radar_values = json.dumps([
            min(100, gpg * 20),     # ~5 goals/game = 100
            min(100, apg * 25),     # ~4 assists/game = 100
            min(100, ppg * 12.5),   # ~8 points/game = 100
            min(100, gbpg * 15),    # ~6.7 GB/game = 100
            min(100, sh_pct),       # already percentage
        ])

        return {
            "player": player,
            "label": str(player),
            "missing": False,
            "stats": {
                "GP": gp, "G": st.goals, "A": st.assists, "Pts": st.points,
                "GB": st.ground_balls, "TO": st.turnovers, "CT": st.caused_turnovers,
                "GPG": gpg, "APG": apg, "PPG": ppg,
                "Sh%": f"{sh_pct}%",
            },
            "radar_values": radar_values,
        }

    side_a = _build_side(player_a_id)
    side_b = _build_side(player_b_id)

    return render(request, "dashboard/compare_players.html", {
        "active_page": "compare_players",
        "player_choices": player_choices,
        "player_a": player_a_id,
        "player_b": player_b_id,
        "side_a": side_a,
        "side_b": side_b,
        "radar_labels": radar_labels,
        "year": year,
        "division": str(division),
        "years": available_years,
    })


# ──────────────────────────────────────────────
# Player Lookup (individual stats page)
# ──────────────────────────────────────────────

MAX_RECENT_PLAYERS = 8


def player_lookup_view(request):
    """Player search page: pick year + division, then search for a player to view."""
    from dashboard.models import Player

    year_raw = (request.GET.get("year") or "").strip()
    div_raw = (request.GET.get("division") or "").strip()
    division = int(div_raw) if div_raw in ("1", "2") else 1

    available_years = list(
        Player.objects.values_list("academic_year", flat=True)
        .distinct()
        .order_by("-academic_year")
    )
    if not available_years:
        available_years = [2024]

    try:
        year = int(year_raw)
        if year not in available_years:
            year = available_years[0]
    except (ValueError, TypeError):
        year = available_years[0]

    filtered_players = list(
        Player.objects.filter(academic_year=year, division=division)
        .values_list("id", "name", "team_name", "academic_year", "division")
        .order_by("name")[:500]
    )
    player_choices = [
        (f"{name} – {team} ({yr} D{div})", pid)
        for pid, name, team, yr, div in filtered_players
    ]

    recent_players = request.session.get("recent_players", [])

    return render(request, "dashboard/player_lookup.html", {
        "active_page": "player_lookup",
        "player_choices": player_choices,
        "year": year,
        "division": str(division),
        "years": available_years,
        "recent_players": recent_players,
    })


def player_detail_view(request, player_id: int):
    """Individual player stats page with radar chart."""
    from dashboard.models import Player, SeasonTotals

    try:
        player = Player.objects.get(id=player_id)
    except Player.DoesNotExist:
        return render(request, "dashboard/player_detail.html", {
            "active_page": "player_lookup",
            "player": None,
        })

    st = player.season_totals.first()

    # Push to recent players in session
    recent = request.session.get("recent_players", [])
    entry = {"id": player.id, "label": str(player)}
    recent = [r for r in recent if r.get("id") != player.id]
    recent.insert(0, entry)
    request.session["recent_players"] = recent[:MAX_RECENT_PLAYERS]
    request.session.modified = True

    position = player.position.strip() if player.position and player.position.strip() else None

    if st is None:
        return render(request, "dashboard/player_detail.html", {
            "active_page": "player_lookup",
            "player": player,
            "position": position,
            "stats": None,
        })

    gp = max(st.games_played, 1)
    gpg = round(st.goals / gp, 2) if st.goals else 0
    apg = round(st.assists / gp, 2) if st.assists else 0
    ppg = round(st.points / gp, 2) if st.points else 0
    gbpg = round(st.ground_balls / gp, 2) if st.ground_balls else 0
    sh_pct = round(st.goals / max(st.shots, 1) * 100, 1) if st.shots else 0

    radar_labels = json.dumps(["Goals/G", "Assists/G", "Points/G", "GB/G", "Shooting %"])
    radar_values = json.dumps([
        min(100, gpg * 20),
        min(100, apg * 25),
        min(100, ppg * 12.5),
        min(100, gbpg * 15),
        min(100, sh_pct),
    ])

    stats = {
        "Games Played": st.games_played,
        "Games Started": st.games_started,
        "Goals": st.goals,
        "Assists": st.assists,
        "Points": st.points,
        "Shots": st.shots,
        "Shots on Goal": st.shots_on_goal,
        "Ground Balls": st.ground_balls,
        "Turnovers": st.turnovers,
        "Caused Turnovers": st.caused_turnovers,
        "Faceoffs Won": st.faceoffs_won,
        "Faceoffs Lost": st.faceoffs_lost,
    }
    if st.saves is not None:
        stats["Saves"] = st.saves
    if st.goals_allowed is not None:
        stats["Goals Allowed"] = st.goals_allowed

    kpis = {
        "GP": st.games_played,
        "G": st.goals,
        "A": st.assists,
        "Pts": st.points,
        "GB": st.ground_balls,
        "Sh%": f"{sh_pct}%",
    }
    per_game = {
        "GPG": gpg,
        "APG": apg,
        "PPG": ppg,
        "GB/G": gbpg,
    }

    return render(request, "dashboard/player_detail.html", {
        "active_page": "player_lookup",
        "player": player,
        "position": position,
        "stats": stats,
        "kpis": kpis,
        "per_game": per_game,
        "radar_labels": radar_labels,
        "radar_values": radar_values,
    })
