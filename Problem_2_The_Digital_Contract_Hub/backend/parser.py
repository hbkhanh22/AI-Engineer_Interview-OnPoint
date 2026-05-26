"""
parser.py - Gemini Multimodal-powered contract parser.

Handles:
  1. Uploading PDF/image files to Gemini Files API
  2. Extracting structured JSON metadata (parties, dates, amounts, clauses)
  3. Extracting per-page raw text for RAG indexing
"""

import os
import re
import json
import time
import logging
import tempfile
from typing import Optional
from pathlib import Path

import google.generativeai as genai

logger = logging.getLogger("ContractHub-Parser")


# ── Gemini initialization ───────────────────────────────────────────────────

def init_gemini(api_key: str):
    genai.configure(api_key=api_key)


def get_model(model_name: str = "gemini-2.0-flash"):
    return genai.GenerativeModel(model_name)


# ── Upload helper ────────────────────────────────────────────────────────────

def upload_file_to_gemini(file_path: str) -> genai.types.File:
    """
    Upload a local file to Gemini Files API and wait until it is ACTIVE.
    Returns the File object whose .uri can be used in content parts.
    """
    mime_map = {
        ".pdf":  "application/pdf",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }
    suffix = Path(file_path).suffix.lower()
    mime_type = mime_map.get(suffix, "application/octet-stream")

    logger.info(f"Uploading {file_path} to Gemini Files API ...")
    uploaded = genai.upload_file(file_path, mime_type=mime_type)

    # Poll until ACTIVE (usually < 10 s for small files)
    for _ in range(30):
        f = genai.get_file(uploaded.name)
        if f.state.name == "ACTIVE":
            logger.info(f"File active: {f.uri}")
            return f
        if f.state.name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed: {f.name}")
        time.sleep(2)
    raise TimeoutError("File did not become ACTIVE within 60 seconds.")


# ── Prompt templates ─────────────────────────────────────────────────────────

COMBINED_PROMPT = """
You are an expert legal contract analyst. Read the contract document attached carefully.

Perform two tasks:
1. Extract the contract metadata and key clauses.
2. Extract the full text content of every page (transcribe exactly).

You MUST return the results as a SINGLE valid JSON object matching the schema below.
Do NOT include markdown code fences (```json), backticks, or any explanation outside the JSON.

JSON schema to follow EXACTLY:
{
  "metadata": {
    "party_a": "Full legal name of Party A (string or null)",
    "party_b": "Full legal name of Party B (string or null)",
    "contract_type": "Contract type: NDA, Service Agreement, Purchase Agreement, Lease Agreement, Employment Contract, Partnership Agreement, or Other (string or null)",
    "effective_date": "Contract start / effective date in YYYY-MM-DD format (string or null)",
    "expiration_date": "Contract end / expiration date in YYYY-MM-DD format (string or null)",
    "renewal_notice_days": <integer days advance notice required for renewal/termination, or null>,
    "total_value": <numeric contract value as a float, or null>,
    "currency": "Currency code e.g. VND, USD (string or null)",
    "governing_law": "Jurisdiction / governing law (string or null)",
    "key_clauses": [
      {
        "clause_type": "Category: Termination, Penalty, Confidentiality, Dispute Resolution, Payment, Intellectual Property, Warranty, Indemnification, or Other",
        "section_title": "Exact section/article title as written in the document",
        "page_number": <integer page number starting from 1>,
        "summary": "One to three sentence plain-language summary of this clause"
      }
    ]
  },
  "pages": [
    {
      "page_number": <integer page number starting from 1>,
      "page_text": "Full text content of this page. Preserve line breaks and paragraphs. Include all text: headers, body, footnotes, signatures, stamps."
    }
  ]
}

Rules:
- Extract ALL text from every page. Do not summarize the page text; transcribe it exactly.
- Do not skip pages even if they appear blank (use "[Blank page]" as page_text).
- If a field cannot be found, use null. Do not guess or hallucinate values.
""".strip()


# ── Core extraction functions ────────────────────────────────────────────────

def extract_combined_data(file_obj: genai.types.File, api_key: str) -> dict:
    """
    Send the uploaded file to Gemini and extract both structured metadata and page texts in a single call.
    Returns a dict with keys "metadata" and "pages".
    """
    init_gemini(api_key)
    model = get_model()

    retries = 6
    delay = 5.0
    last_error = None
    for attempt in range(retries):
        try:
            logger.info(f"Combined extraction attempt {attempt+1}/{retries}...")
            response = model.generate_content(
                [file_obj, COMBINED_PROMPT],
                generation_config={"response_mime_type": "application/json"}
            )
            raw = response.text.strip()
            data = json.loads(raw)
            logger.info("Combined extraction successful.")
            return data
        except json.JSONDecodeError as e:
            last_error = f"JSON parse error: {e}. Raw: {raw[:200] if 'raw' in locals() else 'None'}"
            logger.warning(f"Attempt {attempt+1} failed due to JSON decode error: {e}")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"Attempt {attempt+1} failed with exception: {e}")
            err_lower = last_error.lower()
            if "key not valid" in err_lower or "api_key_invalid" in err_lower or "api key" in err_lower or "403" in err_lower:
                raise
        
        if attempt < retries - 1:
            logger.info(f"Retrying combined extraction in {delay} seconds...")
            time.sleep(delay)
            delay = min(delay * 2, 30.0)

    raise RuntimeError(f"Failed to extract contract data after {retries} attempts. Last error: {last_error}")


def extract_metadata(file_obj: genai.types.File, api_key: str) -> dict:
    """[Deprecated] Thin wrapper around extract_combined_data for compatibility."""
    result = extract_combined_data(file_obj, api_key)
    return result.get("metadata", {})


def extract_page_texts(file_obj: genai.types.File, api_key: str) -> list[dict]:
    """[Deprecated] Thin wrapper around extract_combined_data for compatibility."""
    result = extract_combined_data(file_obj, api_key)
    return result.get("pages", [])


# ── Orchestration ────────────────────────────────────────────────────────────

def process_contract(file_path: str, api_key: str) -> dict:
    """
    Full pipeline:
      1. Upload file to Gemini
      2. Extract structured metadata & page texts in a single API call (to prevent 429 rate limit)
    Returns a dict with keys: metadata, pages
    """
    init_gemini(api_key)
    file_obj = upload_file_to_gemini(file_path)

    try:
        result = extract_combined_data(file_obj, api_key)
    finally:
        # Clean up the uploaded file from Gemini (save storage quota)
        try:
            genai.delete_file(file_obj.name)
            logger.info(f"Deleted Gemini file: {file_obj.name}")
        except Exception:
            pass

    return {
        "metadata": result.get("metadata", {}),
        "pages": result.get("pages", [])
    }
