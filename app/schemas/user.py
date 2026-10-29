from pydantic import BaseModel

class User(BaseModel):
    username: str
    name: str 
    

class NewUser(User):
    password: str

    class Config:
        schema_extra = {
            "example": {
                "username": "johndoe@example.com",
                "name": "John Doe",
                "password": "yourpassword"
            }
        }

class UserInDB(User):
    hashed_password: str

