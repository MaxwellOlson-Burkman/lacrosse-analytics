"""RAG chain: retriever + prompt + ChatOllama."""

from __future__ import annotations

from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser

from .store import load_vector_store


SYSTEM_PROMPT = """You are an NCAA lacrosse analytics assistant. Answer ONLY using the provided context below. Do not use general sports knowledge or guess. If the answer is not in the context, say "I don't see that in the provided data." Statistical accuracy is paramount."""

USER_PROMPT = """Context from team reports and model data:

{context}

Question: {question}

Answer (use only the context above):"""


def _format_docs(docs):
    return "\n\n---\n\n".join(doc.page_content for doc in docs)


def create_chain(
    chroma_path,
    *,
    embedding_model: str = "nomic-embed-text",
    llm_model: str = "llama3.2",
    collection_name: str = "lacrosse_team_reports",
    k: int = 5,
):
    """Build RAG chain: retriever -> format_docs -> prompt -> LLM -> string."""
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        try:
            from langchain_community.chat_models import ChatOllama
        except ImportError:
            from langchain_community.chat_models.ollama import ChatOllama

    vector_store = load_vector_store(
        chroma_path,
        embedding_model=embedding_model,
        collection_name=collection_name,
    )
    retriever = vector_store.as_retriever(search_kwargs={"k": k})

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", USER_PROMPT),
    ])
    llm = ChatOllama(model=llm_model, temperature=0)

    chain = (
        {"context": retriever | _format_docs, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    return chain, retriever


def query(
    question: str,
    chroma_path,
    *,
    embedding_model: str = "nomic-embed-text",
    llm_model: str = "llama3.2",
    collection_name: str = "lacrosse_team_reports",
    k: int = 5,
    return_sources: bool = False,
):
    """Run a single RAG query. If return_sources, returns (answer, list of docs)."""
    chain, retriever = create_chain(
        chroma_path,
        embedding_model=embedding_model,
        llm_model=llm_model,
        collection_name=collection_name,
        k=k,
    )
    docs = retriever.invoke(question)
    answer = chain.invoke(question)
    if return_sources:
        return answer, docs
    return answer
