"""
Module PUR d'extraction des mesures — MVP « Veille météo et climatique au
Burundi ».

RÔLE : faire le pont entre le dictionnaire renvoyé par la route
`app.routes.weather.get_weather_data` (fusion de
`WeatherRequest.make_weather_request`, `make_flood_request` et
`get_flood_past_data`) et le dict `mesures` attendu par
`app.services.advice.generer_conseil`.

Entrée attendue (clefs utilisées) :
    {
        "hourly_data": {
            "date": [...],                 # grille horaire UTC (7 j passés + prévision)
            "precipitation": [...],        # mm / heure précédente
            "wind_gusts_10m": [...],       # km/h (unité par défaut Open-Meteo)
            ...
        },
        "daily_river_data": {              # débit GloFAS prévu (7 jours)
            "date": [...], "river_discharge": [...]
        },
        "daily_river_past_data_p75": {     # p75 GloFAS, 31 j passés + 1 j prévu
            "date": [...], "river_discharge": [...]
        },
    }

Sortie (clefs lues par advice.generer_conseil) :
    pluie_24h, pluie_72h, pluie_7j_passes, jours_secs, rafales_kmh,
    debit_prevu, debit_reference

CARACTÈRE PUR : aucun appel réseau, aucune dépendance à FastAPI -> 100 %
testable unitairement.

RÈGLE DE PRUDENCE (cohérente avec advice.py) : dès qu'une fenêtre de calcul
est incomplète ou contient une valeur manquante (None / NaN), la mesure vaut
`None`. Le conseil correspondant passe alors en « non_evalue » ; on ne
fabrique JAMAIS une valeur partielle qui pourrait être lue comme un signal
faible.

CONVENTION OPEN-METEO : une valeur horaire datée T porte sur l'heure
PRÉCÉDENTE (T-1h, T]. Les fenêtres sont donc ouvertes à gauche et fermées à
droite : « prochaines 24 h » = (t0, t0 + 24 h], « 7 derniers jours » =
(t0 - 168 h, t0], où t0 = `maintenant` arrondi à l'heure inférieure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.config import Seuils

HEURES_24H = 24
HEURES_72H = 72
HEURES_7J = 7 * 24


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def extraire_mesures(
    donnees: dict,
    maintenant: datetime | None = None,
    seuils: Seuils | None = None,
) -> dict:
    """
    Extrait les mesures nécessaires aux conseils à partir du dict renvoyé par
    la route météo.

    Paramètres
    ----------
    donnees : dict renvoyé par `get_weather_data` (dates en `pd.Timestamp`
        ou en chaînes ISO 8601 — les deux sont acceptées, ce qui permet aussi
        de traiter une réponse déjà sérialisée en JSON).
    maintenant : instant de référence (UTC si naïf). Par défaut : maintenant.
    seuils : seuils du MVP (utilisé pour `jours_sec_mm`).

    Retour
    ------
    dict prêt pour `advice.generer_conseil(mesures, zone, culture, ...)`.
    """
    seuils = seuils or Seuils()
    t0 = _instant_reference(maintenant)

    horaire = donnees.get("hourly_data") or {}
    pluie = _serie(horaire, "precipitation")
    rafales = _serie(horaire, "wind_gusts_10m")

    debit_prevu_serie = _serie(donnees.get("daily_river_data"), "river_discharge")
    debit_ref_serie = _serie(donnees.get("daily_river_past_data_p75"), "river_discharge")

    return {
        "pluie_24h": _cumul(pluie, t0, t0 + pd.Timedelta(hours=HEURES_24H)),
        "pluie_72h": _cumul(pluie, t0, t0 + pd.Timedelta(hours=HEURES_72H)),
        "pluie_7j_passes": _cumul(pluie, t0 - pd.Timedelta(hours=HEURES_7J), t0),
        "jours_secs": _jours_secs(pluie, t0, seuils),
        "rafales_kmh": _maximum(rafales, t0, t0 + pd.Timedelta(hours=HEURES_24H)),
        "debit_prevu": _debit_prevu(debit_prevu_serie),
        "debit_reference": _debit_reference(debit_ref_serie, debit_prevu_serie, t0),
    }


# ---------------------------------------------------------------------------
# Construction des séries
# ---------------------------------------------------------------------------


def _instant_reference(maintenant: datetime | None) -> pd.Timestamp:
    """Instant de référence en UTC, arrondi à l'heure inférieure."""
    ts = pd.Timestamp(maintenant or datetime.now(timezone.utc))
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.floor("h")


def _serie(bloc: dict | None, cle: str) -> pd.Series | None:
    """
    Construit une série temporelle (index UTC trié, valeurs float, NaN pour
    les manquants) à partir d'un bloc {"date": [...], cle: [...]}.

    Renvoie None si le bloc est absent ou si dates et valeurs ne sont pas
    alignées (garantie d'alignement date <-> mesure, cf. services/weather.py).
    """
    if not bloc:
        return None
    dates = bloc.get("date")
    valeurs = bloc.get(cle)
    if dates is None or valeurs is None or len(dates) == 0 or len(dates) != len(valeurs):
        return None
    try:
        index = pd.to_datetime(list(dates), utc=True)
    except (TypeError, ValueError):
        return None
    valeurs_num = pd.to_numeric(pd.Series(list(valeurs), dtype=object), errors="coerce")
    return pd.Series(valeurs_num.to_numpy(dtype=float), index=index).sort_index()


def _pas(serie: pd.Series) -> pd.Timedelta | None:
    """Pas de temps de la série (médiane des écarts)."""
    if len(serie) < 2:
        return None
    pas = serie.index.to_series().diff().median()
    return pas if pd.notna(pas) and pas > pd.Timedelta(0) else None


def _fenetre_complete(serie: pd.Series | None, debut, fin) -> pd.Series | None:
    """
    Valeurs de la fenêtre (debut, fin] si elle est COMPLÈTE (nombre de pas
    attendu) et SANS valeur manquante ; sinon None.
    """
    if serie is None:
        return None
    pas = _pas(serie)
    if pas is None:
        return None
    attendu = int(round((fin - debut) / pas))
    fenetre = serie[(serie.index > debut) & (serie.index <= fin)]
    if attendu <= 0 or len(fenetre) < attendu or fenetre.isna().any():
        return None
    return fenetre


# ---------------------------------------------------------------------------
# Mesures
# ---------------------------------------------------------------------------


def _cumul(serie, debut, fin) -> float | None:
    """Cumul (mm) sur la fenêtre (debut, fin], None si incomplète."""
    fenetre = _fenetre_complete(serie, debut, fin)
    return None if fenetre is None else round(float(fenetre.sum()), 2)


def _maximum(serie, debut, fin) -> float | None:
    """Maximum sur la fenêtre (debut, fin], None si incomplète."""
    fenetre = _fenetre_complete(serie, debut, fin)
    return None if fenetre is None else round(float(fenetre.max()), 2)


def _jours_secs(serie, t0, seuils: Seuils) -> int | None:
    """
    Nombre de jours CONSÉCUTIFS à venir (blocs de 24 h glissants depuis t0)
    avec un cumul < `seuils.jours_sec_mm`.

    Les blocs de 24 h à partir de t0 évitent toute ambiguïté de fuseau
    horaire. Le comptage s'arrête au premier jour pluvieux ou à la fin de
    l'horizon de prévision. Si le comptage est interrompu par une donnée
    manquante AVANT d'atteindre le seuil `jours_secs_min`, on ne peut pas
    conclure -> None.
    """
    if serie is None:
        return None
    dernier = serie.index.max()
    jours = 0
    while True:
        debut = t0 + pd.Timedelta(days=jours)
        fin = debut + pd.Timedelta(days=1)
        if fin > dernier:
            break  # fin de l'horizon de prévision : comptage terminé
        cumul = _cumul(serie, debut, fin)
        if cumul is None:
            # Donnée manquante : le décompte n'est fiable que s'il atteint
            # déjà le seuil de déclenchement.
            return jours if jours >= seuils.jours_secs_min else None
        if cumul >= seuils.jours_sec_mm:
            break
        jours += 1
    if jours == 0 and t0 + pd.Timedelta(days=1) > dernier:
        return None  # pas même un jour complet de prévision disponible
    return jours


def _debit_prevu(serie: pd.Series | None) -> float | None:
    """Débit GloFAS maximal prévu sur l'horizon (m³/s)."""
    if serie is None or serie.isna().all():
        return None
    return round(float(np.nanmax(serie.to_numpy())), 3)


def _debit_reference(
    serie_ref: pd.Series | None,
    serie_prevue: pd.Series | None,
    t0: pd.Timestamp,
) -> float | None:
    """
    Débit de référence : 75e centile des valeurs quotidiennes
    `river_discharge_p75` des 31 DERNIERS jours (choix MVP, cf.
    WeatherRequest.get_flood_past_data et app/config.py).

    Le jour de prévision ajouté par l'API (forecast_days=1) est exclu : on ne
    garde que les dates antérieures au premier jour de la prévision de débit
    (ou à t0 si celle-ci est absente).
    """
    if serie_ref is None:
        return None
    limite = serie_prevue.index.min() if serie_prevue is not None else t0
    passe = serie_ref[serie_ref.index < limite].dropna()
    if passe.empty:
        return None
    return round(float(np.percentile(passe.to_numpy(), 75)), 3)
