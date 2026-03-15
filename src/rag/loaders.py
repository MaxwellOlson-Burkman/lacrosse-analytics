"""Load team reports and optional metadata as LangChain Documents."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document


def _parse_report_filename(path: Path) -> dict:
    """Parse {year}_D{div}_{team_name}.txt into metadata."""
    stem = path.stem
    match = re.match(r"^(\d{4})_D(\d)_(.+)$", stem)
    if match:
        return {
            "academic_year": int(match.group(1)),
            "division": int(match.group(2)),
            "team_name": match.group(3),
            "source": str(path),
        }
    return {"source": str(path)}


def load_team_reports(reports_dir: Path) -> list[Document]:
    """Load all team report .txt files as one Document per file."""
    reports_dir = Path(reports_dir)
    if not reports_dir.is_dir():
        return []

    docs = []
    for path in sorted(reports_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        meta = _parse_report_filename(path)
        docs.append(Document(page_content=text.strip(), metadata=meta))
    return docs


def load_metadata_docs(
    feature_importance_path: Optional[Path] = None,
    model_metadata_path: Optional[Path] = None,
) -> list[Document]:
    """Load feature importance and/or model metadata as single Documents for global context."""
    docs = []

    if feature_importance_path and Path(feature_importance_path).exists():
        with open(feature_importance_path, encoding="utf-8") as f:
            data = json.load(f)
        lines = ["Model feature importance (higher = more predictive of winning %):"]
        for name, val in data.items():
            lines.append(f"  {name}: {val:.4f}")
        docs.append(
            Document(
                page_content="\n".join(lines),
                metadata={"source": "feature_importance.json", "type": "metadata"},
            )
        )

    if model_metadata_path and Path(model_metadata_path).exists():
        with open(model_metadata_path, encoding="utf-8") as f:
            data = json.load(f)
        lines = ["Model metadata:", f"  Model type: {data.get('model_type', 'N/A')}"]
        if "metrics" in data:
            for k, v in data["metrics"].items():
                lines.append(f"  {k}: {v}")
        if "feature_names" in data:
            lines.append("  Features: " + ", ".join(data["feature_names"][:10]) + ("..." if len(data["feature_names"]) > 10 else ""))
        docs.append(
            Document(
                page_content="\n".join(lines),
                metadata={"source": "model_metadata.json", "type": "metadata"},
            )
        )

    return docs
