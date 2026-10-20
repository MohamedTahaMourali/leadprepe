# leadprep

Prépare les CSV de **leadfinder** (95 fichiers, un par département) pour la prospection : nettoyage, base centrale,
classeurs Excel par segment, dashboard et files d'attente pour les agents.

```
data/raw/*.csv ──ingest──> data/leads.db (SQLite) ──> exports Excel / CSV / Parquet
                                   │                 ├─> dashboard Streamlit
                                   │                 └─> files d'agents (JSONL) + classement des secteurs (JSON)
```

## Démarrage

```bash
pip install -r requirements.txt
cp /chemin/vers/output/france/*.csv data/raw/      # les 95 CSV (les fichiers commençant par « _ » sont ignorés)
python -m leadprep run                             # ingestion + exports + classement des secteurs
streamlit run leadprep/dashboard.py                # dashboard
```

`run` est idempotent : un CSV inchangé est ignoré (hash SHA-256), un CSV modifié est réingéré sans doublons.
Planification : `0 6 * * * cd /chemin/leadprep && python -m leadprep run` (cron) ou une tâche planifiée Windows.
Pour pointer directement sur la sortie de `run_france.py` : `python -m leadprep run --input ../output/france`.

## Segments (partition exclusive, évaluée dans cet ordre)

| Feuille Excel | Règle |
|---|---|
| `1_Complet` | téléphone **et** email **et** site |
| `2_Tel_sans_email` | téléphone rempli, email vide (site indifférent) |
| `3_Email_sans_tel` | email rempli, téléphone vide (site indifférent) |
| `4_Site_seul` | site rempli, téléphone et email vides |
| `5b_Tel_email_sans_site` | téléphone + email sans site : **cas non couvert par vos 5 règles**, isolé pour ne pas tomber dans « Autres » (0 ligne dans 01.csv) |
| `5_Autres` | aucun contact |

« Email rempli » = au moins un email trouvé. Avec `--strict-email`, il faut un email au **MX valide**
(13 fiches de 01.csv ont un email à MX non valide). Changer ce réglage impose `--force`.

## Ce qui est ajouté pour les agents

- `email_principal` choisi parmi les emails de la fiche : MX valide > domaine du site > nominatif ; domaines tiers
  (oracle, sage, hébergeurs...) pénalisés. `email_type` (générique/nominatif), `email_domaine_proprio`, `email_gratuit`.
- `telephone_principal`, `telephone_e164` (+33...), `telephone_mobile` ; `telephone_suspect` / `email_suspect` quand
  une fiche liste plus de 5 numéros ou emails (page annuaire, réseau : en 01.csv, 60 fiches à téléphones suspects).
- `site_domaine`, `site_fiable` (1 = confirmé par SIREN), `dirigeant_principal`, `effectif_ordre`, `departement` (2A/2B, DOM).
- `lead_score` 0-100 et `priorite` (haute ≥ 55, moyenne ≥ 30) : points pour téléphone, email MX valide, email sur le
  domaine du site, site, site confirmé SIREN, effectif, hors entrepreneur individuel. Réglable dans `config.py`.

## Agents

```bash
python -m leadprep agent-queue site_analyst --limit 500 --min-score 55
python -m leadprep agent-queue email_writer --secteur comptabilite avocats --format jsonl
python -m leadprep agent-queue phone_caller --limit 200
python -m leadprep stats                           # classement des secteurs en console
```
Les agents enregistrent leur travail avec `agents.record_result(conn, siren, agent, "done", {...})` (table
`agent_results`) : un lead traité sort de la file. L'agent stratège lit `data/exports/agents/secteurs_priorises.json`.
Les entrepreneurs individuels sont exclus des files par défaut (`--include-ei` pour les inclure).

## Choix du stockage

SQLite : ~2 millions d'entreprises attendues, or une feuille Excel plafonne à 1 048 576 lignes et un classeur de cette
taille est inutilisable. SQLite est un fichier unique, interrogeable en SQL, par pandas, Streamlit et les agents.
Excel reste un format de **sortie** (par fichier : tout ; national : sans la feuille « Autres », `--with-autres` pour
l'inclure, découpée automatiquement). Si l'équipe grandit : PostgreSQL, le schéma se transpose tel quel.

## Tests

```bash
python -m unittest discover -s tests -v     # 13 tests
```
