# -*- coding: utf-8 -*-
"""
FastAPI backend - PJN Scraper Dashboard
"""

import os
import sys
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

sys.path.insert(0, os.path.dirname(__file__))
import database as db

app = FastAPI(title="PJN - Caja de Salta", docs_url=None, redoc_url=None)

_TEMPLATE = os.path.join(os.path.dirname(__file__), "templates", "index.html")


@app.on_event("startup")
def startup():
    db.inicializar_db()


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(_TEMPLATE, encoding="utf-8") as f:
        return f.read()


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
