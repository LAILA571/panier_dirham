"""
investing_scraper.py — Cours EUR/USD (colonne « Price ») d'Investing.com.

Principe respecté :
- on tente une lecture simple de la page publique ;
- si Investing.com bloque l'accès (erreur 403/429, captcha, page sans tableau),
  on NE CONTOURNE PAS la protection : on lève une erreur claire et on propose
  d'importer un fichier CSV ou Excel (fonction read_investing_file).

Ce module fournit aussi scale_eurusd() : dans certains fichiers Excel, le cours
EUR/USD est stocké en nombre entier (11160 = 1.1160). Cette fonction le corrige.
"""

from __future__ import annotations

import logging
import re
import time

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

from src.bam_scraper import ScrapingError, parse_decimal_fr

logger = logging.getLogger(__name__)

INVESTING_URL = "https://www.investing.com/currencies/eur-usd-historical-data"
USER_AGENT = "PanierDirhamApp/1.0 (usage personnel ; consultation de l'historique EUR/USD)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT_SECONDS = 20
MAX_RETRIES = 2

# Plage plausible du cours EUR/USD ; hors plage = valeur rejetée
EURUSD_RANGE = (0.8, 1.6)


# ---------------------------------------------------------------------------
# Outils communs
# ---------------------------------------------------------------------------
def scale_eurusd(values: pd.Series) -> pd.Series:
    """
    Remet le cours EUR/USD à l'échelle décimale.
    Si la médiane dépasse 100, le cours est stocké en entier (11160 -> 1.1160).
    """
    numeric = pd.to_numeric(values, errors="coerce")
    median = numeric.median()
    if pd.notna(median) and median > 100:
        logger.info("EUR/USD stocké en entier : division par 10 000 appliquée.")
        return numeric / 10000.0
    return numeric


def _parse_investing_date(value, swap_datetime: bool) -> pd.Timestamp | None:
    """
    Convertit une date Investing en Timestamp.

    - Texte « 10/30/2026 » : format américain mois/jour/année (export Investing).
    - Texte « Oct 30, 2026 » : format anglais.
    - Cellule date Excel : si swap_datetime=True, on inverse jour et mois
      quand le jour est ≤ 12. Ce cas a été observé dans le fichier fourni :
      Excel avait lu « 09/10/2026 » comme le 9 octobre au lieu du 10 septembre.
      Après inversion, les 996 dates communes avec le fichier BAM concordent
      exactement. Mettre swap_datetime=False si vos dates sont correctes.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)) or hasattr(value, "year"):
        ts = pd.Timestamp(value)
        if swap_datetime and ts.day <= 12:
            try:
                ts = pd.Timestamp(year=ts.year, month=ts.day, day=ts.month)
            except ValueError:
                return None
        return ts.normalize()
    text = str(value).strip()
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return pd.Timestamp(pd.to_datetime(text, format=fmt))
        except (ValueError, TypeError):
            continue
    return None


def _clean_eurusd_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Nettoyage commun : dates valides, pas de futur, plage plausible, doublons, tri."""
    out = df.dropna(subset=["Date", "EUR_USD"]).copy()
    out = out[out["Date"] <= pd.Timestamp.today().normalize()]
    out = out[out["EUR_USD"].between(*EURUSD_RANGE)]
    out = out.groupby("Date", as_index=False)["EUR_USD"].first()   # doublons supprimés
    return out.sort_values("Date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Lecture de la page (si accessible)
# ---------------------------------------------------------------------------
def _fetch_investing_html(url: str = INVESTING_URL) -> str:
    """Télécharge la page avec timeout et retry. N'essaie aucun contournement."""
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
            if resp.status_code == 200:
                return resp.text
            last = f"HTTP {resp.status_code}"
            if resp.status_code in (401, 403, 429):
                # Blocage explicite : on arrête immédiatement, sans insister
                break
        except requests.RequestException as exc:
            last = str(exc)
        if attempt < MAX_RETRIES:
            time.sleep(2 * attempt)
    raise ScrapingError(
        f"Investing.com n'est pas accessible en lecture automatique ({last}). "
        "Importez un fichier CSV ou Excel contenant les cours EUR/USD."
    )


def parse_investing_html(html: str) -> pd.DataFrame:
    """Lit le tableau d'historique de la page et renvoie Date, EUR_USD (colonne Price)."""
    soup = BeautifulSoup(html, "lxml")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        headers = [h.get_text(strip=True).lower() for h in rows[0].find_all(["th", "td"])] if rows else []
        if "date" not in headers or "price" not in headers:
            continue
        i_date, i_price = headers.index("date"), headers.index("price")
        records = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if len(cells) <= max(i_date, i_price):
                continue
            records.append({
                "Date": _parse_investing_date(cells[i_date], swap_datetime=False),
                "EUR_USD": parse_decimal_fr(cells[i_price]),
            })
        if records:
            return _clean_eurusd_frame(pd.DataFrame(records))
    raise ScrapingError("Tableau d'historique EUR/USD introuvable sur la page Investing.com.")


# ---------------------------------------------------------------------------
# Lecture d'un fichier CSV ou Excel fourni par l'utilisateur (solution de secours)
# ---------------------------------------------------------------------------
def read_investing_file(path_or_buffer, sheet_name=None, swap_datetime: bool = True,
                        filename: str = "") -> pd.DataFrame:
    """
    Lit un export Investing (CSV ou Excel) et renvoie Date, EUR_USD.

    - Colonnes reconnues : « Date » et « Price » (insensible à la casse).
    - sheet_name : nom de l'onglet Excel ; si absent, on prend le premier onglet.
    - swap_datetime : voir _parse_investing_date.
    """
    name = filename.lower() if filename else str(path_or_buffer).lower()
    if name.endswith(".csv"):
        raw = pd.read_csv(path_or_buffer, sep=None, engine="python", dtype=str)
    else:
        raw = pd.read_excel(path_or_buffer, sheet_name=sheet_name or 0, dtype=object)

    cols = {str(c).strip().lower(): c for c in raw.columns}
    if "date" not in cols or "price" not in cols:
        raise ValueError(f"Colonnes « Date » et « Price » introuvables. Colonnes trouvées : {list(raw.columns)}")

    df = pd.DataFrame({
        "Date": [_parse_investing_date(v, swap_datetime) for v in raw[cols["date"]]],
        "EUR_USD": scale_eurusd(raw[cols["price"]].map(
            lambda v: parse_decimal_fr(v) if isinstance(v, str) else v)),
    })
    n_brut = len(df)
    df = _clean_eurusd_frame(df)
    logger.info("Investing (fichier) : %d lignes valides sur %d.", len(df), n_brut)
    return df


# ---------------------------------------------------------------------------
# Fonction publique demandée par le cahier des charges
# ---------------------------------------------------------------------------
def get_investing_eurusd(fallback_file=None, fallback_filename: str = "") -> pd.DataFrame:
    """
    Renvoie un DataFrame avec les colonnes Date, EUR_USD.

    1. Essaie la page Investing.com (lecture simple, sans contournement).
    2. Si elle est bloquée et qu'un fichier de secours est fourni, le lit à la place.
    3. Sinon lève ScrapingError (l'application garde l'historique existant).
    """
    try:
        return _clean_eurusd_frame(parse_investing_html(_fetch_investing_html()))
    except ScrapingError as exc:
        if fallback_file is None:
            raise
        logger.warning("Investing indisponible (%s) : utilisation du fichier de secours.", exc)
        return read_investing_file(fallback_file, filename=fallback_filename)
