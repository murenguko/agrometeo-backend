from enum import Enum
from pydantic import BaseModel
from typing import Dict,List



class Location(str, Enum):
    gitega='gitega'
    bujumbura='bujumbura'
    rutana='rutana'
    karusi='karusi'
    # Bujumbura Rural est MIXTE (relief 700–2 600 m) : le classement
    # plaine/montagne se fait PAR LOCALITÉ, pas par province entière.
    # L'Imbo est la plaine (< 1 000 m) -> seuil 50 mm/24 h ;
    # le Mumirwa/Mugamba sont les hautes terres (> 1 350 m) -> seuil 100 mm/24 h.
    buja_rural_imbo='buja_rural_imbo'      # sous-zone plaine (point indicatif, à confirmer Jours 1–2)
    buja_rural_mumirwa='buja_rural_mumirwa'  # sous-zone hautes terres (point indicatif, à confirmer Jours 1–2)



