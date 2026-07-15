# -*- coding: utf-8 -*-
"""
Capa de persistencia PostgreSQL para PJN Scraper.
Tablas propias con prefijo pjn_ para no interferir con tablas existentes.
"""

import os
import psycopg2
import psycopg2.extras


# ---------------------------------------------------------------------------
# URL de conexion
# ---------------------------------------------------------------------------
def _get_db_url() -> str:
    # 1. Variable de entorno (Docker/VPS)
    url = os.environ.get("DATABASE_URL") or os.environ.get("database")
    if url:
        return url
    # 2. Archivo .env local
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("database="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError(
        "No se encontro la URL de la base de datos. "
        "Define DATABASE_URL en el entorno o 'database=' en .env"
    )


def _connect():
    return psycopg2.connect(_get_db_url(), connect_timeout=15)


# ---------------------------------------------------------------------------
# DDL — solo CREATE IF NOT EXISTS, nunca DROP
# ---------------------------------------------------------------------------
def inicializar_db():
    """Crea las tablas pjn_* si no existen. No toca tablas de otras apps."""
    con = _connect()
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pjn_expedientes (
            id                SERIAL PRIMARY KEY,
            numero            TEXT NOT NULL,
            anio              TEXT NOT NULL,
            caratula          TEXT,
            caja_se_presenta  TEXT,
            fecha_analisis    TIMESTAMP,
            jurisdiccion      TEXT,
            juzgado           TEXT,
            secretaria        TEXT,
            UNIQUE(numero, anio)
        )
    """)

    # Migración: agrega columnas nuevas si la tabla ya existía sin ellas
    for col in ("jurisdiccion", "juzgado", "secretaria"):
        cur.execute(
            f"ALTER TABLE pjn_expedientes ADD COLUMN IF NOT EXISTS {col} TEXT"
        )
    cur.execute(
        "ALTER TABLE pjn_expedientes ADD COLUMN IF NOT EXISTS fuente TEXT DEFAULT 'Extractor PJN'"
    )

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pjn_participantes (
            id              SERIAL PRIMARY KEY,
            expediente_id   INTEGER NOT NULL
                            REFERENCES pjn_expedientes(id) ON DELETE CASCADE,
            tipo            TEXT,
            nombre          TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pjn_abogados (
            id               SERIAL PRIMARY KEY,
            participante_id  INTEGER NOT NULL
                             REFERENCES pjn_participantes(id) ON DELETE CASCADE,
            nombre           TEXT,
            tomo_folio       TEXT,
            cuit             TEXT
        )
    """)

    con.commit()
    cur.close()
    con.close()


# ---------------------------------------------------------------------------
# Consultas de lectura
# ---------------------------------------------------------------------------
def ya_fue_procesado(numero: str, anio: str) -> bool:
    """True si el expediente ya existe en la BD (con cualquier resultado)."""
    con = _connect()
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM pjn_expedientes WHERE numero=%s AND anio=%s",
        (str(numero), str(anio))
    )
    row = cur.fetchone()
    cur.close()
    con.close()
    return row is not None


def obtener_procesados() -> set:
    """Retorna set de (numero, anio) de todos los expedientes ya en la BD."""
    con = _connect()
    cur = con.cursor()
    cur.execute("SELECT numero, anio FROM pjn_expedientes")
    rows = cur.fetchall()
    cur.close()
    con.close()
    return {(r[0], r[1]) for r in rows}


def obtener_paginados(pagina: int, por_pagina: int, filtro: str = "") -> tuple:
    """
    Retorna (expedientes, total) para la página dada.
    expedientes: lista de dicts con participantes y abogados anidados.
    total: cantidad total de expedientes que coinciden con el filtro.
    """
    con = _connect()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    offset = (pagina - 1) * por_pagina

    if filtro:
        like = f"%{filtro}%"
        where = """
            WHERE e.id IN (
                SELECT DISTINCT e2.id FROM pjn_expedientes e2
                LEFT JOIN pjn_participantes p2 ON p2.expediente_id = e2.id
                LEFT JOIN pjn_abogados a2     ON a2.participante_id = p2.id
                WHERE e2.numero   ILIKE %s
                   OR e2.anio     ILIKE %s
                   OR e2.caratula ILIKE %s
                   OR p2.nombre   ILIKE %s
                   OR a2.nombre   ILIKE %s
            )
        """
        params_count = (like, like, like, like, like)
        params_page  = (like, like, like, like, like, por_pagina, offset)
    else:
        where = ""
        params_count = ()
        params_page  = (por_pagina, offset)

    cur.execute(f"SELECT COUNT(*) FROM pjn_expedientes e {where}", params_count)
    total = cur.fetchone()["count"]

    cur.execute(
        f"SELECT e.* FROM pjn_expedientes e {where} ORDER BY e.id LIMIT %s OFFSET %s",
        params_page,
    )
    expedientes = cur.fetchall()

    if not expedientes:
        cur.close()
        con.close()
        return [], total

    # Una sola query para todos los participantes de la página
    exp_ids = [exp["id"] for exp in expedientes]
    cur.execute(
        "SELECT * FROM pjn_participantes WHERE expediente_id = ANY(%s) ORDER BY id",
        (exp_ids,)
    )
    participantes = cur.fetchall()

    # Una sola query para todos los abogados de esos participantes
    part_ids = [p["id"] for p in participantes]
    abogados_por_part = {}
    if part_ids:
        cur.execute(
            "SELECT * FROM pjn_abogados WHERE participante_id = ANY(%s) ORDER BY id",
            (part_ids,)
        )
        for ab in cur.fetchall():
            abogados_por_part.setdefault(ab["participante_id"], []).append(dict(ab))

    # Agrupar participantes por expediente
    parts_por_exp = {}
    for p in participantes:
        p_dict = dict(p)
        p_dict["abogados"] = abogados_por_part.get(p["id"], [])
        parts_por_exp.setdefault(p["expediente_id"], []).append(p_dict)

    result = []
    for exp in expedientes:
        exp_dict = dict(exp)
        if exp_dict.get("fecha_analisis"):
            exp_dict["fecha_analisis"] = str(exp_dict["fecha_analisis"])
        exp_dict["participantes"] = parts_por_exp.get(exp["id"], [])
        result.append(exp_dict)

    cur.close()
    con.close()
    return result, total


def obtener_todos() -> list:
    """Devuelve todos los expedientes con participantes y abogados anidados."""
    expedientes, _ = obtener_paginados(pagina=1, por_pagina=999999)
    return expedientes


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------
def eliminar_expediente(id: int):
    """Elimina un expediente y sus participantes/abogados en cascada."""
    con = _connect()
    cur = con.cursor()
    cur.execute("DELETE FROM pjn_expedientes WHERE id=%s", (id,))
    con.commit()
    cur.close()
    con.close()


def guardar_expediente(numero: str, anio: str, caratula: str,
                       caja_se_presenta: str, participantes: list = None,
                       jurisdiccion: str = "", juzgado: str = "", secretaria: str = "",
                       fuente: str = "Extractor PJN"):
    """
    Inserta o actualiza un expediente y reemplaza sus participantes/abogados.
    participantes: [{tipo, nombre, abogados:[{nombre, tomo_folio, cuit}]}]
    """
    if participantes is None:
        participantes = []

    con = _connect()
    cur = con.cursor()

    cur.execute("""
        INSERT INTO pjn_expedientes
            (numero, anio, caratula, caja_se_presenta, fecha_analisis, jurisdiccion, juzgado, secretaria, fuente)
        VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s, %s)
        ON CONFLICT(numero, anio) DO UPDATE SET
            caratula         = EXCLUDED.caratula,
            caja_se_presenta = EXCLUDED.caja_se_presenta,
            fecha_analisis   = NOW(),
            jurisdiccion     = EXCLUDED.jurisdiccion,
            juzgado          = EXCLUDED.juzgado,
            secretaria       = EXCLUDED.secretaria,
            fuente           = EXCLUDED.fuente
    """, (str(numero), str(anio), caratula, caja_se_presenta,
          jurisdiccion or "", juzgado or "", secretaria or "", fuente or "Extractor PJN"))

    cur.execute(
        "SELECT id FROM pjn_expedientes WHERE numero=%s AND anio=%s",
        (str(numero), str(anio))
    )
    expediente_id = cur.fetchone()[0]

    # Solo reemplazar participantes si extrajimos datos nuevos.
    # Si la lista llega vacía (error de inicio de Chrome, login fallido, etc.)
    # conservamos los que ya estaban guardados de un intento anterior.
    if not participantes:
        con.commit()
        cur.close()
        con.close()
        return

    cur.execute("DELETE FROM pjn_participantes WHERE expediente_id=%s", (expediente_id,))

    for p in participantes:
        cur.execute(
            "INSERT INTO pjn_participantes (expediente_id, tipo, nombre) VALUES (%s,%s,%s) RETURNING id",
            (expediente_id, p.get("tipo", ""), p.get("nombre", ""))
        )
        participante_id = cur.fetchone()[0]
        for ab in p.get("abogados", []):
            cur.execute(
                "INSERT INTO pjn_abogados (participante_id, nombre, tomo_folio, cuit) "
                "VALUES (%s,%s,%s,%s)",
                (participante_id, ab.get("nombre", ""), ab.get("tomo_folio", ""), ab.get("cuit", ""))
            )

    con.commit()
    cur.close()
    con.close()
