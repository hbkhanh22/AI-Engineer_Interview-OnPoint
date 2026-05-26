"""
database.py - SQLite database connection, schema creation, and CRUD operations
for the Digital Contract Hub.
"""

import sqlite3
import os
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("ContractHub-DB")

# Path to the SQLite database file
DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage"))
DB_PATH = os.path.join(DB_DIR, "contracts.db")


def get_connection() -> sqlite3.Connection:
    """Returns a new SQLite connection with row_factory set for dict-like access."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Initialize database tables if they do not exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS contracts (
            id                  TEXT PRIMARY KEY,
            file_name           TEXT NOT NULL,
            file_path           TEXT NOT NULL,
            party_a             TEXT,
            party_b             TEXT,
            contract_type       TEXT,
            effective_date      TEXT,
            expiration_date     TEXT,
            renewal_notice_days INTEGER,
            total_value         REAL,
            currency            TEXT,
            governing_law       TEXT,
            status              TEXT DEFAULT 'Active',
            uploaded_at         TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS contract_pages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id   TEXT NOT NULL,
            page_number   INTEGER NOT NULL,
            page_text     TEXT,
            FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS contract_clauses (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            contract_id   TEXT NOT NULL,
            clause_type   TEXT,
            section_title TEXT,
            page_number   INTEGER,
            summary       TEXT,
            FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
        );
    """)

    conn.commit()
    conn.close()
    logger.info(f"Database initialized at: {DB_PATH}")


def insert_contract(data: Dict[str, Any]) -> bool:
    """Insert a new contract record into the database."""
    conn = get_connection()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO contracts
            (id, file_name, file_path, party_a, party_b, contract_type,
             effective_date, expiration_date, renewal_notice_days,
             total_value, currency, governing_law, status)
            VALUES
            (:id, :file_name, :file_path, :party_a, :party_b, :contract_type,
             :effective_date, :expiration_date, :renewal_notice_days,
             :total_value, :currency, :governing_law, :status)
        """, data)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error inserting contract: {e}")
        return False
    finally:
        conn.close()


def insert_pages(contract_id: str, pages: List[Dict[str, Any]]):
    """Insert page-level text content for a contract."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM contract_pages WHERE contract_id = ?", (contract_id,))
        conn.executemany(
            "INSERT INTO contract_pages (contract_id, page_number, page_text) VALUES (?, ?, ?)",
            [(contract_id, p["page_number"], p["page_text"]) for p in pages]
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error inserting pages for {contract_id}: {e}")
    finally:
        conn.close()


def insert_clauses(contract_id: str, clauses: List[Dict[str, Any]]):
    """Insert extracted key clauses for a contract."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM contract_clauses WHERE contract_id = ?", (contract_id,))
        conn.executemany(
            """INSERT INTO contract_clauses
               (contract_id, clause_type, section_title, page_number, summary)
               VALUES (?, ?, ?, ?, ?)""",
            [(contract_id,
              c.get("clause_type"), c.get("section_title"),
              c.get("page_number"), c.get("summary")) for c in clauses]
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error inserting clauses for {contract_id}: {e}")
    finally:
        conn.close()


def get_all_contracts(search: Optional[str] = None,
                      status: Optional[str] = None) -> List[Dict]:
    """Retrieve all contracts with optional search/filter."""
    conn = get_connection()
    try:
        query = """
            SELECT c.*,
                   (SELECT COUNT(*) FROM contract_clauses cl WHERE cl.contract_id = c.id) as clause_count
            FROM contracts c
            WHERE 1=1
        """
        params = []
        if search:
            query += " AND (c.party_a LIKE ? OR c.party_b LIKE ? OR c.file_name LIKE ? OR c.contract_type LIKE ?)"
            s = f"%{search}%"
            params.extend([s, s, s, s])
        if status and status != "all":
            query += " AND c.status = ?"
            params.append(status)
        query += " ORDER BY c.uploaded_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_contract_by_id(contract_id: str) -> Optional[Dict]:
    """Retrieve a single contract with its clauses."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM contracts WHERE id = ?", (contract_id,)
        ).fetchone()
        if not row:
            return None
        contract = dict(row)
        clauses = conn.execute(
            "SELECT * FROM contract_clauses WHERE contract_id = ? ORDER BY page_number",
            (contract_id,)
        ).fetchall()
        contract["clauses"] = [dict(c) for c in clauses]
        return contract
    finally:
        conn.close()


def get_pages_by_contract(contract_id: str) -> List[Dict]:
    """Retrieve all page texts for a contract."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT page_number, page_text FROM contract_pages WHERE contract_id = ? ORDER BY page_number",
            (contract_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_contract(contract_id: str) -> bool:
    """Delete a contract and all related data."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error deleting contract {contract_id}: {e}")
        return False
    finally:
        conn.close()


def update_contract_status(contract_id: str, status: str) -> bool:
    """Update the status of a contract (Active, Expired, Terminated)."""
    conn = get_connection()
    try:
        conn.execute("UPDATE contracts SET status = ? WHERE id = ?", (status, contract_id))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating status for {contract_id}: {e}")
        return False
    finally:
        conn.close()


def auto_update_expired_contracts():
    """Mark contracts as Expired if expiration_date has passed."""
    conn = get_connection()
    try:
        conn.execute("""
            UPDATE contracts
            SET status = 'Expired'
            WHERE expiration_date IS NOT NULL
              AND expiration_date < date('now')
              AND status = 'Active'
        """)
        conn.commit()
    finally:
        conn.close()
