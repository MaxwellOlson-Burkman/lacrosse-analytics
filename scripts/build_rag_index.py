"""Build the RAG vector index from team reports and metadata (Ollama + ChromaDB)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.rag.store import build_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build RAG index from team reports")
    parser.add_argument("--config", type=str, default="config/rag_config.yaml", help="Path to RAG config YAML")
    parser.add_argument("--reports-dir", type=str, help="Override reports directory")
    parser.add_argument("--chroma-dir", type=str, help="Override Chroma persist directory")
    parser.add_argument("--batch-size", type=int, default=50, help="Documents per embedding batch (default 50)")
    args = parser.parse_args()

    config_path = PROJECT_ROOT / args.config
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    reports_dir = PROJECT_ROOT / (args.reports_dir or config.get("reports_dir", "models/team_reports"))
    chroma_path = PROJECT_ROOT / (args.chroma_dir or config.get("chroma_persist_dir", "data/chroma"))
    feature_importance_path = PROJECT_ROOT / config.get("feature_importance_path", "models/feature_importance.json")
    model_metadata_path = PROJECT_ROOT / config.get("model_metadata_path", "models/model_metadata.json")
    team_aliases_path = PROJECT_ROOT / config.get("team_aliases_path", "config/team_aliases.yaml")
    embedding_model = config.get("embedding_model", "nomic-embed-text")
    collection_name = config.get("collection_name", "lacrosse_team_reports")

    if not reports_dir.is_dir():
        print(f"Reports directory not found: {reports_dir}", file=sys.stderr)
        sys.exit(1)

    def progress(n_done: int, total: int) -> None:
        print(f"Embedded {n_done}/{total} documents...", flush=True)

    print("Loading documents...", flush=True)
    n = build_index(
        reports_dir,
        chroma_path,
        feature_importance_path=feature_importance_path,
        model_metadata_path=model_metadata_path,
        team_aliases_path=team_aliases_path,
        embedding_model=embedding_model,
        collection_name=collection_name,
        batch_size=args.batch_size,
        progress_callback=progress,
    )
    print(f"Indexed {n} documents to {chroma_path}")
    print("Run a query: python scripts/query_rag.py \"Your question here\"")


if __name__ == "__main__":
    main()
