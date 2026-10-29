"""
Tests unitaires du module pur de conseils (app/services/advice.py).

Aucun appel réseau : les mesures sont simulées. On vérifie :
- le classement plaine / hautes terres et les seuils appliqués ;
- chaque règle (pluie 24 h / 72 h / 7 j, vent, jours secs, débit riz) ;
- les statuts (conseil_actif / sans_signal / non_evalue) ;
- les garanties de message (jamais « évacuation » ni « pas de risque »).
"""

import pytest

from app.config import classe_pluie, cultures_prevalentes
from app.services.advice import (
    MAIS,
    RIZ,
    generer_conseil,
    generer_conseils,
)


def mesures_neutres(**overrides) -> dict:
    """Mesures 'calmes' : aucun seuil ne doit se déclencher par défaut."""
    base = {
        "pluie_24h": 5.0,          # < 50 mm
        "pluie_72h": 12.0,         # < 60 mm
        "pluie_7j_passes": 8.0,    # < 50 mm
        "jours_secs": 0,           # < 5
        "rafales_kmh": 18.0,       # < 40 km/h
        "debit_prevu": 10.0,       # < debit_reference -> pas de signal riz
        "debit_reference": 12.0,   # p75 des 31 derniers jours
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Classement plaine / hautes terres
# ---------------------------------------------------------------------------


def test_classement_zones():
    assert classe_pluie("bujumbura") == "plaine"
    assert classe_pluie("gitega") == "montagne"
    assert classe_pluie("karusi") == "montagne"
    assert classe_pluie("rutana") == "montagne"


def test_buja_rural_mixte():
    assert classe_pluie("buja_rural", "imbo") == "plaine"
    assert classe_pluie("buja_rural", "mumirwa") == "montagne"
    # Précaution : sous-zone inconnue / absente -> seuil plaine le plus bas.
    assert classe_pluie("buja_rural") == "plaine"
    assert classe_pluie("buja_rural", "localite_douteuse") == "plaine"


def test_invalides():
    with pytest.raises(ValueError):
        classe_pluie("zone_fg")
    with pytest.raises(ValueError):
        classe_pluie("gitega", "imbo")  # pas de sous-zone hors zones mixtes


# ---------------------------------------------------------------------------
# Seuils pluie 24 h appliqués selon la localité
# ---------------------------------------------------------------------------


def test_pluie_24h_zone_montagne():
    # 108 mm franchit 100 mm (hautes terres) mais pas 150 : gitega OK.
    c = generer_conseil(mesures_neutres(pluie_24h=108.0), "gitega", MAIS)
    assert c["statut"] == "conseil_actif"
    assert any(r.startswith("pluie_24h_≥_100") for r in c["regles_declenchees"])
    assert "aires de séchage" in c["message"]


def test_pluie_24h_zone_plaine_50_mm():
    # 55 mm : franchit le seuil plaine (50) pas celui des hautes terres.
    c_plain = generer_conseil(mesures_neutres(pluie_24h=55.0), "bujumbura", "bananier")
    assert any(r.startswith("pluie_24h_≥_50") for r in c_plain["regles_declenchees"])

    # Le même 55 mm NE déclenche PAS la règle sur une colline du Mumirwa
    # (seuil 100 mm) : c'est exactement le cas d'un épisode pluvieux de plaine.
    c_hill = generer_conseil(
        mesures_neutres(pluie_24h=55.0), "buja_rural", "bananier", sous_zone="mumirwa"
    )
    assert not any(r.startswith("pluie_24h") for r in c_hill["regles_declenchees"])

    # Mais sans sous-zone (doute sur la localité) -> précaution -> seuil 50 mm.
    c_uncertain = generer_conseil(mesures_neutres(pluie_24h=55.0), "buja_rural", "bananier")
    assert any(r.startswith("pluie_24h_≥_50") for r in c_uncertain["regles_declenchees"])


# ---------------------------------------------------------------------------
# Vent
# ---------------------------------------------------------------------------


def test_vent_bananier_40_kmh():
    c = generer_conseil(mesures_neutres(rafales_kmh=42.0), "gitega", "bananier")
    assert c["statut"] == "conseil_actif"
    assert "brise-vent" in c["message"]


def test_sous_le_seuil_vent_pas_de_declenchemen():
    c = generer_conseil(mesures_neutres(rafales_kmh=39.9), "gitega", "bananier")
    assert not any(r.startswith("rafales") for r in c["regles_declenchees"])


# ---------------------------------------------------------------------------
# Jours secs annoncés
# ---------------------------------------------------------------------------


def test_jours_secs():
    c = generer_conseil(mesures_neutres(jours_secs=6), "karusi", "haricot")
    assert any(r.startswith("jours_secs_≥_5") for r in c["regles_declenchees"])


# ---------------------------------------------------------------------------
# Riz et module débit (bas-fonds)
# ---------------------------------------------------------------------------


def test_riz_debit_inhabituel():
    c = generer_conseil(
        mesures_neutres(debit_prevu=15.5, debit_reference=12.0),
        "rutana", RIZ,
    )
    assert c["statut"] == "conseil_actif"
    assert any(r == "debit_prevu>debit_reference_p75" for r in c["regles_declenchees"])
    # Garanties de formulation : jamais "évacuation" ni "inondation certaine".
    msg = c["message"].lower()
    assert "évacuation" not in msg
    assert "inondation certaine" not in msg
    assert "diguettes" in c["message"]
    # La limitation GloFAS est obligatoirement affichée.
    assert any("débordement" in l for l in c["limitations"])


def test_riz_pluie_sans_debit_inhabituel():
    # Bas-fond de l'Imbo (plaine) : 75 mm/72 h dépasse le seuil indicatif
    # plaine (60 mm) mais le débit prévu reste sous la référence p75.
    c = generer_conseil(mesures_neutres(pluie_72h=75.0), "buja_rural", RIZ,
                        sous_zone="imbo")
    assert any(r.startswith("pluie_72h") for r in c["regles_declenchees"])
    assert "points bas" in c["message"]
    # Pas de règle débit : le debit est resté sous la référence.
    assert not any("debit" in r for r in c["regles_declenchees"])


# ---------------------------------------------------------------------------
# Cultures sensibles à l'humidité (haricot, manioc, patate douce, pdt)
# ---------------------------------------------------------------------------


def test_humidite_7j_passes():
    # pluie 72 h calme MAIS 7 jours passés > 50 mm -> vérification quand même.
    c = generer_conseil(
        mesures_neutres(pluie_72h=20.0, pluie_7j_passes=55.0),
        "karusi", "haricot",
    )
    assert c["statut"] == "conseil_actif"
    assert any(r.startswith("pluie_7j_passes") for r in c["regles_declenchees"])
    assert "rigoles" in c["message"]


def test_patate_douce_mais_buttes():
    c = generer_conseil(mesures_neutres(pluie_72h=95.0), "gitega", "patate_douce")
    assert "buttes" in c["message"]


# ---------------------------------------------------------------------------
# Statuts
# ---------------------------------------------------------------------------


def test_sans_signal():
    c = generer_conseil(mesures_neutres(), "gitega", "haricot")
    assert c["statut"] == "sans_signal"
    # Le statut sans signal ne doit JAMAIS dire "pas de risque" (§ 6.4).
    assert "pas de risque" not in c["message"].lower()


def test_non_evalue_si_donnees_manquantes():
    c = generer_conseil(
        {"pluie_24h": None, "pluie_72h": None, "pluie_7j_passes": None,
         "jours_secs": None, "rafales_kmh": None},
        "gitega", "haricot",
    )
    assert c["statut"] == "non_evalue"
    # Le message lui-même porte l'avertissement d'absence de données :
    # formulations exactes garanties par le module (§ 6.4).
    assert "n'est PAS une vigilance faible" in " ".join(c["limitations"])


def test_non_evalue_si_valeur_nan():
    # Une mesure non numérique est traitée comme une donnée absente.
    c = generer_conseil(mesures_neutres(rafales_kmh=float("nan")), "gitega", "manioc")
    assert c["statut"] == "non_evalue"
    assert "rafales_kmh" in c["message"]


def test_conseil_actif_reste_actif_avec_donnee_absente():
    # pluie critique déclenchée malgré rafales indisponibles : le conseil
    # reste actif mais pas de règle vent inventée.
    c = generer_conseil(mesures_neutres(pluie_24h=120.0, rafales_kmh=None), "gitega", MAIS)
    assert c["statut"] == "conseil_actif"
    assert not any(r.startswith("rafales") for r in c["regles_declenchees"])


# ---------------------------------------------------------------------------
# Format de sortie et validations
# ---------------------------------------------------------------------------


def test_format_sortie():
    c = generer_conseil(mesures_neutres(), "rutana", RIZ)
    attendus = {
        "zone", "sous_zone", "culture", "date_evaluation", "statut",
        "regles_declenchees", "mesures_utilisees", "message",
        "duree_validite", "source_donnees", "limitations",
    }
    assert attendus.issubset(c.keys())
    assert c["zone"] == "rutana"
    assert c["sous_zone"] is None
    assert c["culture"] == "riz"


def test_cultures_inconnue():
    with pytest.raises(ValueError):
        generer_conseil(mesures_neutres(), "gitega", "cafe")


def test_generer_conseils_multi_zones():
    # La sous-zone est ignorée pour les zones non mixtes (gitega, rutana) : les
    # conseils utilisent bien le seuil montagne (100 mm).
    res = generer_conseils(mesures_neutres(pluie_24h=105.0), ["gitega", "rutana"],
                           cultures=[MAIS], sous_zone="mumirwa")
    assert len(res) == 2
    for c in res:
        assert c["statut"] == "conseil_actif"
        assert any(r.startswith("pluie_24h_≥_100") for r in c["regles_declenchees"])


def test_cultures_prevalentes_par_zone():
    assert cultures_prevalentes("bujumbura") == ("riz", "mais", "manioc", "bananier")
    assert cultures_prevalentes("buja_rural_imbo") == ("riz", "mais", "manioc", "bananier")
    assert cultures_prevalentes("buja_rural_mumirwa") == (
        "bananier", "mais", "haricot", "manioc", "pomme_de_terre"
    )
    assert cultures_prevalentes("gitega") == (
        "mais", "haricot", "bananier", "riz", "patate_douce", "pomme_de_terre"
    )
    assert cultures_prevalentes("karusi") == (
        "mais", "haricot", "bananier", "manioc", "patate_douce"
    )
    assert cultures_prevalentes("rutana") == (
        "riz", "mais", "haricot", "manioc", "bananier"
    )


def test_generer_conseils_utilise_cultures_prevalentes_par_defaut():
    res_karusi = generer_conseils(mesures_neutres(), "karusi")
    assert [c["culture"] for c in res_karusi] == [
        "mais", "haricot", "bananier", "manioc", "patate_douce"
    ]

    res_mumirwa = generer_conseils(mesures_neutres(), "buja_rural_mumirwa")
    assert [c["culture"] for c in res_mumirwa] == [
        "bananier", "mais", "haricot", "manioc", "pomme_de_terre"
    ]
    assert all(c["zone"] == "buja_rural" and c["sous_zone"] == "mumirwa" for c in res_mumirwa)
