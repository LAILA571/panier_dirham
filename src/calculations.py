"""
calculations.py — Calculs du panier du Dirham.

Ce module ne fait AUCUN accès réseau et ne lit AUCUN fichier.
Il reçoit des DataFrames et renvoie des DataFrames : c'est ce qui permet
de le tester facilement avec des données fictives.

Rappel des conventions :
- Date_calcul : date de publication BAM pour laquelle on calcule un cours théorique (t).
- Date_input  : dernière date réelle COMMUNE (USD, EUR, EUR/USD disponibles) STRICTEMENT
                antérieure à t. C'est la règle t-1 : on n'utilise jamais le jour t.
- Les cours calculés ne sont JAMAIS réinjectés comme inputs : seuls les cours réels servent.

Formules utilisées (version financièrement cohérente, voir README) :

    USD/MAD estimé(t) = w_USD × USD/MAD_BAM(t_input)
                      + w_EUR × ( EUR/MAD_BAM(t_input) ÷ EUR/USD(t_input) )

    EUR/MAD estimé(t) = USD/MAD estimé(t) × EUR/USD(t_input)

Pourquoi une DIVISION pour la partie EUR (et non une multiplication, comme dans la
formule littérale du cahier des charges) :
    1 EUR = 10,96 MAD  et  1 EUR = 1,118 USD
    donc 1 USD = 10,96 / 1,118 = 9,80 MAD  (cohérent avec USD/MAD BAM ≈ 9,84)
    La multiplication 10,96 × 1,118 = 12,25 MAD donnerait un cours absurde.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Colonnes des données réelles (historique) utilisées comme inputs
REAL_COLS = ["USD_MAD_BAM", "EUR_MAD_BAM", "EUR_USD"]

# Pondération USD par défaut (en %). Ce n'est PAS une constante de calcul :
# l'interface la fait varier. Elle sert seulement de valeur initiale.
DEFAULT_W_USD_PCT = 40.0


class WeightError(ValueError):
    """Erreur levée si une pondération est invalide."""


# ---------------------------------------------------------------------------
# 1. Pondérations
# ---------------------------------------------------------------------------
def weights_from_usd_pct(w_usd_pct: float) -> tuple[float, float]:
    """
    Transforme le poids USD saisi en % (0 à 100) en deux poids (fractions) dont la somme = 1.

    Exemple : 40 -> (0.40, 0.60)
    """
    if w_usd_pct is None or not np.isfinite(w_usd_pct):
        raise WeightError("Le poids USD doit être un nombre.")
    if not 0 <= w_usd_pct <= 100:
        raise WeightError(f"Le poids USD doit être entre 0 et 100 % (reçu : {w_usd_pct}).")
    w_usd = round(w_usd_pct / 100.0, 10)
    w_eur = round(1.0 - w_usd, 10)  # Poids EUR = 100 % - Poids USD
    return w_usd, w_eur


# ---------------------------------------------------------------------------
# 2. Préparation de l'historique réel
# ---------------------------------------------------------------------------
def prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie l'historique réel avant calcul :
    - Date convertie en datetime, dates invalides supprimées ;
    - une seule ligne par date (on garde la première valeur non vide par colonne) ;
    - tri chronologique.
    Les valeurs manquantes restent manquantes (NaN) : jamais remplacées par 0.
    """
    df = history.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    for col in REAL_COLS:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.groupby("Date", as_index=False)[REAL_COLS].first()
    return df.sort_values("Date").reset_index(drop=True)


def real_common_base(history: pd.DataFrame) -> pd.DataFrame:
    """
    Renvoie uniquement les dates où les 3 cours réels sont disponibles en même temps.
    Ce sont les seules dates utilisables comme inputs d'un calcul.
    """
    hist = prepare_history(history)
    return hist.dropna(subset=REAL_COLS).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 3. Calcul des cours théoriques pour chaque date de publication BAM
# ---------------------------------------------------------------------------
def compute_theoretical(history: pd.DataFrame, w_usd_pct: float = DEFAULT_W_USD_PCT) -> pd.DataFrame:
    """
    Calcule les cours théoriques pour chaque date où BAM a publié au moins un cours réel,
    et compare ces cours avec les cours réels publiés (quand ils existent).

    Retourne un DataFrame avec les colonnes :
        Date_calcul, Date_input, USD_MAD_BAM_t_1, EUR_MAD_BAM_t_1, EUR_USD_t_1,
        Poids_USD, Poids_EUR, USD_MAD_calcule, USD_MAD_BAM_reel, Ecart_USD_MAD,
        Ecart_USD_MAD_pct, EUR_MAD_calcule, EUR_MAD_BAM_reel, Ecart_EUR_MAD, Ecart_EUR_MAD_pct

    Une date sans aucun input réel antérieur (tout début de l'historique) n'apparaît pas.
    """
    w_usd, w_eur = weights_from_usd_pct(w_usd_pct)

    hist = prepare_history(history)
    base = real_common_base(history)

    # Dates de publication BAM = dates où au moins un des deux cours BAM est connu
    targets = hist.loc[hist[["USD_MAD_BAM", "EUR_MAD_BAM"]].notna().any(axis=1),
                       ["Date", "USD_MAD_BAM", "EUR_MAD_BAM"]].copy()
    targets = targets.rename(columns={
        "Date": "Date_calcul",
        "USD_MAD_BAM": "USD_MAD_BAM_reel",
        "EUR_MAD_BAM": "EUR_MAD_BAM_reel",
    })

    # Inputs : pour chaque date t, la dernière base STRICTEMENT antérieure à t.
    # allow_exact_matches=False garantit qu'on n'utilise jamais le jour t lui-même.
    inputs = base.rename(columns={
        "Date": "Date_input",
        "USD_MAD_BAM": "USD_MAD_BAM_t_1",
        "EUR_MAD_BAM": "EUR_MAD_BAM_t_1",
        "EUR_USD": "EUR_USD_t_1",
    })

    merged = pd.merge_asof(
        targets.sort_values("Date_calcul"),
        inputs.sort_values("Date_input"),
        left_on="Date_calcul",
        right_on="Date_input",
        direction="backward",
        allow_exact_matches=False,
    )
    merged = merged.dropna(subset=["Date_input"]).reset_index(drop=True)
    if merged.empty:
        return _empty_results()

    merged["Poids_USD"] = w_usd
    merged["Poids_EUR"] = w_eur

    # Formule corrigée : partie EUR convertie en USD par une DIVISION par EUR/USD
    merged["USD_MAD_calcule"] = (
        w_usd * merged["USD_MAD_BAM_t_1"]
        + w_eur * (merged["EUR_MAD_BAM_t_1"] / merged["EUR_USD_t_1"])
    )
    # EUR/MAD = USD/MAD × EUR/USD
    merged["EUR_MAD_calcule"] = merged["USD_MAD_calcule"] * merged["EUR_USD_t_1"]

    # Écarts : calculés uniquement quand le cours réel existe (sinon NaN, jamais 0)
    merged["Ecart_USD_MAD"] = merged["USD_MAD_calcule"] - merged["USD_MAD_BAM_reel"]
    merged["Ecart_USD_MAD_pct"] = merged["Ecart_USD_MAD"] / merged["USD_MAD_BAM_reel"] * 100
    merged["Ecart_EUR_MAD"] = merged["EUR_MAD_calcule"] - merged["EUR_MAD_BAM_reel"]
    merged["Ecart_EUR_MAD_pct"] = merged["Ecart_EUR_MAD"] / merged["EUR_MAD_BAM_reel"] * 100

    return merged[RESULT_COLUMNS].reset_index(drop=True)


RESULT_COLUMNS = [
    "Date_calcul", "Date_input",
    "USD_MAD_BAM_t_1", "EUR_MAD_BAM_t_1", "EUR_USD_t_1",
    "Poids_USD", "Poids_EUR",
    "USD_MAD_calcule", "USD_MAD_BAM_reel", "Ecart_USD_MAD", "Ecart_USD_MAD_pct",
    "EUR_MAD_calcule", "EUR_MAD_BAM_reel", "Ecart_EUR_MAD", "Ecart_EUR_MAD_pct",
]


def _empty_results() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def pending_estimate(history: pd.DataFrame, w_usd_pct: float, calc_date: pd.Timestamp) -> dict:
    """
    Estimation du jour courant, affichée SÉPARÉMENT, avec le statut
    « En attente de publication BAM ». Elle utilise la dernière base réelle commune.
    Retourne un dict, ou un dict avec None si l'historique est vide.
    """
    base = real_common_base(history)
    if base.empty:
        return {}
    w_usd, w_eur = weights_from_usd_pct(w_usd_pct)
    last = base.iloc[-1]
    usd = w_usd * last["USD_MAD_BAM"] + w_eur * (last["EUR_MAD_BAM"] / last["EUR_USD"])
    eur = usd * last["EUR_USD"]
    return {
        "Date_calcul": pd.Timestamp(calc_date).normalize(),
        "Date_input": last["Date"],
        "USD_MAD_calcule": usd,
        "EUR_MAD_calcule": eur,
        "statut": "En attente de publication BAM",
    }


# ---------------------------------------------------------------------------
# 4. Indicateurs de performance
# ---------------------------------------------------------------------------
def _metrics_for(calc: pd.Series, real: pd.Series) -> dict:
    """Calcule les indicateurs sur les seules dates où calc ET real existent."""
    both = pd.DataFrame({"calc": calc, "real": real}).dropna()
    n = len(both)
    if n == 0:
        return {k: np.nan for k in ["n", "MAE", "RMSE", "Biais", "Erreur_pct_moy",
                                     "MAPE", "Ecart_type", "Correlation"]} | {"n": 0}
    err = both["calc"] - both["real"]
    pct = err / both["real"] * 100
    return {
        "n": n,
        "MAE": err.abs().mean(),                               # erreur absolue moyenne
        "RMSE": float(np.sqrt((err ** 2).mean())),             # racine de l'erreur quadratique moyenne
        "Biais": err.mean(),                                   # biais : sens de l'erreur (+ = surestime)
        "Erreur_pct_moy": pct.mean(),                          # erreur moyenne signée en %
        "MAPE": pct.abs().mean(),                              # erreur absolue moyenne en %
        "Ecart_type": err.std(ddof=1) if n > 1 else np.nan,    # dispersion des erreurs
        "Correlation": both["calc"].corr(both["real"]) if n > 1 else np.nan,
    }


def compute_metrics(results: pd.DataFrame) -> dict:
    """Indicateurs pour USD/MAD et EUR/MAD à partir du DataFrame de résultats."""
    if results.empty:
        empty = _metrics_for(pd.Series(dtype=float), pd.Series(dtype=float))
        return {"USD": empty, "EUR": dict(empty)}
    return {
        "USD": _metrics_for(results["USD_MAD_calcule"], results["USD_MAD_BAM_reel"]),
        "EUR": _metrics_for(results["EUR_MAD_calcule"], results["EUR_MAD_BAM_reel"]),
    }


# ---------------------------------------------------------------------------
# 5. Comparaison de scénarios de pondération
# ---------------------------------------------------------------------------
DEFAULT_SCENARIOS = [40, 50, 60, 30, 70]


def compare_scenarios(history: pd.DataFrame, scenarios_usd_pct=None) -> pd.DataFrame:
    """
    Tableau comparatif : pour chaque poids USD, recalcule l'historique et renvoie
    les erreurs MAE / RMSE. L'historique source n'est jamais modifié.
    """
    if scenarios_usd_pct is None:
        scenarios_usd_pct = DEFAULT_SCENARIOS
    rows = []
    for w in scenarios_usd_pct:
        res = compute_theoretical(history, w)
        m = compute_metrics(res)
        rows.append({
            "Poids USD": w / 100,
            "Poids EUR": round(100 - w, 10) / 100,
            "MAE USD/MAD": m["USD"]["MAE"],
            "RMSE USD/MAD": m["USD"]["RMSE"],
            "MAE EUR/MAD": m["EUR"]["MAE"],
            "RMSE EUR/MAD": m["EUR"]["RMSE"],
            "Nb dates USD": m["USD"]["n"],
            "Nb dates EUR": m["EUR"]["n"],
        })
    return pd.DataFrame(rows)
