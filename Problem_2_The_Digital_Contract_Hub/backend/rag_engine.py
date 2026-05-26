"""
rag_engine.py - Lightweight RAG engine using a pure NumPy/JSON vector store.

Replaces ChromaDB with a simple file-based index that stores embeddings as
.npy arrays and metadata as .json — zero extra dependencies beyond numpy.

Handles:
  - Chunking contract page text
  - Embedding via Gemini text-embedding-004
  - Cosine similarity search (top-k retrieval)
  - Answer generation with source citations via Gemini 2.0 Flash
"""

import os
import re
import json
import time
import logging
import numpy as np
from typing import List, Dict, Any, Optional

import google.generativeai as genai

logger = logging.getLogger("ContractHub-RAG")

# ── Vector store paths ────────────────────────────────────────────────────────

STORE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "storage", "vector_store")
)
VECTORS_FILE = os.path.join(STORE_DIR, "vectors.npy")   # shape (N, 768)
META_FILE    = os.path.join(STORE_DIR, "metadata.json") # list of dicts


def _load_store():
    """Load the persisted vector matrix and metadata list from disk."""
    os.makedirs(STORE_DIR, exist_ok=True)
    if os.path.exists(VECTORS_FILE) and os.path.exists(META_FILE):
        try:
            vectors = np.load(VECTORS_FILE)          # (N, D)
            with open(META_FILE, "r", encoding="utf-8") as f:
                metadata = json.load(f)
            return vectors, metadata
        except Exception as e:
            logger.warning(f"Could not load vector store, resetting: {e}")
    return np.empty((0, 768), dtype=np.float32), []


def _save_store(vectors: np.ndarray, metadata: list):
    """Persist the vector matrix and metadata list to disk."""
    os.makedirs(STORE_DIR, exist_ok=True)
    np.save(VECTORS_FILE, vectors.astype(np.float32))
    with open(META_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


# ── Embedding helpers ─────────────────────────────────────────────────────────

def _embed_texts(texts: List[str], api_key: str) -> np.ndarray:
    """
    Batch-embed a list of texts with Gemini text-embedding-004.
    Returns a float32 numpy array of shape (len(texts), D).
    """
    genai.configure(api_key=api_key)
    all_vecs = []
    BATCH = 50
    for i in range(0, len(texts), BATCH):
        batch = texts[i : i + BATCH]
        retries, delay = 6, 4.0
        for attempt in range(retries):
            try:
                result = genai.embed_content(
                    model="models/text-embedding-004",
                    content=batch,
                    task_type="RETRIEVAL_DOCUMENT",
                )
                # result["embedding"] is a list of lists when batch > 1
                vecs = result["embedding"]
                if isinstance(vecs[0], float):   # single text returned flat
                    vecs = [vecs]
                all_vecs.extend(vecs)
                break
            except Exception as e:
                err = str(e)
                if ("429" in err or "quota" in err.lower() or "limit" in err.lower() or "exhausted" in err.lower()) and attempt < retries - 1:
                    logger.warning(f"Embedding rate limit, waiting {delay}s ...")
                    time.sleep(delay)
                    delay = min(delay * 2, 20.0)
                elif attempt < retries - 1:
                    logger.warning(f"Embedding failed (non-quota), retrying in {delay}s: {e}")
                    time.sleep(delay)
                    delay = min(delay * 2, 20.0)
                else:
                    raise
    return np.array(all_vecs, dtype=np.float32)


def _embed_query(query: str, api_key: str) -> np.ndarray:
    """Embed a single query string with retries. Returns shape (D,)."""
    genai.configure(api_key=api_key)
    retries, delay = 5, 3.0
    for attempt in range(retries):
        try:
            result = genai.embed_content(
                model="models/text-embedding-004",
                content=query,
                task_type="RETRIEVAL_QUERY",
            )
            vec = result["embedding"]
            return np.array(vec, dtype=np.float32)
        except Exception as e:
            err = str(e)
            if attempt < retries - 1:
                logger.warning(f"Query embedding rate limit or error, retrying in {delay}s: {e}")
                time.sleep(delay)
                delay = min(delay * 2, 15.0)
            else:
                raise


def _cosine_similarity(mat: np.ndarray, vec: np.ndarray) -> np.ndarray:
    """
    Compute cosine similarity between each row of mat and vec.
    mat: (N, D), vec: (D,)  → returns (N,)
    """
    mat_norm = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    vec_norm = vec / (np.linalg.norm(vec) + 1e-9)
    return mat_norm @ vec_norm


# ── Chunking ──────────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 400, overlap: int = 80) -> List[str]:
    """Split text into overlapping word-based chunks."""
    words = text.split()
    chunks, start = [], 0
    while start < len(words):
        chunks.append(" ".join(words[start : start + chunk_size]))
        start += chunk_size - overlap
    return [c for c in chunks if len(c.strip()) > 50]


# ── Indexing ──────────────────────────────────────────────────────────────────

def index_contract(
    contract_id: str,
    file_name: str,
    pages: List[Dict[str, Any]],
    api_key: str,
):
    """
    Chunk and embed all page texts for a contract, then append to the store.
    Existing chunks for this contract_id are removed first (idempotent).
    """
    vectors, metadata = _load_store()

    # Remove existing entries for this contract
    if metadata:
        keep = [i for i, m in enumerate(metadata) if m["contract_id"] != contract_id]
        if keep:
            vectors  = vectors[keep]
            metadata = [metadata[i] for i in keep]
        else:
            vectors  = np.empty((0, 768), dtype=np.float32)
            metadata = []

    # Build chunks
    new_texts, new_metas = [], []
    for page in pages:
        page_num = page["page_number"]
        text = page.get("page_text", "").strip()
        if not text:
            continue
        for idx, chunk in enumerate(_chunk_text(text)):
            new_texts.append(chunk)
            new_metas.append({
                "contract_id": contract_id,
                "file_name":   file_name,
                "page_number": page_num,
                "chunk_index": idx,
                "text":        chunk,
            })

    if not new_texts:
        logger.warning(f"No chunks to index for {contract_id}")
        return

    logger.info(f"Embedding {len(new_texts)} chunks for {contract_id} ...")
    new_vecs = _embed_texts(new_texts, api_key)    # (M, D)

    # Append
    if vectors.shape[0] == 0:
        vectors = new_vecs
    else:
        vectors = np.vstack([vectors, new_vecs])
    metadata.extend(new_metas)

    _save_store(vectors, metadata)
    logger.info(f"Indexed {len(new_texts)} chunks. Store total: {len(metadata)}.")


def delete_contract_index(contract_id: str):
    """Remove all indexed chunks for a contract from the store."""
    vectors, metadata = _load_store()
    if not metadata:
        return
    keep = [i for i, m in enumerate(metadata) if m["contract_id"] != contract_id]
    removed = len(metadata) - len(keep)
    if removed:
        vectors  = vectors[keep] if keep else np.empty((0, 768), dtype=np.float32)
        metadata = [metadata[i] for i in keep]
        _save_store(vectors, metadata)
        logger.info(f"Removed {removed} chunks for {contract_id}.")


# ── Retrieval ─────────────────────────────────────────────────────────────────

def retrieve_relevant_chunks(
    query: str,
    api_key: str,
    contract_ids: Optional[List[str]] = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Return top_k most relevant chunks via cosine similarity.
    Optionally filter to specific contract_ids.
    """
    vectors, metadata = _load_store()
    if not metadata or vectors.shape[0] == 0:
        return []

    # Apply contract filter
    if contract_ids:
        indices = [i for i, m in enumerate(metadata)
                   if m["contract_id"] in contract_ids]
        if not indices:
            return []
        sub_vecs = vectors[indices]
        sub_meta = [metadata[i] for i in indices]
    else:
        sub_vecs = vectors
        sub_meta = metadata

    query_vec = _embed_query(query, api_key)
    sims = _cosine_similarity(sub_vecs, query_vec)

    top_k = min(top_k, len(sub_meta))
    top_indices = np.argsort(sims)[::-1][:top_k]

    results = []
    for i in top_indices:
        m = sub_meta[i]
        results.append({
            "text":             m["text"],
            "file_name":        m["file_name"],
            "contract_id":      m["contract_id"],
            "page_number":      m["page_number"],
            "relevance_score":  round(float(sims[i]), 4),
        })
    return results


# ── Answer generation ─────────────────────────────────────────────────────────

QA_PROMPT_TEMPLATE = """
You are a professional legal contract assistant. Use ONLY the provided contract excerpts below to answer the user's question.

[CONTRACT EXCERPTS]
{context}
[END OF EXCERPTS]

User Question: {question}

STRICT RULES:
1. Answer clearly and concisely in the same language as the question.
2. After EVERY piece of information you state, add a citation: [filename, Page X].
   Example: "The contract expires on December 31, 2026 [ContractA.pdf, Page 4]."
3. If multiple excerpts support the same fact, cite all of them.
4. If the answer cannot be found in the excerpts, reply:
   "Tôi không tìm thấy thông tin này trong các hợp đồng được cung cấp."
5. Do NOT invent, guess, or extrapolate information.
6. Use bullet points when listing multiple facts.
""".strip()


def generate_answer(
    question: str,
    chunks: List[Dict[str, Any]],
    api_key: str,
) -> Dict[str, Any]:
    """
    Generate a cited answer using Gemini given retrieved chunks.
    Returns {'answer': str, 'sources': list}.
    """
    if not chunks:
        return {
            "answer": "Không tìm thấy tài liệu liên quan trong cơ sở dữ liệu hợp đồng.",
            "sources": [],
        }

    context = "\n\n".join(
        f"--- Excerpt {i+1} (File: {c['file_name']}, Page: {c['page_number']}, "
        f"Score: {c['relevance_score']}) ---\n{c['text']}"
        for i, c in enumerate(chunks)
    )
    prompt = QA_PROMPT_TEMPLATE.format(context=context, question=question)

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    answer_text = ""
    retries, delay = 5, 3.0
    for attempt in range(retries):
        try:
            answer_text = model.generate_content(prompt).text.strip()
            break
        except Exception as e:
            err = str(e)
            if ("429" in err or "quota" in err.lower() or "limit" in err.lower() or "exhausted" in err.lower()) and attempt < retries - 1:
                logger.warning(f"QA rate limit, waiting {delay}s ...")
                time.sleep(delay)
                delay = min(delay * 2, 15.0)
            elif attempt < retries - 1:
                logger.warning(f"QA generation failed (non-quota), retrying in {delay}s: {e}")
                time.sleep(delay)
                delay = min(delay * 2, 15.0)
            else:
                logger.error(f"Answer generation failed: {e}")
                answer_text = f"[Lỗi sinh câu trả lời: {err}]"
                break

    # Deduplicate sources
    seen, sources = set(), []
    for c in chunks:
        key = (c["file_name"], c["page_number"])
        if key not in seen:
            seen.add(key)
            sources.append({
                "file_name":       c["file_name"],
                "contract_id":     c["contract_id"],
                "page_number":     c["page_number"],
                "relevance_score": c["relevance_score"],
            })

    return {"answer": answer_text, "sources": sources}
