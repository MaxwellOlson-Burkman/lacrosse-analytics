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


def parse_team_report_kpis(report_content: str):
    """Parse a team report text into KPI dict and radar data. Returns None on failure."""
    if not report_content:
        return None
    lines = report_content.strip().splitlines()
    kpis = {}

    for line in lines:
        line = line.strip()
        if line.startswith("Record:"):
            kpis["record"] = line.split(":", 1)[1].strip()
        elif line.startswith("Winning %:"):
            kpis["win_pct"] = line.split(":", 1)[1].strip()
        elif line.startswith("Predicted Winning %:"):
            kpis["predicted_win_pct"] = line.split(":", 1)[1].strip()
        elif line.startswith("Stats-based SOS rank:") or line.startswith("Model schedule difficulty rank:"):
            kpis["sos_rank"] = line.split(":", 1)[1].strip()

    if not kpis.get("record"):
        return None

    # Radar: extract stat values from the "Stat Comparison" section
    stat_keys = {
        "scoring_offense": "Scoring Offense",
        "scoring_defense": "Scoring Defense",
        "clearing_percentage": "Clearing %",
        "ground_balls_per_game": "Ground Balls",
        "possession_value_index": "Possession Value",
    }
    raw_stats = {}
    for line in lines:
        line_stripped = line.strip()
        for key in stat_keys:
            if line_stripped.startswith(f"{key}:"):
                m = re.search(r":\s*([\d.]+)", line_stripped)
                if m:
                    raw_stats[key] = float(m.group(1))

    # Normalize to 0-100 with rough scales per stat
    scales = {
        "scoring_offense": (5, 20),
        "scoring_defense": (5, 20),
        "clearing_percentage": (0.6, 1.0),
        "ground_balls_per_game": (15, 50),
        "possession_value_index": (0.05, 0.25),
    }

    radar_labels = []
    radar_values = []
    for key, label in stat_keys.items():
        radar_labels.append(label)
        val = raw_stats.get(key)
        if val is not None:
            lo, hi = scales[key]
            normalized = max(0, min(100, (val - lo) / (hi - lo) * 100))
            radar_values.append(round(normalized, 1))
        else:
            # Missing stat should display as N/A in the UI (Chart.js uses null).
            radar_values.append(None)

    kpis["radar_labels"] = radar_labels
    kpis["radar_values"] = radar_values
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

    # Build per-division year/team lists for the left panel
    d1_years = sorted({r["year"] for r in team_index if r["division"] == 1}, reverse=True)
    d2_years = sorted({r["year"] for r in team_index if r["division"] == 2}, reverse=True)
    d1_year_sel = request.GET.get("d1_year", "").strip()
    d2_year_sel = request.GET.get("d2_year", "").strip()
    d1_year_sel_i = int(d1_year_sel) if d1_year_sel.isdigit() else (d1_years[0] if d1_years else None)
    d2_year_sel_i = int(d2_year_sel) if d2_year_sel.isdigit() else (d2_years[0] if d2_years else None)

    d1_teams = [
        (r["name"], r["stem"])
        for r in team_index
        if r["division"] == 1 and (d1_year_sel_i is None or r["year"] == d1_year_sel_i)
    ]
    d2_teams = [
        (r["name"], r["stem"])
        for r in team_index
        if r["division"] == 2 and (d2_year_sel_i is None or r["year"] == d2_year_sel_i)
    ]

    # Backwards-compatible division filter for the old TEAM-SEASON dropdown/search
    division = request.GET.get("division", "").strip()
    if division in ("1", "2"):
        filtered_choices = [(l, v) for l, v in team_choices if f"_D{division}_" in v]
    else:
        filtered_choices = team_choices
        division = ""

    # Team selection
    stem = request.GET.get("stem", "").strip()
    report_content = get_report_content(stem) if stem else None
    kpis = parse_team_report_kpis(report_content) if report_content else None

    selected_stem_label = ""
    if stem:
        selected_stem_label = _label_for_stem(stem, team_choices)
        _push_recent_stem(request, stem, selected_stem_label)

    config = load_rag_config()
    chroma_ok = chroma_index_exists(config)
    chat_messages = request.session.get("chat_messages", [])
    recent_stems = request.session.get("recent_stems", [])

    # Radar data as JSON for Chart.js
    radar_labels = json.dumps(kpis["radar_labels"]) if kpis else None
    radar_values = json.dumps(kpis["radar_values"]) if kpis else None

    return render(
        request,
        "dashboard/index.html",
        {
            "team_choices": filtered_choices,
            "selected_stem": stem,
            "selected_stem_label": selected_stem_label,
            "report_content": report_content,
            "kpis": kpis,
            "radar_labels": radar_labels,
            "radar_values": radar_values,
            "chroma_ok": chroma_ok,
            "chat_messages": chat_messages,
            "recent_stems": recent_stems,
            "division": division,
            "d1_years": d1_years,
            "d2_years": d2_years,
            "d1_year_selected": d1_year_sel_i,
            "d2_year_selected": d2_year_sel_i,
            "d1_teams": d1_teams,
            "d2_teams": d2_teams,
        },
    )


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

    # Load data (cached per process by pandas; OK for now)
    data_path = Path(settings.BASE_DIR) / "data" / "processed" / "team_stats_with_sos.csv"
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
    top = subset.head(50).to_dict(orient="records")

    # Options for dropdowns
    years = sorted(df["academic_year"].dropna().astype(int).unique().tolist(), reverse=True)
    stats = [{"key": k, "label": v[0]} for k, v in allowed_stats.items() if (k == "predicted_winning_percentage" or k in df.columns)]

    return render(
        request,
        "dashboard/rankings.html",
        {
            "year": year,
            "division": str(division),
            "stat": stat,
            "stat_label": label,
            "rows": top,
            "years": years,
            "stats": stats,
        },
    )


def clear_chat_partial(request):
    """HTMX endpoint: clear chat, return empty chat fragment."""
    if request.method != "POST":
        return HttpResponse(status=405)
    request.session["chat_messages"] = []
    request.session.modified = True
    return render(request, "dashboard/partials/chat_messages.html", {"chat_messages": []})
