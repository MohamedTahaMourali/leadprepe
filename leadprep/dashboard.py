"""Dashboard Streamlit : `streamlit run leadprep/dashboard.py` (ou `python -m streamlit run ...`).

Toutes les métriques viennent de queries.py (même définition que la CLI et les agents).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # lancement direct par streamlit

import pandas as pd
import plotly.express as px
import streamlit as st

from leadprep import agents as ag
from leadprep import config as cfg
from leadprep import db, excel_export as xl, queries as q

st.set_page_config(page_title="Leadprep — prospection TPE/PME", page_icon="📊", layout="wide")
SEG_LABEL = {k: v[0] for k, v in cfg.SEGMENTS.items()}
SEG_COLORS = {"complet": "#2E7D32", "tel_sans_email": "#F9A825", "email_sans_tel": "#1976D2",
              "site_seul": "#8E24AA", "tel_email_sans_site": "#00897B", "autres": "#9E9E9E"}


def conn():
    if not cfg.DB_PATH.exists():
        st.error(f"Base introuvable : {cfg.DB_PATH}. Lancez d'abord `python -m leadprep ingest`.")
        st.stop()
    return db.connect(readonly=True)


@st.cache_data(ttl=60, show_spinner=False)
def run(fn_name: str, filters_key: tuple, **kw):
    """Cache par (fonction, filtres) ; une connexion lecture seule par appel."""
    f = {k: list(v) if isinstance(v, tuple) else v for k, v in filters_key}
    c = conn()
    try:
        return getattr(q, fn_name)(c, f, **kw)
    finally:
        c.close()


def key(f: dict) -> tuple:
    return tuple(sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in f.items() if v))


# ------------------------------------------------------------------ filtres
c0 = conn()
opts = q.filter_options(c0)
c0.close()
st.sidebar.header("Filtres")
f = {
    "secteurs": st.sidebar.multiselect("Secteur", opts["secteurs"]),
    "departements": st.sidebar.multiselect("Département", opts["departements"]),
    "segments": st.sidebar.multiselect("Segment", list(cfg.SEGMENTS), format_func=lambda s: SEG_LABEL[s]),
    "priorites": st.sidebar.multiselect("Priorité", ["haute", "moyenne", "basse"]),
    "exclude_ei": st.sidebar.checkbox("Exclure les entrepreneurs individuels", value=False),
    "min_score": st.sidebar.slider("Score de lead minimum", 0, 100, 0, 5),
}
if st.sidebar.button("↻ Recharger les données"):
    st.cache_data.clear()
fk = key(f)

st.title("📊 Leadprep — tableau de bord de prospection")
k = run("kpis", fk)
if not k["n"]:
    st.warning("Aucune entreprise ne correspond aux filtres.")
    st.stop()

tab_ov, tab_sec, tab_geo, tab_q, tab_ex, tab_ag = st.tabs(
    ["Vue d'ensemble", "Secteurs à viser", "Géographie", "Qualité des données", "Explorateur & export", "Agents"])

# ------------------------------------------------------------------ vue d'ensemble
with tab_ov:
    c = st.columns(6)
    c[0].metric("Entreprises", f"{k['n']:,}".replace(",", " "))
    c[1].metric("Contactables (tel ou email)", f"{k['contactables']:,}".replace(",", " "), f"{100*k['contactables']/k['n']:.1f} %")
    c[2].metric("Fiches complètes", f"{k['complets']:,}".replace(",", " "), f"{100*k['complets']/k['n']:.1f} %")
    c[3].metric("Emails à MX valide", f"{k['email_mx_ok']:,}".replace(",", " "))
    c[4].metric("Sites confirmés par SIREN", f"{k['site_siren']:,}".replace(",", " "), f"sur {k['avec_site']} sites", delta_color="off")
    c[5].metric("Priorité haute", f"{k['haute']:,}".replace(",", " "))
    seg = run("segment_counts", fk)
    seg["libellé"] = seg["segment"].map(SEG_LABEL)
    a, b = st.columns([3, 2])
    fig = px.bar(seg, x="libellé", y="n", color="segment", color_discrete_map=SEG_COLORS, text="n",
                 title="Entreprises par segment")
    fig.update_layout(showlegend=False, xaxis_title=None, yaxis_title=None)
    a.plotly_chart(fig, use_container_width=True)
    fig = px.pie(seg[seg["segment"] != "autres"], names="libellé", values="n", hole=.45,
                 color="segment", color_discrete_map=SEG_COLORS, title="Répartition des fiches exploitables")
    b.plotly_chart(fig, use_container_width=True)
    st.dataframe(seg[["libellé", "n", "pct"]].rename(columns={"n": "lignes", "pct": "%"}), hide_index=True)

# ------------------------------------------------------------------ secteurs
with tab_sec:
    sp = run("sector_priority", fk)
    st.caption("Score secteur (0-100) = volume de contactables hors EI (échelle log) {volume:.0%} · taux de contact {taux_contact:.0%} · "
               "qualité moyenne des leads {qualite:.0%} · part d'entreprises avec salariés {taille:.0%}. "
               "Pondérations modifiables dans config.py.".format(**cfg.SECTOR_WEIGHTS))
    a, b = st.columns(2)
    top = sp.head(15).iloc[::-1]
    a.plotly_chart(px.bar(top, x="score_secteur", y="secteur", orientation="h", title="Top secteurs (score)"),
                   use_container_width=True)
    b.plotly_chart(px.scatter(sp, x="taux_contact", y="n_contactables_hors_ei", size="n", color="score_moyen",
                              hover_name="secteur", log_y=True, color_continuous_scale="Viridis",
                              title="Volume vs taux de contact (taille = entreprises, couleur = qualité)"),
                   use_container_width=True)
    st.dataframe(sp, hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ géographie
with tab_geo:
    dp = run("dept_table", fk)
    a, b = st.columns(2)
    a.plotly_chart(px.bar(dp.head(25), x="departement", y="n_contactables", color="taux_contact",
                          title="Contactables par département (25 premiers)"), use_container_width=True)
    sd = run("sector_by_dept", fk)
    if not sd.empty:
        piv = sd.pivot_table(index="secteur", columns="departement", values="n_contactables", fill_value=0)
        b.plotly_chart(px.imshow(piv, aspect="auto", color_continuous_scale="Blues",
                                 title="Contactables : secteur x département (top 20)"), use_container_width=True)
    st.dataframe(dp, hide_index=True, use_container_width=True)

# ------------------------------------------------------------------ qualité
with tab_q:
    qt = run("quality_tables", fk)
    st.info("Point d'attention : un site « NOM » est un site probable (le nom de l'entreprise y figure) ; seul « SIREN » est certain. "
            "Vérifiez la part de sites SIREN avant de lancer des campagnes.")
    a, b, c = st.columns(3)
    a.plotly_chart(px.pie(qt["site_confiance"], names="valeur", values="n", hole=.4, title="Confiance du site"), use_container_width=True)
    b.plotly_chart(px.pie(qt["email_origine"], names="valeur", values="n", hole=.4, title="Origine de l'email principal"), use_container_width=True)
    c.plotly_chart(px.bar(qt["effectif"], x="valeur", y="n", title="Effectif (INSEE)"), use_container_width=True)
    s = qt["suspects"].iloc[0]
    d = st.columns(3)
    d[0].metric("Fiches à téléphones suspects (> %d)" % cfg.MAX_PHONES, int(s["telephones_suspects"] or 0))
    d[1].metric("Fiches à emails suspects (> %d)" % cfg.MAX_EMAILS, int(s["emails_suspects"] or 0))
    d[2].metric("Emails sur domaine tiers", int(s["emails_domaine_tiers"] or 0))
    st.plotly_chart(px.histogram(qt["scores"], x="lead_score", nbins=20, title="Distribution du score (leads contactables)"),
                    use_container_width=True)
    st.subheader("Historique d'ingestion")
    c1 = conn()
    st.dataframe(q.ingest_overview(c1), hide_index=True, use_container_width=True)
    c1.close()

# ------------------------------------------------------------------ explorateur
with tab_ex:
    n_show = st.select_slider("Lignes affichées", [500, 1000, 2000, 5000], 1000)
    c1 = conn()
    df = q.leads_page(c1, f, limit=n_show)
    c1.close()
    st.dataframe(xl._present(df), hide_index=True, use_container_width=True)
    st.caption(f"{len(df)} lignes affichées sur {k['n']} (triées par score).")
    buf = io.BytesIO()
    c1 = conn()
    full = q.leads_page(c1, f, limit=200_000)   # export limité pour rester fluide ; utilisez la CLI pour l'intégralité
    c1.close()
    st.download_button("⬇ CSV (jusqu'à 200 000 lignes filtrées)",
                       xl._present(full).to_csv(index=False, sep=";").encode("utf-8-sig"), "leads_filtres.csv", "text/csv")

# ------------------------------------------------------------------ agents
with tab_ag:
    st.write("Aperçu des files d'attente (leads pas encore traités par chaque agent, entrepreneurs individuels exclus).")
    c1 = conn()
    cols = st.columns(len(ag.AGENTS))
    for col, name in zip(cols, ag.AGENTS):
        qdf = ag.queue(c1, name, limit=100_000)
        done = c1.execute("SELECT COUNT(*) FROM agent_results WHERE agent=? AND status='done'", (name,)).fetchone()[0]
        col.metric(name, f"{len(qdf):,}".replace(",", " "), f"{done} traités", delta_color="off")
    c1.close()
    st.code("python -m leadprep agent-queue email_writer --limit 500 --min-score 55 --format jsonl", language="bash")
