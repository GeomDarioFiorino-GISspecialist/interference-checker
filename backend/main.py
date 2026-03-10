
Copia

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Any
import geopandas as gpd
import secrets
from shapely.geometry import shape
from shapely.ops import transform
import pyproj
from pathlib import Path

app = FastAPI(title="Interference Checker API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)
# ─── Credenziali ─────────────────────────────────────────────
VALID_USERNAME = "admin"
VALID_PASSWORD = "DFGIS"

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_user = secrets.compare_digest(credentials.username, VALID_USERNAME)
    correct_pass = secrets.compare_digest(credentials.password, VALID_PASSWORD)
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=401,
            detail="Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# ─── Contatti per interferenze ───────────────────────────────
CONTACT_EMAIL = "dario98frn@gmail.com"
CONTACT_PHONE = "+39 389 915 7166"
COMPANY_NAME  = "Dario Fiorino"

# ─── Layer protetti ──────────────────────────────────────────
LAYERS_DIR = Path(__file__).parent / "layers"

LAYER_CONFIG = {
    "gasdotti": {
        "file": "gasdotti.geojson",
        "label": "Gasdotto",
        "icon": "⚠️",
        "use_feature_buffer": False,
        "default_buffer_m": 25,
    },
}

_LAYERS: dict[str, gpd.GeoDataFrame] = {}

@app.on_event("startup")
def load_layers():
    for key, cfg in LAYER_CONFIG.items():
        path = LAYERS_DIR / cfg["file"]
        if path.exists():
            gdf = gpd.read_file(path)
            gdf = gdf.set_crs("EPSG:4326", allow_override=True)
            _LAYERS[key] = gdf
            print(f"✅ Layer caricato: {key} ({len(gdf)} feature)")

# ─── Proiezione metrica ───────────────────────────────────────
def to_metric(geom):
    project = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32632", always_xy=True).transform
    return transform(project, geom)

# ─── Schema ──────────────────────────────────────────────────
class CheckRequest(BaseModel):
    geojson: dict[str, Any]

# ─── Endpoint verifica ────────────────────────────────────────
@app.post("/check-interference")
def check_interference(req: CheckRequest, username: str = Depends(verify_credentials)):
    try:
        features = req.geojson.get("features", [])
        if not features:
            raise ValueError("Nessuna feature trovata")
        geom_input = shape(features[0]["geometry"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"GeoJSON non valido: {e}")

    geom_metric = to_metric(geom_input)
    results = []

    for layer_key, cfg in LAYER_CONFIG.items():
        gdf = _LAYERS.get(layer_key)
        if gdf is None:
            continue

        for _, feature in gdf.iterrows():
            feat_geom = feature.geometry
            feat_geom_metric = to_metric(feat_geom)

            buffer_m = cfg.get("default_buffer_m", 0)
            if cfg["use_feature_buffer"] and "buffer_m" in feature:
                buffer_m = float(feature["buffer_m"])

            feat_buffered = feat_geom_metric.buffer(buffer_m) if buffer_m > 0 else feat_geom_metric

            if geom_metric.intersects(feat_buffered):
                dist = geom_metric.distance(feat_geom_metric)
                interference_type = (
                    "intersezione diretta"
                    if geom_metric.intersects(feat_geom_metric)
                    else f"entro fascia di rispetto ({buffer_m}m)"
                )

                results.append({
                    "layer": cfg["label"],
                    "icon": cfg["icon"],
                    "nome": feature.get("nome", "N/A"),
                    "tipo_interferenza": interference_type,
                    "distanza_minima_m": round(dist, 2),
                    "buffer_applicato_m": buffer_m,
                    # Campi aggiuntivi dalla rete
                    "specie_rete": feature.get("type", "N/D"),
                    "materiale": feature.get("material", "N/D"),
                    "diametro": str(feature.get("diameter", "N/D")),
                })

    return {
        "status": "interferente" if results else "non interferente",
        "interferenze_count": len(results),
        "interferenze": results,
        "contatti": {
            "azienda": COMPANY_NAME,
            "email": CONTACT_EMAIL,
            "telefono": CONTACT_PHONE,
        } if results else None,
    }

@app.get("/health")
def health():
    return {"status": "ok", "layers_loaded": list(_LAYERS.keys())}
