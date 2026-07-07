# -*- coding: utf-8 -*-
"""
GUI principal — PJN Scraper (Caja de Salta)
Requiere: pip install customtkinter
"""

import sys
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk
import database as db
import pjn_scraper as scraper

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ---------------------------------------------------------------------------
# Redireccionamiento de stdout al log de la GUI
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
# Ventana principal
# ---------------------------------------------------------------------------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("PJN Scraper — Caja de Salta")
        self.geometry("1060x740")
        self.minsize(800, 600)

        self._log_queue: queue.Queue = queue.Queue()
        self._worker_thread: threading.Thread | None = None

        db.inicializar_db()
        self._build_ui()
        self._drain_log_queue()

    # ------------------------------------------------------------------
    # Construcción general
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.tabview = ctk.CTkTabview(self, command=self._on_tab_change)
        self.tabview.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
        self.tabview.add("⚙  Proceso")
        self.tabview.add("👥  Intervinientes")

        self._build_tab_proceso()
        self._build_tab_intervinientes()

    def _on_tab_change(self):
        if "Intervinientes" in self.tabview.get():
            self._actualizar_tree()

    # ------------------------------------------------------------------
    # Tab 1: Proceso (scraping)
    # ------------------------------------------------------------------
    def _build_tab_proceso(self):
        tab = self.tabview.tab("⚙  Proceso")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(4, weight=1)

        # ── Credenciales ────────────────────────────────────────────────
        fc = ctk.CTkFrame(tab)
        fc.grid(row=0, column=0, padx=4, pady=(4, 2), sticky="ew")
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

        # ── Archivos ─────────────────────────────────────────────────────
        ff = ctk.CTkFrame(tab)
        ff.grid(row=1, column=0, padx=4, pady=2, sticky="ew")
        ff.columnconfigure(1, weight=1)

        ctk.CTkLabel(ff, text="Excel de entrada:").grid(
            row=0, column=0, padx=(10, 4), pady=6, sticky="w")
        self.entry_entrada = ctk.CTkEntry(ff)
        self.entry_entrada.grid(row=0, column=1, padx=4, pady=6, sticky="ew")
        self.entry_entrada.insert(0, "expedientes.xlsx")
        ctk.CTkButton(ff, text="...", width=36,
                      command=self._browse_entrada).grid(row=0, column=2, padx=(4, 10))

        ctk.CTkLabel(ff, text="Excel de salida:").grid(
            row=1, column=0, padx=(10, 4), pady=6, sticky="w")
        self.entry_salida = ctk.CTkEntry(ff)
        self.entry_salida.grid(row=1, column=1, padx=4, pady=6, sticky="ew")
        self.entry_salida.insert(0, "resultado.xlsx")
        ctk.CTkButton(ff, text="...", width=36,
                      command=self._browse_salida).grid(row=1, column=2, padx=(4, 10))

        # ── Opciones + botones ───────────────────────────────────────────
        fo = ctk.CTkFrame(tab)
        fo.grid(row=2, column=0, padx=4, pady=2, sticky="ew")
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

        # ── Barra de progreso ────────────────────────────────────────────
        self.progress_bar = ctk.CTkProgressBar(tab)
        self.progress_bar.grid(row=3, column=0, padx=4, pady=(2, 0), sticky="ew")
        self.progress_bar.set(0)

        # ── Log ──────────────────────────────────────────────────────────
        self.log_box = ctk.CTkTextbox(tab, wrap="word", state="disabled",
                                      font=("Consolas", 11))
        self.log_box.grid(row=4, column=0, padx=4, pady=(4, 4), sticky="nsew")

    # ------------------------------------------------------------------
    # Tab 2: Intervinientes (visor de BD)
    # ------------------------------------------------------------------
    def _build_tab_intervinientes(self):
        tab = self.tabview.tab("👥  Intervinientes")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        # ── Toolbar ──────────────────────────────────────────────────────
        ft = ctk.CTkFrame(tab)
        ft.grid(row=0, column=0, padx=4, pady=(4, 2), sticky="ew")
        ft.columnconfigure(1, weight=1)

        ctk.CTkButton(ft, text="↻ Actualizar", width=110,
                      command=self._actualizar_tree).grid(
            row=0, column=0, padx=(10, 6), pady=8)

        self.entry_buscar = ctk.CTkEntry(
            ft, placeholder_text="Filtrar por número, año, carátula o nombre...")
        self.entry_buscar.grid(row=0, column=1, padx=4, pady=8, sticky="ew")
        self.entry_buscar.bind("<Return>", lambda _e: self._actualizar_tree())

        ctk.CTkButton(ft, text="Buscar", width=80,
                      command=self._actualizar_tree).grid(
            row=0, column=2, padx=(4, 6), pady=8)

        ctk.CTkButton(ft, text="Expandir todo", width=110,
                      command=self._expandir_todo).grid(
            row=0, column=3, padx=(4, 6), pady=8)

        ctk.CTkButton(ft, text="Contraer todo", width=110,
                      command=self._contraer_todo).grid(
            row=0, column=4, padx=(4, 6), pady=8)

        self.lbl_total_exp = ctk.CTkLabel(ft, text="")
        self.lbl_total_exp.grid(row=0, column=5, padx=(6, 10))

        # ── Treeview ─────────────────────────────────────────────────────
        frame_tree = ctk.CTkFrame(tab)
        frame_tree.grid(row=1, column=0, padx=4, pady=(0, 4), sticky="nsew")
        frame_tree.columnconfigure(0, weight=1)
        frame_tree.rowconfigure(0, weight=1)

        self._aplicar_estilo_tree()

        cols = ("tipo", "resultado", "detalle", "cuit")
        self.tree = ttk.Treeview(frame_tree, style="PJN.Treeview",
                                  columns=cols, show="tree headings",
                                  selectmode="browse")

        self.tree.heading("#0",         text="Nombre / Descripción",    anchor="w")
        self.tree.heading("tipo",       text="Tipo",                    anchor="center")
        self.tree.heading("resultado",  text="Resultado",               anchor="center")
        self.tree.heading("detalle",    text="Carátula / Tomo-Folio / Fecha", anchor="w")
        self.tree.heading("cuit",       text="CUIT",                    anchor="center")

        self.tree.column("#0",        width=300, stretch=True,  minwidth=180)
        self.tree.column("tipo",      width=110, stretch=False, anchor="center")
        self.tree.column("resultado", width=80,  stretch=False, anchor="center")
        self.tree.column("detalle",   width=260, stretch=True,  anchor="w")
        self.tree.column("cuit",      width=130, stretch=False, anchor="center")

        # Colores por tipo de fila
        self.tree.tag_configure("exp_si",    foreground="#66bb6a", font=("Consolas", 11, "bold"))
        self.tree.tag_configure("exp_no",    foreground="#ef5350", font=("Consolas", 11, "bold"))
        self.tree.tag_configure("exp_error", foreground="#ffa726", font=("Consolas", 11, "bold"))
        self.tree.tag_configure("actor",     foreground="#90caf9")
        self.tree.tag_configure("demandado", foreground="#f48fb1")
        self.tree.tag_configure("tercero",   foreground="#ce93d8")
        self.tree.tag_configure("parte",     foreground="#eeeeee")
        self.tree.tag_configure("abogado",   foreground="#b0bec5")

        vsb = ttk.Scrollbar(frame_tree, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(frame_tree, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0,  column=1, sticky="ns")
        hsb.grid(row=1,  column=0, sticky="ew")

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
    # Lógica del árbol
    # ------------------------------------------------------------------
    def _actualizar_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        filtro = self.entry_buscar.get().strip().lower()

        try:
            expedientes = db.obtener_todos()
        except Exception:
            return

        count = 0
        for exp in expedientes:
            num       = exp["numero"]
            anio      = exp["anio"]
            caratula  = exp.get("caratula") or ""
            resultado = exp.get("caja_se_presenta") or "—"
            fecha     = (exp.get("fecha_analisis") or "")[:10]

            # Filtro
            if filtro:
                haystack = f"{num} {anio} {caratula}".lower()
                # También busca en nombres de participantes y abogados
                for p in exp.get("participantes", []):
                    haystack += f" {p.get('nombre','')}".lower()
                    for ab in p.get("abogados", []):
                        haystack += f" {ab.get('nombre','')}".lower()
                if filtro not in haystack:
                    continue

            tag_exp = ("exp_si"    if resultado == "Si"
                  else "exp_no"    if resultado == "No"
                  else "exp_error")

            caratula_txt = (caratula[:55] + "…") if len(caratula) > 55 else caratula

            exp_node = self.tree.insert(
                "", "end",
                text=f"  {num} / {anio}",
                values=("", resultado, caratula_txt, ""),
                tags=(tag_exp,),
                open=False,
            )

            participantes = exp.get("participantes", [])
            if not participantes:
                self.tree.insert(exp_node, "end",
                    text="    (sin intervinientes registrados)",
                    values=("", "", "", ""),
                    tags=("abogado",))
            else:
                for p in participantes:
                    tipo   = p.get("tipo") or ""
                    nombre = p.get("nombre") or ""

                    tl = tipo.lower()
                    if "actor" in tl or "actora" in tl:
                        ptag = "actor"
                    elif "demandado" in tl:
                        ptag = "demandado"
                    elif "tercero" in tl:
                        ptag = "tercero"
                    else:
                        ptag = "parte"

                    p_node = self.tree.insert(
                        exp_node, "end",
                        text=f"    👤  {nombre}",
                        values=(tipo, "", fecha, ""),
                        tags=(ptag,),
                        open=True,
                    )

                    abogados = p.get("abogados", [])
                    if not abogados:
                        self.tree.insert(p_node, "end",
                            text="        (sin abogados registrados)",
                            values=("", "", "", ""),
                            tags=("abogado",))
                    else:
                        for ab in abogados:
                            self.tree.insert(
                                p_node, "end",
                                text=f"        ⚖  {ab.get('nombre','')}" ,
                                values=("Abogado", "", ab.get("tomo_folio", ""), ab.get("cuit", "")),
                                tags=("abogado",),
                            )

            count += 1

        texto_total = f"{count} expediente(s)"
        if filtro:
            texto_total += f"  (filtrado de {len(expedientes)})"
        self.lbl_total_exp.configure(text=texto_total)

    def _expandir_todo(self):
        def _expand(item):
            self.tree.item(item, open=True)
            for child in self.tree.get_children(item):
                _expand(child)
        for item in self.tree.get_children():
            _expand(item)

    def _contraer_todo(self):
        for item in self.tree.get_children():
            self.tree.item(item, open=False)

    # ------------------------------------------------------------------
    # Proceso: control del scraper
    # ------------------------------------------------------------------
    def _browse_entrada(self):
        path = filedialog.askopenfilename(filetypes=[("Excel", "*.xlsx *.xls")])
        if path:
            self.entry_entrada.delete(0, "end")
            self.entry_entrada.insert(0, path)

    def _browse_salida(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
        if path:
            self.entry_salida.delete(0, "end")
            self.entry_salida.insert(0, path)

    def _start(self):
        usuario  = self.entry_usuario.get().strip()
        password = self.entry_password.get().strip()
        entrada  = self.entry_entrada.get().strip()
        salida   = self.entry_salida.get().strip()
        headless = self.var_headless.get()

        if not usuario or not password:
            self._append_log("[Error] Ingresa usuario y contraseña.\n")
            return
        if not entrada or not salida:
            self._append_log("[Error] Especifica los archivos de entrada y salida.\n")
            return

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress_bar.set(0)
        self.lbl_progreso.configure(text="")

        self._log_stream = _LogStream(self._log_queue)
        sys.stdout = self._log_stream

        self._worker_thread = threading.Thread(
            target=self._run_scraper,
            args=(entrada, salida, usuario, password, headless),
            daemon=True,
        )
        self._worker_thread.start()

    def _stop(self):
        scraper._stop_requested = True
        self.btn_stop.configure(state="disabled")
        self._append_log(
            "\n[GUI] Deteniendo… el expediente actual terminará antes de parar.\n")

    def _run_scraper(self, entrada, salida, usuario, password, headless):
        try:
            scraper.ejecutar_desde_excel(
                archivo_entrada=entrada,
                archivo_salida=salida,
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
        self._append_log("\n[GUI] Proceso finalizado.\n")

    # ------------------------------------------------------------------
    # Log
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
    app = App()
    app.mainloop()
