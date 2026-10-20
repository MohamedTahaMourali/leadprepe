"""CLI : `python -m leadprep <commande>`. La commande `run` enchaîne tout (idéale pour cron / tâche planifiée)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config as cfg
from . import agents as ag
from . import db, excel_export as xl, queries as q


def _conn():
    conn = db.connect()
    db.init_db(conn)
    return conn


def cmd_ingest(a) -> int:
    files = db.discover_csv(a.input or [cfg.RAW_DIR])
    if not files:
        print(f"Aucun CSV trouvé dans {a.input or [cfg.RAW_DIR]}", file=sys.stderr)
        return 2
    conn = _conn()
    new = skipped = failed = 0
    for i, f in enumerate(files, 1):
        try:
            r = db.ingest_file(conn, f, strict_email=a.strict_email, force=a.force)
        except Exception as e:  # un fichier cassé ne bloque pas les 94 autres
            failed += 1
            print(f"[{i}/{len(files)}] {f.name} : ÉCHEC ({e})", file=sys.stderr)
            continue
        if r["skipped"]:
            skipped += 1
            print(f"[{i}/{len(files)}] {f.name} : déjà ingéré, ignoré")
        else:
            new += 1
            print(f"[{i}/{len(files)}] {f.name} : {r['lignes']} lignes ({r['nouveaux']} nouvelles, "
                  f"{r['mis_a_jour']} mises à jour, {r['rejets']} rejetées)")
    total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    print(f"\n{new} ingéré(s), {skipped} ignoré(s), {failed} en échec. Base : {total} entreprises ({cfg.DB_PATH})")
    return 1 if failed else 0


def cmd_export(a) -> int:
    conn = _conn()
    if a.per_file:
        paths = xl.export_per_file(conn, a.out, include_autres=not a.no_autres)
        print(f"{len(paths)} classeur(s) par fichier -> {paths[0].parent if paths else a.out}")
    if a.national:
        print("Classeur national ->", xl.export_global(conn, a.out, include_autres=a.with_autres))
    if a.segments:
        print(f"{len(xl.export_parquet_csv(conn))} fichier(s) CSV/Parquet par segment ->", cfg.EXPORT_DIR / "segments")
    return 0


def cmd_stats(a) -> int:
    conn = _conn()
    k = q.kpis(conn)
    print(f"Entreprises : {k['n']}   contactables : {k['contactables']}   complètes : {k['complets']}")
    print(q.segment_counts(conn).to_string(index=False))
    print()
    print(q.sector_priority(conn).head(a.top)[["rang", "secteur", "n", "n_contactables_hors_ei", "taux_contact",
                                               "score_moyen", "score_secteur"]].to_string(index=False))
    return 0


def cmd_agent(a) -> int:
    conn = _conn()
    df = ag.queue(conn, a.agent, a.limit, a.min_score, a.secteur, a.include_ei)
    out = Path(a.out or cfg.EXPORT_DIR / "agents" / f"{a.agent}.{a.format}")
    ag.export_queue(df, out)
    print(f"{len(df)} lead(s) dans la file '{a.agent}' -> {out}")
    return 0


def cmd_run(a) -> int:
    rc = cmd_ingest(argparse.Namespace(input=a.input, strict_email=a.strict_email, force=False))
    conn = _conn()
    xl.export_per_file(conn)
    xl.export_global(conn)
    xl.export_parquet_csv(conn)
    p = ag.export_sector_ranking(conn, cfg.EXPORT_DIR / "agents" / "secteurs_priorises.json")
    print(f"Exports prêts dans {cfg.EXPORT_DIR} ; classement des secteurs -> {p}")
    return rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leadprep", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("ingest", help="nettoie et charge les CSV dans la base (idempotent)")
    i.add_argument("--input", "-i", nargs="+", type=Path, help=f"dossiers/fichiers CSV (défaut : {cfg.RAW_DIR})")
    i.add_argument("--strict-email", action="store_true", help="« email rempli » = au moins un email au MX valide")
    i.add_argument("--force", action="store_true", help="réingère même si le fichier est inchangé")
    i.set_defaults(fn=cmd_ingest)

    e = sub.add_parser("export", help="exports Excel / CSV")
    e.add_argument("--per-file", action="store_true", help="un classeur par fichier source")
    e.add_argument("--national", action="store_true", help="un classeur global")
    e.add_argument("--segments", action="store_true", help="CSV/Parquet par segment")
    e.add_argument("--with-autres", action="store_true", help="national : inclure la feuille « Autres » (très volumineuse)")
    e.add_argument("--no-autres", action="store_true", help="par fichier : omettre la feuille « Autres »")
    e.add_argument("--out", type=Path)
    e.set_defaults(fn=cmd_export)

    s = sub.add_parser("stats", help="résumé et classement des secteurs en console")
    s.add_argument("--top", type=int, default=15)
    s.set_defaults(fn=cmd_stats)

    g = sub.add_parser("agent-queue", help="exporte la file d'attente d'un agent")
    g.add_argument("agent", choices=sorted(ag.AGENTS))
    g.add_argument("--limit", type=int, default=500)
    g.add_argument("--min-score", type=int, default=0)
    g.add_argument("--secteur", nargs="+")
    g.add_argument("--include-ei", action="store_true", help="inclure les entrepreneurs individuels")
    g.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    g.add_argument("--out")
    g.set_defaults(fn=cmd_agent)

    r = sub.add_parser("run", help="tout enchaîner : ingestion + exports + classement des secteurs")
    r.add_argument("--input", "-i", nargs="+", type=Path)
    r.add_argument("--strict-email", action="store_true")
    r.set_defaults(fn=cmd_run)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    return a.fn(a)
