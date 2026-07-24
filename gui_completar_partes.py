# -*- coding: utf-8 -*-
"""
Completar partes de demanda — PJN Scraper (Caja de Salta)

Busca expedientes cuyo detalle_demanda contiene '(Parte X de Y)' (guardados con
el scraper anterior que solo capturaba una parte) y los re-scrapea para obtener
TODAS las partes, guardando todas las URLs separadas por '|'.
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
from selenium.common.exceptions import TimeoutException

import database as db
import pjn_scraper as scraper

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_stop_requested = False


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def extraer_datos_expediente(driver, num, anio):
    """Navega al expediente y extrae todos los datos de demanda (todas las partes)."""
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

    _, url_demanda, fecha_demanda, detalle_demanda = scraper.extraer_datos_inicio(
        driver, "expediente:action-table"
    )
    log(f"[Actuaciones] Demanda: {'sí ('+str(url_demanda.count('|')+1)+' parte/s)' if url_demanda else 'no'}")

    # Históricas: pueden tener la demanda también
    try:
        div_historicas = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.ID, "expediente:btnActuacionesHistoricas"))
        )
        div_historicas.find_element(By.TAG_NAME, "a").click()

        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.ID, "expediente:action-historic-table"))
        )

        _, url_dem_hist, fecha_dem_hist, det_dem_hist = scraper.extraer_datos_inicio(
            driver, "expediente:action-historic-table"
        )
        if url_dem_hist:
            url_demanda     = url_dem_hist
            fecha_demanda   = fecha_dem_hist
            detalle_demanda = det_dem_hist
            log(f"[Historica] Demanda encontrada ({url_dem_hist.count('|')+1} parte/s)")

    except TimeoutException:
        log("[Historica] Sin registros históricos.")
    except Exception:
        log(f"[Historica] Error: {traceback.format_exc()}")

    return url_demanda, fecha_demanda, detalle_demanda


def ejecutar_completar(lista, usuario, password, headless, on_progreso=None):
    global _stop_requested
    _stop_requested = False

    total = len(lista)
    log(f"[BD] {total} expediente(s) con partes incompletas.")

    if total == 0:
        log("[OK] No hay expedientes con partes incompletas.")
        return

    for idx, exp in enumerate(lista, 1):
        if _stop_requested:
            log("[Detenido] El usuario detuvo el proceso.")
            break

        num      = exp["numero"]
        anio     = exp["anio"]
        caratula = (exp["caratula"] or "")[:60]

        log(f"\n[{idx}/{total}] {caratula}")
        log(f"        Expediente: {num} / {anio}")
        log(f"        Detalle actual: {exp.get('detalle_demanda','—')}")

        driver = None
        try:
            driver = scraper.inicializar_navegador(headless=headless)
            scraper._login_y_abrir_formulario(driver, usuario, password)
            url_demanda, fecha_demanda, detalle_demanda = extraer_datos_expediente(
                driver, num, anio
            )

            if url_demanda:
                partes = url_demanda.count("|") + 1
                db.actualizar_solo_demanda(
                    exp["id"], url_demanda, fecha_demanda, detalle_demanda
                )
                log(f"[Guardado] {partes} parte(s) | detalle='{detalle_demanda}'")
            else:
                log(f"[Aviso] No se encontró demanda — se mantiene el valor anterior.")

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


class AppCompletarPartes(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PJN — Completar Partes de Demanda")
        self.geometry("1060x780")
        self.minsize(800, 600)

        self._log_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._pendientes: list = []

        db.inicializar_db()
        self._build_ui()
        self._cargar_pendientes()
        self._drain_log_queue()

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=2)
        self.rowconfigure(5, weight=3)

        # ── Header ──────────────────────────────────────────────────────
        fh = ctk.CTkFrame(self)
        fh.grid(row=0, column=0, padx=8, pady=(8, 2), sticky="ew")
        fh.columnconfigure(1, weight=1)

        ctk.CTkLabel(fh, text="Expedientes con partes incompletas",
                     font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, padx=(12, 8), pady=8, sticky="w")
        self.lbl_conteo = ctk.CTkLabel(fh, text="Cargando…", text_color="#ffa726",
                                        font=("Segoe UI", 12))
        self.lbl_conteo.grid(row=0, column=1, padx=4, pady=8, sticky="w")
        ctk.CTkButton(fh, text="↻ Actualizar", width=110,
                      command=self._cargar_pendientes).grid(
            row=0, column=2, padx=(4, 12), pady=8)

        # ── Treeview ─────────────────────────────────────────────────────
        from tkinter import ttk
        frame_tree = ctk.CTkFrame(self)
        frame_tree.grid(row=1, column=0, padx=8, pady=2, sticky="nsew")
        frame_tree.columnconfigure(0, weight=1)
        frame_tree.rowconfigure(0, weight=1)

        s = ttk.Style()
        s.theme_use("default")
        s.configure("PJN2.Treeview",
            background="#1e1e1e", foreground="#ffffff",
            rowheight=24, fieldbackground="#1e1e1e",
            font=("Consolas", 10), borderwidth=0)
        s.configure("PJN2.Treeview.Heading",
            background="#1c3a5e", foreground="#ffffff",
            font=("Segoe UI", 10, "bold"), relief="flat")
        s.map("PJN2.Treeview",
            background=[("selected", "#1f538d")],
            foreground=[("selected", "#ffffff")])

        cols = ("numero_anio", "caratula", "detalle_actual")
        self.tree = ttk.Treeview(frame_tree, style="PJN2.Treeview",
                                  columns=cols, show="headings",
                                  selectmode="browse")

        self.tree.heading("numero_anio",    text="Número / Año",   anchor="w")
        self.tree.heading("caratula",       text="Carátula",        anchor="w")
        self.tree.heading("detalle_actual", text="Detalle actual",  anchor="w")

        self.tree.column("numero_anio",    width=130, stretch=False, anchor="w")
        self.tree.column("caratula",       width=420, stretch=True,  anchor="w")
        self.tree.column("detalle_actual", width=400, stretch=True,  anchor="w")

        self.tree.tag_configure("pendiente", foreground="#ffa726",
                                 font=("Consolas", 10))

        vsb = ttk.Scrollbar(frame_tree, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(frame_tree, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        # ── Credenciales ─────────────────────────────────────────────────
        fc = ctk.CTkFrame(self)
        fc.grid(row=2, column=0, padx=8, pady=2, sticky="ew")
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
        fo.grid(row=3, column=0, padx=8, pady=2, sticky="ew")
        fo.columnconfigure(3, weight=1)

        self.var_headless = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(fo, text="Modo silencioso (sin ventana Chrome)",
                        variable=self.var_headless).grid(
            row=0, column=0, padx=(10, 20), pady=8, sticky="w")

        self.btn_start = ctk.CTkButton(
            fo, text="▶  Completar todos", width=160,
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
        self.progress_bar.grid(row=4, column=0, padx=8, pady=(2, 0), sticky="ew")
        self.progress_bar.set(0)

        # ── Log ───────────────────────────────────────────────────────────
        self.log_box = ctk.CTkTextbox(self, wrap="word", state="disabled",
                                       font=("Consolas", 11))
        self.log_box.grid(row=5, column=0, padx=8, pady=(4, 8), sticky="nsew")

    def _cargar_pendientes(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            self._pendientes = db.obtener_con_partes_incompletas()
        except Exception as e:
            self._append_log(f"[Error] No se pudo cargar la BD: {e}\n")
            self._pendientes = []

        n = len(self._pendientes)
        self.lbl_conteo.configure(
            text=f"{n} expediente{'s' if n != 1 else ''} con partes incompletas",
            text_color="#ffa726" if n > 0 else "#66bb6a",
        )

        for item in self._pendientes:
            num_anio = f"{item['numero']} / {item['anio']}"
            caratula = item.get("caratula") or "—"
            detalle  = item.get("detalle_demanda") or "—"
            self.tree.insert("", "end",
                values=(num_anio, caratula, detalle),
                tags=("pendiente",))

        if n == 0:
            self._append_log("[OK] No hay expedientes con partes incompletas.\n")
        else:
            self._append_log(f"[Info] {n} expedientes listos para completar.\n")

    def _start(self):
        usuario  = self.entry_usuario.get().strip()
        password = self.entry_password.get().strip()
        headless = self.var_headless.get()

        if not usuario or not password:
            self._append_log("[Error] Ingresá usuario y contraseña.\n")
            return
        if not self._pendientes:
            self._append_log("[Aviso] No hay expedientes pendientes. Presioná ↻ Actualizar.\n")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress_bar.set(0)
        self.lbl_progreso.configure(text="")

        self._log_stream = _LogStream(self._log_queue)
        sys.stdout = self._log_stream

        self._worker_thread = threading.Thread(
            target=self._run_scraper,
            args=(list(self._pendientes), usuario, password, headless),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop(self):
        global _stop_requested
        _stop_requested = True
        self.btn_stop.configure(state="disabled")
        self._append_log("\n[GUI] Deteniendo… el expediente actual terminará antes de parar.\n")

    def _run_scraper(self, lista, usuario, password, headless):
        try:
            ejecutar_completar(
                lista=lista,
                usuario=usuario,
                password=password,
                headless=headless,
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
        self._append_log("\n[GUI] Proceso finalizado. Actualizando lista…\n")
        self.after(500, self._cargar_pendientes)

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
    app = AppCompletarPartes()
    app.mainloop()
