# -*- coding: utf-8 -*-
"""
Prueba de extraccion de fecha_inicio, url_demanda y fecha_demanda
para un expediente especifico. Ejecutar desde consola:
  python test_expediente.py [numero] [anio]
Por defecto usa 6527/2014.
"""

import sys
import traceback
from datetime import datetime

import database as db
import pjn_scraper as scraper
from gui_fecha_inicio import extraer_datos_expediente

USUARIO  = "20286335528"
PASSWORD = "Federal2025#"
HEADLESS = False  # False = ver el Chrome, True = silencioso


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def main():
    numero = sys.argv[1] if len(sys.argv) > 1 else "6527"
    anio   = sys.argv[2] if len(sys.argv) > 2 else "2014"

    db.inicializar_db()

    log(f"=== Probando expediente {numero}/{anio} ===")

    driver = None
    try:
        driver = scraper.inicializar_navegador(headless=HEADLESS)
        scraper._login_y_abrir_formulario(driver, USUARIO, PASSWORD)

        fecha, url_demanda, fecha_demanda = extraer_datos_expediente(driver, numero, anio)

        log(f"\n{'='*50}")
        log(f"fecha_inicio  : {fecha}")
        log(f"fecha_demanda : {fecha_demanda}")
        log(f"url_demanda   : {url_demanda}")
        log(f"{'='*50}")

        # Preguntar antes de guardar
        guardar = input("\n¿Guardar en la base de datos? (s/n): ").strip().lower()
        if guardar == "s":
            import psycopg2
            con = db._connect()
            cur = con.cursor()
            cur.execute(
                "SELECT id FROM pjn_expedientes WHERE numero=%s AND anio=%s",
                (numero, anio)
            )
            row = cur.fetchone()
            cur.close()
            con.close()
            if row:
                db.actualizar_datos_inicio(row[0], fecha, url_demanda or "NINGUNA", fecha_demanda)
                log("[Guardado] OK")
            else:
                log("[Error] Expediente no encontrado en la BD")

    except Exception:
        log(f"[Error]\n{traceback.format_exc()}")
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    main()
