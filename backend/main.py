import os
import secrets
import smtplib
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pyproj
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from shapely.geometry import shape
from shapely.ops import transform

app = FastAPI(title="Interference Checker API", version="3.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Credenziali ──────────────────────────────────────────────
VALID_USERNAME = "admin"
VALID_PASSWORD = "DFGIS"
security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, VALID_USERNAME)
    ok_pass = secrets.compare_digest(credentials.password, VALID_PASSWORD)
    if not (ok_user and ok_pass):
        raise HTTPException(
            status_code=401,
            detail="Credenziali non valide",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# ─── Email ────────────────────────────────────────────────────
GMAIL_USER     = "dario98frn@gmail.com"
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
NOTIFY_TO      = "dario98frn@gmail.com"
CONTACT_EMAIL  = "dario98frn@gmail.com"
CONTACT_PHONE  = "+39 389 389 3893"
COMPANY_NAME   = "DarioGIS"

# ─── Layer ───────────────────────────────────────────────────
LAYERS_DIR = Path(__file__).parent / "layers"

LAYER_CONFIG = {
    "gasdotti": {
        "file": "gasdotti.geojson",
        "label": "Gasdotto",
        "icon": "⚠️",
        "use_feature_buffer": True,
        "default_buffer_m": 0,
    },
}

_LAYERS: dict = {}

def load_layers():
    for key, cfg in LAYER_CONFIG.items():
        path = LAYERS_DIR / cfg["file"]
        if path.exists():
            gdf = gpd.read_file(path)
            gdf = gdf.set_crs("EPSG:4326", allow_override=True)
            _LAYERS[key] = gdf
            print(f"Layer caricato: {key} ({len(gdf)} feature)")
            print(f"Colonne: {list(gdf.columns)}")

load_layers()

# ─── Proiezione ───────────────────────────────────────────────
def to_metric(geom):
    project = pyproj.Transformer.from_crs(
        "EPSG:4326", "EPSG:32632", always_xy=True
    ).transform
    return transform(project, geom)

# ─── Leggi campo ─────────────────────────────────────────────
def gf(feature, name):
    try:
        v = feature[name]
        if v is None or str(v) == "nan":
            return "N/D"
        return str(v)
    except Exception:
        return "N/D"

# ─── Email ────────────────────────────────────────────────────
def send_email(nome, azienda, esito, interferenze, now, pdf_b64=None):
    try:
        msg = MIMEMultipart()
        msg["From"]    = GMAIL_USER
        msg["To"]      = NOTIFY_TO
        msg["Subject"] = f"[Verifica Interferenze] {esito.upper()} — {nome} ({now})"

        righe = ""
        if interferenze:
            for i, inf in enumerate(interferenze, 1):
                righe += (
                    f"\n  {i}. {inf.get('layer','')} — {inf.get('tipo_interferenza','')}"
                    f"\n     ID: {inf.get('id','')} | Comune: {inf.get('area_code','')}"
                    f"\n     Specie: {inf.get('specie_rete','')} | Materiale: {inf.get('materiale','')} | Diametro: {inf.get('diametro','')}"
                    f"\n     Distanza minima: {inf.get('distanza_minima_m','')} m\n"
                )
        else:
            righe = "\n  Nessuna interferenza rilevata.\n"

        corpo = (
            "Nuova verifica interferenze ricevuta.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "RICHIEDENTE\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Nome:    {nome}\n"
            f"Azienda: {azienda}\n"
            f"Data:    {now}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"ESITO: {esito.upper()}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{righe}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Messaggio generato automaticamente."
        )
        msg.attach(MIMEText(corpo, "plain"))

        if pdf_b64:
            pdf_bytes = base64.b64decode(pdf_b64)
            part = MIMEBase("application", "octet-stream")
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            fname = f"verifica_{nome.replace(' ','_')}.pdf"
            part.add_header("Content-Disposition", f"attachment; filename={fname}")
            msg.attach(part)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.sendmail(GMAIL_USER, NOTIFY_TO, msg.as_string())
        print(f"Email inviata per {nome} — {esito}")
    except Exception as e:
        print(f"Errore invio email: {e}")

# ─── Schemi ──────────────────────────────────────────────────
class CheckRequest(BaseModel):
    geojson: dict
    nome:    str = ""
    azienda: str = ""

class ReportRequest(BaseModel):
    nome:         str
    azienda:      str
    esito:        str
    interferenze: list
    now:          str
    pdf_b64:      Optional[str] = None

# ─── Endpoints ───────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "layers_loaded": list(_LAYERS.keys())}

@app.get("/debug-fields")
def debug_fields(username: str = Depends(verify_credentials)):
    gdf = _LAYERS.get("gasdotti")
    if gdf is None:
        return {"error": "layer non caricato"}
    row = gdf.iloc[0]
    return {
        "colonne": list(gdf.columns),
        "valori_prima_riga": {col: str(row[col]) for col in gdf.columns}
    }

@app.get("/debug-email")
def debug_email(username: str = Depends(verify_credentials)):
    app_pass = os.environ.get("GMAIL_APP_PASS", "NON TROVATA")
    send_email("Test", "Test Azienda", "test", [], "test", None)
    return {
        "gmail_pass_trovata": app_pass != "NON TROVATA",
        "gmail_pass_lunghezza": len(app_pass)
    }

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
            feat_geom_metric = to_metric(feature.geometry)
            buffer_m = cfg.get("default_buffer_m", 0)
            if cfg["use_feature_buffer"] and "buffer_m" in feature.index:
                try:
                    buffer_m = float(feature["buffer_m"])
                except Exception:
                    buffer_m = 0

            feat_buffered = feat_geom_metric.buffer(buffer_m) if buffer_m > 0 else feat_geom_metric

            if geom_metric.intersects(feat_buffered):
                dist = geom_metric.distance(feat_geom_metric)
                itype = (
                    "intersezione diretta"
                    if geom_metric.intersects(feat_geom_metric)
                    else f"entro fascia di rispetto ({buffer_m}m)"
                )
                results.append({
                    "layer":             cfg["label"],
                    "icon":              cfg["icon"],
                    "tipo_interferenza": itype,
                    "distanza_minima_m": round(dist, 2),
                    "buffer_applicato_m": buffer_m,
                    "specie_rete":       gf(feature, "type"),
                    "id":                gf(feature, "id"),
                    "area_code":         gf(feature, "area_code"),
                    "lunghezza":         gf(feature, "length"),
                    "diametro":          gf(feature, "nominal_di"),
                    "materiale":         gf(feature, "material"),
                })

    return {
        "status":             "interferente" if results else "non interferente",
        "interferenze_count": len(results),
        "interferenze":       results,
        "contatti": {
            "azienda":  COMPANY_NAME,
            "email":    CONTACT_EMAIL,
            "telefono": CONTACT_PHONE,
        } if results else None,
    }

@app.post("/send-report")
def send_report(req: ReportRequest, username: str = Depends(verify_credentials)):
    send_email(
        nome=req.nome,
        azienda=req.azienda,
        esito=req.esito,
        interferenze=req.interferenze,
        now=req.now,
        pdf_b64=req.pdf_b64,
    )
    return {"status": "ok"}
