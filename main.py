# -*- coding: utf-8 -*-
"""
FastAPI backend - PJN Scraper Dashboard
"""

import os
import sys
import threading

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))
import database as db
import pjn_scraper as scraper

app = FastAPI(title="PJN - Caja de Salta", docs_url=None, redoc_url=None)

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "index.html")
_EXCEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "expedientes_web.xlsx")

# ---------------------------------------------------------------------------
# Estado global del scraper (compartido con el hilo background)
# ---------------------------------------------------------------------------
_estado = {
    "corriendo": False,
    "actual": 0,
    "total": 0,
    "logs": [],
    "excel_subido": os.path.exists(_EXCEL_PATH),
    "nombre_excel": os.path.basename(_EXCEL_PATH) if os.path.exists(_EXCEL_PATH) else None,
}
_estado_lock = threading.Lock()


class _TeeStream:
    """Escribe en stdout original Y captura en la lista de logs."""
    def __init__(self, original):
        self._orig = original

    def write(self, s):
        self._orig.write(s)
        if s and s.strip():
            with _estado_lock:
                _estado["logs"].append(s.rstrip("\n"))
                if len(_estado["logs"]) > 150:
                    del _estado["logs"][:50]

    def flush(self):
        self._orig.flush()

    def __getattr__(self, name):
        return getattr(self._orig, name)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
def startup():
    db.inicializar_db()


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index():
    with open(_TEMPLATE, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# API datos
# ---------------------------------------------------------------------------
@app.get("/api/stats")
async def stats():
    expedientes = db.obtener_todos()
    total = len(expedientes)
    si    = sum(1 for e in expedientes if e.get("caja_se_presenta") == "Si")
    no    = sum(1 for e in expedientes if e.get("caja_se_presenta") == "No")
    return {
        "total":   total,
        "si":      si,
        "no":      no,
        "error":   total - si - no,
        "pct_si":  round(si  * 100 / total) if total else 0,
        "pct_no":  round(no  * 100 / total) if total else 0,
    }


@app.get("/api/expedientes")
async def expedientes():
    return db.obtener_todos()


# ---------------------------------------------------------------------------
# API scraper
# ---------------------------------------------------------------------------
@app.post("/api/subir-excel")
async def subir_excel(archivo: UploadFile = File(...)):
    contenido = await archivo.read()
    with open(_EXCEL_PATH, "wb") as f:
        f.write(contenido)
    with _estado_lock:
        _estado["excel_subido"] = True
        _estado["nombre_excel"] = archivo.filename
    return {"ok": True, "nombre": archivo.filename}


class _EjecutarBody(BaseModel):
    usuario: str
    password: str


@app.post("/api/ejecutar")
async def ejecutar(body: _EjecutarBody):
    with _estado_lock:
        if _estado["corriendo"]:
            raise HTTPException(409, "El scraper ya está en ejecución")
        if not _estado["excel_subido"] or not os.path.exists(_EXCEL_PATH):
            raise HTTPException(400, "Primero subí el archivo Excel")
        if not body.usuario or not body.password:
            raise HTTPException(400, "Usuario y contraseña son obligatorios")

        usuario  = body.usuario
        password = body.password

        _estado["corriendo"] = True
        _estado["actual"]    = 0
        _estado["total"]     = 0
        _estado["logs"]      = []
        scraper._stop_requested = False

    def on_progreso(actual, total):
        with _estado_lock:
            _estado["actual"] = actual
            _estado["total"]  = total

    def run():
        old_stdout = sys.stdout
        sys.stdout = _TeeStream(old_stdout)
        try:
            scraper.ejecutar_desde_excel(
                archivo_entrada=_EXCEL_PATH,
                usuario=usuario,
                password=password,
                headless=True,
                on_progreso=on_progreso,
            )
        finally:
            sys.stdout = old_stdout
            with _estado_lock:
                _estado["corriendo"] = False

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.post("/api/detener")
async def detener():
    scraper._stop_requested = True
    return {"ok": True}


@app.get("/api/estado-scraper")
async def estado_scraper():
    with _estado_lock:
        r = dict(_estado)
        r["logs"] = list(_estado["logs"])
    r["usuario_default"] = os.environ.get("PJN_USUARIO", "")
    r["password_default"] = os.environ.get("PJN_PASSWORD", "")
    return r


@app.get("/api/chrome-log")
async def chrome_log():
    """Devuelve el log verbose de ChromeDriver para diagnosticar crashes."""
    import subprocess
    log_path = "/tmp/chromedriver.log"
    resultado = {}
    if os.path.exists(log_path):
        with open(log_path, errors="replace") as f:
            resultado["chromedriver_log"] = f.read()[-8000:]
    else:
        resultado["chromedriver_log"] = "Archivo no encontrado — ejecutá el scraper primero"
    try:
        v = subprocess.run(["chromium", "--version"], capture_output=True, text=True, timeout=5)
        resultado["chromium_version"] = v.stdout.strip() or v.stderr.strip()
    except Exception as e:
        resultado["chromium_version"] = f"Error: {e}"
    return resultado


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
