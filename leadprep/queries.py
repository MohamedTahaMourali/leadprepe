"""Requêtes d'analyse partagées par le dashboard, la CLI et les agents (une seule définition des métriques)."""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from . import config as cfg
from .db import read_sql

Filters = Dict[str, object]  # secteurs, departements, segments (listes) ; exclude_ei, min_score, fichier


def where_clause(f: Optional[Filters] = None) -> Tuple[str, list]:
    f, conds, params = f or {}, [], []
    for key, col in (("secteurs", "secteur"), ("departements", "departement"), ("segments", "segment"),
                     ("priorites", "priorite")):
        vals = f.get(key)
        if vals:
            conds.append(f"{col} IN ({','.join('?' * len(vals))})")
            params += list(vals)
    if f.get("exclude_ei"):
        conds.append("is_ei = 0")
    if f.get("min_score"):
        conds.append("lead_score >= ?")
        params.append(int(f["min_score"]))
    if f.get("fichier"):
        conds.append("fichier_source = ?")
        params.append(f["fichier"])
    return (" WHERE " + " AND ".join(conds)) if conds else "", params


def kpis(conn: sqlite3.Connection, f: Optional[Filters] = None) -> Dict[str, float]:
    w, p = where_clause(f)
    r = conn.execute(f"""SELECT COUNT(*) n, COALESCE(SUM(contactable),0) contactables,
        COALESCE(SUM(segment='complet'),0) complets, COALESCE(SUM(a_email),0) avec_email,
        COALESCE(SUM(a_telephone),0) avec_tel, COALESCE(SUM(a_site),0) avec_site,
        COALESCE(SUM(email_mx_ok),0) email_mx_ok, COALESCE(SUM(site_fiable),0) site_siren,
        COALESCE(SUM(is_ei),0) ei, COALESCE(AVG(CASE WHEN contactable=1 THEN lead_score END),0) score_moyen,
        COALESCE(SUM(priorite='haute'),0) haute FROM leads{w}""", p).fetchone()
    return dict(r)


def segment_counts(conn, f=None) -> pd.DataFrame:
    w, p = where_clause(f)
    df = read_sql(conn, f"SELECT segment, COUNT(*) n FROM leads{w} GROUP BY segment", p)
    order = list(cfg.SEGMENTS)
    df["segment"] = pd.Categorical(df["segment"], order, ordered=True)
    df = df.sort_values("segment").reset_index(drop=True)
    df["pct"] = (100 * df["n"] / df["n"].sum()).round(1) if len(df) else []
    df["segment"] = df["segment"].astype(str)
    return df


def _norm(s: pd.Series) -> pd.Series:
    rng = s.max() - s.min()
    return (s - s.min()) / rng if rng > 0 else pd.Series(0.5, index=s.index)


def sector_priority(conn, f=None, weights: Optional[Dict[str, float]] = None) -> pd.DataFrame:
    """Classement des secteurs à viser. Score 0-100 = volume (log, hors EI) + taux de contact + qualité + taille."""
    w, p = where_clause(f)
    d = read_sql(conn, f"""SELECT secteur, COUNT(*) n, SUM(contactable) n_contactables,
        SUM(contactable=1 AND is_ei=0) n_contactables_hors_ei, SUM(segment='complet') n_complet,
        SUM(email_mx_ok) n_email_mx_ok, AVG(CASE WHEN contactable=1 THEN lead_score END) score_moyen,
        SUM(contactable=1 AND is_ei=0 AND effectif_ordre>=3) n_avec_salaries
        FROM leads{w} GROUP BY secteur""", p)
    if d.empty:
        return d
    wt = weights or cfg.SECTOR_WEIGHTS
    d["score_moyen"] = d["score_moyen"].fillna(0).round(1)
    d["taux_contact"] = (d["n_contactables"] / d["n"]).round(3)
    d["taux_complet"] = (d["n_complet"] / d["n"]).round(3)
    d["part_avec_salaries"] = (d["n_avec_salaries"] / d["n_contactables_hors_ei"].replace(0, np.nan)).fillna(0).round(3)
    d["score_secteur"] = (100 * (wt["volume"] * _norm(np.log1p(d["n_contactables_hors_ei"]))
                                 + wt["taux_contact"] * _norm(d["taux_contact"])
                                 + wt["qualite"] * _norm(d["score_moyen"])
                                 + wt["taille"] * _norm(d["part_avec_salaries"]))).round(1)
    d = d.sort_values("score_secteur", ascending=False).reset_index(drop=True)
    d.insert(0, "rang", d.index + 1)
    return d


def dept_table(conn, f=None) -> pd.DataFrame:
    w, p = where_clause(f)
    d = read_sql(conn, f"""SELECT departement, COUNT(*) n, SUM(contactable) n_contactables,
        SUM(segment='complet') n_complet, ROUND(AVG(lead_score),1) score_moyen FROM leads{w}
        GROUP BY departement ORDER BY n_contactables DESC""", p)
    d["taux_contact"] = (d["n_contactables"] / d["n"]).round(3)
    return d


def sector_by_dept(conn, f=None, top_depts: int = 20) -> pd.DataFrame:
    w, p = where_clause(f)
    return read_sql(conn, f"""SELECT departement, secteur, SUM(contactable) n_contactables FROM leads{w}
        GROUP BY departement, secteur HAVING departement IN (SELECT departement FROM leads{w}
        GROUP BY departement ORDER BY SUM(contactable) DESC LIMIT {int(top_depts)})""", p + p)


def quality_tables(conn, f=None) -> Dict[str, pd.DataFrame]:
    w, p = where_clause(f)
    q = lambda sql: read_sql(conn, sql, p)
    return {
        "site_confiance": q(f"SELECT CASE WHEN site_confiance='' THEN 'aucun site' ELSE site_confiance END AS valeur, "
                            f"COUNT(*) n FROM leads{w} GROUP BY 1"),
        "email_type": q(f"SELECT CASE WHEN email_principal='' THEN 'aucun email' ELSE email_type END AS valeur, "
                        f"COUNT(*) n FROM leads{w} GROUP BY 1"),
        "email_origine": q(f"""SELECT CASE WHEN email_principal='' THEN 'aucun email' WHEN email_tiers=1 THEN 'domaine tiers'
                            WHEN email_gratuit=1 THEN 'messagerie gratuite' WHEN email_domaine_proprio=1 THEN 'domaine du site'
                            ELSE 'autre domaine' END AS valeur, COUNT(*) n FROM leads{w} GROUP BY 1"""),
        "effectif": q(f"SELECT effectif AS valeur, effectif_ordre, COUNT(*) n FROM leads{w} GROUP BY 1, 2 ORDER BY 2"),
        "suspects": q(f"SELECT SUM(telephone_suspect) telephones_suspects, SUM(email_suspect) emails_suspects, "
                      f"SUM(email_tiers) emails_domaine_tiers FROM leads{w}"),
        "scores": q(f"SELECT lead_score FROM leads{w}{' AND' if w else ' WHERE'} contactable=1"),
    }


def leads_page(conn, f=None, limit: int = 2000, order: str = "lead_score DESC") -> pd.DataFrame:
    w, p = where_clause(f)
    if order not in ("lead_score DESC", "nom ASC", "secteur ASC, lead_score DESC"):
        order = "lead_score DESC"
    return read_sql(conn, f"SELECT * FROM leads{w} ORDER BY {order} LIMIT {int(limit)}", p)


def filter_options(conn) -> Dict[str, List[str]]:
    return {
        "secteurs": [r[0] for r in conn.execute("SELECT DISTINCT secteur FROM leads ORDER BY 1")],
        "departements": [r[0] for r in conn.execute("SELECT DISTINCT departement FROM leads WHERE departement<>'' ORDER BY 1")],
        "fichiers": [r[0] for r in conn.execute("SELECT DISTINCT fichier_source FROM leads ORDER BY 1")],
    }


def ingest_overview(conn) -> pd.DataFrame:
    return read_sql(conn, "SELECT fichier, nb_lignes, nb_rejets, nb_nouveaux, nb_mis_a_jour, ingested_at "
                          "FROM ingest_log ORDER BY ingested_at DESC")
