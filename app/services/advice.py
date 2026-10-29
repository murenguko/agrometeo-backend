"""
Module PUR de génération de conseils agricoles — MVP « Veille météo et
climatique au Burundi » (zones pilotes : Bujumbura, Bujumbura Rural, Gitega,
Karusi, Rutana).

CARACTÈRE PUR ET SANS ROUTE (choix validé avec l'équipe) :
- ce module ne fait AUCUN appel réseau ;
- il reçoit déjà extraits les mesures (dict `mesures`) et renvoie un dict
  `conseil` conforme au format de sortie du cahier des charges (section 7) ;
- le branchement (extraction + règles) se fait ailleurs pour garder ce module
  100 % testable unitairement.

LES QUATRE QUESTIONS que chaque message doit couvrir : où (zone/sous-zone),
pour quelle période (durée de validité), quelles mesures (valeurs + seuils),
que vérifier concrètement (le message commence TOUJOURS par une action).

RÈGLES DE SÉCURITÉ DES MESSAGES (non négociables, cf. limitations) :
- JAMAIS « inondation certaine », JAMAIS « évacuation », JAMAIS « pas de
  risque » ;
- l'absence de données affiche le statut « non_évalué », jamais un signal
  faible ;
- « débit inhabituel » = plus haut que le p75 des 31 derniers jours, pas un
  débordement.
"""

from datetime import datetime, timezone
import math

from app.config import Seuils, classe_pluie, cultures_prevalentes, normaliser_zone

# ---------------------------------------------------------------------------
# Cultures gérées et leurs sensibilités
# ---------------------------------------------------------------------------

RIZ = "riz"
BANANIER = "bananier"
MAIS = "mais"
CULTURES_HUMIDITE = frozenset({"haricot", "manioc", "patate_douce", "pomme_de_terre"})
CULTURES = frozenset({RIZ, BANANIER, MAIS, *CULTURES_HUMIDITE})

# ---------------------------------------------------------------------------


def _msg_pluie_24h(culture: str) -> str:
    """Message du seuil pluie critique 24 h (texte adapté par culture)."""
    if culture == MAIS:
        return (
            "Si vous faites sécher du maïs en plein air, prévoyez de le mettre "
            "à l'abri ou de le couvrir avant les pluies annoncées. Vérifiez les "
            "fossés et les accès autour des aires de séchage."
        )
    return (
        "Protégez les produits en cours de séchage et vérifiez les fossés et "
        "les accès exposés."
    )


def _msg_vent(culture: str) -> str:
    """Message du seuil rafales (texte adapté : bananier/maïs = plants)."""
    if culture == BANANIER:
        return (
            "Vérifiez les plants exposés au vent. Si des brise-vent existent, "
            "contrôlez leur état ; les régimes proches de la maturité peuvent "
            "être plus vulnérables à la chute."
        )
    if culture == MAIS:
        return (
            "Vérifiez les plants exposés au vent : les rafales fortes peuvent "
            "provoquer le versement ou le déracinement du maïs et des jeunes "
            "plants. Contrôlez aussi les installations légères."
        )
    return (
        "Vérifiez les cultures exposées, les supports et les installations "
        "légères."
    )


def _msg_humidite(culture: str) -> str:
    """Message pluie prolongée / sols gorgés pour haricot, manioc, etc."""
    base = (
        "Après des pluies prolongées, vérifiez l'apparition de rigoles ou de "
        "ravinements et l'évacuation de l'eau autour des parcelles."
    )
    if culture == "patate_douce":
        base += " Vérifiez aussi l'état des buttes."
    return base


# Limitations affichées par zone (section 6 du cahier des charges).
LIMITATIONS_ZONE: dict[str, tuple[str, ...]] = {
    "bujumbura": (),
    "buja_rural": (
        "Relief très contrasté de Bujumbura Rural (700–2 600 m) : un même "
        "épisode peut dépasser le seuil de plaine sans atteindre celui des "
        "collines. Le classement plaine / montagne est fait PAR LOCALITÉ ; "
        "sous-zone inconnue -> seuil plaine (50 mm) par précaution. Les petits "
        "bassins (Mugere, Kirasa, Mukobore, Mubarazi) peuvent être mal "
        "représentés par GloFAS : module érosion/glissements non couvert.",
        "Les versants escarpés sont naturellement exposés à l'érosion et aux "
        "glissements, mais ce module n'est pas inclus dans ce périmètre "
        "météo–crues : aucun conseil de glissement n'est affiché.",
    ),
    "gitega": (
        "Les affluents secondaires de la Ruvubu ne sont pas couverts par "
        "GloFAS à cette résolution.",
    ),
    "karusi": (
        "Les cours d'eau identifiés (Gasenyi, Rusheri, Gikunguza) sont "
        "secondaires : la pertinence du point GloFAS associé doit être "
        "considérée comme expérimentale.",
    ),
    "rutana": (
        "Un point modèle GloFAS existe pour Rutana : vérifier qu'il correspond "
        "bien au cours d'eau local avant de s'y fier.",
    ),
}

# Zones où le module crues est PRÉCIS (bas-fonds rizicoles priorisés).
ZONES_RIZ_PRIORITAIRES = frozenset({"buja_rural", "rutana", "gitega"})


# ---------------------------------------------------------------------------
# Moteur de règles (fonction pure)
# ---------------------------------------------------------------------------


def generer_conseil(
    mesures: dict,
    zone: str,
    culture: str,
    sous_zone: str | None = None,
    maintenant: datetime | None = None,
    seuils: Seuils | None = None,
) -> dict:
    """
    Génère UN conseil pour (zone, sous_zone, culture) à partir des mesures.

    Paramètres
    ----------
    mesures : dict avec les clefs (toutes optionnelles, `None` si la donnée
        manque — le conseil passe alors à « non évalué » pour les règles
        concernées) :
        - pluie_24h : mm (critique 24 h, modèle) ;
        - pluie_72h : mm (cumul indicatif) ;
        - pluie_7j_passes : mm (estimation modèle des 7 derniers jours) ;
        - jours_secs : jours consécutifs < 2 mm prévus ;
        - rafales_kmh : rafales max attendues ;
        - debit_prevu / debit_reference : m³/s au point GloFAS.

    retour : dict conforme à la section 7 :
        zone, sous_zone, culture, date_evaluation, statut, regles_declenchees,
        mesures_utilisees, message, duree_validite, source_donnees,
        limitations.
    """
    seuils = seuils or Seuils()
    maintenant = maintenant or datetime.now(timezone.utc)
    date_evaluation = maintenant.strftime("%Y-%m-%d %H:%M (heure UTC)")
    source = f"Open-Meteo (OpenMeteo Flood API pour le débit), {maintenant:%d/%m/%Y}"

    if culture not in CULTURES:
        raise ValueError(f"Culture non gérée : {culture!r}")

    zone, sous_zone = normaliser_zone(zone, sous_zone)

    # La sous-zone n'a de sens QUE pour les zones mixtes (Bujumbura Rural) :
    # pour toute autre zone elle est ignorée dans le calcul des seuils (mais
    # reste rapportée dans la sortie pour traçabilité).
    classe = classe_pluie(
        zone, sous_zone if zone == "buja_rural" else None
    )  # "plaine" ou "montagne"

    # Seuils effectifs selon le classe plaine / hautes terres de la localité.
    seuil_24 = (
        seuils.pluie_crit_plaine_24h if classe == "plaine" else seuils.pluie_crit_montagne_24h
    )
    seuil_72 = (
        seuils.pluie_crit_plaine_72h if classe == "plaine" else seuils.pluie_crit_montagne_72h
    )

    # ------------------------------------------------------------------
    # 1) Applications des règles avec garde-fous : une mesure absente
    #    (`None`, NaN, ...) rend la règle NON ÉVALUÉE, jamais "sans risque".
    # ------------------------------------------------------------------
    regles: list[str] = []
    messages: list[str] = []
    duree = "prochaines 24 h"
    invalide: list[str] = []
    # Limitations supplémentaires activées en cours d'évaluation (ex. module
    # débit) : affichées en complément des limitations générales.
    limitations_specifiques: list[str] = []

    # --- pluie critique 24 h (toutes cultures, seuil plaine/montagne) ---
    pluie_24h = _nb(mesures.get("pluie_24h"))
    if pluie_24h is None:
        invalide.append("pluie_24h")
    elif pluie_24h >= seuil_24:
        regles.append(f"pluie_24h_≥_{seuil_24:g}mm")
        messages.append(_msg_pluie_24h(culture))

    # --- pluie prolongée 72 h / riz / cultures sensibles à l'humidité ---
    pluie_72h = _nb(mesures.get("pluie_72h"))
    if pluie_72h is None:
        invalide.append("pluie_72h")

    if culture == RIZ:
        # La zone riz dépend D'ABORD du débit (bas-fonds). Le message intégral
        # section 5.2 est celui qui est déclenché en priorité.
        debit_prevu = _nb(mesures.get("debit_prevu"))
        debit_ref = _nb(mesures.get("debit_reference"))
        if debit_prevu is None or debit_ref is None:
            invalide.extend(["debit_prevu", "debit_reference"])
        elif debit_prevu > debit_ref:
            regles.append("debit_prevu>debit_reference_p75")
            messages.append(
                "Le débit prévu est inhabituel pour ce point à cette période. "
                "Vérifiez les diguettes, les canaux et les accès du bas-fond. "
                "Une pluie normale n'est pas un problème pour le riz, mais un "
                "débit inhabituel justifie une vérification locale."
            )
            duree = "prochaines 72 h"
            # Limitation impérative (section 6.4) : GloFAS ne connaît ni les
            # berges, ni les seuils de débordement locaux, ni la hauteur d'eau.
            limitations_specifiques.append(
                "Débit GloFAS (~5 km, grands bassins) : ne prouve pas un "
                "débordement. Un débit inhabituel signifie seulement "
                "« plus haut que le p75 des 31 derniers jours ». Vérification "
                "locale indispensable."
            )
        elif debit_prevu <= debit_ref and pluie_72h is not None and pluie_72h >= seuil_72:
            # Pas de débit inhabituel MAIS pluie prolongée annoncée :
            # surveillance des points bas du bas-fond (section 5.2).
            regles.append(f"pluie_72h_≥_{seuil_72:g}mm")
            messages.append(
                "Surveillez l'accumulation d'eau dans les points bas du "
                "bas-fond et vérifiez que les évacuations ne sont pas "
                "obstruées."
            )
            duree = "prochaines 72 h"
    else:
        accept_humidite = culture in CULTURES_HUMIDITE
        if pluie_72h is not None and pluie_72h >= seuil_72:
            regles.append(f"pluie_72h_≥_{seuil_72:g}mm")
            messages.append(_msg_humidite(culture) if accept_humidite else (
                "Surveillez l'accumulation d'eau et les endroits où le sol se "
                "dégrade."
            ))
            duree = "prochaines 72 h"
        elif accept_humidite and _nb(mesures.get("pluie_7j_passes")) is not None:
            # Pour haricot/manioc/patate douce/pomme de terre, une pluie déjà
            # récente élevée (> seuil 7 j) suffit aussi à déclencher la
            # vérification (section 5.4).
            pluie_7j = _nb(mesures.get("pluie_7j_passes"))
            if pluie_7j >= seuils.pluie_7j_passes_crit:
                regles.append(f"pluie_7j_passes_≥_{seuils.pluie_7j_passes_crit:g}mm")
                messages.append(_msg_humidite(culture))
                duree = "prochains 7 jours"
            else:
                pass

    # --- vent (toutes cultures, attention renforcée bananier/maïs) ---
    rafales = _nb(mesures.get("rafales_kmh"))
    if rafales is None:
        invalide.append("rafales_kmh")
    elif rafales >= seuils.vent_rafale_kmh:
        regles.append(f"rafales_≥_{seuils.vent_rafale_kmh:g}kmh")
        messages.append(_msg_vent(culture))

    # --- jours secs annoncés (toutes cultures) ---
    jours_secs = _nb(mesures.get("jours_secs"))
    if jours_secs is None:
        invalide.append("jours_secs")
    elif jours_secs >= seuils.jours_secs_min:
        regles.append(f"jours_secs_≥_{seuils.jours_secs_min}")
        messages.append(
            "Période sèche annoncée : surveillez l'état des cultures et "
            "vérifiez localement la disponibilité en eau."
        )

    # ------------------------------------------------------------------
    # 2) Statut : conseil_actif si une règle s'est déclenchée ; sinon
    #    non_évalué si au moins une donnée utile manquait ; sinon sans_signal.
    # ------------------------------------------------------------------
    if regles:
        statut = "conseil_actif"
        message = " ".join(messages)
    elif invalide:
        statut = "non_evalue"
        manque = ", ".join(sorted(invalide))
        message = (
            "Conseil non évaluable pour l'instant : données non disponibles "
            f"pour {manque}. L'absence de données ne signifie pas l'absence de "
            "risque ni une vigilance faible : vérifiez localement l'état des "
            "cultures et le terrain."
        )
    else:
        statut = "sans_signal"
        message = (
            "Aucun seuil expérimental n'est franchi d'après les données "
            "disponibles. Restez attentif : la prévision est locale et n'est "
            "pas une certitude."
        )

    limitations = _limitations(zone, classe, seuil_24) + tuple(limitations_specifiques)
    if statut == "non_evalue":
        limitations = ("Non évalué (absence de données). L'absence de données "
                       "n'est PAS une vigilance faible.",) + limitations

    mesures_utilisees: dict = {}
    if pluie_24h is not None or "pluie_24h" in invalide:
        mesures_utilisees["pluie_24h_mm"] = pluie_24h
        mesures_utilisees["seuil_24h_mm"] = seuil_24
    if pluie_72h is not None or "pluie_72h" in invalide:
        mesures_utilisees["pluie_72h_mm"] = pluie_72h
        mesures_utilisees["seuil_72h_mm"] = seuil_72
    if rafales is not None or "rafales_kmh" in invalide:
        mesures_utilisees["rafales_kmh"] = rafales
        mesures_utilisees["seuil_rafales_kmh"] = seuils.vent_rafale_kmh
    if jours_secs is not None or "jours_secs" in invalide:
        mesures_utilisees["jours_secs"] = jours_secs
        mesures_utilisees["seuil_jours_secs"] = seuils.jours_secs_min

    return {
        "zone": zone,
        "sous_zone": sous_zone,
        "culture": culture,
        "date_evaluation": date_evaluation,
        "statut": statut,
        "regles_declenchees": regles,
        "mesures_utilisees": mesures_utilisees,
        "message": message,
        "duree_validite": duree,
        "source_donnees": source,
        "limitations": list(limitations),
    }


def generer_conseils(
    mesures: dict,
    zones: str | list[str],
    cultures: list[str] | None = None,
    sous_zone: str | None = None,
    **kw,
) -> list[dict]:
    """
    Dérive UN conseil par (zone, culture).

    Lorsque `cultures` n'est pas fourni, utilise uniquement les cultures
    prévalentes dans chaque zone (et sous-zone pour Bujumbura Rural).
    """
    liste_zones = [zones] if isinstance(zones, str) else list(zones)
    resultats: list[dict] = []
    for zone in liste_zones:
        z_norm, sz_norm = normaliser_zone(zone, sous_zone)
        cultures_zone = (
            list(cultures)
            if cultures is not None
            else list(cultures_prevalentes(z_norm, sz_norm if z_norm == "buja_rural" else None))
        )
        for culture in cultures_zone:
            resultats.append(
                generer_conseil(mesures, z_norm, culture, sous_zone=sz_norm, **kw)
            )
    return resultats


# ---------------------------------------------------------------------------


def _nb(value) -> float | None:
    """Normalise une mesure numérique (`None` si absente / non numérique)."""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f


def _limitations(zone, classe, seuil_24) -> tuple[str, ...]:
    """Assemble le bloc de limitations affiché à l'utilisateur (section 6)."""
    lieu = "hautes terres" if classe == "montagne" else "plaines"
    lines = [
        "Prévision issue de modèles : la pluie peut être très localisée. La "
        "probabilité de pluie (%) ne remplace pas la quantité (mm), qui seule "
        "est utilisée ici.",
        f"Seuil critique de pluie applicable pour les {lieu} : {seuil_24:g} "
        "mm/24 h. Seuil expérimental, non validé pour l'alerte officielle au "
        "Burundi — invitation à vérifier, pas une certitude.",
    ]
    if zone == "buja_rural" and classe == "montagne":
        lines.append(
            "Sous-zone Mumirwa/Mugamba (hautes terres) : seuil montagne "
            f"({seuil_24:g} mm/24 h) appliqué. Sous-zone inconnue -> seuil "
            "plaine (50 mm) par précaution."
        )
    return tuple(lines) + LIMITATIONS_ZONE.get(zone, ())
