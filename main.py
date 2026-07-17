# -*- coding: utf-8 -*-
"""
FastAPI backend - PJN Scraper Dashboard
"""

import io
import os
import sys
import threading

import pandas as pd
from fastapi import FastAPI, Query, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, Response
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
    return db.obtener_stats()


@app.get("/api/expedientes")
async def expedientes(
    pagina:       int = Query(default=1,  ge=1),
    por_pagina:   int = Query(default=50, ge=1, le=200),
    resultado:    str = Query(default=""),
    juzgado:      str = Query(default=""),
    secretaria:   str = Query(default=""),
    busqueda:     str = Query(default=""),
    actores:      str = Query(default=""),
    demandados:   str = Query(default=""),
    terceros:     str = Query(default=""),
    con_demanda:  str = Query(default=""),
    fecha_desde:  str = Query(default=""),
    fecha_hasta:  str = Query(default=""),
):
    def split(s): return [x.strip() for x in s.split(',') if x.strip()] if s else []
    items, total = db.obtener_paginados(
        pagina, por_pagina,
        filtro=busqueda, resultado=resultado,
        juzgado=juzgado, secretaria=secretaria,
        actores=split(actores), demandados=split(demandados), terceros=split(terceros),
        con_demanda=bool(con_demanda), fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
    )
    return {"items": items, "total": total}


@app.get("/api/participantes")
async def participantes():
    return db.obtener_participantes_por_tipo()


@app.get("/api/abogados")
async def abogados(
    filtro_ab:         str = Query(default=""),
    filtro_representa: str = Query(default=""),
):
    return db.obtener_abogados_stats(filtro_ab, filtro_representa)


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
        nombre_excel = _estado.get("nombre_excel") or "expedientes_web.xlsx"

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
                fuente=nombre_excel,
            )
        finally:
            sys.stdout = old_stdout
            with _estado_lock:
                _estado["corriendo"] = False

    threading.Thread(target=run, daemon=True).start()
    return {"ok": True}


@app.delete("/api/expedientes/{exp_id}")
async def eliminar_expediente(exp_id: int):
    db.eliminar_expediente(exp_id)
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


@app.get("/api/exportar")
async def exportar(
    resultado: str = Query(default="todos"),
    juzgado: str = Query(default=""),
    secretaria: str = Query(default=""),
    busqueda: str = Query(default=""),
    filtro_ab: str = Query(default=""),
    filtro_representa: str = Query(default=""),
    actores: str = Query(default=""),
    demandados: str = Query(default=""),
    terceros: str = Query(default=""),
    con_demanda: str = Query(default=""),
    fecha_desde: str = Query(default=""),
    fecha_hasta: str = Query(default=""),
):
    def split(s): return [x.strip() for x in s.split(',') if x.strip()] if s else []
    actores_list = split(actores)
    demandados_list = split(demandados)
    terceros_list = split(terceros)

    expedientes = db.obtener_todos()

    # ── Filtros de expedientes ──────────────────────────────────────────────
    if resultado in ("Si", "No"):
        expedientes = [e for e in expedientes if e.get("caja_se_presenta") == resultado]
    elif resultado == "error":
        expedientes = [e for e in expedientes if e.get("caja_se_presenta") not in ("Si", "No")]

    if juzgado:
        expedientes = [e for e in expedientes if e.get("juzgado") == juzgado]

    if secretaria:
        expedientes = [e for e in expedientes if e.get("secretaria") == secretaria]

    if busqueda:
        q = busqueda.lower()
        def _matches(e):
            hay = f"{e.get('numero','')} {e.get('anio','')} {e.get('caratula','')} {e.get('juzgado','')} {e.get('secretaria','')}".lower()
            for p in e.get("participantes", []):
                hay += " " + (p.get("nombre") or "").lower()
                for ab in p.get("abogados", []):
                    hay += " " + (ab.get("nombre") or "").lower()
            return q in hay
        expedientes = [e for e in expedientes if _matches(e)]

    if actores_list or demandados_list or terceros_list:
        def _tiene_parte(exp, tipo_key, nombres):
            if not nombres:
                return True
            nombres_lower = {n.lower() for n in nombres}
            for p in exp.get("participantes", []):
                if tipo_key in (p.get("tipo") or "").lower():
                    if (p.get("nombre") or "").lower() in nombres_lower:
                        return True
            return False
        expedientes = [
            e for e in expedientes
            if _tiene_parte(e, 'actor',    actores_list)
            and _tiene_parte(e, 'demandado', demandados_list)
            and _tiene_parte(e, 'tercero',   terceros_list)
        ]

    if con_demanda:
        expedientes = [
            e for e in expedientes
            if e.get("url_demanda") and e.get("url_demanda") not in ("NINGUNA", "")
        ]

    if fecha_desde or fecha_hasta:
        from datetime import date as _date
        def _parse_fecha(f):
            if not f:
                return None
            try:
                d, m, y = f.split('/')
                return _date(int(y), int(m), int(d))
            except Exception:
                return None
        desde = _date.fromisoformat(fecha_desde) if fecha_desde else None
        hasta = _date.fromisoformat(fecha_hasta) if fecha_hasta else None
        def _en_rango(e):
            fd = _parse_fecha(e.get("fecha_inicio"))
            if fd is None:
                return False
            if desde and fd < desde:
                return False
            if hasta and fd > hasta:
                return False
            return True
        expedientes = [e for e in expedientes if _en_rango(e)]

    # Hoja 1: Expedientes
    exptes_rows = []
    for e in expedientes:
        partes = e.get("participantes", [])
        actor     = next((p["nombre"] for p in partes if "actor"     in (p.get("tipo") or "").lower()), "")
        demandado = next((p["nombre"] for p in partes if "demandado" in (p.get("tipo") or "").lower()), "")
        exptes_rows.append({
            "Expediente":    f"{e['numero']}/{e['anio']}",
            "Carátula":      e.get("caratula", ""),
            "Resultado":     e.get("caja_se_presenta", ""),
            "Jurisdicción":  e.get("jurisdiccion", ""),
            "Juzgado":       e.get("juzgado", ""),
            "Secretaría":    e.get("secretaria", ""),
            "Actor":         actor,
            "Demandado":     demandado,
            "Fecha Análisis": (e.get("fecha_analisis") or "")[:10],
            "Fuente":        e.get("fuente", "Extractor PJN"),
        })

    # Hoja 2: Abogados únicos de los expedientes filtrados
    abogados_map: dict = {}
    for e in expedientes:
        res = e.get("caja_se_presenta")
        for parte in e.get("participantes", []):
            parte_nombre = parte.get("nombre") or ""
            for ab in parte.get("abogados", []):
                nombre = ab.get("nombre") or "Sin nombre"
                if nombre not in abogados_map:
                    abogados_map[nombre] = {
                        "Abogado":           nombre,
                        "Tomo/Folio":        ab.get("tomo_folio", ""),
                        "CUIT":              ab.get("cuit", ""),
                        "Total Expedientes": 0,
                        "Se Presenta":       0,
                        "No Presenta":       0,
                        "Error/Pendiente":   0,
                        "_ids":              set(),
                        "_partes":           set(),
                    }
                entry = abogados_map[nombre]
                entry["_partes"].add(parte_nombre.lower())
                if e["id"] not in entry["_ids"]:
                    entry["_ids"].add(e["id"])
                    entry["Total Expedientes"] += 1
                    if res == "Si":       entry["Se Presenta"] += 1
                    elif res == "No":     entry["No Presenta"] += 1
                    else:                 entry["Error/Pendiente"] += 1

    abogados_rows = sorted(
        [{k: v for k, v in entry.items() if k not in ("_ids", "_partes")}
         for entry in abogados_map.values()],
        key=lambda x: -x["Total Expedientes"],
    )

    # ── Filtros de abogados (aplican solo sobre la hoja Abogados) ──────────
    if filtro_ab:
        q = filtro_ab.lower()
        abogados_rows = [
            r for r in abogados_rows
            if q in r["Abogado"].lower() or q in (r.get("CUIT") or "").lower()
        ]

    if filtro_representa:
        q = filtro_representa.lower()
        abogados_rows = [
            r for r in abogados_rows
            if any(q in pn for pn in abogados_map[r["Abogado"]]["_partes"])
        ]

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame(exptes_rows).to_excel(writer, index=False, sheet_name="Expedientes")
        pd.DataFrame(abogados_rows).to_excel(writer, index=False, sheet_name="Abogados")
    buf.seek(0)

    return Response(
        content=buf.read(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="exportacion.xlsx"'},
    )


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
