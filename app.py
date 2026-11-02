"""
app.py — Interface Streamlit « Panier du Dirham ».

Lancement (voir README) : streamlit run app.py
Cette interface ne contient AUCUN calcul financier : elle appelle les modules de src/.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Permet d'importer le dossier src/ quel que soit le dossier de lancement
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import calculations as calc  # noqa: E402
from src import charts  # noqa: E402
from src import data_manager as dm  # noqa: E402
from src.investing_scraper import read_investing_file  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")

st.set_page_config(page_title="Panier du Dirham", page_icon="🏦", layout="wide")

st.title("🏦 PANIER DU DIRHAM")
st.caption("Analyse, calcul et suivi des cours théoriques USD/MAD et EUR/MAD "
           "à partir du panier de devises du Dirham.")


def fmt_date(ts) -> str:
    """Affiche une date au format français JJ/MM/AAAA."""
    return pd.Timestamp(ts).strftime("%d/%m/%Y") if pd.notna(ts) else "—"


def last_valid(df: pd.DataFrame, col: str):
    """Renvoie (date, valeur) de la dernière observation non vide d'une colonne."""
    s = df[["Date", col]].dropna()
    if s.empty:
        return None, None
    return s["Date"].iloc[-1], s[col].iloc[-1]


# ---------------------------------------------------------------------------
# Chargement initial de l'historique (premier lancement : création du CSV)
# ---------------------------------------------------------------------------
try:
    history = dm.initialize_history()
except Exception as exc:  # message explicite plutôt qu'une page d'erreur brute
    st.error(f"Impossible de charger l'historique : {exc}")
    history = None

# Message laissé par l'action précédente (ex. après une mise à jour)
if "message" in st.session_state:
    kind, text = st.session_state.pop("message")
    getattr(st, kind)(text)

# ---------------------------------------------------------------------------
# Barre latérale
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("📂 Données")

    upload = st.file_uploader("Importer le fichier Excel historique (.xlsx)", type=["xlsx"],
                              help="Lit l'onglet « cours fx ». Les données existantes sont conservées.")
    if upload is not None and st.button("Fusionner ce fichier à l'historique"):
        try:
            result = dm.import_excel_history(upload)
            st.session_state["message"] = ("success",
                f"{len(result['added'])} date(s) ajoutée(s), "
                f"{len(result['conflicts'])} conflit(s) ignoré(s).")
        except Exception as exc:
            st.session_state["message"] = ("error", f"Import impossible : {exc}")
        st.rerun()

    with st.expander("Source EUR/USD de secours (optionnel)"):
        st.caption("Utile si Investing.com bloque la lecture automatique.")
        eurusd_file = st.file_uploader("Fichier CSV ou Excel Investing (Date, Price)",
                                       type=["csv", "xlsx"], key="eurusd_file")
        swap_dates = st.checkbox("Dates Excel inversées jour/mois", value=True,
                                 help="Cochez si les dates Excel ont été lues en jour/mois inversés "
                                      "(c'est le cas du fichier d'origine). Décochez si vos dates sont justes.")

    if st.button("🔄 Mettre à jour les données", type="primary"):
        with st.spinner("Récupération de BAM et d'Investing.com…"):
            try:
                eurusd_df = None
                if eurusd_file is not None:
                    eurusd_df = read_investing_file(eurusd_file, swap_datetime=swap_dates,
                                                    filename=eurusd_file.name)
                status = dm.run_update(eurusd_df=eurusd_df)
                n_err = len(status["erreurs"])
                st.session_state["message"] = (
                    "success" if n_err == 0 else "warning",
                    f"Mise à jour terminée : {status['lignes_ajoutees']} date(s) ajoutée(s)."
                    + (f" {n_err} avertissement(s), voir l'onglet Mise à jour." if n_err else ""),
                )
            except Exception as exc:
                st.session_state["message"] = ("error", f"Mise à jour impossible : {exc}")
        st.rerun()

    if history is not None and len(history):
        st.metric("Dernière date historique", fmt_date(history["Date"].max()))
    last_status = dm.load_last_update()
    if last_status:
        st.caption(f"Dernière mise à jour : {last_status['derniere_maj'].replace('T', ' ')}")

    st.divider()
    st.header("⚖️ Pondérations")
    w_usd_pct = st.slider("Poids USD (%)", min_value=0, max_value=100, value=40, step=1)
    w_eur_pct = 100 - w_usd_pct
    col_a, col_b = st.columns(2)
    col_a.metric("Poids USD", f"{w_usd_pct} %")
    col_b.metric("Poids EUR", f"{w_eur_pct} %")

    st.divider()
    st.header("🔎 Filtres")
    devise = st.selectbox("Devise", ["USD/MAD", "EUR/MAD", "Les deux"], index=2)

if history is None or history.empty:
    st.info("Aucun historique disponible. Importez votre fichier Excel dans la barre latérale.")
    st.stop()

# ---------------------------------------------------------------------------
# Calculs (recalculés à chaque changement de pondération, sans toucher aux données)
# ---------------------------------------------------------------------------
results_all = calc.compute_theoretical(history, w_usd_pct)

with st.sidebar:
    if not results_all.empty:
        d_min = results_all["Date_calcul"].min().date()
        d_max = results_all["Date_calcul"].max().date()
        date_range = st.date_input("Période", value=(d_min, d_max), min_value=d_min, max_value=d_max)
    else:
        date_range = None

if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = pd.Timestamp(date_range[0]), pd.Timestamp(date_range[1])
    results = results_all[results_all["Date_calcul"].between(start, end)]
else:
    results = results_all

metrics = calc.compute_metrics(results)

# ---------------------------------------------------------------------------
# Onglets
# ---------------------------------------------------------------------------
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "📊 Vue d'ensemble", "📈 Évolution des cours", "📉 Analyse des écarts",
    "⚖️ Pondérations et sensibilité", "🗂️ Données historiques", "🔄 Mise à jour des données",
])

# --- Onglet 1 : vue d'ensemble ----------------------------------------------
with tab1:
    st.subheader("Derniers cours")
    d_usd, v_usd = last_valid(history, "USD_MAD_BAM")
    d_eur, v_eur = last_valid(history, "EUR_MAD_BAM")
    d_fx, v_fx = last_valid(history, "EUR_USD")

    c1, c2, c3 = st.columns(3)
    c1.metric(f"USD/MAD BAM ({fmt_date(d_usd)})", f"{v_usd:.4f}" if v_usd is not None else "—")
    c2.metric(f"EUR/MAD BAM ({fmt_date(d_eur)})", f"{v_eur:.4f}" if v_eur is not None else "—")
    c3.metric(f"EUR/USD Investing ({fmt_date(d_fx)})", f"{v_fx:.4f}" if v_fx is not None else "—")

    st.subheader("Cours théoriques et écarts")
    if results.empty:
        st.warning("Pas assez de données pour calculer un cours théorique sur cette période.")
    else:
        last = results.iloc[-1]
        c4, c5, c6, c7 = st.columns(4)
        c4.metric(f"USD/MAD théorique ({fmt_date(last['Date_calcul'])})", f"{last['USD_MAD_calcule']:.4f}")
        c5.metric(f"EUR/MAD théorique ({fmt_date(last['Date_calcul'])})", f"{last['EUR_MAD_calcule']:.4f}")
        real_rows = results.dropna(subset=["Ecart_USD_MAD"])
        if not real_rows.empty:
            lr = real_rows.iloc[-1]
            c6.metric(f"Dernier écart USD/MAD ({fmt_date(lr['Date_calcul'])})",
                      f"{lr['Ecart_USD_MAD']:+.4f}", f"{lr['Ecart_USD_MAD_pct']:+.3f} %")
            c7.metric(f"Dernier écart EUR/MAD ({fmt_date(lr['Date_calcul'])})",
                      f"{lr['Ecart_EUR_MAD']:+.4f}", f"{lr['Ecart_EUR_MAD_pct']:+.3f} %")

    # Estimation du jour courant, séparée des données réelles
    est = calc.pending_estimate(history, w_usd_pct, pd.Timestamp.today())
    if est:
        st.info(
            f"**Estimation du {fmt_date(est['Date_calcul'])} — {est['statut']}** · "
            f"USD/MAD ≈ {est['USD_MAD_calcule']:.4f} · EUR/MAD ≈ {est['EUR_MAD_calcule']:.4f} "
            f"(calculée à partir du {fmt_date(est['Date_input'])})."
        )

    st.subheader("Pondérations utilisées")
    st.write(f"USD **{w_usd_pct} %** · EUR **{w_eur_pct} %**")

# --- Onglet 2 : évolution des cours -----------------------------------------
with tab2:
    if results.empty:
        st.warning("Aucune donnée à afficher.")
    else:
        if devise in ("USD/MAD", "Les deux"):
            st.plotly_chart(charts.fig_level(results, "USD"), config=charts.PLOT_CONFIG)
        if devise in ("EUR/MAD", "Les deux"):
            st.plotly_chart(charts.fig_level(results, "EUR"), config=charts.PLOT_CONFIG)
        if devise == "Les deux":
            st.plotly_chart(charts.fig_comparison(results), config=charts.PLOT_CONFIG)

# --- Onglet 3 : analyse des écarts ------------------------------------------
with tab3:
    if results.empty:
        st.warning("Aucune donnée à afficher.")
    else:
        st.plotly_chart(charts.fig_abs_errors(results), config=charts.PLOT_CONFIG)
        st.plotly_chart(charts.fig_pct_errors(results), config=charts.PLOT_CONFIG)

# --- Onglet 4 : pondérations et sensibilité ---------------------------------
with tab4:
    st.caption("Les pondérations se modifient dans la barre latérale ; tout cet onglet se recalcule.")
    st.subheader("Performance du modèle")
    if results.empty or metrics["USD"]["n"] == 0:
        st.warning("Aucune date ne dispose à la fois d'un cours théorique et d'un cours réel BAM.")
    else:
        for cur, label in [("USD", "USD/MAD"), ("EUR", "EUR/MAD")]:
            m = metrics[cur]
            st.markdown(f"**{label}** — {m['n']} date(s) comparée(s)")
            k = st.columns(4)
            k[0].metric("MAE", f"{m['MAE']:.4f}")
            k[1].metric("RMSE", f"{m['RMSE']:.4f}")
            k[2].metric("Biais moyen", f"{m['Biais']:+.4f}")
            k[3].metric("Corrélation", f"{m['Correlation']:.4f}" if pd.notna(m["Correlation"]) else "—")
            k2 = st.columns(2)
            k2[0].metric("Erreur moyenne (%)", f"{m['Erreur_pct_moy']:+.3f} %")
            k2[1].metric("Écart-type des erreurs", f"{m['Ecart_type']:.4f}" if pd.notna(m["Ecart_type"]) else "—")

    st.subheader("Comparaison de scénarios")
    scenarios = st.multiselect("Scénarios USD (%) à comparer", options=list(range(0, 101, 5)),
                               default=calc.DEFAULT_SCENARIOS)
    if scenarios:
        table = calc.compare_scenarios(history, sorted(scenarios))
        st.dataframe(table.style.format({
            "Poids USD": "{:.0%}", "Poids EUR": "{:.0%}",
            "MAE USD/MAD": "{:.4f}", "RMSE USD/MAD": "{:.4f}",
            "MAE EUR/MAD": "{:.4f}", "RMSE EUR/MAD": "{:.4f}"}), hide_index=True)

    st.subheader("Impact du poids USD sur les erreurs")
    sweep = calc.compare_scenarios(history, list(range(0, 101, 10)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=sweep["Poids USD"], y=sweep["MAE USD/MAD"], mode="lines+markers", name="MAE USD/MAD"))
    fig.add_trace(go.Scatter(x=sweep["Poids USD"], y=sweep["MAE EUR/MAD"], mode="lines+markers", name="MAE EUR/MAD"))
    fig.update_layout(template="plotly_white", height=380, xaxis_title="Poids USD",
                      yaxis_title="MAE (MAD)", xaxis_tickformat=".0%", hovermode="x unified")
    st.plotly_chart(fig, config=charts.PLOT_CONFIG)

# --- Onglet 5 : données historiques -----------------------------------------
with tab5:
    st.subheader("Historique des données réelles")
    st.dataframe(history, hide_index=True)
    st.subheader("Résultats calculés (avec les inputs utilisés)")
    st.dataframe(results, hide_index=True)
    st.caption("Les exports contiennent les dates et valeurs non arrondies.")
    d1, d2, d3 = st.columns(3)
    d1.download_button("⬇️ Réel (CSV)", dm.export_csv_bytes(history),
                       file_name="historique_reel.csv", mime="text/csv")
    d2.download_button("⬇️ Calculs (CSV)", dm.export_csv_bytes(results),
                       file_name="resultats_calcules.csv", mime="text/csv")
    d3.download_button("⬇️ Tout (Excel)",
                       dm.export_excel_bytes({"Reel": history, "Calculs": results, "Scenarios": calc.compare_scenarios(history)}),
                       file_name="panier_dirham_export.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

# --- Onglet 6 : mise à jour -------------------------------------------------
with tab6:
    st.subheader("Dernière mise à jour")
    status = dm.load_last_update()
    if status is None:
        st.info("Aucune mise à jour n'a encore été lancée. Utilisez le bouton de la barre latérale.")
    else:
        st.write(f"**Date et heure :** {status['derniere_maj'].replace('T', ' ')}")
        st.write(f"**Dernière date de l'historique :** {status['derniere_date_historique'] or '—'}")
        st.metric("Lignes ajoutées", status["lignes_ajoutees"])
        if status["dates_ajoutees"]:
            st.write("**Dates ajoutées :** " + ", ".join(status["dates_ajoutees"]))
        if status["dates_ignorees"]:
            st.write("**Dates ignorées (déjà présentes) :** " + ", ".join(status["dates_ignorees"]))
        if status["erreurs"]:
            st.subheader("Avertissements")
            for err in status["erreurs"]:
                st.warning(err)
        else:
            st.success("Aucune erreur signalée.")
