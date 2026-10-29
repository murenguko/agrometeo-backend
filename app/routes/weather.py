from fastapi import APIRouter, Depends
from typing import Annotated
from app.schemas.weather import Location
from app.services.weather import WeatherRequest
from app.core.security import oauth2_scheme
from app.core.security import security
from app.schemas.user import User
from app.services.measures import extraire_mesures
from app.services.advice import generer_conseils
from app.config import Seuils

router = APIRouter()

weather_service = WeatherRequest()

coordinates={
    "gitega":{
        "lat": -3.42708,
        "long": 29.9246
    },
    "karusi":{
        "lat": -3.10139,
        "long":  30.16278
        },
    "bujumbura":{
        "lat": -3.38193,
        "long":29.36142
        },
    "rutana":{
        "lat": -3.9279,
        "long": 29.992
        },
    
    # Bujumbura Rural est MIXTE : les seuils plaine (50 mm/24h) et hautes
    # terres (100 mm/24h) ne sont PAS les mêmes. Deux points distincts :
    # - buja_rural_imbo : plaine de l'Imbo (< 1 000 m) -> seuil 50 mm/24 h.
    # - buja_rural_mumirwa : Mumirwa/Mugamba (> 1 350 m) -> seuil 100 mm/24 h.
    # IMPORTANT : ces deux points sont INDICATIFS et doivent être validés
    # cellule par cellule (coordonnées/rivières/altitude) aux Jours 1–2 du
    # projet (cf. app/config.py et app/services/advice.py).
    "buja_rural_imbo":{
        "lat": -3.4292,
        "long": 29.3165
        },
    "buja_rural_mumirwa":{
        "lat": -3.4389,
        "long": 29.6944
        },
}


@router.get("/{location}", response_model= None)
async def get_weather_data(
    location: Location,
    token: Annotated[User, Depends(security.get_current_user)]
):
    lat, long = coordinates[location]["lat"],coordinates[location]["long"]
    weather_results = await weather_service.make_weather_request(location,lat, long)
    river_results = await weather_service.make_flood_request(location, lat, long)
    past_river_data = await weather_service.get_flood_past_data(lat,long)
    all_results = weather_results | river_results | past_river_data

    mesures = extraire_mesures(all_results, seuils=Seuils())
    print("Mesures: ", mesures)
    conseils = generer_conseils(mesures, location.value)
    return all_results | {"conseils": conseils}
