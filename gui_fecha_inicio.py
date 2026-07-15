# -*- coding: utf-8 -*-
"""
Extractor de Fecha de Inicio — PJN Scraper (Caja de Salta)
Procesa los expedientes en la BD que aún no tienen fecha_inicio.
"""

import sys
import queue
import threading
import traceback
import tkinter as tk
from datetime import datetime

import customtkinter as ctk
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import database as db
import pjn_scraper as scraper

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_stop_requested = False


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def extraer_datos_expediente(driver, num, anio):
    """
    Busca el expediente y extrae en un único pase:
      - fecha_inicio: fecha de la última fila de la última página (la más antigua)
      - url_demanda: viewer URL del primer ESCRITO INCORPORADO+DEMANDA encontrado
    Busca primero en actuaciones principales y luego en históricas (si existen),
    usando históricas como fuente de fecha aún más antigua y de demanda alternativa.
    Devuelve (fecha_inicio, url_demanda).
    """
    log(f"[Buscando] {num}/{anio}")

    select_camara = Select(driver.find_element(By.ID, "formPublica:camaraNumAni"))
    select_camara.select_by_value("24")

    input_numero = driver.find_element(By.ID, "formPublica:numero")
    input_numero.clear()
    input_numero.send_keys(num)

    input_anio = driver.find_element(By.ID, "formPublica:anio")
    input_anio.clear()
    input_anio.send_keys(anio)

    driver.find_element(By.ID, "formPublica:buscarPorNumeroButton").click()

    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.ID, "expediente:action-table"))
    )

    fecha, url_demanda, fecha_demanda = scraper.extraer_datos_inicio(driver, "expediente:action-table")
    log(f"[Actuaciones] Última fecha: {fecha} | Demanda: {'sí' if url_demanda else 'no'}")

    # Históricas: pueden tener fecha aún más antigua y/o la demanda
    try:
        div_historicas = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "expediente:btnActuacionesHistoricas"))
        )
        div_historicas.find_element(By.TAG_NAME, "a").click()

        tabla_historicas_id = "expediente:action-historic-table"
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.ID, tabla_historicas_id))
        )

        fecha_hist, url_demanda_hist, fecha_demanda_hist = scraper.extraer_datos_inicio(driver, tabla_historicas_id)
        if fecha_hist:
            fecha = fecha_hist
            log(f"[Historica] Fecha más antigua: {fecha}")
        if url_demanda_hist:
            url_demanda = url_demanda_hist
            fecha_demanda = fecha_demanda_hist

    except Exception:
        log(f"[Historica] Error: {traceback.format_exc()}")

    return fecha, url_demanda, fecha_demanda


def ejecutar_extraccion(usuario, password, headless, on_progreso=None):
    global _stop_requested
    _stop_requested = False

    pendientes = db.obtener_pendientes_datos_inicio()
    total = len(pendientes)
    log(f"[BD] {total} expediente(s) pendiente(s) de procesar.")

    if total == 0:
        log("[Exito] Todos los expedientes ya tienen fecha_inicio y url_demanda.")
        return

    for idx, exp in enumerate(pendientes, 1):
        if _stop_requested:
            log("[Detenido] El usuario detuvo el proceso.")
            break

        num      = exp["numero"]
        anio     = exp["anio"]
        caratula = (exp["caratula"] or "")[:60]

        log(f"\n[{idx}/{total}] {caratula}")
        log(f"        Expediente: {num} / {anio}")

        driver = None
        try:
            driver = scraper.inicializar_navegador(headless=headless)
            scraper._login_y_abrir_formulario(driver, usuario, password)
            fecha, url_demanda, fecha_demanda = extraer_datos_expediente(driver, num, anio)

            # url_demanda=None means not found; store 'NINGUNA' so we don't re-check
            db.actualizar_datos_inicio(exp["id"], fecha, url_demanda or "NINGUNA", fecha_demanda)
            log(f"[Guardado] fecha_inicio={fecha} | fecha_demanda={fecha_demanda} | url_demanda={'encontrada' if url_demanda else 'NINGUNA'}")

        except Exception:
            log(f"[Error] Fallo en {num}/{anio}:\n{traceback.format_exc()}")
        finally:
            if driver:
                driver.quit()

        if on_progreso:
            on_progreso(idx, total)

    log("[Exito] Proceso completado.")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class _LogStream:
    def __init__(self, q: queue.Queue):
        self.q = q
        self._original = sys.stdout

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Extractor Fecha Inicio — Caja de Salta")
        self.geometry("860x600")
        self.minsize(700, 480)

        self._log_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None

        db.inicializar_db()
        self._build_ui()
        self._drain_log_queue()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # ── Credenciales ─────────────────────────────────────────────────
        fc = ctk.CTkFrame(self)
        fc.grid(row=0, column=0, padx=8, pady=(8, 2), sticky="ew")
        fc.columnconfigure((1, 3), weight=1)

        ctk.CTkLabel(fc, text="Usuario CUIL:").grid(
            row=0, column=0, padx=(10, 4), pady=8, sticky="w")
        self.entry_usuario = ctk.CTkEntry(fc, width=200)
        self.entry_usuario.grid(row=0, column=1, padx=4, pady=8, sticky="ew")
        self.entry_usuario.insert(0, "20286335528")

        ctk.CTkLabel(fc, text="Contraseña:").grid(
            row=0, column=2, padx=(14, 4), pady=8, sticky="w")
        self.entry_password = ctk.CTkEntry(fc, show="*", width=200)
        self.entry_password.grid(row=0, column=3, padx=(4, 10), pady=8, sticky="ew")
        self.entry_password.insert(0, "Federal2025#")

        # ── Opciones + botones ────────────────────────────────────────────
        fo = ctk.CTkFrame(self)
        fo.grid(row=1, column=0, padx=8, pady=2, sticky="ew")
        fo.columnconfigure(3, weight=1)

        self.var_headless = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(fo, text="Modo silencioso (sin ventana Chrome)",
                        variable=self.var_headless).grid(
            row=0, column=0, padx=(10, 20), pady=8, sticky="w")

        self.btn_start = ctk.CTkButton(
            fo, text="▶  Iniciar", width=110,
            fg_color="#2e7d32", hover_color="#1b5e20",
            command=self._start)
        self.btn_start.grid(row=0, column=1, padx=6, pady=8)

        self.btn_stop = ctk.CTkButton(
            fo, text="■  Detener", width=110,
            fg_color="#c62828", hover_color="#7f0000",
            state="disabled", command=self._stop)
        self.btn_stop.grid(row=0, column=2, padx=6, pady=8)

        self.lbl_progreso = ctk.CTkLabel(fo, text="")
        self.lbl_progreso.grid(row=0, column=3, padx=(10, 10), sticky="e")

        # ── Barra de progreso ─────────────────────────────────────────────
        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.grid(row=2, column=0, padx=8, pady=(2, 0), sticky="ew")
        self.progress_bar.set(0)

        # ── Log ───────────────────────────────────────────────────────────
        self.log_box = ctk.CTkTextbox(self, wrap="word", state="disabled",
                                      font=("Consolas", 11))
        self.log_box.grid(row=3, column=0, padx=8, pady=(4, 8), sticky="nsew")

    def _start(self):
        usuario  = self.entry_usuario.get().strip()
        password = self.entry_password.get().strip()
        headless = self.var_headless.get()

        if not usuario or not password:
            self._append_log("[Error] Ingresa usuario y contraseña.\n")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress_bar.set(0)
        self.lbl_progreso.configure(text="")

        self._log_stream = _LogStream(self._log_queue)
        sys.stdout = self._log_stream

        self._worker_thread = threading.Thread(
            target=self._run,
            args=(usuario, password, headless),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop(self):
        global _stop_requested
        _stop_requested = True
        self.btn_stop.configure(state="disabled")
        self._append_log("\n[GUI] Deteniendo… el expediente actual terminará antes de parar.\n")

    def _run(self, usuario, password, headless):
        try:
            ejecutar_extraccion(
                usuario, password, headless,
                on_progreso=self._cb_progreso,
            )
        except Exception as e:
            print(f"\n[Error no esperado] {e}\n")
        finally:
            sys.stdout = self._log_stream._original
            self.after(0, self._on_finished)

    def _cb_progreso(self, actual: int, total: int):
        fraccion = actual / total if total else 0
        self.after(0, lambda: self.progress_bar.set(fraccion))
        self.after(0, lambda: self.lbl_progreso.configure(
            text=f"{actual} / {total} procesados"))

    def _on_finished(self):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self._append_log("\n[GUI] Proceso finalizado.\n")

    def _drain_log_queue(self):
        try:
            while True:
                text = self._log_queue.get_nowait()
                self._append_log(text)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _append_log(self, text: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")


if __name__ == "__main__":
    app = App()
    app.mainloop()
