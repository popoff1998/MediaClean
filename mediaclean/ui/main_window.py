"""
Main window for MediaClean application.
"""

import json
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QIcon, QFont, QPixmap
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QPushButton, QLabel, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QTextEdit, QCheckBox, QComboBox, QListWidget, QListWidgetItem,
    QMessageBox, QSplitter, QStatusBar, QAbstractItemView,
    QRadioButton, QButtonGroup, QStackedWidget, QSpinBox,
)

from mediaclean.scanner import EpisodeFile
from mediaclean.scanner import override_season
from mediaclean.tmdb_client import TMDBClient, TMDBSeries
from mediaclean.omdb_client import OMDBClient
from mediaclean.renamer import plan_renames, sanitize_filename
from mediaclean.constants import DEFAULT_OUTPUT_FOLDER
from mediaclean.ui.workers import ScanWorker, TMDBSearchWorker, TMDBLoadEpisodesWorker, RenameWorker


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MediaClean — Series Organizer for Plex")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)

        # State
        self.episodes: List[EpisodeFile] = []
        self.tmdb_client: Optional[TMDBClient] = None
        self.tmdb_results: List[TMDBSeries] = []
        self.selected_series: Optional[TMDBSeries] = None
        self.source_folder: Optional[Path] = None

        # Workers (keep references to avoid GC)
        self._scan_worker = None
        self._search_worker = None
        self._load_worker = None
        self._rename_worker = None
        self._poster_worker = None

        # Settings
        self.settings = QSettings("MediaClean", "MediaClean")

        self._build_ui()
        self._load_settings()

    # ──────────────────────────── UI CONSTRUCTION ────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(10)

        # ── Title Bar ──
        title_bar = QHBoxLayout()
        lbl_title = QLabel("MediaClean")
        lbl_title.setObjectName("title")
        lbl_subtitle = QLabel("Organiza tus series para Plex")
        lbl_subtitle.setObjectName("subtitle")
        title_bar.addWidget(lbl_title)
        title_bar.addWidget(lbl_subtitle)
        title_bar.addStretch()
        main_layout.addLayout(title_bar)

        # ── Splitter: left (config) / right (preview) ──
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, stretch=1)

        # LEFT PANEL
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)

        # -- Source folder --
        grp_source = QGroupBox("1. Carpeta de la serie descargada")
        src_layout = QHBoxLayout(grp_source)
        self.txt_source = QLineEdit()
        self.txt_source.setPlaceholderText("Selecciona la carpeta raíz de la serie...")
        self.txt_source.setReadOnly(True)
        self.btn_browse = QPushButton("Explorar…")
        self.btn_browse.clicked.connect(self._on_browse)
        src_layout.addWidget(self.txt_source, stretch=1)
        src_layout.addWidget(self.btn_browse)
        left_layout.addWidget(grp_source)

        # -- Mode selector --
        grp_mode = QGroupBox("2. Identificar serie")
        mode_layout = QVBoxLayout(grp_mode)

        mode_row = QHBoxLayout()
        self.rb_tmdb = QRadioButton("Buscar online")
        self.rb_manual = QRadioButton("Nombre manual")
        self.rb_tmdb.setChecked(True)
        self.mode_group = QButtonGroup()
        self.mode_group.addButton(self.rb_tmdb, 0)
        self.mode_group.addButton(self.rb_manual, 1)
        self.mode_group.idToggled.connect(self._on_mode_changed)
        mode_row.addWidget(self.rb_tmdb)
        mode_row.addWidget(self.rb_manual)
        mode_row.addStretch()
        mode_layout.addLayout(mode_row)

        # Stacked widget: page 0 = TMDB, page 1 = Manual
        self.stack_mode = QStackedWidget()

        # --- Page 0: TMDB / OMDB ---
        page_tmdb = QWidget()
        tmdb_layout = QGridLayout(page_tmdb)
        tmdb_layout.setContentsMargins(0, 4, 0, 0)

        tmdb_layout.addWidget(QLabel("Proveedor:"), 0, 0)
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(["TMDB", "OMDB"])
        self.cmb_provider.setToolTip(
            "TMDB: themoviedb.org (títulos en varios idiomas)\n"
            "OMDB: omdbapi.com (más fácil de obtener API key, títulos en inglés)"
        )
        self.cmb_provider.currentIndexChanged.connect(self._on_provider_changed)
        tmdb_layout.addWidget(self.cmb_provider, 0, 1)

        tmdb_layout.addWidget(QLabel("API Key:"), 1, 0)
        self.txt_api_key = QLineEdit()
        self.txt_api_key.setPlaceholderText("Tu API key de TMDB (themoviedb.org)")
        self.txt_api_key.setEchoMode(QLineEdit.Password)
        tmdb_layout.addWidget(self.txt_api_key, 1, 1, 1, 2)

        self.lbl_language = QLabel("Idioma:")
        tmdb_layout.addWidget(self.lbl_language, 2, 0)
        self.cmb_language = QComboBox()
        self.cmb_language.addItems(["es-ES", "en-US", "pt-BR", "fr-FR", "de-DE", "it-IT"])
        tmdb_layout.addWidget(self.cmb_language, 2, 1)

        tmdb_layout.addWidget(QLabel("Buscar serie:"), 3, 0)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Nombre de la serie...")
        self.txt_search.returnPressed.connect(self._on_search_tmdb)
        tmdb_layout.addWidget(self.txt_search, 3, 1)
        self.btn_search = QPushButton("Buscar")
        self.btn_search.clicked.connect(self._on_search_tmdb)
        tmdb_layout.addWidget(self.btn_search, 3, 2)

        self.stack_mode.addWidget(page_tmdb)

        # --- Page 1: Manual ---
        page_manual = QWidget()
        manual_layout = QVBoxLayout(page_manual)
        manual_layout.setContentsMargins(0, 4, 0, 0)

        manual_layout.addWidget(QLabel("Nombre de la serie (tal como quieres que aparezca en Plex):"))
        self.txt_manual_name = QLineEdit()
        self.txt_manual_name.setPlaceholderText("Ej: Breaking Bad")
        manual_layout.addWidget(self.txt_manual_name)

        self.btn_apply_manual = QPushButton("Aplicar nombre manual")
        self.btn_apply_manual.clicked.connect(self._on_apply_manual)
        manual_layout.addWidget(self.btn_apply_manual)
        manual_layout.addStretch()

        self.stack_mode.addWidget(page_manual)

        mode_layout.addWidget(self.stack_mode)
        left_layout.addWidget(grp_mode)

        # -- TMDB Results --
        self.grp_results = QGroupBox("3. Seleccionar serie")
        res_layout = QVBoxLayout(self.grp_results)
        self.list_results = QListWidget()
        self.list_results.itemClicked.connect(self._on_series_selected)
        res_layout.addWidget(self.list_results)

        # Series info row: poster thumbnail + text
        self.info_frame = QWidget()
        self.info_frame.setMinimumHeight(110)
        self.info_frame.setVisible(False)
        info_row = QHBoxLayout(self.info_frame)
        info_row.setContentsMargins(4, 4, 4, 4)
        info_row.setSpacing(10)

        self.lbl_poster = QLabel()
        self.lbl_poster.setFixedSize(68, 100)
        self.lbl_poster.setAlignment(Qt.AlignCenter)
        self.lbl_poster.setStyleSheet(
            "background-color: #313244; border: 1px solid #45475a; border-radius: 4px;"
            "font-size: 11px; color: #6c7086;"
        )
        info_row.addWidget(self.lbl_poster, alignment=Qt.AlignTop)

        self.lbl_series_info = QLabel("")
        self.lbl_series_info.setWordWrap(True)
        self.lbl_series_info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        info_row.addWidget(self.lbl_series_info, stretch=1)

        res_layout.addWidget(self.info_frame)
        left_layout.addWidget(self.grp_results)

        # -- Options --
        grp_options = QGroupBox("4. Opciones")
        opt_layout = QVBoxLayout(grp_options)

        # Season override
        season_row = QHBoxLayout()
        self.chk_force_season = QCheckBox("Forzar temporada:")
        self.chk_force_season.setToolTip(
            "Marca esta casilla para forzar la temporada de todos los episodios.\n"
            "Útil cuando la detección automática no acierta."
        )
        self.chk_force_season.toggled.connect(self._on_force_season_toggled)
        season_row.addWidget(self.chk_force_season)
        self.spn_season = QSpinBox()
        self.spn_season.setMinimum(1)
        self.spn_season.setMaximum(99)
        self.spn_season.setValue(1)
        self.spn_season.setEnabled(False)
        self.spn_season.setFixedWidth(70)
        season_row.addWidget(self.spn_season)
        self.btn_apply_season = QPushButton("Aplicar")
        self.btn_apply_season.setEnabled(False)
        self.btn_apply_season.setFixedWidth(80)
        self.btn_apply_season.clicked.connect(self._on_apply_season_override)
        season_row.addWidget(self.btn_apply_season)
        season_row.addStretch()
        opt_layout.addLayout(season_row)

        # File operation mode
        mode_file_row = QHBoxLayout()
        mode_file_row.addWidget(QLabel("Operación:"))
        self.rb_copy = QRadioButton("Copiar (conserva originales)")
        self.rb_move = QRadioButton("Mover (elimina originales)")
        self.rb_copy.setChecked(True)
        self.file_mode_group = QButtonGroup()
        self.file_mode_group.addButton(self.rb_copy, 0)
        self.file_mode_group.addButton(self.rb_move, 1)
        mode_file_row.addWidget(self.rb_copy)
        mode_file_row.addWidget(self.rb_move)
        mode_file_row.addStretch()
        opt_layout.addLayout(mode_file_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Carpeta de salida:"))
        self.txt_output = QLineEdit()
        self.txt_output.setPlaceholderText(DEFAULT_OUTPUT_FOLDER)
        out_row.addWidget(self.txt_output, stretch=1)
        self.btn_browse_output = QPushButton("…")
        self.btn_browse_output.setFixedWidth(40)
        self.btn_browse_output.clicked.connect(self._on_browse_output)
        out_row.addWidget(self.btn_browse_output)
        opt_layout.addLayout(out_row)

        left_layout.addWidget(grp_options)
        left_layout.addStretch()

        splitter.addWidget(left_panel)

        # RIGHT PANEL
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 0, 0, 0)

        # -- Episodes table --
        grp_preview = QGroupBox("Vista previa de renombrado")
        preview_layout = QVBoxLayout(grp_preview)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Archivo original", "Temporada", "Episodio", "Título episodio", "Nuevo nombre"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # Allow editing only season (col 1) and episode (col 2)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        self.table.cellChanged.connect(self._on_cell_changed)
        preview_layout.addWidget(self.table)

        # Action buttons row
        btn_row = QHBoxLayout()
        self.btn_scan = QPushButton("Escanear carpeta")
        self.btn_scan.clicked.connect(self._on_scan)
        self.btn_scan.setEnabled(False)
        btn_row.addWidget(self.btn_scan)

        self.btn_preview = QPushButton("Previsualizar renombrado")
        self.btn_preview.clicked.connect(self._on_preview)
        self.btn_preview.setEnabled(False)
        btn_row.addWidget(self.btn_preview)

        self.btn_execute = QPushButton("Ejecutar")
        self.btn_execute.setObjectName("btnSuccess")
        self.btn_execute.clicked.connect(self._on_execute)
        self.btn_execute.setEnabled(False)
        btn_row.addWidget(self.btn_execute)

        preview_layout.addLayout(btn_row)
        right_layout.addWidget(grp_preview, stretch=1)

        # -- Progress --
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        right_layout.addWidget(self.progress_bar)

        # -- Log --
        grp_log = QGroupBox("Registro de operaciones")
        log_layout = QVBoxLayout(grp_log)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMaximumHeight(160)
        log_layout.addWidget(self.txt_log)
        right_layout.addWidget(grp_log)

        splitter.addWidget(right_panel)
        splitter.setSizes([380, 620])

        # Status bar
        self.statusBar().showMessage("Listo")

    # ──────────────────────────── SETTINGS ────────────────────────────

    def _load_settings(self):
        provider = self.settings.value("api_provider", "TMDB")
        idx_prov = self.cmb_provider.findText(provider)
        if idx_prov >= 0:
            self.cmb_provider.setCurrentIndex(idx_prov)
        # Ensure provider UI is synced (handles case where index didn't change)
        self._on_provider_changed(self.cmb_provider.currentIndex())

        lang = self.settings.value("tmdb_language", "es-ES")
        last_output = self.settings.value("last_output_dir", "")
        idx = self.cmb_language.findText(lang)
        if idx >= 0:
            self.cmb_language.setCurrentIndex(idx)
        if last_output:
            self.txt_output.setText(last_output)

    def _save_settings(self):
        provider = self.cmb_provider.currentText()
        self.settings.setValue("api_provider", provider)
        if provider == "OMDB":
            self.settings.setValue("omdb_api_key", self.txt_api_key.text().strip())
        else:
            self.settings.setValue("tmdb_api_key", self.txt_api_key.text().strip())
        self.settings.setValue("tmdb_language", self.cmb_language.currentText())
        if self.source_folder:
            self.settings.setValue("last_browse_dir", str(self.source_folder.parent))
        output_text = self.txt_output.text().strip()
        if output_text:
            self.settings.setValue("last_output_dir", output_text)

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)

    # ──────────────────────────── LOG HELPERS ────────────────────────────

    def _log(self, msg: str):
        self.txt_log.append(msg)

    def _log_error(self, msg: str):
        self.txt_log.append(f'<span style="color:#f38ba8;">ERROR: {msg}</span>')

    def _log_success(self, msg: str):
        self.txt_log.append(f'<span style="color:#a6e3a1;">{msg}</span>')

    # ──────────────────────────── ACTIONS ────────────────────────────

    def _on_mode_changed(self, button_id: int, checked: bool):
        """Toggle between TMDB and Manual mode."""
        if not checked:
            return
        self.stack_mode.setCurrentIndex(button_id)
        self.grp_results.setVisible(button_id == 0)
        if button_id == 1:
            # Clear TMDB selection and allow manual preview
            self.selected_series = None
            self.lbl_series_info.setText("")
            self.lbl_poster.clear()
            self.info_frame.setVisible(False)
            self.list_results.clear()

    def _on_apply_manual(self):
        """Apply a manually entered series name."""
        name = self.txt_manual_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Nombre vacío", "Escribe el nombre de la serie.")
            return

        # Create a minimal TMDBSeries with just the name (no episode titles)
        self.selected_series = TMDBSeries(
            tmdb_id=0,
            name=name,
        )
        self.lbl_series_info.setText("")
        self.btn_preview.setEnabled(True)
        self._log(f"Nombre manual aplicado: <b>{name}</b>")
        self.statusBar().showMessage(f"Serie: {name} (manual)")

        # Auto-preview if we already have scanned episodes
        if self.episodes:
            self._on_preview()

    def _on_browse(self):
        # Start in the last browsed parent directory
        start_dir = self.settings.value("last_browse_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de la serie",
            start_dir,
        )
        if folder:
            self.source_folder = Path(folder)
            self.txt_source.setText(folder)
            self.btn_scan.setEnabled(True)
            # Save parent for next time
            self.settings.setValue("last_browse_dir", str(self.source_folder.parent))
            # Auto-fill search / manual name with folder name guess
            from mediaclean.scanner import guess_series_name
            guess = guess_series_name(self.source_folder.name)
            if guess:
                self.txt_search.setText(guess)
                self.txt_manual_name.setText(guess)
            self._log(f"Carpeta seleccionada: {folder}")

    def _on_browse_output(self):
        start_dir = self.txt_output.text().strip() or self.settings.value("last_output_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta de salida",
            start_dir,
        )
        if folder:
            self.txt_output.setText(folder)
            self.settings.setValue("last_output_dir", folder)

    def _on_scan(self):
        if not self.source_folder:
            return
        self.statusBar().showMessage("Escaneando carpeta…")
        self.btn_scan.setEnabled(False)

        self._scan_worker = ScanWorker(self.source_folder)
        self._scan_worker.finished.connect(self._on_scan_finished)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _on_scan_finished(self, episodes: List[EpisodeFile]):
        self.episodes = episodes
        self._update_table()
        self.btn_scan.setEnabled(True)

        rar_count = sum(1 for e in episodes if e.needs_extract)
        vid_count = len(episodes) - rar_count
        status_parts = []
        if vid_count:
            status_parts.append(f"{vid_count} vídeo(s)")
        if rar_count:
            status_parts.append(f"{rar_count} RAR(s)")
        summary = ", ".join(status_parts) if status_parts else "0 archivos"
        self.statusBar().showMessage(f"Encontrados: {summary}")
        self._log(f"Escaneados: {summary}")

        if not episodes:
            QMessageBox.information(
                self, "Sin resultados",
                "No se encontraron archivos de vídeo en la carpeta seleccionada."
            )
        else:
            # Auto-detect dominant season and show it in the spinner
            seasons = [ep.season for ep in episodes if ep.season is not None]
            if seasons:
                from collections import Counter
                dominant = Counter(seasons).most_common(1)[0][0]
                self.spn_season.setValue(dominant)
                self._log(f"Temporada detectada: <b>{dominant}</b> "
                          f"(puedes cambiarla en Opciones o editar la tabla)")

    def _on_scan_error(self, msg: str):
        self.btn_scan.setEnabled(True)
        self._log_error(msg)
        self.statusBar().showMessage("Error al escanear")

    def _on_provider_changed(self, index: int):
        """Toggle UI elements based on selected API provider."""
        is_omdb = (self.cmb_provider.currentText() == "OMDB")
        # Save current key before switching
        current_key = self.txt_api_key.text().strip()
        if current_key:
            if is_omdb:
                # Was TMDB, save its key
                self.settings.setValue("tmdb_api_key", current_key)
            else:
                # Was OMDB, save its key
                self.settings.setValue("omdb_api_key", current_key)

        if is_omdb:
            self.txt_api_key.setPlaceholderText("Tu API key de OMDB (omdbapi.com/apikey.aspx)")
            stored_key = self.settings.value("omdb_api_key", "")
            self.lbl_language.setVisible(False)
            self.cmb_language.setVisible(False)
        else:
            self.txt_api_key.setPlaceholderText("Tu API key de TMDB (themoviedb.org)")
            stored_key = self.settings.value("tmdb_api_key", "")
            self.lbl_language.setVisible(True)
            self.cmb_language.setVisible(True)

        self.txt_api_key.setText(stored_key if stored_key else "")
        # Reset client so it gets re-created with the right provider
        self.tmdb_client = None

    def _ensure_tmdb_client(self) -> bool:
        api_key = self.txt_api_key.text().strip()
        provider = self.cmb_provider.currentText()

        if not api_key:
            if provider == "OMDB":
                QMessageBox.warning(
                    self, "API Key requerida",
                    "Introduce tu API Key de OMDB.\n\n"
                    "Puedes obtener una gratis en:\nhttps://www.omdbapi.com/apikey.aspx"
                )
            else:
                QMessageBox.warning(
                    self, "API Key requerida",
                    "Introduce tu API Key de TMDB.\n\n"
                    "Puedes obtener una gratis en:\nhttps://www.themoviedb.org/settings/api"
                )
            return False

        lang = self.cmb_language.currentText()
        if provider == "OMDB":
            self.tmdb_client = OMDBClient(api_key, language=lang)
        else:
            self.tmdb_client = TMDBClient(api_key, language=lang)
        self._save_settings()
        return True

    def _on_search_tmdb(self):
        query = self.txt_search.text().strip()
        if not query:
            return
        if not self._ensure_tmdb_client():
            return

        self.btn_search.setEnabled(False)
        self.list_results.clear()
        provider = self.cmb_provider.currentText()
        self.statusBar().showMessage(f"Buscando en {provider}…")

        self._search_worker = TMDBSearchWorker(self.tmdb_client, query)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.error.connect(self._on_search_error)
        self._search_worker.start()

    def _on_search_finished(self, results: List[TMDBSeries]):
        self.tmdb_results = results
        self.btn_search.setEnabled(True)
        self.list_results.clear()

        if not results:
            provider = self.cmb_provider.currentText()
            self.statusBar().showMessage(f"Sin resultados en {provider}")
            self._log(f"No se encontraron series en {provider}")
            return

        for s in results:
            year = s.first_air_date[:4] if s.first_air_date else "?"
            item = QListWidgetItem(f"{s.name} ({year}) — {s.original_name}")
            item.setData(Qt.UserRole, s.tmdb_id)
            self.list_results.addItem(item)

        self.statusBar().showMessage(f"Encontradas {len(results)} series")
        provider = self.cmb_provider.currentText()
        self._log(f"{provider}: {len(results)} resultados para la búsqueda")

    def _on_search_error(self, msg: str):
        self.btn_search.setEnabled(True)
        provider = self.cmb_provider.currentText()
        self._log_error(f"{provider}: {msg}")
        self.statusBar().showMessage(f"Error en búsqueda {provider}")

    def _on_series_selected(self, item: QListWidgetItem):
        tmdb_id = item.data(Qt.UserRole)
        # Find the series object
        self.selected_series = None
        for s in self.tmdb_results:
            if s.tmdb_id == tmdb_id:
                self.selected_series = s
                break

        if not self.selected_series:
            return

        year = self.selected_series.first_air_date[:4] if self.selected_series.first_air_date else "?"
        overview = self.selected_series.overview
        overview_text = f"<br><i>{overview[:200]}{'…' if len(overview) > 200 else ''}</i>" if overview else ""
        self.lbl_series_info.setText(
            f"<b>{self.selected_series.name}</b> ({year}){overview_text}"
        )
        self.info_frame.setVisible(True)

        # Load poster thumbnail
        self._load_poster(self.selected_series.poster_url)

        # Now load episodes
        if not self._ensure_tmdb_client():
            return

        provider = self.cmb_provider.currentText()
        self.statusBar().showMessage(f"Cargando episodios desde {provider}…")

        # Determine which seasons we need
        seasons_needed = set()
        for ep in self.episodes:
            if ep.season is not None:
                seasons_needed.add(ep.season)
        if not seasons_needed:
            seasons_needed = None  # Load all
        else:
            seasons_needed = sorted(seasons_needed)

        self._load_worker = TMDBLoadEpisodesWorker(
            self.tmdb_client, self.selected_series, seasons_needed
        )
        self._load_worker.finished.connect(self._on_episodes_loaded)
        self._load_worker.error.connect(self._on_episodes_load_error)
        self._load_worker.start()

    def _on_episodes_loaded(self, series: TMDBSeries):
        self.selected_series = series
        self.btn_preview.setEnabled(True)
        n = len(series.episodes)
        provider = self.cmb_provider.currentText()
        self._log_success(f"Cargados {n} episodios de '{series.name}' desde {provider}")
        self.statusBar().showMessage(f"{n} episodios cargados de {provider}")

        # Auto-preview if we already have scanned episodes
        if self.episodes:
            self._on_preview()

    def _on_episodes_load_error(self, msg: str):
        provider = self.cmb_provider.currentText()
        self._log_error(f"{provider} Episodios: {msg}")
        self.statusBar().showMessage("Error cargando episodios")

    @staticmethod
    def _thumbnail_url(url: str) -> str:
        """Rewrite poster URL to request a small thumbnail.

        OMDB returns Amazon-hosted URLs like:
            …/MV5B…._V1_SX300.jpg
        We can replace the size suffix to get a tiny version.
        TMDB URLs use path segments like /w200/ which we swap to /w92/.
        """
        if not url:
            return url
        # Amazon (OMDB): replace SX300 → SX100
        if "media-amazon.com" in url or "_V1_" in url:
            import re
            url = re.sub(r'_V1_.*\.jpg', '_V1_SX100.jpg', url)
        # TMDB: use smallest profile
        elif "/w200/" in url:
            url = url.replace("/w200/", "/w92/")
        return url

    def _load_poster(self, url: str):
        """Download and display the series poster thumbnail."""
        self.lbl_poster.clear()
        if not url:
            self.lbl_poster.setText("")
            return

        self.lbl_poster.setText("⏳")

        thumb_url = self._thumbnail_url(url)
        from mediaclean.ui.workers import PosterWorker
        self._poster_worker = PosterWorker(thumb_url)
        self._poster_worker.finished.connect(self._on_poster_loaded)
        self._poster_worker.error.connect(self._on_poster_error)
        self._poster_worker.start()

    def _on_poster_loaded(self, image_data: bytes):
        """Display the downloaded poster thumbnail."""
        pixmap = QPixmap()
        pixmap.loadFromData(image_data)
        if pixmap.isNull():
            self.lbl_poster.setText("")
            return
        scaled = pixmap.scaled(
            self.lbl_poster.size(),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.lbl_poster.setPixmap(scaled)

    def _on_poster_error(self, msg: str):
        self.lbl_poster.setText("")

    def _on_preview(self):
        if not self.episodes:
            QMessageBox.information(
                self, "Faltan datos",
                "Necesitas escanear una carpeta primero."
            )
            return

        if not self.selected_series:
            # If in manual mode, try to apply the name automatically
            if self.rb_manual.isChecked():
                name = self.txt_manual_name.text().strip()
                if name:
                    self.selected_series = TMDBSeries(tmdb_id=0, name=name)
                else:
                    QMessageBox.information(
                        self, "Faltan datos",
                        "Escribe el nombre de la serie en el campo manual."
                    )
                    return
            else:
                QMessageBox.information(
                    self, "Faltan datos",
                    "Selecciona una serie de TMDB o cambia a modo manual."
                )
                return

        output_base = self._get_output_path()
        plan_renames(self.episodes, self.selected_series, output_base)
        self._update_table()
        self.btn_execute.setEnabled(True)
        self._log("Previsualización generada. Revisa los nombres y pulsa 'Ejecutar' para copiar.")
        self.statusBar().showMessage("Previsualización lista")

    def _on_execute(self):
        planned = [e for e in self.episodes if e.new_path]
        if not planned:
            QMessageBox.warning(self, "Nada que hacer", "No hay archivos para procesar.")
            return

        is_move = self.rb_move.isChecked()
        action_verb = "mover" if is_move else "copiar"
        warning_text = (
            f"Se van a {action_verb} {len(planned)} archivos a la carpeta de salida.\n\n"
        )
        if is_move:
            warning_text += (
                "⚠️ ATENCIÓN: Los archivos originales SE ELIMINARÁN del origen.\n\n"
                "¿Estás seguro?"
            )
        else:
            warning_text += "Los archivos originales NO se modificarán.\n\n¿Continuar?"

        reply = QMessageBox.question(
            self, "Confirmar",
            warning_text,
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_execute.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.btn_preview.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(planned))
        self.statusBar().showMessage("Procesando archivos…")

        file_mode = "move" if is_move else "copy"
        self._rename_worker = RenameWorker(
            self.episodes,
            file_mode=file_mode,
        )
        self._rename_worker.progress.connect(self._on_progress)
        self._rename_worker.finished.connect(self._on_execute_finished)
        self._rename_worker.error.connect(self._on_execute_error)
        self._rename_worker.start()

    def _on_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)

    def _on_execute_finished(self, log_messages: list):
        for msg in log_messages:
            if msg.startswith("ERROR"):
                self._log_error(msg)
            elif msg.startswith("SKIP"):
                self._log(f'<span style="color:#fab387;">{msg}</span>')
            else:
                self._log_success(msg)

        self.progress_bar.setValue(self.progress_bar.maximum())
        self.btn_scan.setEnabled(True)
        self.btn_preview.setEnabled(True)
        self.statusBar().showMessage("¡Proceso completado!")
        self._log_success("═══ Proceso completado con éxito ═══")

        QMessageBox.information(
            self, "Completado",
            "Los archivos se han organizado correctamente.\n"
            "Ya puedes mover la carpeta de salida a tu biblioteca de Plex."
        )

    def _on_execute_error(self, msg: str):
        self._log_error(msg)
        self.btn_execute.setEnabled(True)
        self.btn_scan.setEnabled(True)
        self.statusBar().showMessage("Error durante el procesamiento")

    # ──────────────────────────── HELPERS ────────────────────────────

    def _on_force_season_toggled(self, checked: bool):
        self.spn_season.setEnabled(checked)
        self.btn_apply_season.setEnabled(checked)

    def _on_apply_season_override(self):
        """Apply the forced season to all episodes."""
        if not self.episodes:
            QMessageBox.information(self, "Sin episodios", "Escanea una carpeta primero.")
            return
        new_season = self.spn_season.value()
        override_season(self.episodes, new_season)
        self._update_table()
        self._log(f"Temporada forzada a <b>{new_season}</b> para todos los episodios")
        self.statusBar().showMessage(f"Temporada forzada: {new_season}")
        # Re-run preview if we have a series selected
        if self.selected_series:
            self._on_preview()

    def _on_cell_changed(self, row: int, col: int):
        """
        Handle manual edits to Season (col 1) or Episode (col 2) cells.
        """
        if col not in (1, 2):
            return
        if row >= len(self.episodes):
            return

        item = self.table.item(row, col)
        if item is None:
            return

        try:
            value = int(item.text())
            if value < 0:
                raise ValueError
        except ValueError:
            # Revert to original value
            ep = self.episodes[row]
            if col == 1:
                item.setText(str(ep.season) if ep.season is not None else "?")
            else:
                item.setText(str(ep.episode) if ep.episode is not None else "?")
            return

        ep = self.episodes[row]
        if col == 1:
            ep.season = value
        else:
            ep.episode = value

        # Clear planned rename since data changed
        ep.new_name = None
        ep.new_path = None
        self.table.blockSignals(True)
        self.table.setItem(row, 4, QTableWidgetItem(""))
        self.table.blockSignals(False)

    def _get_output_path(self) -> Path:
        custom = self.txt_output.text().strip()
        if custom:
            return Path(custom)
        if self.source_folder:
            return self.source_folder.parent / DEFAULT_OUTPUT_FOLDER
        return Path.home() / DEFAULT_OUTPUT_FOLDER

    def _update_table(self):
        self.table.blockSignals(True)  # Prevent cellChanged during population
        self.table.setRowCount(len(self.episodes))
        for row, ep in enumerate(self.episodes):
            # Original filename (read-only)
            label = ep.original_path.name
            if ep.needs_extract:
                label = f"📦 {label}"
            item_orig = QTableWidgetItem(label)
            item_orig.setFlags(item_orig.flags() & ~Qt.ItemIsEditable)
            if ep.needs_extract:
                item_orig.setToolTip("Archivo comprimido (RAR) — se extraerá automáticamente")
            self.table.setItem(row, 0, item_orig)

            # Season (EDITABLE)
            s_text = str(ep.season) if ep.season is not None else "?"
            item_s = QTableWidgetItem(s_text)
            item_s.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, item_s)

            # Episode (EDITABLE)
            e_text = str(ep.episode) if ep.episode is not None else "?"
            item_e = QTableWidgetItem(e_text)
            item_e.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, item_e)

            # TMDB episode name (read-only)
            tmdb_name = ""
            if self.selected_series and ep.season is not None and ep.episode is not None:
                tmdb_ep = self.selected_series.get_episode(ep.season, ep.episode)
                if tmdb_ep:
                    tmdb_name = tmdb_ep.name
            item_tmdb = QTableWidgetItem(tmdb_name)
            item_tmdb.setFlags(item_tmdb.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 3, item_tmdb)

            # New filename (read-only)
            new_name = ep.new_name if ep.new_name else ""
            item_new = QTableWidgetItem(new_name)
            item_new.setFlags(item_new.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 4, item_new)

        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        # Re-stretch first and last columns
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
