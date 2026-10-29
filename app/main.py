from fastapi import FastAPI
from app.routes import weather, token, user

app = FastAPI()

app.include_router(weather.router)
app.include_router(token.router)
app.include_router(user.router)

@app.get("/")
async def root():
    return {"message": "Welcome to our app!"}