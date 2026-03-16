"""Phase 4: Streamlit dashboard – chat with RAG and look up team reports."""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st
import yaml

REPORTS_DIR = PROJECT_ROOT / "models" / "team_reports"


def _load_rag_config():
    config_path = PROJECT_ROOT / "config" / "rag_config.yaml"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _chroma_index_exists(config: dict) -> bool:
    chroma_dir = config.get("chroma_persist_dir", "data/chroma")
    return (PROJECT_ROOT / chroma_dir).is_dir()


def _build_team_choices():
    """List (display_label, filename_stem) for team reports, sorted by year desc, div, name."""
    if not REPORTS_DIR.is_dir():
        return []
    pattern = re.compile(r"^(\d{4})_D(\d)_(.+)$")
    choices = []
    for path in REPORTS_DIR.glob("*.txt"):
        m = pattern.match(path.stem)
        if m:
            year, div, name = m.group(1), m.group(2), m.group(3)
            label = f"{year} D{div} – {name}"
            choices.append((label, path.stem, int(year), int(div), name))
    choices.sort(key=lambda x: (-x[2], x[3], x[4]))
    return [(c[0], c[1]) for c in choices]


def main():
    st.set_page_config(page_title="Lacrosse Analytics", layout="centered")
    config = _load_rag_config()
    chroma_ok = _chroma_index_exists(config)

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "last_sources" not in st.session_state:
        st.session_state.last_sources = None

    st.sidebar.title("Lacrosse Analytics")
    st.sidebar.caption("Ask questions or pick a team to view its report.")
    if st.sidebar.button("Clear chat"):
        st.session_state.messages = []
        st.session_state.last_sources = None
        st.rerun()

    tab_chat, tab_lookup = st.tabs(["Chat", "Team lookup"])

    with tab_chat:
        if not chroma_ok:
            chroma_dir = config.get("chroma_persist_dir", "data/chroma")
            st.warning(
                f"RAG index not found at `{chroma_dir}`. Run: `python scripts/build_rag_index.py` to build it. "
                "Team lookup below works without the index."
            )
        else:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])
            if st.session_state.last_sources and st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
                with st.expander("View sources for last response"):
                    for i, doc in enumerate(st.session_state.last_sources, 1):
                        src = doc.metadata.get("source", doc.metadata.get("team_name", "?"))
                        st.caption(f"{i}. {src}")
                        st.text(doc.page_content[:400] + ("..." if len(doc.page_content) > 400 else ""))

        if chroma_ok:
            if prompt := st.chat_input("Ask about lacrosse stats..."):
                st.session_state.messages.append({"role": "user", "content": prompt})
                with st.chat_message("assistant"):
                    with st.spinner("Thinking..."):
                        try:
                            from src.rag.chain import query
                            chroma_path = PROJECT_ROOT / config.get("chroma_persist_dir", "data/chroma")
                            answer, sources = query(
                                prompt,
                                chroma_path,
                                embedding_model=config.get("embedding_model", "nomic-embed-text"),
                                llm_model=config.get("llm_model", "llama3.2"),
                                collection_name=config.get("collection_name", "lacrosse_team_reports"),
                                k=config.get("retriever_k", 5),
                                return_sources=True,
                            )
                            st.session_state.messages.append({"role": "assistant", "content": answer})
                            st.session_state.last_sources = sources
                        except Exception as e:
                            err_msg = f"Error: {e}"
                            st.session_state.messages.append({"role": "assistant", "content": err_msg})
                            st.session_state.last_sources = None
                st.rerun()

    with tab_lookup:
        choices = _build_team_choices()
        if not choices:
            st.info("No team reports found. Run `python run_training.py` to generate reports in `models/team_reports/`.")
        else:
            labels = [c[0] for c in choices]
            stems = [c[1] for c in choices]
            selected_label = st.selectbox("Select a team-season", labels, index=0)
            idx = labels.index(selected_label)
            selected_stem = stems[idx]
            report_path = REPORTS_DIR / f"{selected_stem}.txt"
            if report_path.exists():
                content = report_path.read_text(encoding="utf-8")
                st.text_area("Report", value=content, height=400, disabled=True)
            else:
                st.warning(f"File not found: {report_path}")


if __name__ == "__main__":
    main()
