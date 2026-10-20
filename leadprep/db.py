"""Base centrale SQLite : schéma, ingestion idempotente (hash des fichiers) et upsert par SIREN.

Pourquoi SQLite plutôt qu'un XLSX : 95 fichiers x ~21 000 lignes ~ 2 M d'entreprises, alors qu'une feuille Excel
est limitée à 1 048 576 lignes. SQLite = un seul fichier, zéro serveur, SQL, lisible par pandas, par Streamlit
et par les agents. Excel reste un format d'EXPORT (voir excel_export.py).
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List

import pandas as pd

from . import config as cfg
from .cleaning import INT_COLUMNS, OUT_COLUMNS, clean, read_csv

SCHEMA_VERSION = 1


def connect(path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path or cfg.DB_PATH)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    cols = ",\n  ".join(
        f"{c} {'INTEGER' if c in INT_COLUMNS else 'TEXT'}{' PRIMARY KEY' if c == 'siren' else ''}"
        for c in OUT_COLUMNS)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS leads (
      {cols}
    );
    CREATE INDEX IF NOT EXISTS ix_leads_secteur ON leads(secteur);
    CREATE INDEX IF NOT EXISTS ix_leads_dept ON leads(departement);
    CREATE INDEX IF NOT EXISTS ix_leads_segment ON leads(segment);
    CREATE INDEX IF NOT EXISTS ix_leads_score ON leads(lead_score DESC);
    CREATE INDEX IF NOT EXISTS ix_leads_fichier ON leads(fichier_source);

    CREATE TABLE IF NOT EXISTS ingest_log (
      fichier TEXT NOT NULL, sha256 TEXT NOT NULL, strict_email INTEGER NOT NULL,
      nb_lignes INTEGER, nb_rejets INTEGER, nb_nouveaux INTEGER, nb_mis_a_jour INTEGER,
      ingested_at TEXT DEFAULT (datetime('now')),
      PRIMARY KEY (fichier, sha256, strict_email)
    );

    -- Résultats des agents (analyse de site, rédaction d'email, appel...) : une ligne par (lead, agent).
    CREATE TABLE IF NOT EXISTS agent_results (
      siren TEXT NOT NULL, agent TEXT NOT NULL, status TEXT NOT NULL,   -- done | failed | skipped
      payload TEXT, updated_at TEXT DEFAULT (datetime('now')),
      PRIMARY KEY (siren, agent)
    );

    DROP VIEW IF EXISTS v_segments;
    CREATE VIEW v_segments AS
      SELECT segment, COUNT(*) AS n, ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM leads), 1) AS pct
      FROM leads GROUP BY segment;
    DROP VIEW IF EXISTS v_sector_stats;
    CREATE VIEW v_sector_stats AS
      SELECT secteur, COUNT(*) AS n, SUM(contactable) AS n_contactables,
             SUM(contactable = 1 AND is_ei = 0) AS n_contactables_hors_ei,
             SUM(segment = 'complet') AS n_complet,
             ROUND(AVG(CASE WHEN contactable = 1 THEN lead_score END), 1) AS score_moyen,
             SUM(contactable = 1 AND effectif_ordre >= 3) AS n_contactables_3plus
      FROM leads GROUP BY secteur;
    DROP VIEW IF EXISTS v_dept_stats;
    CREATE VIEW v_dept_stats AS
      SELECT departement, COUNT(*) AS n, SUM(contactable) AS n_contactables,
             SUM(segment = 'complet') AS n_complet, ROUND(AVG(lead_score), 1) AS score_moyen
      FROM leads GROUP BY departement;
    """)
    conn.commit()


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_csv(inputs: Iterable[Path]) -> List[Path]:
    """Fichiers CSV d'un ou plusieurs dossiers/fichiers ; ignore les fusions (`_france_all.csv`) et temporaires."""
    files: List[Path] = []
    for p in map(Path, inputs):
        found = sorted(p.glob("*.csv")) if p.is_dir() else [p]
        files += [f for f in found if not f.name.startswith(("_", "~", "."))]
    return files


def _upsert_sql() -> str:
    cols = ", ".join(OUT_COLUMNS)
    marks = ", ".join("?" for _ in OUT_COLUMNS)
    sets = ", ".join(f"{c}=excluded.{c}" for c in OUT_COLUMNS if c != "siren")
    # Un SIREN déjà connu n'est remplacé que si la nouvelle ligne est au moins aussi riche (score).
    return (f"INSERT INTO leads ({cols}) VALUES ({marks}) ON CONFLICT(siren) DO UPDATE SET {sets} "
            f"WHERE excluded.lead_score >= leads.lead_score")


def ingest_file(conn: sqlite3.Connection, path: Path, strict_email: bool = False, force: bool = False) -> Dict:
    """Ingère un CSV. Retourne un dict de bilan ; `skipped=True` si ce fichier (même contenu) est déjà chargé."""
    path = Path(path)
    digest = sha256_of(path)
    if not force and conn.execute(
            "SELECT 1 FROM ingest_log WHERE fichier=? AND sha256=? AND strict_email=?",
            (path.name, digest, int(strict_email))).fetchone():
        return {"fichier": path.name, "skipped": True}
    df, rejected = clean(read_csv(path), path.name, strict_email)
    before = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    rows = df.astype(object).where(df.notna(), None).itertuples(index=False, name=None)
    with conn:  # transaction : tout ou rien par fichier
        conn.executemany(_upsert_sql(), rows)
        after = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        conn.execute("INSERT OR REPLACE INTO ingest_log (fichier, sha256, strict_email, nb_lignes, nb_rejets, "
                     "nb_nouveaux, nb_mis_a_jour) VALUES (?,?,?,?,?,?,?)",
                     (path.name, digest, int(strict_email), len(df), rejected, after - before,
                      len(df) - (after - before)))
    return {"fichier": path.name, "skipped": False, "lignes": len(df), "rejets": rejected,
            "nouveaux": after - before, "mis_a_jour": len(df) - (after - before)}


def read_sql(conn: sqlite3.Connection, sql: str, params=()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)
