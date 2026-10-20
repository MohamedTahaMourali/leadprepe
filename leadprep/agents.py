"""Files d'attente pour les agents aval. Chaque agent lit sa file, traite, puis enregistre son résultat.

    site_analyst  : analyse du site web           (leads avec site)
    email_writer  : rédaction d'email             (leads avec email exploitable)
    phone_caller  : appel téléphonique            (leads avec téléphone non suspect)
    sector_strategist : lit le classement des secteurs (queries.sector_priority), pas de file par lead.

Un lead sort de la file dès qu'un résultat 'done' ou 'skipped' existe pour (siren, agent).
Par défaut les entrepreneurs individuels sont exclus (données personnelles, prudence RGPD).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .db import read_sql
from .queries import sector_priority

COMMON = ["siren", "nom", "secteur", "ville", "departement", "dirigeant_principal", "effectif", "lead_score", "priorite"]
AGENTS: Dict[str, Dict] = {
    "site_analyst": {"where": "a_site = 1",
                     "columns": COMMON + ["site_web", "site_domaine", "site_confiance", "site_fiable"]},
    "email_writer": {"where": "a_email = 1 AND email_principal <> '' AND email_tiers = 0 AND email_suspect = 0",
                     "columns": COMMON + ["email_principal", "email_type", "email_mx_ok", "site_web",
                                          "site_confiance"]},
    "phone_caller": {"where": "a_telephone = 1 AND telephone_suspect = 0",
                     "columns": COMMON + ["telephone_principal", "telephone_e164", "telephone_mobile",
                                          "site_web", "email_principal"]},
}


def queue(conn: sqlite3.Connection, agent: str, limit: int = 500, min_score: int = 0,
          secteurs: Optional[List[str]] = None, include_ei: bool = False) -> pd.DataFrame:
    if agent not in AGENTS:
        raise ValueError(f"Agent inconnu : {agent!r}. Disponibles : {', '.join(AGENTS)}")
    spec = AGENTS[agent]
    conds = [spec["where"], "lead_score >= ?",
             "NOT EXISTS (SELECT 1 FROM agent_results r WHERE r.siren = leads.siren AND r.agent = ? "
             "AND r.status IN ('done','skipped'))"]
    params: list = [min_score, agent]
    if not include_ei:
        conds.append("is_ei = 0")
    if secteurs:
        conds.append(f"secteur IN ({','.join('?' * len(secteurs))})")
        params += secteurs
    sql = (f"SELECT {', '.join(spec['columns'])} FROM leads WHERE {' AND '.join(conds)} "
           f"ORDER BY lead_score DESC, siren LIMIT {int(limit)}")
    return read_sql(conn, sql, params)


def record_result(conn: sqlite3.Connection, siren: str, agent: str, status: str, payload: Optional[dict] = None) -> None:
    if status not in ("done", "failed", "skipped"):
        raise ValueError("status doit valoir done, failed ou skipped")
    with conn:
        conn.execute("INSERT INTO agent_results (siren, agent, status, payload, updated_at) "
                     "VALUES (?,?,?,?,datetime('now')) ON CONFLICT(siren, agent) DO UPDATE SET "
                     "status=excluded.status, payload=excluded.payload, updated_at=excluded.updated_at",
                     (siren, agent, status, json.dumps(payload or {}, ensure_ascii=False)))


def export_queue(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".jsonl":
        with path.open("w", encoding="utf-8") as f:
            for rec in df.to_dict("records"):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    else:
        df.to_csv(path, index=False, sep=";", encoding="utf-8-sig")
    return path


def export_sector_ranking(conn, path: Path, filters=None, top: int = 0) -> Path:
    """JSON pour l'agent stratège : classement des secteurs + pondérations utilisées."""
    from . import config as cfg
    d = sector_priority(conn, filters)
    if top:
        d = d.head(top)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"poids": cfg.SECTOR_WEIGHTS, "secteurs": d.to_dict("records")},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path
