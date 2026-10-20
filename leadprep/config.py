"""Configuration centrale : chemins, listes éditables, pondérations. Tout ce qui se règle est ici."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("LEADPREP_DATA", ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"                                   # déposez ici les CSV de leadfinder
DB_PATH = Path(os.getenv("LEADPREP_DB", DATA_DIR / "leads.db"))
EXPORT_DIR = DATA_DIR / "exports"

# --------------------------------------------------------------------------- segments
# Partition exclusive, évaluée dans cet ordre. "Rempli" = valeur non vide après nettoyage.
SEGMENTS = {
    "complet":             ("1_Complet",              "Téléphone + email + site"),
    "tel_sans_email":      ("2_Tel_sans_email",       "Téléphone rempli, email vide (site indifférent)"),
    "email_sans_tel":      ("3_Email_sans_tel",       "Email rempli, téléphone vide (site indifférent)"),
    "site_seul":           ("4_Site_seul",            "Site rempli, téléphone et email vides"),
    "tel_email_sans_site": ("5b_Tel_email_sans_site", "Téléphone + email mais sans site (cas non prévu par les 5 règles)"),
    "autres":              ("5_Autres",               "Aucun contact (introuvable)"),
}

# --------------------------------------------------------------------------- effectif INSEE
EFFECTIF_ORDER = ["inconnu", "0 salarié", "1 ou 2", "3 à 5", "6 à 9", "10 à 19", "20 à 49", "50 à 99",
                  "100 à 199", "200 à 249", "250 à 499", "500 à 999", "1 000 à 1 999",
                  "2 000 à 4 999", "5 000 à 9 999", "10 000 et plus"]
EFFECTIF_RANK = {label: i for i, label in enumerate(EFFECTIF_ORDER)}

# --------------------------------------------------------------------------- qualité des contacts
MAX_PHONES = 5          # au-delà : page annuaire / réseau, pas le numéro de l'entreprise -> flag suspect
MAX_EMAILS = 5
GENERIC_LOCALS = {"contact", "info", "infos", "accueil", "bonjour", "hello", "secretariat", "cabinet",
                  "administration", "commercial", "direction", "agence", "devis", "reception", "mail"}
FREE_PROVIDERS = {"gmail.com", "orange.fr", "wanadoo.fr", "hotmail.com", "hotmail.fr", "yahoo.fr",
                  "yahoo.com", "outlook.com", "outlook.fr", "free.fr", "sfr.fr", "neuf.fr", "laposte.net",
                  "live.fr", "icloud.com", "bbox.fr", "aol.com", "gmx.fr", "protonmail.com", "me.com"}
# Domaines vus dans les pages de sites mais qui ne sont pas ceux de l'entreprise (éditeurs, hébergeurs...).
THIRD_PARTY_DOMAINS = {"oracle.com", "sage.com", "domainmarket.com", "ovh.com", "ovh.net", "wix.com",
                       "wixpress.com", "sentry.io", "godaddy.com", "gandi.net", "ionos.com", "ionos.fr",
                       "1and1.com", "squarespace.com", "wordpress.com", "google.com", "microsoft.com",
                       "apple.com", "shopify.com", "example.com", "jimdo.com", "weebly.com", "o2switch.fr"}

# --------------------------------------------------------------------------- score de lead (0-100)
PRIORITY_HIGH = 55
PRIORITY_MID = 30

# --------------------------------------------------------------------------- priorisation des secteurs
SECTOR_WEIGHTS = {"volume": 0.35, "taux_contact": 0.25, "qualite": 0.25, "taille": 0.15}
