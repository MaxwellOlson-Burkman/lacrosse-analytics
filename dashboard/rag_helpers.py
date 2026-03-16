"""RAG config, Chroma check, and team report choices for dashboard views."""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from django.conf import settings

BASE = Path(settings.BASE_DIR)


def load_rag_config():
    """Load RAG config from config/rag_config.yaml. Returns dict or {}."""
    path = BASE / "config" / "rag_config.yaml"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def chroma_index_exists(config: dict | None = None) -> bool:
    """True if the configured Chroma persist directory exists."""
    if config is None:
        config = load_rag_config()
    chroma_dir = config.get("chroma_persist_dir", "data/chroma")
    return (BASE / chroma_dir).is_dir()


def get_team_choices():
    """List of (display_label, stem) for team reports, sorted by year desc, div, name."""
    reports_dir = BASE / "models" / "team_reports"
    if not reports_dir.is_dir():
        return []
    pattern = re.compile(r"^(\d{4})_D(\d)_(.+)$")
    choices = []
    for path in reports_dir.glob("*.txt"):
        m = pattern.match(path.stem)
        if m:
            year, div, name = m.group(1), m.group(2), m.group(3)
            label = f"{year} D{div} – {name}"
            choices.append((label, path.stem, int(year), int(div), name))
    choices.sort(key=lambda x: (-x[2], x[3], x[4]))
    return [(c[0], c[1]) for c in choices]


def get_team_index():
    """Structured index of team reports for building UI dropdowns.
    Returns list of dicts: {year:int, division:int, name:str, stem:str, label:str}.
    """
    reports_dir = BASE / "models" / "team_reports"
    if not reports_dir.is_dir():
        return []
    pattern = re.compile(r"^(\d{4})_D(\d)_(.+)$")
    rows = []
    for path in reports_dir.glob("*.txt"):
        m = pattern.match(path.stem)
        if not m:
            continue
        year, div, name = int(m.group(1)), int(m.group(2)), m.group(3)
        stem = path.stem
        label = f"{year} D{div} – {name}"
        rows.append({"year": year, "division": div, "name": name, "stem": stem, "label": label})
    rows.sort(key=lambda r: (-r["year"], r["division"], r["name"]))
    return rows


def get_report_content(stem: str) -> str | None:
    """Return raw content of models/team_reports/{stem}.txt or None if missing."""
    path = BASE / "models" / "team_reports" / f"{stem}.txt"
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()
