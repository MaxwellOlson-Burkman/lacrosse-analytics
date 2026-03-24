"""Run a single RAG query (Ollama + ChromaDB)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.rag.chain import query


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the lacrosse RAG")
    parser.add_argument("question", type=str, nargs="*", help="Question to ask (or pass as single string)")
    parser.add_argument("--config", type=str, default="config/rag_config.yaml", help="Path to RAG config YAML")
    parser.add_argument("--chroma-dir", type=str, help="Override Chroma persist directory")
    parser.add_argument("--sources", action="store_true", help="Print retrieved source snippets")
    args = parser.parse_args()

    question = " ".join(args.question).strip()
    if not question:
        print("Usage: python scripts/query_rag.py \"Your question here\"", file=sys.stderr)
        sys.exit(1)

    config_path = PROJECT_ROOT / args.config
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    chroma_path = PROJECT_ROOT / (args.chroma_dir or config.get("chroma_persist_dir", "data/chroma"))
    if not chroma_path.is_dir():
        print(f"Chroma index not found at {chroma_path}. Run: python scripts/build_rag_index.py", file=sys.stderr)
        sys.exit(1)

    reports_dir = PROJECT_ROOT / config.get("reports_dir", "models/team_reports")
    team_aliases_path = PROJECT_ROOT / config.get("team_aliases_path", "config/team_aliases.yaml")
    embedding_model = config.get("embedding_model", "nomic-embed-text")
    llm_model = config.get("llm_model", "llama3.2")
    collection_name = config.get("collection_name", "lacrosse_team_reports")
    k = config.get("retriever_k", 5)

    result = query(
        question,
        str(chroma_path),
        embedding_model=embedding_model,
        llm_model=llm_model,
        collection_name=collection_name,
        k=k,
        return_sources=args.sources,
        reports_dir=reports_dir,
        team_aliases_path=team_aliases_path,
    )
    if args.sources:
        answer, docs = result
        print(answer)
        print("\n--- Sources ---")
        for i, doc in enumerate(docs, 1):
            meta = doc.metadata
            src = meta.get("source", meta.get("team_name", "?"))
            print(f"{i}. {src}")
            print(doc.page_content[:300] + "..." if len(doc.page_content) > 300 else doc.page_content)
            print()
    else:
        print(result)


if __name__ == "__main__":
    main()
