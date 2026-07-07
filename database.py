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
    """True si el expediente ya tiene resultado Si o No en la BD."""
    con = _connect()
    cur = con.cursor()
    cur.execute(
        "SELECT caja_se_presenta FROM pjn_expedientes WHERE numero=%s AND anio=%s",
        (str(numero), str(anio))
    )
    row = cur.fetchone()
    cur.close()
    con.close()
    return bool(row and row[0] in ("Si", "No"))


def obtener_procesados() -> set:
    """Retorna set de (numero, anio) con resultado Si o No."""
    con = _connect()
    cur = con.cursor()
    cur.execute(
        "SELECT numero, anio FROM pjn_expedientes WHERE caja_se_presenta IN ('Si','No')"
    )
    rows = cur.fetchall()
    cur.close()
    con.close()
    return {(r[0], r[1]) for r in rows}


def obtener_todos() -> list:
    """Devuelve todos los expedientes con participantes y abogados anidados."""
    con = _connect()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    expedientes = cur.execute(
        "SELECT * FROM pjn_expedientes ORDER BY id"
    ) or cur.fetchall()
    # fetchall() despues de execute()
    cur.execute("SELECT * FROM pjn_expedientes ORDER BY id")
    expedientes = cur.fetchall()

    result = []
    for exp in expedientes:
        exp_dict = dict(exp)

        cur.execute(
            "SELECT * FROM pjn_participantes WHERE expediente_id=%s ORDER BY id",
            (exp["id"],)
        )
        participantes = cur.fetchall()
        exp_dict["participantes"] = []

        for p in participantes:
            p_dict = dict(p)
            cur.execute(
                "SELECT * FROM pjn_abogados WHERE participante_id=%s ORDER BY id",
                (p["id"],)
            )
            p_dict["abogados"] = [dict(ab) for ab in cur.fetchall()]
            exp_dict["participantes"].append(p_dict)

        # Convertir timestamp a string para compatibilidad con JSON / Streamlit
        if exp_dict.get("fecha_analisis"):
            exp_dict["fecha_analisis"] = str(exp_dict["fecha_analisis"])

        result.append(exp_dict)

    cur.close()
    con.close()
    return result


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
                       jurisdiccion: str = "", juzgado: str = "", secretaria: str = ""):
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
            (numero, anio, caratula, caja_se_presenta, fecha_analisis, jurisdiccion, juzgado, secretaria)
        VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s)
        ON CONFLICT(numero, anio) DO UPDATE SET
            caratula         = EXCLUDED.caratula,
            caja_se_presenta = EXCLUDED.caja_se_presenta,
            fecha_analisis   = NOW(),
            jurisdiccion     = EXCLUDED.jurisdiccion,
            juzgado          = EXCLUDED.juzgado,
            secretaria       = EXCLUDED.secretaria
    """, (str(numero), str(anio), caratula, caja_se_presenta,
          jurisdiccion or "", juzgado or "", secretaria or ""))

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
