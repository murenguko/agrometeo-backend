"""
Tests unitaires du module pur d'extraction (app/services/measures.py).

Aucun appel réseau : on simule le dict renvoyé par la route
`app.routes.weather.get_weather_data`.
"""

from datetime import datetime, timezone
import math

import pandas as pd

from app.services.advice import generer_conseil
from app.services.measures import extraire_mesures

MAINTENANT = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
T0 = pd.Timestamp(MAINTENANT)


def donnees_route(precip=None, rafales=None, debit=None, debit_p75=None) -> dict:
    """
    Dict au format de la route : 7 jours passés + 7 jours de prévision
    horaires (à partir du 21/10 00:00 UTC), débit prévu 7 j, p75 31 j + 1 j.
    """
    dates_h = pd.date_range("2026-10-21", periods=14 * 24, freq="h", tz="UTC")
    precip = precip or {}
    rafales = rafales or {}
    dates_q_prev = pd.date_range("2026-10-28", periods=7, freq="D", tz="UTC")
    dates_q_ref = pd.date_range("2026-09-27", periods=32, freq="D", tz="UTC")
    return {
        "location": "bujumbura",
        "hourly_data": {
            "date": dates_h.tolist(),
            "precipitation": [precip.get(d, 0.0) for d in dates_h],
            "wind_gusts_10m": [rafales.get(d, 10.0) for d in dates_h],
        },
        "daily_river_data": {
            "date": dates_q_prev.tolist(),
            "river_discharge": debit or [10.0] * 7,
        },
        "daily_river_past_data_p75": {
            "date": dates_q_ref.tolist(),
            "river_discharge": debit_p75 or [12.0] * 32,
        },
    }


def h(n: int) -> pd.Timestamp:
    return T0 + pd.Timedelta(hours=n)


def test_temps_calme():
    m = extraire_mesures(donnees_route(), maintenant=MAINTENANT)
    assert m["pluie_24h"] == 0.0
    assert m["pluie_72h"] == 0.0
    assert m["pluie_7j_passes"] == 0.0
    assert m["jours_secs"] == 6  # horizon complet disponible après t0
    assert m["rafales_kmh"] == 10.0
    assert m["debit_prevu"] == 10.0
    assert m["debit_reference"] == 12.0


def test_pluie_24h_et_72h():
    precip = {h(i): 10.0 for i in range(1, 7)}          # 60 mm dans les 24 h
    precip |= {h(i): 5.0 for i in range(30, 34)}        # +20 mm à J+1
    m = extraire_mesures(donnees_route(precip=precip), maintenant=MAINTENANT)
    assert m["pluie_24h"] == 60.0
    assert m["pluie_72h"] == 80.0
    assert m["jours_secs"] == 0


def test_bornes_fenetre_open_meteo():
    # La valeur datée t0 porte sur l'heure passée -> exclue des 24 h à venir,
    # incluse dans les 7 jours passés. Celle datée t0+24h est incluse.
    precip = {h(0): 7.0, h(24): 3.0}
    rafales = {h(0): 80.0, h(24): 42.0}
    m = extraire_mesures(donnees_route(precip=precip, rafales=rafales), maintenant=MAINTENANT)
    assert m["pluie_24h"] == 3.0
    assert m["pluie_7j_passes"] == 7.0
    assert m["rafales_kmh"] == 42.0


def test_pluie_7j_passes():
    precip = {h(-i): 5.0 for i in range(0, 12)}
    m = extraire_mesures(donnees_route(precip=precip), maintenant=MAINTENANT)
    assert m["pluie_7j_passes"] == 60.0
    assert m["pluie_24h"] == 0.0


def test_valeur_manquante_rend_la_mesure_non_evaluee():
    precip = {h(30): math.nan}
    m = extraire_mesures(donnees_route(precip=precip), maintenant=MAINTENANT)
    assert m["pluie_24h"] == 0.0          # fenêtre 24 h intacte
    assert m["pluie_72h"] is None         # NaN dans la fenêtre 72 h
    assert m["jours_secs"] is None        # interrompu à 1 jour (< 5)


def test_jours_secs_fiable_malgre_trou_tardif():
    precip = {h(24 * 5 + 3): None}        # trou au 6e jour, après 5 jours secs
    m = extraire_mesures(donnees_route(precip=precip), maintenant=MAINTENANT)
    assert m["jours_secs"] == 5


def test_debit_prevu_et_reference():
    debit = [10.0, 15.0, 12.0, 11.0, 9.0, 8.0, 7.0]
    p75 = [float(i) for i in range(1, 32)] + [999.0]  # dernier = jour prévu, exclu
    m = extraire_mesures(donnees_route(debit=debit, debit_p75=p75), maintenant=MAINTENANT)
    assert m["debit_prevu"] == 15.0
    assert m["debit_reference"] == 23.5  # p75 de 1..31


def test_blocs_absents():
    m = extraire_mesures({"location": "gitega"}, maintenant=MAINTENANT)
    assert all(v is None for v in m.values())


def test_dates_serialisees_json():
    d = donnees_route(precip={h(1): 55.0})
    for bloc in ("hourly_data", "daily_river_data", "daily_river_past_data_p75"):
        d[bloc]["date"] = [ts.isoformat() for ts in d[bloc]["date"]]
    m = extraire_mesures(d, maintenant=MAINTENANT)
    assert m["pluie_24h"] == 55.0


def test_branchement_avec_generer_conseil():
    precip = {h(i): 10.0 for i in range(1, 7)}  # 60 mm >= 50 mm (plaine)
    mesures = extraire_mesures(donnees_route(precip=precip), maintenant=MAINTENANT)
    conseil = generer_conseil(mesures, "bujumbura", "mais", maintenant=MAINTENANT)
    assert conseil["statut"] == "conseil_actif"
    assert "pluie_24h_≥_50mm" in conseil["regles_declenchees"]
