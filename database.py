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
    cur.execute(
        "ALTER TABLE pjn_expedientes ADD COLUMN IF NOT EXISTS fecha_inicio TEXT"
    )
    cur.execute(
        "ALTER TABLE pjn_expedientes ADD COLUMN IF NOT EXISTS url_demanda TEXT"
    )
    cur.execute(
        "ALTER TABLE pjn_expedientes ADD COLUMN IF NOT EXISTS fecha_demanda TEXT"
    )
    cur.execute(
        "ALTER TABLE pjn_expedientes ADD COLUMN IF NOT EXISTS detalle_demanda TEXT"
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
def obtener_stats() -> dict:
    """Retorna conteos y listas de valores únicos en una sola conexión."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT
            COUNT(*)                                                  AS total,
            COUNT(*) FILTER (WHERE caja_se_presenta = 'Si')          AS si,
            COUNT(*) FILTER (WHERE caja_se_presenta = 'No')          AS no,
            COUNT(*) FILTER (WHERE caja_se_presenta NOT IN ('Si','No')) AS error
        FROM pjn_expedientes
    """)
    row = cur.fetchone()
    total, si, no, error = row
    cur.execute("SELECT DISTINCT juzgado   FROM pjn_expedientes WHERE juzgado   IS NOT NULL AND juzgado   <> '' ORDER BY 1")
    juzgados = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT secretaria FROM pjn_expedientes WHERE secretaria IS NOT NULL AND secretaria <> '' ORDER BY 1")
    secretarias = [r[0] for r in cur.fetchall()]
    cur.close()
    con.close()
    return {
        "total":       total,
        "si":          si,
        "no":          no,
        "error":       error,
        "pct_si":      round(si  * 100 / total) if total else 0,
        "pct_no":      round(no  * 100 / total) if total else 0,
        "juzgados":    juzgados,
        "secretarias": secretarias,
    }


def obtener_abogados_stats(filtro_ab: str = "", filtro_representa: str = "") -> list:
    """Computa estadísticas de abogados server-side."""
    con = _connect()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT
            a.nombre                AS nombre,
            a.tomo_folio            AS tomo_folio,
            a.cuit                  AS cuit,
            p.tipo                  AS parte_tipo,
            p.nombre                AS parte_nombre,
            e.id                    AS exp_id,
            e.caja_se_presenta      AS resultado
        FROM pjn_abogados a
        JOIN pjn_participantes p ON p.id = a.participante_id
        JOIN pjn_expedientes   e ON e.id = p.expediente_id
        ORDER BY a.nombre
    """)
    rows = cur.fetchall()

    mapa = {}
    for r in rows:
        key = r["nombre"] or "Sin nombre"
        if key not in mapa:
            mapa[key] = {
                "nombre":     key,
                "tomo_folio": r["tomo_folio"] or "",
                "cuit":       r["cuit"] or "",
                "total": 0, "si": 0, "no": 0, "error": 0,
                "_exp_ids":       set(),
                "_partes":        set(),
                "_detalle":       [],
                "_representados": {},
            }
        entry = mapa[key]
        entry["_partes"].add((r["parte_nombre"] or "").lower())
        entry["_detalle"].append({
            "exp_id":       r["exp_id"],
            "parte_tipo":   r["parte_tipo"] or "",
            "parte_nombre": r["parte_nombre"] or "",
            "resultado":    r["resultado"],
        })
        rep_key = (r["parte_tipo"] or "", r["parte_nombre"] or "")
        if rep_key not in entry["_representados"]:
            entry["_representados"][rep_key] = {"total": 0, "si": 0, "no": 0, "error": 0}
        rep = entry["_representados"][rep_key]
        rep["total"] += 1
        res_r = r["resultado"]
        if res_r == "Si":   rep["si"]    += 1
        elif res_r == "No": rep["no"]    += 1
        else:               rep["error"] += 1
        if r["exp_id"] not in entry["_exp_ids"]:
            entry["_exp_ids"].add(r["exp_id"])
            entry["total"] += 1
            res = r["resultado"]
            if res == "Si":   entry["si"]    += 1
            elif res == "No": entry["no"]    += 1
            else:             entry["error"] += 1

    # Cargar datos de expedientes para el panel de detalle
    exp_ids_todos = set()
    for e in mapa.values():
        exp_ids_todos.update(e["_exp_ids"])

    exp_data = {}
    if exp_ids_todos:
        cur.execute(
            "SELECT id, numero, anio, caratula, caja_se_presenta FROM pjn_expedientes WHERE id = ANY(%s)",
            (list(exp_ids_todos),)
        )
        for row in cur.fetchall():
            exp_data[row["id"]] = dict(row)

    cur.close()
    con.close()

    # Reconstruir con lista de expedientes en formato que espera el frontend
    for entry in mapa.values():
        entry["representados"] = sorted(
            [
                {"parte_tipo": k[0], "parte_nombre": k[1], **v}
                for k, v in entry["_representados"].items()
            ],
            key=lambda x: -x["total"],
        )
        entry["expedientes"] = [
            {
                "exp": {
                    "id":               d["exp_id"],
                    "numero":           exp_data[d["exp_id"]]["numero"],
                    "anio":             exp_data[d["exp_id"]]["anio"],
                    "caratula":         exp_data[d["exp_id"]]["caratula"] or "",
                    "caja_se_presenta": exp_data[d["exp_id"]]["caja_se_presenta"],
                },
                "parte_tipo":  d["parte_tipo"],
                "parte_nombre": d["parte_nombre"],
            }
            for d in entry["_detalle"] if d["exp_id"] in exp_data
        ]

    result = [
        {k: v for k, v in e.items() if not k.startswith("_")}
        for e in mapa.values()
    ]
    result.sort(key=lambda x: -x["total"])

    if filtro_ab:
        q = filtro_ab.lower()
        result = [r for r in result if q in r["nombre"].lower() or q in r["cuit"].lower()]
    if filtro_representa:
        q = filtro_representa.lower()
        result = [r for r in result if any(q in pn for pn in mapa[r["nombre"]]["_partes"])]

    return result


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


def obtener_participantes_por_tipo() -> dict:
    """Retorna dict {actor: [nombres...], demandado: [...], tercero: [...]} con nombres únicos ordenados."""
    con = _connect()
    cur = con.cursor()
    cur.execute("""
        SELECT LOWER(tipo), nombre
        FROM pjn_participantes
        WHERE nombre IS NOT NULL AND nombre <> ''
        GROUP BY LOWER(tipo), nombre
        ORDER BY nombre
    """)
    resultado = {'actor': [], 'demandado': [], 'tercero': []}
    seen = {'actor': set(), 'demandado': set(), 'tercero': set()}
    for tipo_raw, nombre in cur.fetchall():
        t = tipo_raw or ''
        if 'actor' in t:
            key = 'actor'
        elif 'demandado' in t:
            key = 'demandado'
        elif 'tercero' in t:
            key = 'tercero'
        else:
            continue
        if nombre not in seen[key]:
            seen[key].add(nombre)
            resultado[key].append(nombre)
    cur.close()
    con.close()
    return resultado


def obtener_paginados(pagina: int, por_pagina: int, filtro: str = "",
                      resultado: str = "", juzgado: str = "", secretaria: str = "",
                      actores: list = None, demandados: list = None, terceros: list = None,
                      con_demanda: bool = False, fecha_desde: str = "", fecha_hasta: str = "") -> tuple:
    """
    Retorna (expedientes, total) para la página dada.
    expedientes: lista de dicts con participantes y abogados anidados.
    total: cantidad total de expedientes que coinciden con los filtros.
    """
    con = _connect()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    offset = (pagina - 1) * por_pagina
    conditions = []
    params = []

    if filtro:
        like = f"%{filtro}%"
        conditions.append("""
            e.id IN (
                SELECT DISTINCT e2.id FROM pjn_expedientes e2
                LEFT JOIN pjn_participantes p2 ON p2.expediente_id = e2.id
                LEFT JOIN pjn_abogados a2     ON a2.participante_id = p2.id
                WHERE e2.numero   ILIKE %s
                   OR e2.anio     ILIKE %s
                   OR e2.caratula ILIKE %s
                   OR p2.nombre   ILIKE %s
                   OR a2.nombre   ILIKE %s
            )
        """)
        params.extend([like, like, like, like, like])

    if resultado == "Si":
        conditions.append("e.caja_se_presenta = 'Si'")
    elif resultado == "No":
        conditions.append("e.caja_se_presenta = 'No'")
    elif resultado == "error":
        conditions.append("e.caja_se_presenta NOT IN ('Si','No')")

    if juzgado:
        conditions.append("e.juzgado = %s")
        params.append(juzgado)

    if secretaria:
        conditions.append("e.secretaria = %s")
        params.append(secretaria)

    for tipo_key, nombres in [('actor', actores or []), ('demandado', demandados or []), ('tercero', terceros or [])]:
        if nombres:
            conditions.append("""
                EXISTS (
                    SELECT 1 FROM pjn_participantes pf
                    WHERE pf.expediente_id = e.id
                      AND LOWER(pf.nombre) = ANY(%s)
                      AND LOWER(pf.tipo) LIKE %s
                )
            """)
            params.extend([[n.lower() for n in nombres], f'%{tipo_key}%'])

    if con_demanda:
        conditions.append(
            "e.url_demanda IS NOT NULL AND e.url_demanda NOT IN ('NINGUNA', '')"
        )

    if fecha_desde:
        conditions.append(
            "e.fecha_inicio IS NOT NULL AND e.fecha_inicio != ''"
            " AND e.fecha_inicio ~ '^\\d{1,2}/\\d{1,2}/\\d{4}$'"
            " AND TO_DATE(e.fecha_inicio, 'DD/MM/YYYY') >= %s::date"
        )
        params.append(fecha_desde)

    if fecha_hasta:
        conditions.append(
            "e.fecha_inicio IS NOT NULL AND e.fecha_inicio != ''"
            " AND e.fecha_inicio ~ '^\\d{1,2}/\\d{1,2}/\\d{4}$'"
            " AND TO_DATE(e.fecha_inicio, 'DD/MM/YYYY') <= %s::date"
        )
        params.append(fecha_hasta)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    cur.execute(f"SELECT COUNT(*) FROM pjn_expedientes e {where}", params)
    total = cur.fetchone()["count"]

    cur.execute(
        f"SELECT e.* FROM pjn_expedientes e {where} ORDER BY e.id LIMIT %s OFFSET %s",
        params + [por_pagina, offset],
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
def obtener_pendientes_datos_inicio() -> list:
    """Retorna expedientes sin fecha_inicio o con url_demanda aún no chequeada (NULL)."""
    con = _connect()
    cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT id, numero, anio, caratula
        FROM pjn_expedientes
        WHERE fecha_inicio IS NULL OR fecha_inicio = ''
           OR url_demanda IS NULL
        ORDER BY id
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    con.close()
    return rows


def actualizar_datos_inicio(id: int, fecha_inicio: str, url_demanda: str,
                            fecha_demanda: str = None, detalle_demanda: str = None):
    """Actualiza fecha_inicio, url_demanda, fecha_demanda y detalle_demanda."""
    con = _connect()
    cur = con.cursor()
    cur.execute(
        """UPDATE pjn_expedientes
           SET fecha_inicio = %s, url_demanda = %s,
               fecha_demanda = %s, detalle_demanda = %s
           WHERE id = %s""",
        (fecha_inicio or None, url_demanda, fecha_demanda or None, detalle_demanda or None, id)
    )
    con.commit()
    cur.close()
    con.close()


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
                       fuente: str = "Extractor PJN", fecha_inicio: str = None,
                       url_demanda: str = None, fecha_demanda: str = None,
                       detalle_demanda: str = None):
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
            (numero, anio, caratula, caja_se_presenta, fecha_analisis,
             jurisdiccion, juzgado, secretaria, fuente,
             fecha_inicio, url_demanda, fecha_demanda, detalle_demanda)
        VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT(numero, anio) DO UPDATE SET
            caratula         = EXCLUDED.caratula,
            caja_se_presenta = EXCLUDED.caja_se_presenta,
            fecha_analisis   = NOW(),
            jurisdiccion     = EXCLUDED.jurisdiccion,
            juzgado          = EXCLUDED.juzgado,
            secretaria       = EXCLUDED.secretaria,
            fuente           = EXCLUDED.fuente,
            fecha_inicio     = COALESCE(EXCLUDED.fecha_inicio,     pjn_expedientes.fecha_inicio),
            url_demanda      = COALESCE(EXCLUDED.url_demanda,      pjn_expedientes.url_demanda),
            fecha_demanda    = COALESCE(EXCLUDED.fecha_demanda,    pjn_expedientes.fecha_demanda),
            detalle_demanda  = COALESCE(EXCLUDED.detalle_demanda,  pjn_expedientes.detalle_demanda)
    """, (str(numero), str(anio), caratula, caja_se_presenta,
          jurisdiccion or "", juzgado or "", secretaria or "", fuente or "Extractor PJN",
          fecha_inicio or None, url_demanda or None, fecha_demanda or None, detalle_demanda or None))

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
