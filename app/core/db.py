from pymongo import MongoClient
from pydantic_settings import BaseSettings
from fastapi import HTTPException
from dotenv import load_dotenv
import os


load_dotenv()

class Client:
    def __init__(self):
        self.__client = MongoClient(str(os.getenv("DB_CONNECTION_STRING")))
        self.__db = self.__client[str(os.getenv("DB"))]

    def get_collection(self, collection):
        return self.__db[collection]

db = Client()