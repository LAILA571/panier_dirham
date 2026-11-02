"""
Tests du projet « Panier du Dirham ».

Ces tests utilisent UNIQUEMENT des données fictives, jamais votre historique réel.
Lancement : pytest -q   (depuis le dossier panier_dirham)
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import calculations as calc  # noqa: E402
from src import data_manager as dm  # noqa: E402
from src.bam_scraper import parse_bam_html, parse_date_fr, parse_decimal_fr  # noqa: E402
from src.investing_scraper import _parse_investing_date, scale_eurusd  # noqa: E402


# ---------------------------------------------------------------------------
# Données fictives
# ---------------------------------------------------------------------------
def fake_history():
    """
    Historique fictif avec un week-end et un jour sans publication.
    Vendredi 6 nov., samedi 7, dimanche 8, lundi 9 : seul le lundi 9 a une publication.
    Valeurs choisies pour que les calculs soient faciles à vérifier à la main.
    """
    return pd.DataFrame({
        "Date": pd.to_datetime(["2026-11-04", "2026-11-05", "2026-11-06", "2026-11-09"]),
        "USD_MAD_BAM": [10.0, 10.2, 10.4, 10.6],
        "EUR_MAD_BAM": [11.0, 11.1, 11.2, 11.3],
        "EUR_USD": [1.1, 1.1, 1.1, 1.1],
    })


# ---------------------------------------------------------------------------
# 1. Pondérations
# ---------------------------------------------------------------------------
def test_poids_40_60_par_defaut():
    assert calc.weights_from_usd_pct(40) == (0.4, 0.6)


@pytest.mark.parametrize("usd", [0, 30, 40, 50, 60, 70, 100])
def test_somme_des_poids_egale_100_pourcent(usd):
    w_usd, w_eur = calc.weights_from_usd_pct(usd)
    assert math.isclose(w_usd + w_eur, 1.0, abs_tol=1e-12)


@pytest.mark.parametrize("bad", [-1, 101, float("nan")])
def test_poids_invalides_refuses(bad):
    with pytest.raises(calc.WeightError):
        calc.weights_from_usd_pct(bad)


# ---------------------------------------------------------------------------
# 2. Calcul USD/MAD et EUR/MAD avec pondérations 40/60
# ---------------------------------------------------------------------------
def test_calcul_usd_mad_40_60_valeurs_connues():
    # Inputs fictifs : USD/MAD = 10, EUR/MAD = 11, EUR/USD = 1.1
    # USD estimé = 0.4 × 10 + 0.6 × (11 / 1.1) = 4 + 6 = 10.0
    h = pd.DataFrame({
        "Date": pd.to_datetime(["2026-11-02", "2026-11-03"]),
        "USD_MAD_BAM": [10.0, 10.5],
        "EUR_MAD_BAM": [11.0, 11.5],
        "EUR_USD": [1.1, 1.1],
    })
    res = calc.compute_theoretical(h, 40)
    assert len(res) == 1
    row = res.iloc[0]
    assert math.isclose(row["USD_MAD_calcule"], 10.0, rel_tol=1e-12)


def test_calcul_eur_mad_est_usd_mad_fois_eur_usd():
    h = pd.DataFrame({
        "Date": pd.to_datetime(["2026-11-02", "2026-11-03"]),
        "USD_MAD_BAM": [10.0, 10.5],
        "EUR_MAD_BAM": [11.0, 11.5],
        "EUR_USD": [1.1, 1.1],
    })
    row = calc.compute_theoretical(h, 40).iloc[0]
    # EUR/MAD = USD/MAD estimé × EUR/USD = 10 × 1.1 = 11
    assert math.isclose(row["EUR_MAD_calcule"], 11.0, rel_tol=1e-12)
    assert math.isclose(row["EUR_MAD_calcule"], row["USD_MAD_calcule"] * 1.1, rel_tol=1e-12)


def test_modification_des_poids_change_les_resultats():
    # EUR/MAD = 11.5 et EUR/USD = 1.1 -> partie EUR = 10.45 : différente de USD = 10.0
    h = fake_history()
    h.loc[h["Date"] == pd.Timestamp("2026-11-04"), "EUR_MAD_BAM"] = 11.5
    r40 = calc.compute_theoretical(h, 40).iloc[0]["USD_MAD_calcule"]
    r70 = calc.compute_theoretical(h, 70).iloc[0]["USD_MAD_calcule"]
    assert r40 != pytest.approx(r70)


def test_donnees_sources_non_modifiees_par_les_poids():
    h = fake_history()
    before = h.copy(deep=True)
    calc.compute_theoretical(h, 30)
    calc.compute_theoretical(h, 80)
    pd.testing.assert_frame_equal(h, before)


# ---------------------------------------------------------------------------
# 3. Décalage temporel t-1 et week-ends
# ---------------------------------------------------------------------------
def test_pas_d_utilisation_du_jour_t_lui_meme():
    h = fake_history()
    res = calc.compute_theoretical(h, 40)
    for _, r in res.iterrows():
        assert r["Date_input"] < r["Date_calcul"]


def test_lundi_utilise_le_dernier_jour_reel_disponible():
    # Le lundi 9 nov. a pour input le vendredi 6 nov., dernière date réelle commune avant lui :
    # la base est constituée de toutes les lignes où les 3 cours réels existent.
    h = fake_history()
    res = calc.compute_theoretical(h, 40)
    lundi = res[res["Date_calcul"] == pd.Timestamp("2026-11-09")].iloc[0]
    assert lundi["Date_input"] == pd.Timestamp("2026-11-06")


def test_week_end_sans_publication_pas_de_ligne_inventee():
    h = fake_history()
    res = calc.compute_theoretical(h, 40)
    # Le 4 nov. n'a aucune date réelle antérieure : il n'a pas de calcul (pas d'invention).
    assert set(res["Date_calcul"]) == {pd.Timestamp("2026-11-05"), pd.Timestamp("2026-11-06"),
                                       pd.Timestamp("2026-11-09")}
    assert pd.Timestamp("2026-11-07") not in set(res["Date_calcul"])


def test_date_sans_eurusd_n_est_pas_utilisee_comme_input():
    h = fake_history()
    h.loc[h["Date"] == pd.Timestamp("2026-11-06"), "EUR_USD"] = np.nan
    res = calc.compute_theoretical(h, 40)
    lundi = res[res["Date_calcul"] == pd.Timestamp("2026-11-09")].iloc[0]
    assert lundi["Date_input"] == pd.Timestamp("2026-11-05")


# ---------------------------------------------------------------------------
# 4. Écarts
# ---------------------------------------------------------------------------
def test_ecarts_absolus_et_relatifs():
    h = pd.DataFrame({
        "Date": pd.to_datetime(["2026-11-02", "2026-11-03"]),
        "USD_MAD_BAM": [10.0, 9.0],          # réel du 3 nov. = 9.0
        "EUR_MAD_BAM": [11.0, 11.0],
        "EUR_USD": [1.1, 1.1],
    })
    row = calc.compute_theoretical(h, 40).iloc[0]   # calculé = 10.0 pour le 3 nov.
    assert math.isclose(row["Ecart_USD_MAD"], 10.0 - 9.0, rel_tol=1e-12)
    assert math.isclose(row["Ecart_USD_MAD_pct"], (10.0 - 9.0) / 9.0 * 100, rel_tol=1e-12)


def test_pas_de_comparaison_sans_cours_reel():
    h = fake_history()
    h.loc[h["Date"] == pd.Timestamp("2026-11-09"), ["USD_MAD_BAM", "EUR_MAD_BAM"]] = np.nan
    res = calc.compute_theoretical(h, 40)
    # Le 9 nov. ne doit pas être comparé, donc pas de date de publication associée
    assert pd.Timestamp("2026-11-09") not in set(res.loc[res["USD_MAD_BAM_reel"].notna(), "Date_calcul"])


# ---------------------------------------------------------------------------
# 5. Indicateurs MAE et RMSE
# ---------------------------------------------------------------------------
def test_mae_et_rmse_valeurs_connues():
    # erreurs : +1, -1, +2  ->  MAE = 4/3 ; RMSE = sqrt((1+1+4)/3) = sqrt(2)
    res = pd.DataFrame({
        "USD_MAD_calcule": [11.0, 9.0, 12.0],
        "USD_MAD_BAM_reel": [10.0, 10.0, 10.0],
        "EUR_MAD_calcule": [1.0, 1.0, 1.0],
        "EUR_MAD_BAM_reel": [1.0, 1.0, 1.0],
    })
    m = calc.compute_metrics(res)["USD"]
    assert m["MAE"] == pytest.approx(4 / 3)
    assert m["RMSE"] == pytest.approx(math.sqrt(2))
    assert m["Biais"] == pytest.approx(2 / 3)
    assert m["n"] == 3


def test_metriques_sans_donnees_donnent_nan_et_pas_zero():
    m = calc.compute_metrics(pd.DataFrame(columns=calc.RESULT_COLUMNS))["USD"]
    assert m["n"] == 0
    assert math.isnan(m["MAE"])


def test_tableau_scenarios_somme_100():
    table = calc.compare_scenarios(fake_history(), [40, 50, 60, 30, 70])
    assert len(table) == 5
    assert np.allclose(table["Poids USD"] + table["Poids EUR"], 1.0)


# ---------------------------------------------------------------------------
# 6. Gestion des données : doublons, mise à jour sans perte, idempotence
# ---------------------------------------------------------------------------
def test_suppression_des_doublons_et_tri():
    h = pd.DataFrame({
        "Date": ["2026-11-03", "2026-11-02", "2026-11-02"],
        "USD_MAD_BAM": [10.1, 10.0, np.nan],
        "EUR_MAD_BAM": [11.1, 11.0, 11.0],
        "EUR_USD": [1.1, 1.1, 1.1],
    })
    out = dm.clean_history(h)
    assert list(out["Date"]) == [pd.Timestamp("2026-11-02"), pd.Timestamp("2026-11-03")]
    assert out.iloc[0]["USD_MAD_BAM"] == 10.0  # première valeur non vide conservée


def test_mise_a_jour_sans_perte_et_sans_ecrasement():
    existing = pd.DataFrame({
        "Date": pd.to_datetime(["2026-10-30"]),
        "USD_MAD_BAM": [9.8], "EUR_MAD_BAM": [10.9], "EUR_USD": [1.116],
    })
    new = pd.DataFrame({
        "Date": pd.to_datetime(["2026-10-30", "2026-11-02"]),
        "USD_MAD_BAM": [9.9, 9.85], "EUR_MAD_BAM": [10.95, 10.92], "EUR_USD": [1.12, 1.118],
    })
    result = dm.merge_new_rows(existing, new)
    merged = result["merged"].set_index("Date")
    # Date existante : valeur réelle conservée, conflit signalé
    assert merged.loc[pd.Timestamp("2026-10-30"), "USD_MAD_BAM"] == 9.8
    assert len(result["conflicts"]) >= 1
    # Nouvelle date ajoutée
    assert pd.Timestamp("2026-11-02") in result["added"]
    assert len(merged) == 2


def test_relancer_la_mise_a_jour_ne_cree_pas_de_doublons():
    base = pd.DataFrame({
        "Date": pd.to_datetime(["2026-10-30"]),
        "USD_MAD_BAM": [9.8], "EUR_MAD_BAM": [10.9], "EUR_USD": [1.116],
    })
    new = pd.DataFrame({
        "Date": pd.to_datetime(["2026-11-02"]),
        "USD_MAD_BAM": [9.85], "EUR_MAD_BAM": [10.92], "EUR_USD": [1.118],
    })
    first = dm.merge_new_rows(base, new)["merged"]
    second = dm.merge_new_rows(first, new)   # même mise à jour une 2e fois
    assert len(second["merged"]) == len(first) == 2
    assert second["added"] == []                # rien de nouveau
    assert not second["merged"]["Date"].duplicated().any()


def test_colonnes_renommees_selon_configuration():
    raw = pd.DataFrame({"price_date": ["2026-10-30"], "EUR/MAD": [10.9], "USD/MAD": [9.8], "EUR/USD": [11160]})
    mapped = dm.map_columns(raw)
    assert list(mapped.columns) == dm.HISTORY_COLUMNS


def test_normalisation_des_noms_de_colonnes():
    assert dm.normalize_colname("EUR/MAD") == "eurmad"
    assert dm.normalize_colname("price_date") == "pricedate"


def test_colonne_manquante_erreur_claire():
    with pytest.raises(dm.DataError):
        dm.map_columns(pd.DataFrame({"Date": ["2026-10-30"], "USD/MAD": [9.8]}))


# ---------------------------------------------------------------------------
# 7. Conversions de nombres et de dates
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [("9,8138", 9.8138), ("1 234,50", 1234.5),
                                           ("9.8138", 9.8138), ("", None)])
def test_parse_decimal_fr(text, expected):
    value = parse_decimal_fr(text)
    if expected is None:
        assert math.isnan(value)
    else:
        assert value == pytest.approx(expected)


def test_parse_date_fr():
    assert parse_date_fr("30/10/2026") == pd.Timestamp("2026-10-30")
    assert parse_date_fr("31/02/2026") is None       # date impossible : rejetée
    assert parse_date_fr("Devises") is None


def test_scale_eurusd_entier_vers_decimal():
    s = scale_eurusd(pd.Series([11160, 11180, 11154]))
    assert s.iloc[0] == pytest.approx(1.116)


def test_investing_date_texte_americain():
    assert _parse_investing_date("10/30/2026", swap_datetime=False) == pd.Timestamp("2026-10-30")


def test_investing_datetime_inverse_corrigee():
    # Excel a lu « 09/10/2026 » (9 oct.) alors qu'il s'agit du 10 septembre
    ts = pd.Timestamp("2026-10-09")
    assert _parse_investing_date(ts, swap_datetime=True) == pd.Timestamp("2026-09-10")


# ---------------------------------------------------------------------------
# 8. Lecture de la page BAM (HTML fictif reproduisant la structure observée)
# ---------------------------------------------------------------------------
FAKE_BAM_HTML = """
<html><body><table>
<tr><th>Devises</th><th>30/10/2026</th><th>29/10/2026</th></tr>
<tr><td></td><td>Moyen</td><td>Moyen</td></tr>
<tr><td><a href="#">1 DOLLAR U.S.A. (USD)</a></td><td>9,8138</td><td>9,8393</td></tr>
<tr><td><a href="#">1 EURO (EUR)</a></td><td>10,9836</td><td>10,9614</td></tr>
</table></body></html>
"""


def test_parse_bam_html_structure_observee():
    df = parse_bam_html(FAKE_BAM_HTML)
    assert list(df.columns) == ["Date", "USD_MAD_BAM", "EUR_MAD_BAM"]
    assert len(df) == 2
    row = df.set_index("Date").loc[pd.Timestamp("2026-10-29")]
    assert row["USD_MAD_BAM"] == pytest.approx(9.8393)
    assert row["EUR_MAD_BAM"] == pytest.approx(10.9614)


def test_parse_bam_structure_inconnue_leve_erreur():
    from src.bam_scraper import ScrapingError
    with pytest.raises(ScrapingError):
        parse_bam_html("<html><body><p>Maintenance en cours</p></body></html>")


def test_bam_valeur_hors_plage_rejetee():
    html = FAKE_BAM_HTML.replace("9,8138", "98,138")  # erreur de virgule : 98 hors plage
    df = parse_bam_html(html)
    row = df.set_index("Date").loc[pd.Timestamp("2026-10-30")]
    assert math.isnan(row["USD_MAD_BAM"])
    assert row["EUR_MAD_BAM"] == pytest.approx(10.9836)
