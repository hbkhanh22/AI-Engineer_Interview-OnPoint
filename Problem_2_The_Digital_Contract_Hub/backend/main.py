"""
main.py - FastAPI backend for the Digital Contract Hub.

Endpoints:
  POST /api/contracts/upload          - Upload and process a contract file
  GET  /api/contracts                 - List all contracts
  GET  /api/contracts/{id}            - Get contract detail + clauses
  DELETE /api/contracts/{id}          - Delete a contract
  GET  /api/contracts/{id}/pdf        - Serve the original contract PDF/image
  POST /api/chat/query                - RAG Q&A with citations
  GET  /health                        - Health check
"""

import os
import uuid
import time
import logging
import asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

try:
    from . import database as db
    from . import parser as contract_parser
    from . import rag_engine
except ImportError:
    import database as db
    import parser as contract_parser
    import rag_engine

# ── Bootstrap ─────────────────────────────────────────────────────────────────

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("ContractHub-API")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
STORAGE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "storage", "files")
)
os.makedirs(STORAGE_DIR, exist_ok=True)

db.init_db()

app = FastAPI(title="Digital Contract Hub API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve frontend static files ───────────────────────────────────────────────

FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# ── Pydantic models ───────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    contract_ids: Optional[list[str]] = None   # None = search all contracts
    gemini_api_key: Optional[str] = None        # Overrides server .env key


class StatusUpdateRequest(BaseModel):
    status: str  # "Active" | "Expired" | "Terminated"


# ── Helper ────────────────────────────────────────────────────────────────────

def _api_key(override: Optional[str] = None) -> str:
    key = (override or "").strip() or GEMINI_API_KEY
    if not key:
        raise HTTPException(
            status_code=400,
            detail="Gemini API Key chưa được cấu hình. "
                   "Vui lòng thêm key vào tệp .env hoặc gửi kèm trong request.",
        )
    return key


ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "healthy",
        "gemini_configured": bool(GEMINI_API_KEY),
        "storage_dir": STORAGE_DIR,
    }


@app.post("/api/contracts/upload")
async def upload_contract(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    gemini_api_key: Optional[str] = Query(None),
):
    """
    Upload a contract PDF or image.
    Processing (OCR + embedding) runs synchronously and returns the result.
    """
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"File type '{suffix}' not supported. Use: {ALLOWED_EXTENSIONS}")

    api_key = _api_key(gemini_api_key)

    # Save file locally
    contract_id = str(uuid.uuid4())
    safe_name = f"{contract_id}{suffix}"
    local_path = os.path.join(STORAGE_DIR, safe_name)

    content = await file.read()
    with open(local_path, "wb") as f_out:
        f_out.write(content)
    logger.info(f"Saved upload: {local_path}")

    # Run Gemini processing pipeline
    try:
        result = await asyncio.to_thread(
            contract_parser.process_contract, local_path, api_key
        )
    except Exception as e:
        os.remove(local_path)
        raise HTTPException(500, f"Gemini processing failed: {e}")

    metadata = result.get("metadata", {})
    pages = result.get("pages", [])

    # Determine initial status from expiration date
    expiration = metadata.get("expiration_date")
    status = "Active"
    if expiration:
        from datetime import date
        try:
            if date.fromisoformat(expiration) < date.today():
                status = "Expired"
        except ValueError:
            pass

    # Persist to SQLite
    contract_record = {
        "id": contract_id,
        "file_name": file.filename,
        "file_path": local_path,
        "party_a": metadata.get("party_a"),
        "party_b": metadata.get("party_b"),
        "contract_type": metadata.get("contract_type"),
        "effective_date": metadata.get("effective_date"),
        "expiration_date": metadata.get("expiration_date"),
        "renewal_notice_days": metadata.get("renewal_notice_days"),
        "total_value": metadata.get("total_value"),
        "currency": metadata.get("currency"),
        "governing_law": metadata.get("governing_law"),
        "status": status,
    }
    db.insert_contract(contract_record)
    db.insert_pages(contract_id, pages)
    db.insert_clauses(contract_id, metadata.get("key_clauses", []))

    # Index into ChromaDB for RAG
    try:
        await asyncio.to_thread(
            rag_engine.index_contract, contract_id, file.filename, pages, api_key
        )
    except Exception as e:
        logger.warning(f"Vector indexing failed (non-fatal): {e}")

    return {
        "success": True,
        "contract_id": contract_id,
        "message": f"Contract '{file.filename}' processed successfully.",
        "summary": {
            "party_a": metadata.get("party_a"),
            "party_b": metadata.get("party_b"),
            "contract_type": metadata.get("contract_type"),
            "effective_date": metadata.get("effective_date"),
            "expiration_date": metadata.get("expiration_date"),
            "total_value": metadata.get("total_value"),
            "currency": metadata.get("currency"),
            "clauses_found": len(metadata.get("key_clauses", [])),
            "pages_extracted": len(pages),
            "status": status,
        },
    }


@app.get("/api/contracts")
def list_contracts(
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """List all contracts with optional search and status filter."""
    db.auto_update_expired_contracts()
    contracts = db.get_all_contracts(search=search, status=status)
    return {"contracts": contracts, "total": len(contracts)}


@app.get("/api/contracts/{contract_id}")
def get_contract(contract_id: str):
    """Get full detail of a single contract including its key clauses."""
    contract = db.get_contract_by_id(contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found.")
    return contract


@app.delete("/api/contracts/{contract_id}")
def delete_contract(contract_id: str):
    """Delete a contract and remove its vector index."""
    contract = db.get_contract_by_id(contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found.")

    # Remove the stored file
    file_path = contract.get("file_path", "")
    if file_path and os.path.exists(file_path):
        os.remove(file_path)

    db.delete_contract(contract_id)
    rag_engine.delete_contract_index(contract_id)
    return {"success": True, "message": "Contract deleted."}


@app.patch("/api/contracts/{contract_id}/status")
def update_status(contract_id: str, body: StatusUpdateRequest):
    """Manually update the status of a contract."""
    valid = {"Active", "Expired", "Terminated"}
    if body.status not in valid:
        raise HTTPException(400, f"Status must be one of {valid}")
    success = db.update_contract_status(contract_id, body.status)
    if not success:
        raise HTTPException(500, "Failed to update status.")
    return {"success": True, "status": body.status}


@app.get("/api/contracts/{contract_id}/pdf")
def serve_pdf(contract_id: str):
    """Serve the original uploaded file for in-browser PDF viewing."""
    contract = db.get_contract_by_id(contract_id)
    if not contract:
        raise HTTPException(404, "Contract not found.")
    file_path = contract.get("file_path", "")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(404, "Contract file not found on server.")
    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename=contract.get("file_name", "contract.pdf"),
    )


@app.post("/api/chat/query")
async def chat_query(body: ChatRequest):
    """
    RAG question-answering endpoint.
    Returns an answer with source citations from matching contract chunks.
    """
    if not body.question.strip():
        raise HTTPException(400, "Question cannot be empty.")

    api_key = _api_key(body.gemini_api_key)

    # Semantic retrieval
    chunks = await asyncio.to_thread(
        rag_engine.retrieve_relevant_chunks,
        body.question,
        api_key,
        body.contract_ids,
        5,  # top_k
    )

    # Generate cited answer
    result = await asyncio.to_thread(
        rag_engine.generate_answer,
        body.question,
        chunks,
        api_key,
    )

    return {
        "question": body.question,
        "answer": result["answer"],
        "sources": result["sources"],
        "chunks_retrieved": len(chunks),
    }


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
