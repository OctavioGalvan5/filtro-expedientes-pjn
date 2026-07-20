# -*- coding: utf-8 -*-
"""
GUI para reintentar expedientes con error — PJN Scraper (Caja de Salta)
Requiere: pip install customtkinter
"""

import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk
import database as db
import pjn_scraper as scraper

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


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


# ---------------------------------------------------------------------------
class AppReintentar(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PJN Scraper — Reintento de Errores")
        self.geometry("1060x780")
        self.minsize(800, 600)

        self._log_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._errores: list = []

        db.inicializar_db()
        self._build_ui()
        self._cargar_errores()
        self._drain_log_queue()

    # ------------------------------------------------------------------
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=2)   # treeview
        self.rowconfigure(5, weight=3)   # log

        # ── Header ──────────────────────────────────────────────────────
        fh = ctk.CTkFrame(self)
        fh.grid(row=0, column=0, padx=8, pady=(8, 2), sticky="ew")
        fh.columnconfigure(1, weight=1)

        ctk.CTkLabel(fh, text="Expedientes con error",
                     font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, padx=(12, 8), pady=8, sticky="w")
        self.lbl_conteo = ctk.CTkLabel(fh, text="Cargando…", text_color="#ffa726",
                                        font=("Segoe UI", 12))
        self.lbl_conteo.grid(row=0, column=1, padx=4, pady=8, sticky="w")
        ctk.CTkButton(fh, text="↻ Actualizar", width=110,
                      command=self._cargar_errores).grid(
            row=0, column=2, padx=(4, 12), pady=8)

        # ── Treeview ─────────────────────────────────────────────────────
        frame_tree = ctk.CTkFrame(self)
        frame_tree.grid(row=1, column=0, padx=8, pady=2, sticky="nsew")
        frame_tree.columnconfigure(0, weight=1)
        frame_tree.rowconfigure(0, weight=1)

        self._aplicar_estilo_tree()

        cols = ("numero_anio", "caratula", "estado")
        self.tree = ttk.Treeview(frame_tree, style="PJN.Treeview",
                                  columns=cols, show="headings",
                                  selectmode="browse")

        self.tree.heading("numero_anio", text="Número / Año",  anchor="w")
        self.tree.heading("caratula",    text="Carátula",       anchor="w")
        self.tree.heading("estado",      text="Estado",         anchor="center")

        self.tree.column("numero_anio", width=140, stretch=False, anchor="w")
        self.tree.column("caratula",    width=700, stretch=True,  anchor="w")
        self.tree.column("estado",      width=130, stretch=False, anchor="center")

        self.tree.tag_configure("error", foreground="#ffa726",
                                 font=("Consolas", 10, "bold"))

        vsb = ttk.Scrollbar(frame_tree, orient="vertical", command=self.tree.yview)
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
            fo, text="▶  Reintentar todos", width=160,
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

    @staticmethod
    def _aplicar_estilo_tree():
        s = ttk.Style()
        s.theme_use("default")
        s.configure("PJN.Treeview",
            background="#1e1e1e",
            foreground="#ffffff",
            rowheight=24,
            fieldbackground="#1e1e1e",
            font=("Consolas", 10),
            borderwidth=0,
        )
        s.configure("PJN.Treeview.Heading",
            background="#1c3a5e",
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief="flat",
        )
        s.map("PJN.Treeview",
            background=[("selected", "#1f538d")],
            foreground=[("selected", "#ffffff")],
        )
        s.configure("Vertical.TScrollbar",
            background="#3a3a3a", troughcolor="#1e1e1e", arrowcolor="#aaaaaa")
        s.configure("Horizontal.TScrollbar",
            background="#3a3a3a", troughcolor="#1e1e1e", arrowcolor="#aaaaaa")

    # ------------------------------------------------------------------
    def _cargar_errores(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        try:
            self._errores = db.obtener_con_error()
        except Exception as e:
            self._append_log(f"[Error] No se pudo cargar la BD: {e}\n")
            self._errores = []

        n = len(self._errores)
        self.lbl_conteo.configure(
            text=f"{n} expediente{'s' if n != 1 else ''} con error",
            text_color="#ffa726" if n > 0 else "#66bb6a",
        )

        for item in self._errores:
            num_anio  = f"{item['numero']} / {item['anio']}"
            caratula  = item.get("caratula") or "—"
            estado    = item.get("caja_se_presenta") or "Error"
            self.tree.insert("", "end",
                values=(num_anio, caratula, estado),
                tags=("error",))

        if n == 0:
            self._append_log("[Info] No hay expedientes con error en la base de datos.\n")
        else:
            self._append_log(f"[Info] Se cargaron {n} expedientes con error listos para reintentar.\n")

    # ------------------------------------------------------------------
    def _start(self):
        usuario  = self.entry_usuario.get().strip()
        password = self.entry_password.get().strip()
        headless = self.var_headless.get()

        if not usuario or not password:
            self._append_log("[Error] Ingresa usuario y contraseña.\n")
            return
        if not self._errores:
            self._append_log("[Aviso] No hay expedientes con error para reintentar. "
                             "Presioná ↻ Actualizar primero.\n")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress_bar.set(0)
        self.lbl_progreso.configure(text="")

        self._log_stream = _LogStream(self._log_queue)
        sys.stdout = self._log_stream

        self._worker_thread = threading.Thread(
            target=self._run_scraper,
            args=(list(self._errores), usuario, password, headless),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop(self):
        scraper._stop_requested = True
        self.btn_stop.configure(state="disabled")
        self._append_log(
            "\n[GUI] Deteniendo… el expediente actual terminará antes de parar.\n")

    def _run_scraper(self, lista, usuario, password, headless):
        try:
            scraper.ejecutar_lista(
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
        self.after(500, self._cargar_errores)

    # ------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = AppReintentar()
    app.mainloop()
