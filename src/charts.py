"""
charts.py — Graphiques Plotly interactifs.

Chaque fonction reçoit le DataFrame de résultats (issu de calculations.compute_theoretical)
et renvoie une figure Plotly. Les graphiques offrent par défaut : zoom, déplacement,
valeurs au survol, et le bouton de téléchargement PNG de Plotly.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

COLOR_REAL = "#1f77b4"      # bleu : cours réels BAM
COLOR_CALC = "#ff7f0e"      # orange : cours théoriques calculés
COLOR_USD = "#2ca02c"
COLOR_EUR = "#9467bd"
COLOR_ZERO = "#444444"

PLOT_CONFIG = {
    "displaylogo": False,
    "scrollZoom": True,
    "toImageButtonOptions": {"format": "png", "scale": 2},
}


def _layout(fig: go.Figure, title: str, yaxis_title: str) -> go.Figure:
    fig.update_layout(
        title=title,
        template="plotly_white",
        hovermode="x unified",
        dragmode="zoom",
        xaxis_title="Date de publication BAM",
        yaxis_title=yaxis_title,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=20, t=80, b=40),
        height=420,
    )
    return fig


def _series(results: pd.DataFrame, col: str) -> pd.DataFrame:
    """Renvoie Date_calcul et la colonne demandée, sans les valeurs manquantes."""
    return results[["Date_calcul", col]].dropna()


def fig_level(results: pd.DataFrame, currency: str) -> go.Figure:
    """Graphiques 1 et 2 : cours réel BAM contre cours théorique calculé."""
    real_col = f"{currency}_MAD_BAM_reel"
    calc_col = f"{currency}_MAD_calcule"
    fig = go.Figure()
    real = _series(results, real_col)
    calc = _series(results, calc_col)
    fig.add_trace(go.Scatter(x=real["Date_calcul"], y=real[real_col], mode="lines+markers",
                             name=f"{currency}/MAD réel (BAM)", line=dict(color=COLOR_REAL),
                             hovertemplate="%{y:.4f}<extra>réel</extra>"))
    fig.add_trace(go.Scatter(x=calc["Date_calcul"], y=calc[calc_col], mode="lines",
                             name=f"{currency}/MAD théorique", line=dict(color=COLOR_CALC, dash="dot"),
                             hovertemplate="%{y:.4f}<extra>théorique</extra>"))
    return _layout(fig, f"Évolution du {currency}/MAD : réel contre théorique", f"{currency}/MAD")


def fig_comparison(results: pd.DataFrame) -> go.Figure:
    """
    Graphique 3 : USD/MAD et EUR/MAD ramenés à la même base 100 (première date = 100),
    pour comparer leurs évolutions relatives sur un seul graphique lisible.
    """
    fig = go.Figure()
    specs = [
        ("USD_MAD_BAM_reel", "USD/MAD réel", COLOR_USD, "solid"),
        ("USD_MAD_calcule", "USD/MAD théorique", COLOR_USD, "dot"),
        ("EUR_MAD_BAM_reel", "EUR/MAD réel", COLOR_EUR, "solid"),
        ("EUR_MAD_calcule", "EUR/MAD théorique", COLOR_EUR, "dot"),
    ]
    for col, label, color, dash in specs:
        s = _series(results, col)
        if s.empty:
            continue
        rebased = s[col] / s[col].iloc[0] * 100
        fig.add_trace(go.Scatter(x=s["Date_calcul"], y=rebased, mode="lines", name=label,
                                 line=dict(color=color, dash=dash),
                                 hovertemplate="%{y:.2f}<extra>" + label + "</extra>"))
    return _layout(fig, "Comparaison USD/MAD et EUR/MAD (base 100 = première date)", "Indice (base 100)")


def fig_abs_errors(results: pd.DataFrame) -> go.Figure:
    """Graphique 4 : écarts absolus USD et EUR, avec une ligne horizontale à zéro."""
    fig = go.Figure()
    usd = _series(results, "Ecart_USD_MAD")
    eur = _series(results, "Ecart_EUR_MAD")
    fig.add_trace(go.Scatter(x=usd["Date_calcul"], y=usd["Ecart_USD_MAD"], mode="lines+markers",
                             name="Écart USD/MAD", line=dict(color=COLOR_USD)))
    fig.add_trace(go.Scatter(x=eur["Date_calcul"], y=eur["Ecart_EUR_MAD"], mode="lines+markers",
                             name="Écart EUR/MAD", line=dict(color=COLOR_EUR)))
    fig.add_hline(y=0, line_dash="dash", line_color=COLOR_ZERO, annotation_text="0")
    return _layout(fig, "Écarts absolus : calculé − réel (MAD)", "Écart (MAD)")


def fig_pct_errors(results: pd.DataFrame) -> go.Figure:
    """Graphique 5 : écarts relatifs en pourcentage pour les deux devises."""
    fig = go.Figure()
    usd = _series(results, "Ecart_USD_MAD_pct")
    eur = _series(results, "Ecart_EUR_MAD_pct")
    fig.add_trace(go.Scatter(x=usd["Date_calcul"], y=usd["Ecart_USD_MAD_pct"], mode="lines+markers",
                             name="Écart % USD/MAD", line=dict(color=COLOR_USD),
                             hovertemplate="%{y:.3f} %<extra>USD</extra>"))
    fig.add_trace(go.Scatter(x=eur["Date_calcul"], y=eur["Ecart_EUR_MAD_pct"], mode="lines+markers",
                             name="Écart % EUR/MAD", line=dict(color=COLOR_EUR),
                             hovertemplate="%{y:.3f} %<extra>EUR</extra>"))
    fig.add_hline(y=0, line_dash="dash", line_color=COLOR_ZERO)
    return _layout(fig, "Écarts relatifs en % : (calculé − réel) / réel", "Écart (%)")
