from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any
import geopandas as gpd
import json
from shapely.geometry import shape, LineString
from shapely.ops import transform
import pyproj
from functools import partial
from pathlib import Path

app = FastAPI(title="Interference Checker API", version="1.0.0")

# CORS — in produzione limita alle origini autorizzate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Cambia con il tuo dominio in produzione
    allow_methods=["POST"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# Configurazione layer protetti
# ─────────────────────────────────────────────
LAYERS_DIR = Path(__file__).parent / "layers"

LAYER_CONFIG = {
    "gasdotti": {
        "file": "gasdotti.geojson",
        "label": "Gasdotto",
        "icon": "⚠️",
        "use_feature_buffer": True,  # usa il buffer definito in ogni feature
    },
    "fasce_rispetto": {
        "file": "fasce_rispetto.geojson",
        "label": "Fascia di rispetto",
        "icon": "🔴",
        "use_feature_buffer": True,
    },
    "elettrodotti": {
        "file": "elettrodotti.geojson",
        "label": "Elettrodotto",
        "icon": "⚡",
        "use_feature_buffer": True,
    },
}

# Carica i layer all'avvio (mai esposti all'esterno)
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
        else:
            print(f"⚠️  Layer non trovato: {path}")

# ─────────────────────────────────────────────
# Utilità: proiezione per calcolo buffer in metri
# ─────────────────────────────────────────────
def to_metric(geom):
    """Trasforma una geometria da WGS84 a proiezione metrica (UTM32N)."""
    project = pyproj.Transformer.from_crs(
        "EPSG:4326", "EPSG:32632", always_xy=True
    ).transform
    return transform(project, geom)

def to_wgs84(geom):
    """Ritorna da proiezione metrica a WGS84."""
    project = pyproj.Transformer.from_crs(
        "EPSG:32632", "EPSG:4326", always_xy=True
    ).transform
    return transform(project, geom)

# ─────────────────────────────────────────────
# Schema richiesta
# ─────────────────────────────────────────────
class CheckRequest(BaseModel):
    geojson: dict[str, Any]  # GeoJSON della linea disegnata dall'utente

# ─────────────────────────────────────────────
# Endpoint principale
# ─────────────────────────────────────────────
@app.post("/check-interference")
def check_interference(req: CheckRequest):
    # Estrai la geometria dal GeoJSON
    try:
        features = req.geojson.get("features", [])
        if not features:
            raise ValueError("Nessuna feature trovata nel GeoJSON")
        geom_input = shape(features[0]["geometry"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"GeoJSON non valido: {e}")

    # Proietta in metrico per operazioni di buffer
    geom_metric = to_metric(geom_input)

    results = []

    for layer_key, cfg in LAYER_CONFIG.items():
        gdf = _LAYERS.get(layer_key)
        if gdf is None:
            continue

        for _, feature in gdf.iterrows():
            feat_geom = feature.geometry
            feat_geom_metric = to_metric(feat_geom)

            # Determina il buffer da applicare
            buffer_m = 0
            if cfg["use_feature_buffer"] and "buffer_m" in feature:
                buffer_m = float(feature["buffer_m"])

            # Applica buffer alla geometria del layer
            feat_buffered = feat_geom_metric.buffer(buffer_m) if buffer_m > 0 else feat_geom_metric

            # Test di interferenza
            intersects = geom_metric.intersects(feat_buffered)

            if intersects:
                # Calcola la distanza minima reale
                dist = geom_metric.distance(feat_geom_metric)

                interference_type = "intersezione diretta" if geom_metric.intersects(feat_geom_metric) else f"entro fascia di rispetto ({buffer_m}m)"

                results.append({
                    "layer": cfg["label"],
                    "icon": cfg["icon"],
                    "nome": feature.get("nome", "N/A"),
                    "tipo_interferenza": interference_type,
                    "distanza_minima_m": round(dist, 2),
                    "buffer_applicato_m": buffer_m,
                })

    return {
        "status": "interferente" if results else "non interferente",
        "interferenze_count": len(results),
        "interferenze": results,
    }

# ─────────────────────────────────────────────
# Health check
# ─────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "layers_loaded": list(_LAYERS.keys())}
