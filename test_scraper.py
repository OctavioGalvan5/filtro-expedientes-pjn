# -*- coding: utf-8 -*-
"""
Prueba del scraper principal (busqueda de frase + descarga de PDFs)
para un expediente especifico. NO guarda nada en la base de datos.
Uso:
  python test_scraper.py [numero] [anio]
Por defecto usa 1410/2017.
"""

import sys
import traceback
from datetime import datetime

import pjn_scraper as scraper

USUARIO  = "20286335528"
PASSWORD = "Federal2025#"
HEADLESS = False


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def main():
    numero = sys.argv[1] if len(sys.argv) > 1 else "1410"
    anio   = sys.argv[2] if len(sys.argv) > 2 else "2017"

    log(f"=== Probando scraper para expediente {numero}/{anio} ===")
    log("(No se guardara nada en la base de datos)\n")

    driver = None
    try:
        driver = scraper.inicializar_navegador(headless=HEADLESS)
        scraper._login_y_abrir_formulario(driver, USUARIO, PASSWORD)

        encontrado, participantes, jurisdiccion, juzgado, secretaria = \
            scraper._buscar_y_procesar(driver, numero, anio)

        log(f"\n{'='*50}")
        log(f"Frase encontrada : {'SI' if encontrado else 'NO'}")
        log(f"Jurisdiccion     : {jurisdiccion}")
        log(f"Juzgado          : {juzgado}")
        log(f"Secretaria       : {secretaria}")
        log(f"Participantes    : {len(participantes)}")
        for p in participantes:
            log(f"  [{p.get('tipo','?')}] {p.get('nombre','?')} "
                f"({len(p.get('abogados',[]))} abogado/s)")
        log(f"{'='*50}")

    except Exception:
        log(f"[Error]\n{traceback.format_exc()}")
    finally:
        if driver:
            input("\nPresiona Enter para cerrar el navegador...")
            driver.quit()


if __name__ == "__main__":
    main()
