"""
data_manager.py — Gestion de l'historique : lecture de l'Excel, CSV persistant,
fusion, suppression des doublons, sauvegarde et mise à jour.

Deux historiques distincts :
1. panier_dirham_history.csv  : données RÉELLES (BAM + EUR/USD Investing). Jamais écrasées.
2. calculs_history.csv        : résultats CALCULÉS, entièrement dérivés de (1).
                                Ce fichier est régénéré à chaque mise à jour ; il ne contient
                                aucune donnée réelle modifiée.

Règles de sécurité :
- une observation réelle n'est jamais remplacée par une autre ;
- une valeur manquante reste manquante (jamais 0, jamais inventée) ;
- une sauvegarde est créée avant toute réécriture du fichier historique ;
- la mise à jour est idempotente : la relancer ne crée pas de doublons.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd

from src.bam_scraper import ScrapingError, get_bam_rates
from src.calculations import DEFAULT_W_USD_PCT, REAL_COLS, compute_theoretical
from src.investing_scraper import get_investing_eurusd, scale_eurusd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chemins (relatifs au dossier du projet, donc valables dans Colab comme en local)
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
INITIAL_XLSX = DATA_DIR / "historique_initial.xlsx"
HISTORY_CSV = DATA_DIR / "panier_dirham_history.csv"
CALC_CSV = DATA_DIR / "calculs_history.csv"
LAST_UPDATE_JSON = DATA_DIR / "last_update.json"
BACKUP_DIR = DATA_DIR / "sauvegardes"

# Nom de l'onglet à lire dans le fichier Excel initial
INITIAL_SHEET = "cours fx"

# Correspondance CONFIGURABLE des colonnes.
# Clé = nom normalisé (minuscules, sans espaces ni symboles) ; valeur = nom interne.
# Exemple : « EUR/MAD » -> « eurmad » -> EUR_MAD_BAM. Ajoutez ici un nouveau nom si besoin.
COLUMN_MAP = {
    "date": "Date",
    "pricedate": "Date",
    "usdmad": "USD_MAD_BAM",
    "eurmad": "EUR_MAD_BAM",
    "eurusd": "EUR_USD",
}
HISTORY_COLUMNS = ["Date", "USD_MAD_BAM", "EUR_MAD_BAM", "EUR_USD"]

# Plages plausibles pour rejeter les valeurs aberrantes (jamais de valeur inventée)
PLAUSIBLE = {"USD_MAD_BAM": (5.0, 15.0), "EUR_MAD_BAM": (7.0, 15.0), "EUR_USD": (0.8, 1.6)}


class DataError(RuntimeError):
    """Erreur de lecture ou de structure d'un fichier de données."""


# ---------------------------------------------------------------------------
# Normalisation des colonnes
# ---------------------------------------------------------------------------
def normalize_colname(name) -> str:
    """« EUR/MAD » -> « eurmad » ; « price_date » -> « pricedate » ; « Date » -> « date »."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def map_columns(df: pd.DataFrame, column_map: dict | None = None) -> pd.DataFrame:
    """Renomme les colonnes selon COLUMN_MAP et ne garde que celles reconnues."""
    mapping = column_map or COLUMN_MAP
    rename = {}
    for col in df.columns:
        key = normalize_colname(col)
        if key in mapping and mapping[key] not in rename.values():
            rename[col] = mapping[key]
    out = df.rename(columns=rename)
    missing = [c for c in HISTORY_COLUMNS if c not in out.columns]
    if missing:
        raise DataError(
            f"Colonnes manquantes : {missing}. Colonnes trouvées : {list(df.columns)}. "
            "Ajustez COLUMN_MAP dans src/data_manager.py si les noms sont différents."
        )
    return out[HISTORY_COLUMNS].copy()


def _rewindable(source):
    """
    Un fichier téléversé (Streamlit) ne peut être lu qu'une fois : on le copie en mémoire
    pour pouvoir le lire pour lister les onglets, puis pour le charger.
    """
    if hasattr(source, "getvalue"):
        return BytesIO(source.getvalue())
    if hasattr(source, "read"):
        source.seek(0)
        return BytesIO(source.read())
    return source  # chemin de fichier


def find_sheet(path, wanted: str = INITIAL_SHEET) -> str:
    """Trouve l'onglet demandé sans tenir compte de la casse ni des espaces."""
    names = pd.ExcelFile(_rewindable(path)).sheet_names
    for name in names:
        if name.strip().lower() == wanted.strip().lower():
            return name
    raise DataError(f"Onglet « {wanted} » introuvable. Onglets présents : {names}")


# ---------------------------------------------------------------------------
# Nettoyage des valeurs
# ---------------------------------------------------------------------------
def clean_history(df: pd.DataFrame) -> pd.DataFrame:
    """
    Nettoie un historique réel :
    - dates converties, dates invalides supprimées ;
    - valeurs hors plage plausible remplacées par NaN (signalées dans les logs) ;
    - une seule ligne par date (première valeur non vide par colonne) ;
    - tri chronologique.
    """
    out = df.copy()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    out = out.dropna(subset=["Date"])
    for col, (low, high) in PLAUSIBLE.items():
        out[col] = pd.to_numeric(out[col], errors="coerce")
        bad = out[col].notna() & ~out[col].between(low, high)
        if bad.any():
            logger.warning("%d valeur(s) %s hors plage ignorée(s).", int(bad.sum()), col)
            out.loc[bad, col] = np.nan
    out = out.groupby("Date", as_index=False)[HISTORY_COLUMNS[1:]].first()
    return out.sort_values("Date").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Lecture de l'Excel initial
# ---------------------------------------------------------------------------
def load_initial_excel(source, sheet: str = INITIAL_SHEET) -> pd.DataFrame:
    """
    Lit l'onglet « cours fx » d'un fichier Excel (chemin ou fichier téléversé).
    Renvoie Date, USD_MAD_BAM, EUR_MAD_BAM, EUR_USD normalisés.
    """
    sheet_name = find_sheet(source, sheet)
    raw = pd.read_excel(_rewindable(source), sheet_name=sheet_name, dtype=object)
    raw = raw.dropna(how="all")
    df = map_columns(raw)
    df["EUR_USD"] = scale_eurusd(df["EUR_USD"])   # 11160 -> 1.1160 si stocké en entier
    cleaned = clean_history(df)
    logger.info("Excel initial : %d dates valides lues (%s -> %s).",
                len(cleaned),
                cleaned["Date"].min().date() if len(cleaned) else "-",
                cleaned["Date"].max().date() if len(cleaned) else "-")
    return cleaned


# ---------------------------------------------------------------------------
# CSV persistant
# ---------------------------------------------------------------------------
def load_history(path: Path = HISTORY_CSV) -> pd.DataFrame | None:
    """Charge l'historique réel. Renvoie None si le fichier n'existe pas encore."""
    if not Path(path).exists():
        return None
    df = pd.read_csv(path, dtype=str)
    df = map_columns(df) if "Date" not in df.columns else df[HISTORY_COLUMNS]
    for col in HISTORY_COLUMNS[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return clean_history(df)


def backup_file(path: Path, backup_dir: Path = BACKUP_DIR) -> Path | None:
    """Copie le fichier dans le dossier de sauvegardes avec horodatage. Renvoie le chemin."""
    path = Path(path)
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_dir / f"{path.stem}_{stamp}{path.suffix}"
    shutil.copy2(path, target)
    logger.info("Sauvegarde créée : %s", target)
    return target


def _write_csv_safely(df: pd.DataFrame, path: Path) -> None:
    """Écrit dans un fichier temporaire puis remplace l'original (évite un fichier à moitié écrit)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_csv(tmp, index=False, date_format="%Y-%m-%d", float_format="%.10f")
    os.replace(tmp, path)


def save_history(df: pd.DataFrame, path: Path = HISTORY_CSV) -> None:
    """Sauvegarde l'historique réel après une sauvegarde de sécurité de l'ancien fichier."""
    backup_file(path)
    _write_csv_safely(df[HISTORY_COLUMNS], path)


# ---------------------------------------------------------------------------
# Fusion sans écrasement et sans doublons
# ---------------------------------------------------------------------------
def merge_new_rows(existing: pd.DataFrame, new: pd.DataFrame) -> dict:
    """
    Fusionne de nouvelles observations réelles dans l'historique existant.

    Règles :
    - une date déjà présente garde ses valeurs réelles ;
    - une valeur manquante peut être complétée par une nouvelle valeur réelle ;
    - une valeur existante différente de la nouvelle est CONSERVÉE et signalée en conflit.

    Renvoie un dict : merged, added (dates ajoutées ou complétées), ignored (dates déjà
    complètes), conflicts (dates où la nouvelle valeur diffère de l'existante).
    """
    ex = clean_history(existing) if existing is not None and len(existing) else pd.DataFrame(columns=HISTORY_COLUMNS)
    nw = clean_history(new) if new is not None and len(new) else pd.DataFrame(columns=HISTORY_COLUMNS)

    combined = pd.concat([ex, nw], ignore_index=True)
    merged = combined.groupby("Date", as_index=False)[HISTORY_COLUMNS[1:]].first()
    merged = merged.sort_values("Date").reset_index(drop=True)

    added, ignored, conflicts = [], [], []
    ex_idx = ex.set_index("Date")
    for _, row in nw.iterrows():
        d = row["Date"]
        if d not in ex_idx.index:
            added.append(d)
            continue
        before = ex_idx.loc[d]
        changed = False
        for col in HISTORY_COLUMNS[1:]:
            old, val = before[col], row[col]
            if pd.isna(old) and pd.notna(val):
                changed = True
            elif pd.notna(old) and pd.notna(val) and not np.isclose(old, val, rtol=0, atol=1e-12):
                conflicts.append((d, col, old, val))
        (added if changed else ignored).append(d)

    return {"merged": merged, "added": added, "ignored": ignored, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Premier lancement et import manuel
# ---------------------------------------------------------------------------
def initialize_history(history_path: Path = HISTORY_CSV, xlsx_path: Path = INITIAL_XLSX) -> pd.DataFrame | None:
    """
    Premier lancement : crée le CSV à partir de l'Excel initial (onglet « cours fx »).
    Si le CSV existe déjà, il est simplement chargé.
    """
    existing = load_history(history_path)
    if existing is not None:
        return existing
    if not Path(xlsx_path).exists():
        return None
    initial = load_initial_excel(xlsx_path)
    save_history(initial, history_path)
    return initial


def import_excel_history(source, history_path: Path = HISTORY_CSV) -> dict:
    """
    Importe un fichier Excel téléversé dans l'interface et le FUSIONNE à l'historique.
    Rien n'est supprimé : les données existantes sont conservées.
    """
    new = load_initial_excel(source)
    existing = load_history(history_path)
    result = merge_new_rows(existing, new)
    if result["added"]:
        save_history(result["merged"], history_path)
    return result


# ---------------------------------------------------------------------------
# Mise à jour quotidienne
# ---------------------------------------------------------------------------
def fetch_new_observations(eurusd_df: pd.DataFrame | None = None) -> tuple[pd.DataFrame | None, pd.DataFrame | None, list[str]]:
    """
    Récupère BAM et EUR/USD. Une source en échec ne bloque pas l'autre.
    - eurusd_df fourni (fichier importé par l'utilisateur) : on l'utilise, sans scraper Investing.
    - sinon : on tente Investing.com ; en cas de blocage, l'EUR/USD reste absent (jamais inventé).
    Renvoie (bam, eurusd, liste_des_erreurs).
    """
    errors: list[str] = []
    bam = eur = None
    try:
        bam = get_bam_rates()
    except ScrapingError as exc:
        errors.append(f"BAM : {exc}")
    if eurusd_df is not None:
        eur = eurusd_df
    else:
        try:
            eur = get_investing_eurusd()
        except ScrapingError as exc:
            errors.append(f"Investing.com : {exc}")
    return bam, eur, errors


def run_update(eurusd_df: pd.DataFrame | None = None,
               history_path: Path = HISTORY_CSV, calc_path: Path = CALC_CSV,
               status_path: Path = LAST_UPDATE_JSON) -> dict:
    """
    Mise à jour complète de l'historique (fonction appelée par l'interface).

    Étapes : charger l'historique -> récupérer BAM et EUR/USD -> ajouter les nouvelles
    observations réelles -> sauvegarder -> régénérer le fichier des calculs -> noter le statut.
    Idempotente : relancer ne crée aucun doublon.
    """
    existing = load_history(history_path)
    last_before = existing["Date"].max() if existing is not None and len(existing) else None

    bam, eur, errors = fetch_new_observations(eurusd_df)

    added, ignored, conflicts = [], [], []
    if bam is not None and len(bam):
        # On ne garde une date que si BAM la publie : c'est la référence de publication.
        new = bam.merge(eur, on="Date", how="left") if eur is not None else bam.assign(EUR_USD=np.nan)
        if eur is not None:
            missing_eur = sorted(set(bam["Date"]) - set(eur["Date"]))
            for d in missing_eur:
                errors.append(f"EUR/USD absent pour le {pd.Timestamp(d).date()} (BAM disponible).")
        new = new.dropna(subset=["USD_MAD_BAM", "EUR_MAD_BAM"], how="all")
        result = merge_new_rows(existing, new)
        added, ignored, conflicts = result["added"], result["ignored"], result["conflicts"]
        if added:
            save_history(result["merged"], history_path)
            existing = result["merged"]
        for d, col, old, val in conflicts:
            errors.append(f"Conflit ignoré le {pd.Timestamp(d).date()} sur {col} : "
                          f"historique={old}, nouvelle valeur={val} (historique conservé).")

    # Régénération du fichier des calculs (dérivé, déterministe)
    if existing is not None and len(existing):
        calc = compute_theoretical(existing, DEFAULT_W_USD_PCT)
        _write_csv_safely(calc, calc_path)

    status = {
        "derniere_maj": datetime.now().isoformat(timespec="seconds"),
        "derniere_date_historique": (existing["Date"].max().date().isoformat()
                                     if existing is not None and len(existing) else None),
        "avant_mise_a_jour": last_before.date().isoformat() if last_before is not None else None,
        "lignes_ajoutees": len(added),
        "dates_ajoutees": [pd.Timestamp(d).date().isoformat() for d in added],
        "dates_ignorees": [pd.Timestamp(d).date().isoformat() for d in ignored],
        "erreurs": errors,
    }
    status_path = Path(status_path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def load_last_update(status_path: Path = LAST_UPDATE_JSON) -> dict | None:
    """Relit le dernier statut de mise à jour (pour l'affichage)."""
    p = Path(status_path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Exports (valeurs non arrondies)
# ---------------------------------------------------------------------------
def export_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV en mémoire, dates au format ISO et valeurs non arrondies."""
    return df.to_csv(index=False, date_format="%Y-%m-%d").encode("utf-8-sig")


def export_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    """Fichier Excel en mémoire avec un onglet par DataFrame."""
    from io import BytesIO
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
    return buffer.getvalue()
