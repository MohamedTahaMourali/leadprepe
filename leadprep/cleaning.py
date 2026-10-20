"""Nettoyage et enrichissement d'un CSV leadfinder -> DataFrame prêt pour la base.

Sortie : une ligne par entreprise (clé = SIREN), avec contacts normalisés, indicateurs de qualité,
segment, score et priorité. Aucune donnée n'est inventée : tout est dérivé des colonnes d'origine.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from . import config as cfg

EXPECTED = ["secteur", "nom", "siren", "adresse_siege", "code_postal", "ville", "dirigeant", "naf", "effectif",
            "entrepreneur_individuel", "site_web", "site_confiance", "emails", "emails_valides",
            "email_statut", "mx_detail", "telephones", "statut"]

OUT_COLUMNS = [
    "siren", "nom", "secteur", "naf", "adresse_siege", "code_postal", "ville", "departement",
    "dirigeant", "dirigeant_principal", "nb_dirigeants", "effectif", "effectif_ordre", "is_ei",
    "site_web", "site_domaine", "site_confiance", "site_fiable",
    "emails", "emails_valides", "nb_emails", "email_principal", "email_type", "email_domaine_proprio",
    "email_gratuit", "email_tiers", "email_mx_ok", "email_suspect",
    "telephones", "nb_telephones", "telephone_principal", "telephone_e164", "telephone_mobile",
    "telephone_suspect",
    "a_site", "a_email", "a_telephone", "contactable", "segment", "statut_source",
    "lead_score", "priorite", "fichier_source", "date_ingestion",
]
INT_COLUMNS = {"nb_dirigeants", "effectif_ordre", "is_ei", "site_fiable", "nb_emails", "email_domaine_proprio",
               "email_gratuit", "email_tiers", "email_mx_ok", "email_suspect", "nb_telephones",
               "telephone_suspect", "a_site", "a_email", "a_telephone", "contactable", "lead_score"}


# ------------------------------------------------------------------ lecture
def read_csv(path: Path) -> pd.DataFrame:
    """CSV leadfinder : ';' + UTF-8 avec BOM. Repli sur ',' si le séparateur diffère."""
    kw = dict(dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df = pd.read_csv(path, sep=cfg.CSV_SEP if hasattr(cfg, "CSV_SEP") else ";", **kw)
    if df.shape[1] == 1:
        df = pd.read_csv(path, sep=",", **kw)
    missing = {"siren", "nom"} - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} : colonnes obligatoires absentes : {sorted(missing)}")
    return df


# ------------------------------------------------------------------ fonctions pures
def split_pipe(s: str) -> List[str]:
    return [p.strip() for p in str(s).split("|") if p.strip()]


def phone_e164(raw: str) -> str:
    d = re.sub(r"\D", "", raw)
    return "+33" + d[1:] if len(d) == 10 and d.startswith("0") else ""


def departement(cp: str) -> str:
    if len(cp) != 5 or not cp.isdigit():
        return ""
    if cp[:2] in ("97", "98"):
        return cp[:3]
    if cp[:2] == "20":
        return "2A" if int(cp) < 20200 else "2B"
    return cp[:2]


def site_domain(url: str) -> str:
    if not url:
        return ""
    host = (urlparse(url if "//" in url else "//" + url).hostname or "").lower()
    return host.removeprefix("www.")


def _own_domain(dom: str, site_dom: str) -> bool:
    return bool(site_dom) and (dom == site_dom or dom.endswith("." + site_dom) or site_dom.endswith("." + dom))


def parse_emails(emails_raw: str, valid_raw: str, site_dom: str) -> Dict[str, object]:
    """Choisit l'email le plus exploitable (MX valide > domaine du site > nominatif, hors domaines tiers)."""
    emails = list(dict.fromkeys(e.lower() for e in split_pipe(emails_raw)))
    valid = {e.lower() for e in split_pipe(valid_raw)}
    out = dict(emails=" | ".join(emails), nb_emails=len(emails), email_principal="", email_type="",
               email_domaine_proprio=0, email_gratuit=0, email_tiers=0, email_mx_ok=0,
               email_suspect=int(len(emails) > cfg.MAX_EMAILS))
    best, best_rank = None, -10_000
    for e in emails:
        local, _, dom = e.partition("@")
        rank = ((100 if e in valid else 0) + (50 if _own_domain(dom, site_dom) else 0)
                - (80 if dom in cfg.THIRD_PARTY_DOMAINS else 0) + (10 if local not in cfg.GENERIC_LOCALS else 0))
        if rank > best_rank:
            best, best_rank = e, rank
    if best:
        local, _, dom = best.partition("@")
        out.update(email_principal=best, email_type="generique" if local in cfg.GENERIC_LOCALS else "nominatif",
                   email_domaine_proprio=int(_own_domain(dom, site_dom)),
                   email_gratuit=int(dom in cfg.FREE_PROVIDERS), email_tiers=int(dom in cfg.THIRD_PARTY_DOMAINS),
                   email_mx_ok=int(best in valid))
    return out


def parse_phones(raw: str) -> Dict[str, object]:
    phones = list(dict.fromkeys(split_pipe(raw)))
    mobile = next((p for p in phones if re.sub(r"\D", "", p)[:2] in ("06", "07")), "")
    main = phones[0] if phones else ""
    return dict(telephones=" | ".join(phones), nb_telephones=len(phones), telephone_principal=main,
                telephone_e164=phone_e164(main), telephone_mobile=mobile,
                telephone_suspect=int(len(phones) > cfg.MAX_PHONES))


# ------------------------------------------------------------------ pipeline
def clean(raw: pd.DataFrame, source_file: str, strict_email: bool = False) -> Tuple[pd.DataFrame, int]:
    """Retourne (DataFrame nettoyé, nombre de lignes rejetées).

    strict_email=True : « email rempli » = au moins un email au MX valide (sinon : un email trouvé).
    """
    df = raw.reindex(columns=EXPECTED, fill_value="").astype(str).apply(lambda s: s.str.strip())
    df["siren"] = df["siren"].str.replace(r"\D", "", regex=True).str.zfill(9)
    bad = ~df["siren"].str.fullmatch(r"\d{9}") | (df["siren"] == "000000000")
    rejected = int(bad.sum())
    df = df[~bad].reset_index(drop=True)

    # --- identité / zone
    df["nom"] = df["nom"].str.replace(r"\s+", " ", regex=True)
    df["departement"] = df["code_postal"].map(departement)
    df["is_ei"] = (df["entrepreneur_individuel"].str.lower() == "oui").astype(int)
    df["effectif"] = df["effectif"].where(~df["effectif"].isin(["", "NN"]), "inconnu")
    df["effectif_ordre"] = df["effectif"].map(cfg.EFFECTIF_RANK).fillna(0).astype(int)
    dirs = df["dirigeant"].map(split_pipe_comma)
    df["nb_dirigeants"] = dirs.map(len)
    df["dirigeant_principal"] = dirs.map(lambda d: d[0].title() if d else "")

    # --- site
    df["site_web"] = df["site_web"].str.rstrip("/")
    df["site_domaine"] = df["site_web"].map(site_domain)
    df["site_fiable"] = (df["site_confiance"] == "SIREN").astype(int)

    # --- emails et téléphones (analysés seulement quand présents)
    em_cols = ["emails", "nb_emails", "email_principal", "email_type", "email_domaine_proprio", "email_gratuit",
               "email_tiers", "email_mx_ok", "email_suspect"]
    ph_cols = ["telephones", "nb_telephones", "telephone_principal", "telephone_e164", "telephone_mobile",
               "telephone_suspect"]
    em = [parse_emails(e, v, d) if e else None for e, v, d in zip(df["emails"], df["emails_valides"], df["site_domaine"])]
    ph = [parse_phones(t) if t else None for t in df["telephones"]]
    empty_em = parse_emails("", "", "")
    empty_ph = parse_phones("")
    df[em_cols] = pd.DataFrame([x or empty_em for x in em])[em_cols].to_numpy()
    df[ph_cols] = pd.DataFrame([x or empty_ph for x in ph])[ph_cols].to_numpy()
    for c in INT_COLUMNS & set(em_cols + ph_cols):
        df[c] = df[c].astype(int)
    df["emails_valides"] = df["emails_valides"].str.lower()

    # --- indicateurs et segment
    df["a_site"] = (df["site_web"] != "").astype(int)
    df["a_telephone"] = (df["telephones"] != "").astype(int)
    df["a_email"] = ((df["email_mx_ok"] == 1) if strict_email else (df["emails"] != "")).astype(int)
    p, e, s = df["a_telephone"] == 1, df["a_email"] == 1, df["a_site"] == 1
    df["contactable"] = (p | e).astype(int)
    df["segment"] = np.select(
        [p & e & s, p & ~e, e & ~p, s & ~p & ~e, p & e & ~s],
        ["complet", "tel_sans_email", "email_sans_tel", "site_seul", "tel_email_sans_site"], default="autres")
    df["statut_source"] = df["statut"]

    # --- score 0-100 (somme des points, max = 100)
    tel_pts = np.where(p & (df["telephone_suspect"] == 0), 15, np.where(p, 5, 0))
    mail_pts = (np.where(df["email_mx_ok"] == 1, 25, np.where(e, 10, 0))
                + np.where(e & (df["email_domaine_proprio"] == 1), 10, 0)
                - np.where(df["email_tiers"] == 1, 10, 0))
    eff = df["effectif_ordre"]
    eff_pts = np.select([eff >= 5, eff == 4, eff == 3, eff == 2], [20, 15, 10, 5], default=0)
    score = (tel_pts + mail_pts + np.where(s, 10, 0) + np.where(df["site_fiable"] == 1, 10, 0)
             + eff_pts + np.where(df["is_ei"] == 0, 10, 0))
    df["lead_score"] = np.clip(score, 0, 100).astype(int)
    df["priorite"] = np.select([df["lead_score"] >= cfg.PRIORITY_HIGH, df["lead_score"] >= cfg.PRIORITY_MID],
                               ["haute", "moyenne"], default="basse")

    df["fichier_source"] = source_file
    df["date_ingestion"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # une seule ligne par SIREN dans le fichier (garde la plus riche)
    df = df.sort_values("lead_score", ascending=False, kind="stable").drop_duplicates("siren").reset_index(drop=True)
    return df[OUT_COLUMNS], rejected


def split_pipe_comma(s: str) -> List[str]:
    return [p.strip() for p in str(s).split(",") if p.strip()]
