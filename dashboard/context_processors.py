"""Context processors so chat and RAG status are available on all dashboard pages."""
from .rag_helpers import chroma_index_exists, load_rag_config


def dashboard_chat(request):
    """Provide chroma_ok and chat_messages for the persistent chat in the right panel."""
    config = load_rag_config()
    return {
        "chroma_ok": chroma_index_exists(config),
        "chat_messages": request.session.get("chat_messages", []),
    }
