from fastapi import FastAPI
from api.endpoints import register_ws_routes

app = FastAPI()

# run with uvicorn main:app 
register_ws_routes(app)
