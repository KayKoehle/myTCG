from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api.endpoints import register_ws_routes

app = FastAPI()

# LAN play means one instance (the host) is reached cross-origin by other
# players' browsers on the network, so allow any origin. Nothing here is
# authenticated or sensitive — it's a peer-to-peer game on a trusted LAN.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CARD_SVG_DIR = REPO_ROOT / "output_svgs"
if CARD_SVG_DIR.exists():
	app.mount("/assets/cards", StaticFiles(directory=str(CARD_SVG_DIR)), name="card_svgs")

# Card art is generated into per-type subfolders (images/color/creatures,
# .../artefacts, .../spells, ...), but the frontend requests a flat
# /assets/card_png/<name>.png with no type info, so look across all of them.
CARD_PNG_ROOT = REPO_ROOT / "images" / "color"


@app.get("/assets/card_png/{filename}")
async def get_card_png(filename: str) -> FileResponse:
	if not CARD_PNG_ROOT.exists() or "/" in filename or "\\" in filename:
		raise HTTPException(status_code=404)
	for subdir in CARD_PNG_ROOT.iterdir():
		candidate = subdir / filename
		if subdir.is_dir() and candidate.is_file():
			return FileResponse(candidate)
	raise HTTPException(status_code=404)


# run with uvicorn main:app
register_ws_routes(app)
