"""
bam_scraper.py — Récupération des cours de référence USD/MAD et EUR/MAD auprès de Bank Al-Maghrib.

SEULE dépendance à la structure du site BAM : si BAM change sa mise en page,
c'est ce fichier qui doit être adapté. Le reste de l'application ne change pas.

Structure observée sur la page officielle (cours de référence) :
- un tableau HTML ;
- une ligne d'en-tête avec les dates de publication au format JJ/MM/AAAA
  (exemple : 30/10/2026 | 29/10/2026), la plus récente en premier ;
- une ligne par devise, avec le code ISO entre parenthèses dans la 1re cellule,
  par exemple « 1 DOLLAR U.S.A. (USD) » ou « 1 EURO (EUR) » ;
- des nombres avec une VIRGULE décimale, par exemple 9,8138.

Si la structure n'est pas reconnue, on lève ScrapingError avec un message clair.
L'application conserve alors l'historique existant et affiche un avertissement.
"""

from __future__ import annotations

import logging
import re
import time

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BAM_URL = "https://www.bkam.ma/Marches/Principaux-indicateurs/Marche-des-changes/Cours-de-change/Cours-de-reference"

# User-Agent honnête : identifie l'application et son usage personnel
USER_AGENT = "PanierDirhamApp/1.0 (usage personnel ; consultation des cours de reference BAM)"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"}

TIMEOUT_SECONDS = 20
MAX_RETRIES = 3

# Fourchettes de plausibilité : un cours hors de ces bornes est rejeté (jamais inventé)
PLAUSIBLE_RANGES = {"USD": (5.0, 15.0), "EUR": (7.0, 15.0)}

_DATE_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\s*$")
_CODE_RE = re.compile(r"\((USD|EUR)\)")


class ScrapingError(RuntimeError):
    """Erreur de récupération ou de lecture d'une source externe."""


# ---------------------------------------------------------------------------
# Outils de conversion (format français)
# ---------------------------------------------------------------------------
def parse_decimal_fr(text: str) -> float:
    """
    Convertit un nombre au format français en float.
    '9,8138' -> 9.8138 ; '1 234,50' -> 1234.5 ; '9.8138' -> 9.8138 ; '' -> NaN.
    """
    if text is None:
        return np.nan
    s = str(text).replace(" ", "").replace(" ", "").strip()
    if not s or s in {"-", "--"}:
        return np.nan
    if "," in s and "." in s:          # ex. 10.614,50 : le point est un séparateur de milliers
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:                     # ex. 9,8138 : virgule décimale
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_date_fr(text: str) -> pd.Timestamp | None:
    """
    Convertit une date JJ/MM/AAAA (ou JJ-MM-AAAA, JJ.MM.AAAA) en Timestamp.
    Renvoie None si le texte n'est pas une date valide (jamais de date inventée).
    """
    if text is None:
        return None
    m = _DATE_RE.match(str(text))
    if not m:
        return None
    day, month, year = (int(g) for g in m.groups())
    try:
        return pd.Timestamp(year=year, month=month, day=day)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Téléchargement avec timeout, retry et gestion des erreurs HTTP
# ---------------------------------------------------------------------------
def fetch_html(url: str = BAM_URL, retries: int = MAX_RETRIES, timeout: int = TIMEOUT_SECONDS) -> str:
    """Télécharge la page. Réessaie en cas d'erreur temporaire, puis lève ScrapingError."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=timeout)
            if response.status_code == 200:
                return response.text
            last_error = f"HTTP {response.status_code}"
            logger.warning("BAM : %s (tentative %d/%d)", last_error, attempt, retries)
        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning("BAM : erreur réseau (tentative %d/%d) : %s", attempt, retries, exc)
        if attempt < retries:
            time.sleep(2 * attempt)  # attente progressive avant de réessayer
    raise ScrapingError(f"Page BAM inaccessible après {retries} tentatives ({last_error}).")


# ---------------------------------------------------------------------------
# Lecture du tableau
# ---------------------------------------------------------------------------
def parse_bam_html(html: str) -> pd.DataFrame:
    """
    Lit le HTML de la page BAM et renvoie un DataFrame avec les colonnes :
    Date, USD_MAD_BAM, EUR_MAD_BAM (une ligne par date de publication trouvée).
    """
    soup = BeautifulSoup(html, "lxml")
    values: dict[tuple[pd.Timestamp, str], float] = {}
    all_dates: set[pd.Timestamp] = set()

    for table in soup.find_all("table"):
        date_cols: dict[int, pd.Timestamp] = {}
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if not cells:
                continue

            # Ligne d'en-tête : colonnes contenant une date
            header_dates = {i: d for i, t in enumerate(cells) if (d := parse_date_fr(t)) is not None}
            if header_dates and not date_cols:
                date_cols = header_dates
                all_dates.update(date_cols.values())
                continue
            if not date_cols:
                continue

            # Ligne de devise : code USD ou EUR entre parenthèses dans la 1re cellule
            m = _CODE_RE.search(cells[0])
            if not m:
                continue
            code = m.group(1)
            for col_idx, publication_date in date_cols.items():
                if col_idx < len(cells):
                    values[(publication_date, code)] = parse_decimal_fr(cells[col_idx])

        if date_cols and any(k[1] in ("USD", "EUR") for k in values):
            break  # tableau trouvé

    if not all_dates or not values:
        raise ScrapingError(
            "Structure de la page BAM non reconnue : aucun tableau de cours avec dates et "
            "devises USD/EUR. Le scraper doit être adapté (voir bam_scraper.py)."
        )

    rows = []
    for d in sorted(all_dates):
        usd = values.get((d, "USD"), np.nan)
        eur = values.get((d, "EUR"), np.nan)
        usd = _check_range(usd, "USD", d)
        eur = _check_range(eur, "EUR", d)
        if np.isnan(usd) and np.isnan(eur):
            continue  # aucune donnée exploitable pour cette date : on ne l'ajoute pas
        rows.append({"Date": d, "USD_MAD_BAM": usd, "EUR_MAD_BAM": eur})

    return pd.DataFrame(rows, columns=["Date", "USD_MAD_BAM", "EUR_MAD_BAM"])


def _check_range(value: float, currency: str, date: pd.Timestamp) -> float:
    """Rejette une valeur hors plage plausible (renvoie NaN, jamais une valeur inventée)."""
    if np.isnan(value):
        return np.nan
    low, high = PLAUSIBLE_RANGES[currency]
    if not low <= value <= high:
        logger.warning("BAM : %s du %s hors plage plausible (%s) : ignoré.", currency, date.date(), value)
        return np.nan
    return value


# ---------------------------------------------------------------------------
# Fonction publique demandée par le cahier des charges
# ---------------------------------------------------------------------------
def get_bam_rates(url: str = BAM_URL) -> pd.DataFrame:
    """
    Récupère les cours de référence BAM.

    Retour : DataFrame avec les colonnes Date, USD_MAD_BAM, EUR_MAD_BAM.
    Lève ScrapingError si la page est inaccessible ou si sa structure n'est pas reconnue.
    """
    html = fetch_html(url)
    df = parse_bam_html(html)
    logger.info("BAM : %d date(s) lue(s) sur la page.", len(df))
    return df
