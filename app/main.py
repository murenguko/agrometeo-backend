from fastapi import FastAPI
from app.routes import weather, token, user
from fastapi.middleware.cors import CORSMiddleware 

app = FastAPI()

app.include_router(weather.router)
app.include_router(token.router)
app.include_router(user.router)

origins = [
    "http://localhost.tiangolo.com",
    "https://localhost.tiangolo.com",
    "http://localhost",
    "http://localhost:8080",
    "http://localhost:5176"
    "https://agrometeo-backend.vercel.app"
]


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/")
async def root():
    return {"message": "Welcome to our app!"}