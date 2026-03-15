"""ChromaDB vector store with Ollama embeddings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import chromadb
from chromadb.config import Settings
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from .loaders import load_metadata_docs, load_team_reports


ddef _get_embeddings(embedding_model: str):
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(model=embedding_model)


class ChromaVectorStore:
    """Minimal Chroma-backed vector store using chromadb directly (no langchain_community)."""

    def __init__(
        self,
        persist_directory: str,
        collection_name: str,
        embedding_model: str = "nomic-embed-text",
    ):
        self._client = chromadb.PersistentClient(path=persist_directory, settings=Settings(anonymized_telemetry=False))
        self._collection = self._client.get_or_create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})
        self._embedding_model = embedding_model
        self._embeddings = _get_embeddings(embedding_model)

    def add_documents(self, documents: list[Document], ids: Optional[list[str]] = None) -> list[str]:
        if not documents:
            return []
        ids = ids or [str(uuid4()) for _ in documents]
        texts = [d.page_content for d in documents]
        metadatas = [_sanitize_metadata(d.metadata) for d in documents]
        emb = self._embeddings.embed_documents(texts)
        self._collection.add(ids=ids, embeddings=emb, documents=texts, metadatas=metadatas)
        return ids

    def similarity_search(self, query: str, k: int = 5, **kwargs: Any) -> list[Document]:
        emb = self._embeddings.embed_query(query)
        res = self._collection.query(query_embeddings=[emb], n_results=k, include=["documents", "metadatas"])
        docs = []
        if res["documents"] and res["documents"][0]:
            for content, meta in zip(res["documents"][0], res["metadatas"][0] or []):
                docs.append(Document(page_content=content or "", metadata=meta or {}))
        return docs

    def as_retriever(self, **kwargs: Any) -> BaseRetriever:
        k = kwargs.pop("search_kwargs", {}).get("k", 5)
        return _ChromaRetriever(vectorstore=self, k=k, **kwargs)


def _sanitize_metadata(meta: dict) -> dict:
    """Chroma allows only str, int, float, bool in metadata."""
    out = {}
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


class _ChromaRetriever(BaseRetriever):
    vectorstore: ChromaVectorStore
    k: int = 5

    def _get_relevant_documents(self, query: str, *, run_manager: Optional[CallbackManagerForRetrieverRun] = None) -> list[Document]:
        return self.vectorstore.similarity_search(query, k=self.k)


def build_index(
    reports_dir: Path,
    chroma_path: Path,
    *,
    feature_importance_path: Optional[Path] = None,
    model_metadata_path: Optional[Path] = None,
    embedding_model: str = "nomic-embed-text",
    collection_name: str = "lacrosse_team_reports",
) -> int:
    """Load documents, embed with Ollama, persist to Chroma. Returns number of docs indexed."""
    reports_dir = Path(reports_dir)
    chroma_path = Path(chroma_path)
    chroma_path.mkdir(parents=True, exist_ok=True)

    docs: list[Document] = []
    docs.extend(load_team_reports(reports_dir))
    docs.extend(
        load_metadata_docs(
            feature_importance_path=feature_importance_path,
            model_metadata_path=model_metadata_path,
        )
    )
    if not docs:
        return 0

    store = ChromaVectorStore(
        persist_directory=str(chroma_path),
        collection_name=collection_name,
        embedding_model=embedding_model,
    )
    store.add_documents(docs)
    return len(docs)


def load_vector_store(
    chroma_path: Path,
    *,
    embedding_model: str = "nomic-embed-text",
    collection_name: str = "lacrosse_team_reports",
) -> ChromaVectorStore:
    """Load existing Chroma vector store (same embedding model and collection as build_index)."""
    chroma_path = Path(chroma_path)
    return ChromaVectorStore(
        persist_directory=str(chroma_path),
        collection_name=collection_name,
        embedding_model=embedding_model,
    )
