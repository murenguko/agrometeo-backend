from fastapi import APIRouter
from app.schemas.user import UserInDB, User, NewUser
from app.core.security import security
from app.core.db import db


router = APIRouter()

@router.post("/user")
def create_user(
    user: NewUser
) -> User :
    password_hash= security.get_password_hash(user.password)
    user_data = user.model_dump()
    
    user_data.pop("password", None)
    new_user= UserInDB(**user_data, hashed_password=password_hash)
    user_record = new_user.model_dump()
    
    db.get_collection("users").insert_one(user_record)

    return User(**user_record)

    