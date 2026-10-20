"""Exports Excel : un classeur par fichier source (feuilles par segment) et un classeur global.

Excel plafonne à 1 048 576 lignes par feuille : les feuilles trop longues sont découpées (`_1`, `_2`...).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from . import config as cfg
from .queries import where_clause, read_sql

EXPORT_COLUMNS = ["siren", "nom", "secteur", "ville", "code_postal", "departement", "dirigeant_principal",
                  "effectif", "is_ei", "site_web", "site_confiance", "email_principal", "email_type",
                  "email_mx_ok", "emails", "telephone_principal", "telephone_mobile", "telephones",
                  "telephone_suspect", "lead_score", "priorite", "segment", "fichier_source"]
HEADERS = {"is_ei": "entrepreneur_individuel"}
ROW_LIMIT = 1_000_000
NARROW = {"siren": 11, "code_postal": 11, "departement": 11, "effectif": 11, "is_ei": 12, "lead_score": 11,
          "priorite": 10, "email_mx_ok": 11, "telephone_suspect": 12, "site_confiance": 12}
WIDE = {"nom": 38, "site_web": 36, "emails": 42, "email_principal": 34, "telephones": 34, "secteur": 22}


def _present(df: pd.DataFrame) -> pd.DataFrame:
    out = df[EXPORT_COLUMNS].copy()
    out["is_ei"] = out["is_ei"].map({1: "oui", 0: "non"})
    out["email_mx_ok"] = out["email_mx_ok"].map({1: "oui", 0: "non"})
    out["telephone_suspect"] = out["telephone_suspect"].map({1: "oui", 0: ""})
    return out.rename(columns=HEADERS)


def _write_sheet(writer, df: pd.DataFrame, sheet: str, fmt_head) -> None:
    ws_df = _present(df)
    ws_df.to_excel(writer, sheet_name=sheet, index=False)
    ws = writer.sheets[sheet]
    for i, col in enumerate(ws_df.columns):
        ws.write(0, i, col, fmt_head)
        ws.set_column(i, i, NARROW.get(col, WIDE.get(col, 20)))
    ws.freeze_panes(1, 2)
    ws.autofilter(0, 0, max(len(ws_df), 1), len(ws_df.columns) - 1)


def write_workbook(path: Path, df: pd.DataFrame, title: str, include_autres: bool = True) -> Dict[str, int]:
    """Écrit un classeur : feuille « Résumé » + une feuille par segment (feuilles vides omises)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    counts = {seg: int((df["segment"] == seg).sum()) for seg in cfg.SEGMENTS}
    total = len(df)
    opts = {"strings_to_formulas": False, "strings_to_urls": False}   # un nom d'entreprise « =... » ne devient pas une formule
    with pd.ExcelWriter(path, engine="xlsxwriter", engine_kwargs={"options": opts}) as writer:
        head = writer.book.add_format({"bold": True, "bg_color": "#1F4E78", "font_color": "white", "border": 1})
        summary = pd.DataFrame([{"segment": cfg.SEGMENTS[s][0], "description": cfg.SEGMENTS[s][1], "lignes": n,
                                 "part": round(100 * n / total, 1) if total else 0.0}
                                for s, n in counts.items() if n or s != "tel_email_sans_site"])
        summary.loc[len(summary)] = ["TOTAL", title, total, 100.0 if total else 0.0]
        summary.to_excel(writer, sheet_name="Résumé", index=False)
        ws = writer.sheets["Résumé"]
        for i, (col, w) in enumerate(zip(summary.columns, (26, 62, 10, 8))):
            ws.write(0, i, col if col != "part" else "part (%)", head)
            ws.set_column(i, i, w)
        for seg, (sheet, _) in cfg.SEGMENTS.items():
            if not counts[seg] or (seg == "autres" and not include_autres):
                continue
            part = df[df["segment"] == seg].sort_values(["lead_score", "nom"], ascending=[False, True])
            chunks = [part.iloc[i:i + ROW_LIMIT] for i in range(0, len(part), ROW_LIMIT)]
            for k, chunk in enumerate(chunks, 1):
                _write_sheet(writer, chunk, sheet if len(chunks) == 1 else f"{sheet}_{k}", head)
    return counts


def export_per_file(conn, out_dir: Optional[Path] = None, include_autres: bool = True) -> list:
    out_dir = Path(out_dir or cfg.EXPORT_DIR / "excel")
    done = []
    for (fichier,) in conn.execute("SELECT DISTINCT fichier_source FROM leads ORDER BY 1").fetchall():
        df = read_sql(conn, "SELECT * FROM leads WHERE fichier_source=?", (fichier,))
        path = out_dir / f"{Path(fichier).stem}.xlsx"
        write_workbook(path, df, f"Fichier source {fichier}", include_autres)
        done.append(path)
    return done


def export_global(conn, out_dir: Optional[Path] = None, include_autres: bool = False, filters=None) -> Path:
    """Classeur national. Les « autres » (aucun contact, ~75 % des lignes) sont exclus par défaut."""
    out_dir = Path(out_dir or cfg.EXPORT_DIR)
    w, p = where_clause(filters)
    df = read_sql(conn, f"SELECT * FROM leads{w}", p)
    path = out_dir / "leads_national.xlsx"
    write_workbook(path, df, "Base nationale (tous fichiers)", include_autres)
    return path


def export_parquet_csv(conn, out_dir: Optional[Path] = None) -> list:
    """Formats machine pour les agents / BI : CSV par segment (+ Parquet si pyarrow est installé)."""
    out_dir = Path(out_dir or cfg.EXPORT_DIR / "segments")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for seg in cfg.SEGMENTS:
        df = read_sql(conn, "SELECT * FROM leads WHERE segment=?", (seg,))
        if df.empty:
            continue
        p = out_dir / f"{seg}.csv"
        df.to_csv(p, index=False, sep=";", encoding="utf-8-sig")
        written.append(p)
        try:
            df.to_parquet(out_dir / f"{seg}.parquet", index=False)
        except ImportError:
            pass
    return written
