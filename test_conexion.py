# -*- coding: utf-8 -*-
"""Prueba la conexion a PostgreSQL leyendo la URL del .env"""

import sys
import os

_env_path = os.path.join(os.path.dirname(__file__), ".env")
_db_url = None
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("database="):
                _db_url = line.split("=", 1)[1].strip()
                break

if not _db_url:
    print("[ERROR] No se encontro la variable 'database' en .env")
    sys.exit(1)

print(f"URL: {_db_url[:45]}...{_db_url[-10:]}")
print("Conectando...\n")

try:
    import psycopg2
except ImportError:
    print("[ERROR] psycopg2 no instalado. Corra: pip install psycopg2-binary")
    sys.exit(1)

try:
    conn = psycopg2.connect(_db_url, connect_timeout=10)
    cur = conn.cursor()

    cur.execute("SELECT version()")
    version = cur.fetchone()[0]
    print("[OK] Conexion exitosa!")
    print(f"   {version}\n")

    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tablas = [r[0] for r in cur.fetchall()]
    if tablas:
        print(f"Tablas existentes en 'public' ({len(tablas)}):")
        for t in tablas:
            print(f"  - {t}")
    else:
        print("No hay tablas en el esquema 'public' todavia.")

    conn.close()
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)
