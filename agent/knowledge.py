"""RAG knowledge base — ChromaDB + nomic-embed-text-v1 embeddings.

Parses docs/troubleshoting.md into per-article documents, ingests them into a
persistent ChromaDB collection, and exposes search_knowledge() for the
search_rag node.

Index is built lazily on first query and is a no-op if already populated.
CHROMA_PERSIST_DIR env var overrides the default persist path (/tmp/chroma_db).
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional

# ChromaDB requires sqlite3 >= 3.35.0. RHOAI workbench images ship an older
# system sqlite3, but pysqlite3-binary provides a compatible version.
try:
    __import__("pysqlite3")
    sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")
except ImportError:
    pass  # system sqlite3 is new enough, or pysqlite3-binary not installed

import chromadb
import yaml
from chromadb import Documents, EmbeddingFunction, Embeddings
from sentence_transformers import SentenceTransformer

KNOWLEDGE_FILE = Path(__file__).parent.parent / "docs" / "troubleshoting.md"
CHROMA_PERSIST_DIR = os.environ.get("CHROMA_PERSIST_DIR", "/tmp/chroma_db")
COLLECTION_NAME = "runbooks"
EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1"

# Singletons — initialised once per process
_collection: Optional[chromadb.Collection] = None
_model: Optional[SentenceTransformer] = None


# ---------------------------------------------------------------------------
# Embedding function
# ---------------------------------------------------------------------------

class NomicEmbeddingFunction(EmbeddingFunction):
    """Wraps nomic-embed-text-v1 with the required task-type prefixes.

    nomic-embed-text-v1 expects:
      - "search_document: <text>"  when indexing
      - "search_query: <text>"     when querying
    """

    def __init__(self) -> None:
        global _model
        if _model is None:
            _model = SentenceTransformer(
                EMBEDDING_MODEL,
                trust_remote_code=True,
            )

    def __call__(self, input: Documents) -> Embeddings:
        global _model
        return _model.encode(list(input), convert_to_numpy=True).tolist()


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

def _parse_articles(md_path: Path) -> list[dict]:
    """Split the knowledge file into one dict per article.

    Each dict contains:
      id, title, alert_type, severity, automatable, category, content
    """
    text = md_path.read_text()
    parts = re.split(r"\n# Article: ", text)

    articles = []
    for part in parts[1:]:  # parts[0] is the file header / template
        lines = part.strip().split("\n")
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()

        meta: dict = {}
        yaml_match = re.search(r"```yaml\n(.*?)```", body, re.DOTALL)
        if yaml_match:
            try:
                meta = yaml.safe_load(yaml_match.group(1)) or {}
            except yaml.YAMLError:
                pass

        articles.append({
            "id": str(meta.get("id", title)),
            "title": title,
            "alert_type": str(meta.get("alert_type", "UNKNOWN")),
            "severity": str(meta.get("severity", "medium")),
            "automatable": str(meta.get("automatable", "false")),
            "category": str(meta.get("category", "general")),
            "content": f"# Article: {title}\n\n{body}",
        })

    return articles


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

def _get_collection() -> chromadb.Collection:
    global _collection
    if _collection is not None:
        return _collection

    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    _collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=NomicEmbeddingFunction(),
        metadata={"hnsw:space": "cosine"},
    )
    return _collection


def build_index(force: bool = False) -> None:
    """Parse the knowledge file and ingest articles into ChromaDB.

    Skips ingestion if the collection already contains documents unless
    force=True is passed (useful for rebuilding after edits to the KB file).
    """
    collection = _get_collection()

    if not force and collection.count() > 0:
        return

    articles = _parse_articles(KNOWLEDGE_FILE)

    # Prefix documents with the nomic task token expected at index time
    collection.upsert(
        ids=[a["id"] for a in articles],
        documents=[f"search_document: {a['content']}" for a in articles],
        metadatas=[
            {
                "article_id": a["id"],
                "title": a["title"],
                "alert_type": a["alert_type"],
                "severity": a["severity"],
                "automatable": a["automatable"],
                "category": a["category"],
            }
            for a in articles
        ],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search_knowledge(
    query: str,
    alert_type: Optional[str] = None,
    n_results: int = 3,
) -> list[str]:
    """Retrieve the most relevant runbook chunks for a given query.

    Args:
        query:      Natural language description of the problem (alert message,
                    pod status, error text, etc.)
        alert_type: Optional exact-match filter on the alert_type metadata field
                    (e.g. "IMAGE_PULL_BACKOFF"). When provided, only articles
                    for that alert type are searched, giving near-perfect
                    precision for classified alerts.
        n_results:  Maximum number of chunks to return.

    Returns:
        List of matching runbook text strings, ordered by relevance.
    """
    build_index()
    collection = _get_collection()

    total = collection.count()
    if total == 0:
        return []

    where = {"alert_type": alert_type} if alert_type else None

    results = collection.query(
        query_texts=[f"search_query: {query}"],
        n_results=min(n_results, total),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    return results["documents"][0] if results["documents"] else []
