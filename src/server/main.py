from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.endpoints import register_ws_routes

app = FastAPI()

REPO_ROOT = Path(__file__).resolve().parents[2]
CARD_SVG_DIR = REPO_ROOT / "output_svgs"
if CARD_SVG_DIR.exists():
	app.mount("/assets/cards", StaticFiles(directory=str(CARD_SVG_DIR)), name="card_svgs")
CARD_PNG_DIR = REPO_ROOT / "images" / "color" / "creatures"
if CARD_PNG_DIR.exists():
	app.mount("/assets/card_png", StaticFiles(directory=str(CARD_PNG_DIR)), name="card_png")

# run with uvicorn main:app 
register_ws_routes(app)
