"""
Configuration centrale du MVP « Veille météo et climatique au Burundi ».

Ce fichier contient :
1. `Seuils`   : les seuils expérimentaux utilisés par le module de conseils
                (app/services/advice.py). Chaque valeur peut être remplacée par
                une variable d'environnement préfixée SEUIL_ (ex.
                SEUIL_PLUIE_CRIT_PLAINE_24H=40) sans toucher au code —
                utile pour la recalibration après inspection des données des
                cinq zones.

2. `ZONES`    : la classification des cinq zones pilotes entre plaine et
                hautes terres, qui DÉTERMINE LE SEUIL DE PLUIE À APPLIQUER
                (50 mm/24 h en plaine, 100 mm/24 h en montagne — seuils IGEBU).
                Le classement plaine/montagne se fait PAR LOCALITÉ : c'est
                pourquoi Bujumbura Rural est « mixte » et exige une sous-zone
                (« imbo » = plaine, « mumirwa » = hautes terres).

Rappels inscrits dans les limitations du module de conseils :
- tous les seuils sont EXPÉRIMENTAUX et NON VALIDÉS pour l'alerte officielle
  au Burundi ;
- l'absence de données ne doit JAMAIS être présentée comme une vigilance
  faible (statut « non_évalué ») ;
- "débit inhabituel" GloFAS ne veut JAMAIS dire "débordement".
"""

from pydantic_settings import BaseSettings


class Seuils(BaseSettings):
    """
    Seuils expérimentaux du MVP. Overrides possibles via env, préfixe SEUIL_.
    """

    model_config = {"env_prefix": "SEUIL_", "extra": "ignore"}

    # ------------------------------------------------------------------
    # Pluie (section 4.1 du cahier des charges) — seuils IGEBU confirmés
    # ------------------------------------------------------------------
    pluie_crit_plaine_24h: float = 50.0   # mm / 24 h : Bujumbura, Imbo de BJR
    pluie_crit_montagne_24h: float = 100.0  # mm / 24 h : hautes terres

    # Pluie prolongée 72 h : seuil INDICATIF (~60–90 mm/72 h) à ajuster avec
    # les percentiles locaux des cinq zones. On démarre au milieu de la
    # fourchette IGEBU : 60 (plaine) / 90 (hautes terres).
    pluie_crit_plaine_72h: float = 60.0
    pluie_crit_montagne_72h: float = 90.0

    # Pluie récente déjà élevée : > 40–50 mm sur 7 jours (estimations de
    # MODÈLE Open-Meteo, pas des relevés de station). Valeur haute par défaut
    # pour limiter les faux déclenchements.
    pluie_7j_passes_crit: float = 50.0

    # Jours secs annoncés : >= 5 jours consécutifs de prévision avec < 2 mm/j
    jours_sec_mm: float = 2.0
    jours_secs_min: int = 5

    # ------------------------------------------------------------------
    # Vent (section 4.2) — dommages bananier dès 40 km/h (seuil le plus
    # prudent de la fourchette 40–55 km/h)
    # ------------------------------------------------------------------
    vent_rafale_kmh: float = 40.0

    # ------------------------------------------------------------------
    # Débit (section 4.3) — référence = p75 des 31 derniers jours du point
    # GloFAS (approximation MVP du 90e centile historique du même mois, cf.
    # app/services/weather.py::get_flood_past_data). Le message reste
    # "débit inhabituel pour la saison", JAMAIS débordement/évacuation.
    # ------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Classification plaine / hautes terres des zones pilotes
# ---------------------------------------------------------------------------
# "plaine"   -> seuil pluie_crit_plaine_24h  (50 mm/24 h)
# "montagne" -> seuil pluie_crit_montagne_24h (100 mm/24 h)
# "mixte"    -> le seuil dépend de la SOUS-ZONE (localité réelle) ;
#               en cas de doute sur la localité, retenir le seuil le plus BAS
#               (50 mm) par précaution.
ZONES: dict[str, str] = {
    "bujumbura": "plaine",        # plaine de l'Imbo, ~820 m
    "buja_rural": "mixte",        # relief 700–2 600 m : Imbo vs Mumirwa/Mugamba
    "gitega": "montagne",         # plateaux centraux, 1 600–2 000 m
    "karusi": "montagne",         # plateaux centraux, ~1 450–1 850 m
    "rutana": "montagne",         # ~1 650–1 800 m
}

# Sous-zones de Bujumbura Rural (classement PAR LOCALITÉ, pas par province).
SOUS_ZONES_BUJA_RURAL: dict[str, str] = {
    "imbo": "plaine",     # communes de l'Imbo, < 1 000 m -> seuil 50 mm/24 h
    "mumirwa": "montagne",  # Mumirwa/Mugamba, > 1 350 m -> seuil 100 mm/24 h
}

# Alias de localités utilisés par la route API (app/schemas/weather.py) :
# permettent de passer directement "buja_rural_imbo" ou "buja_rural_mumirwa".
ALIAS_LOCALITES: dict[str, tuple[str, str]] = {
    "buja_rural_imbo": ("buja_rural", "imbo"),
    "buja_rural_mumirwa": ("buja_rural", "mumirwa"),
}

# ---------------------------------------------------------------------------
# Cultures prévalentes par zone / sous-zone agro-écologique
# ---------------------------------------------------------------------------
CULTURES_PAR_ZONE: dict[str, tuple[str, ...]] = {
    "bujumbura": ("riz", "mais", "manioc", "bananier"),
    "gitega": ("mais", "haricot", "bananier", "riz", "patate_douce", "pomme_de_terre"),
    "karusi": ("mais", "haricot", "bananier", "manioc", "patate_douce"),
    "rutana": ("riz", "mais", "haricot", "manioc", "bananier"),
}

CULTURES_PAR_SOUS_ZONE_BUJA_RURAL: dict[str, tuple[str, ...]] = {
    "imbo": ("riz", "mais", "manioc", "bananier"),
    "mumirwa": ("bananier", "mais", "haricot", "manioc", "pomme_de_terre"),
}


def normaliser_zone(zone: str, sous_zone: str | None = None) -> tuple[str, str | None]:
    """
    Normalise un identifiant de zone (ou d'alias de localité API comme
    ``buja_rural_imbo``) en couple ``(zone, sous_zone)``.
    """
    if zone in ALIAS_LOCALITES:
        z_base, sz_alias = ALIAS_LOCALITES[zone]
        if sous_zone not in (None, "", sz_alias):
            raise ValueError(
                f"Conflit entre l'alias {zone!r} et sous_zone={sous_zone!r}"
            )
        return z_base, sz_alias
    return zone, sous_zone


def cultures_prevalentes(zone: str, sous_zone: str | None = None) -> tuple[str, ...]:
    """
    Renvoie les cultures prévalentes de la zone (et sous-zone pour Bujumbura
    Rural). Si Bujumbura Rural est interrogé sans sous-zone connue, renvoie les
    cultures de l'Imbo par cohérence avec le repli plaine de `classe_pluie`.
    """
    zone, sous_zone = normaliser_zone(zone, sous_zone)
    if zone not in ZONES:
        raise ValueError(f"Zone pilote inconnue : {zone!r}")
    if zone == "buja_rural":
        if sous_zone in CULTURES_PAR_SOUS_ZONE_BUJA_RURAL:
            return CULTURES_PAR_SOUS_ZONE_BUJA_RURAL[sous_zone]
        return CULTURES_PAR_SOUS_ZONE_BUJA_RURAL["imbo"]
    return CULTURES_PAR_ZONE[zone]


def classe_pluie(zone: str, sous_zone: str | None = None) -> str:
    """
    Renvoie "plaine" ou "montagne" pour une zone (et sous-zone si mixte).

    Règle de précaution : pour une zone mixte, une sous-zone inconnue ou
    absente déclenche le seuil de PLAINE (le plus bas, 50 mm) — cf. cahier
    des charges, section 2, règle pratique.
    """
    zone, sous_zone = normaliser_zone(zone, sous_zone)
    if zone not in ZONES:
        raise ValueError(f"Zone pilote inconnue : {zone!r}")

    classe = ZONES[zone]
    if classe != "mixte":
        if sous_zone not in (None, ""):
            raise ValueError(
                f"zone={zone!r} n'accepte pas de sous_zone (reçu {sous_zone!r})"
            )
        return classe

    if sous_zone in (None, ""):
        return "plaine"  # précaution : seuil le plus bas en cas de doute
    if sous_zone not in SOUS_ZONES_BUJA_RURAL:
        # Précaution (section 2 du cahier des charges) : en cas de doute sur la
        # localité exacte, retenir le seuil le PLUS BAS (50 mm/24 h).
        return "plaine"
    return SOUS_ZONES_BUJA_RURAL[sous_zone]
