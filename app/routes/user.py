from fastapi import APIRouter, HTTPException, status
from pymongo.errors import DuplicateKeyError
from app.schemas.user import UserInDB, User, NewUser
from app.core.security import security
from app.core.db import db


router = APIRouter()

@router.post("/user")
def create_user(
    user: NewUser
) -> User :
    if security.get_user(user.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    password_hash = security.get_password_hash(user.password)
    user_data = user.model_dump()
    
    user_data.pop("password", None)
    new_user = UserInDB(**user_data, hashed_password=password_hash)
    user_record = new_user.model_dump()
    
    try:
        db.get_collection("users").insert_one(user_record)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    return User(**user_record)

    