from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
import os
import jwt
from jwt.exceptions import InvalidTokenError
from datetime import datetime, timedelta, timezone
from app.schemas.user import UserInDB
from typing import Annotated
from app.schemas.security import Token, TokenData
from app.core.db import db

# Load environment variables from the .env file
load_dotenv()



oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

fake_users_db = {
    "johndoe@example.com": {
        "username": "johndoe@example.com",
        "name": "John Doe",
        "hashed_password": "$argon2id$v=19$m=65536,t=3,p=4$SD/Y0VojbYzgQOFpkuoOwg$5ZvE2F8bvlXvm4fC7QfwBSkKzA5TXBbR1X1ktwP45Yc"
    }
}



class Security:
    def __init__(self):
        self.SECRET_KEY=os.getenv("SECRET_KEY")
        self.ALGORITHM=os.getenv("ALGORITHM")
        self.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        self.password_hash= PasswordHash.recommended()
        self.db = db

    def verify_password(self,plain_password, hashed_password):
        return self.password_hash.verify(plain_password, hashed_password)

    def get_password_hash(self,password):
        return self.password_hash.hash(password)
    
    def create_access_token(self,data: dict, expires_delta: timedelta | None = None):
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, self.SECRET_KEY, algorithm=self.ALGORITHM)
        return encoded_jwt

    def get_user(self, username: str):
        collection = self.db.get_collection("users")
        user = collection.find_one({"username": username})
        if user:
            return UserInDB(**user)

    def authenticate_user(self, username: str, password: str):
        DUMMY_HASH= self.password_hash.hash("dummypassword")
        user = self.get_user(username)
        print("User in the database: ", user)
        if not user:
            self.verify_password(password, DUMMY_HASH)
            return False
        if not self.verify_password(password, user.hashed_password):
            return False
        return user

    async def get_current_user(self,token: Annotated[str, Depends(oauth2_scheme)]):
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
        try:
            payload = jwt.decode(token, self.SECRET_KEY, algorithms=[self.ALGORITHM])
            print("Payload: ", payload)
            username = payload.get("sub")
            if username is None:
                raise credentials_exception
            token_data = TokenData(username=username)
        except InvalidTokenError:
            raise credentials_exception
        user = self.get_user( username=token_data.username)
        if user is None:
            raise credentials_exception
        return user


security = Security()