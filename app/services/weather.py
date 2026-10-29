import openmeteo_requests
import requests_cache
from retry_requests import retry
import pandas as pd


class WeatherRequest:
    
    def __init__(self):
        # Setup the Open-Meteo API client with cache and retry on error
        self.cache_session = requests_cache.CachedSession('.cache', expire_after = 3600)
        self.retry_session = retry(self.cache_session, retries = 5, backoff_factor = 0.2)
        self.openmeteo = openmeteo_requests.Client(session = self.retry_session)
        self.weather_url = "https://api.open-meteo.com/v1/forecast"
        self.flood_url="https://flood-api.open-meteo.com/v1/flood"

    async def make_weather_request(self,location, lat, long) -> dict:
        """
        Requête météo horaire Open-Meteo pour une zone pilote.

        IMPORTANT — alignement date <-> mesure :
        Open-Meteo renvoie pour chaque variable horaire un tableau de valeurs
        construit sur la même grille temporelle (hourly_data["date"]). Chaque
        indice de chaque tableau de variable correspond à la même date. Toute
        nouvelle variable ajoutée à "hourly", comme tout changement de
        "past_days"/"forecast_days", prolonge LA GRILLE ENTIÈRE de la même
        longueur pour toutes les variables : il ne faut donc jamais modifier
        un tableau de variable seul sans re-générer "date".

        Ajouts pour le module de conseils (app/services/advice.py) :
        - "past_days": 7 -> les 7 derniers jours sont des ESTIMATIONS DE
          MODÈLE (pas des relevés de station) ; ils alimentent
          exclusivement la règle "pluie_7j_passes" (> 40–50 mm).
        - "wind_gusts_10m" -> rafales à 10 m ; règle vent (>= 40 km/h).
        """
        params = {
            "latitude": lat,
            "longitude": long,
            "timezone": "auto",
            "past_days": 7,  # 7 derniers jours estimés, pour pluie_7j_passes
            "hourly": [
                "temperature_2m",
                "precipitation",
                "wind_speed_10m",
                "precipitation_probability",
                "wind_gusts_10m",  # rafales : règle vent >= 40 km/h
            ],
        }
        responses = self.openmeteo.weather_api(self.weather_url, params = params)

        response = responses[0]
        print(f"Coordinates: {response.Latitude()}°N {response.Longitude()}°E")
        print(f"Elevation: {response.Elevation()} m asl")
        print(f"Timezone difference to GMT+0: {response.UtcOffsetSeconds()}s")

        hourly = response.Hourly()
        # Index des variables : il suit l'ordre exact de la liste "hourly".
        hourly_temperature_2m = hourly.Variables(0).ValuesAsNumpy().tolist()
        hourly_precipitation = hourly.Variables(1).ValuesAsNumpy().tolist()
        hourly_wind_speed_10m = hourly.Variables(2).ValuesAsNumpy().tolist()
        hourly_precipitation_probability = hourly.Variables(3).ValuesAsNumpy().tolist()
        hourly_wind_gusts_10m = hourly.Variables(4).ValuesAsNumpy().tolist()

        # La grille de dates couvre les 7 jours passés ET les jours de
        # prévision. Sa longueur est identique à celle de CHAQUE tableau de
        # variable ci-dessus -> l'alignement date <-> mesure reste garanti
        # malgré l'ajout de past_days=7.
        hourly_data = {
            "date": pd.date_range(
                start = pd.to_datetime(hourly.Time(), unit = "s", utc = True),
                end =  pd.to_datetime(hourly.TimeEnd(), unit = "s", utc = True),
                freq = pd.Timedelta(seconds = hourly.Interval()),
                inclusive = "left"
            )
        }

        hourly_data["date"]= hourly_data["date"].tolist()
        hourly_data["temperature_2m"] = hourly_temperature_2m
        hourly_data["precipitation"] = hourly_precipitation
        hourly_data["wind_speed_10m"] = hourly_wind_speed_10m
        hourly_data["precipitation_probability"] = hourly_precipitation_probability
        hourly_data["wind_gusts_10m"] = hourly_wind_gusts_10m

        

        return {
            "location": location,
            "latitude": response.Latitude(),
            "longitude": response.Longitude(),
            "elevation": response.Elevation(),
            "timezone": response.Timezone(),
            "hourly_data": hourly_data
        }


    async def make_flood_request(self, location, lat, long) -> dict :
        """
        Débit GloFAS prévu au point (lat, long), 7 jours.

        ATTENTION (limitation connue) : GloFAS tourne à ~5 km de résolution et
        est conçu pour les grands bassins (> 500 km²). Les petits cours d'eau
        de Bujumbura Rural (Mugere, Kirasa, Mukobore, Mubarazi) et de Karusi
        (Gasenyi, Rusheri, Gikunguza) peuvent être mal représentés : "un débit
        inhabituel" signifie seulement "plus haut que d'habitude pour la
        saison", JAMAIS "débordement". Association point GloFAS <-> zone à
        vérifier cellule par cellule (Jours 1–2 du projet).
        """
        params = {
            "latitude": lat,
            "longitude": long,
            "daily": "river_discharge",
            "forecast_days": 7,
            "timezone": "auto"
        }
        responses = self.openmeteo.weather_api(self.flood_url, params = params)
        response = responses[0]
        daily = response.Daily()
        daily_river_discharge = daily.Variables(0).ValuesAsNumpy().tolist()

        daily_data = {
            "date": pd.date_range(
                start = pd.to_datetime(daily.Time(), unit = "s", utc = True),
                end =  pd.to_datetime(daily.TimeEnd(), unit = "s", utc = True),
                freq = pd.Timedelta(seconds = daily.Interval()),
                inclusive = "left"
            )
        }

        daily_data["date"] = daily_data["date"].tolist()
        daily_data["river_discharge"] = daily_river_discharge

        return {
            "daily_river_data": daily_data
        }

    async def get_flood_past_data(
            self, lat, long
    ) -> dict :
        """
        Débit GloFAS "référence" au point (lat, long) : p75 quotidien de la
        réanalyse GloFAS sur les 31 derniers jours (+1 jour de prévision pour
        recoller à aujourd'hui).

        Choix MVP (validé avec l'équipe) : on garde le p75 des 31 derniers
        jours comme référence plutôt que le 90e centile historique du même
        mois. La règle de conseil devient donc : "débit prévu plus haut que le
        p75 des 31 derniers jours" -> message "débit inhabituel pour la
        saison", avec la limitation GloFAS. À recalibrer plus tard
        (cf. app/config.py).
        """
        params = {
            "latitude": lat,
            "longitude": long,
            "daily": "river_discharge_p75",
            "past_days": 31,
            "forecast_days": 1,
        }
        responses = self.openmeteo.weather_api(self.flood_url, params = params)

        # Process first location. Add a for-loop for multiple locations or weather models
        response = responses[0]

        daily = response.Daily()
        daily_river_discharge = daily.Variables(0).ValuesAsNumpy().tolist()

        daily_data = {
            "date": pd.date_range(
                start = pd.to_datetime(daily.Time(), unit = "s", utc = True),
                end =  pd.to_datetime(daily.TimeEnd(), unit = "s", utc = True),
                freq = pd.Timedelta(seconds = daily.Interval()),
                inclusive = "left"
            )
        }

        daily_data["date"] = daily_data["date"].tolist()
        daily_data["river_discharge"] = daily_river_discharge

        return {
            "daily_river_past_data_p75": daily_data
        }