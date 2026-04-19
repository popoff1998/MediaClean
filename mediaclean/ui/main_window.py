"""
Main window for MediaClean application.
"""

import json
import re
import csv
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QIcon, QFont, QPixmap, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QPushButton, QLabel, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressBar,
    QTextEdit, QCheckBox, QComboBox, QListWidget, QListWidgetItem,
    QMessageBox, QSplitter, QStatusBar, QAbstractItemView,
    QRadioButton, QButtonGroup, QStackedWidget, QSpinBox,
)

from mediaclean.scanner import EpisodeFile
from mediaclean.scanner import guess_series_name_from_path
from mediaclean.scanner import override_season
from mediaclean.tmdb_client import TMDBSeries
from mediaclean.omdb_client import OMDBClient
from mediaclean.tvdb_client import TVDBClient
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
        self.tmdb_client: Optional[object] = None
        self.tmdb_results: List[TMDBSeries] = []
        self.selected_series: Optional[TMDBSeries] = None
        self.source_folder: Optional[Path] = None
        self.series_assignments: Dict[str, TMDBSeries] = {}
        self.series_assignment_type: Dict[str, str] = {}
        self.episode_groups: Dict[str, List[EpisodeFile]] = {}
        self.group_labels: Dict[str, str] = {}
        self.single_mode_suggestions: List[str] = []
        self.single_mode_suggestion_scores: Dict[str, int] = {}
        self.active_group_key: Optional[str] = None
        self._pending_assignment_group: Optional[str] = None

        # Workers (keep references to avoid GC)
        self._scan_worker = None
        self._search_worker = None
        self._load_worker = None
        self._rename_worker = None
        self._poster_worker = None
        self._active_poster_workers: List[object] = []
        self._poster_request_seq = 0
        self._last_poster_url = ""

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
        grp_batch_mode = QGroupBox("2. Tipo de lote")
        batch_mode_layout = QHBoxLayout(grp_batch_mode)
        self.rb_single_batch = QRadioButton("Serie única")
        self.rb_multi_batch = QRadioButton("Múltiples series")
        self.rb_single_batch.setChecked(True)
        self.batch_mode_group = QButtonGroup()
        self.batch_mode_group.addButton(self.rb_single_batch, 0)
        self.batch_mode_group.addButton(self.rb_multi_batch, 1)
        self.batch_mode_group.idToggled.connect(self._on_batch_mode_changed)
        batch_mode_layout.addWidget(self.rb_single_batch)
        batch_mode_layout.addWidget(self.rb_multi_batch)
        batch_mode_layout.addStretch()
        left_layout.addWidget(grp_batch_mode)

        grp_mode = QGroupBox("3. Identificar serie")
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

        suggestion_row = QHBoxLayout()
        self.lbl_single_suggestion = QLabel("Sugerencia detectada:")
        self.cmb_single_suggestion = QComboBox()
        self.cmb_single_suggestion.setToolTip(
            "Sugerencias inferidas por el nombre de los ficheros detectados."
        )
        self.cmb_single_suggestion.currentIndexChanged.connect(self._on_single_suggestion_changed)
        self.btn_use_single_suggestion = QPushButton("Usar")
        self.btn_use_single_suggestion.setFixedWidth(70)
        self.btn_use_single_suggestion.clicked.connect(self._on_use_single_suggestion)
        suggestion_row.addWidget(self.lbl_single_suggestion)
        suggestion_row.addWidget(self.cmb_single_suggestion, stretch=1)
        suggestion_row.addWidget(self.btn_use_single_suggestion)
        mode_layout.addLayout(suggestion_row)

        # Stacked widget: page 0 = búsqueda online, page 1 = Manual
        self.stack_mode = QStackedWidget()

        # --- Page 0: TVDB / OMDB ---
        page_tmdb = QWidget()
        tmdb_layout = QGridLayout(page_tmdb)
        tmdb_layout.setContentsMargins(0, 4, 0, 0)

        tmdb_layout.addWidget(QLabel("Proveedor:"), 0, 0)
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItems(["TVDB", "OMDB"])
        self.cmb_provider.setToolTip(
            "TVDB: TheTVDB v4 (series y episodios, con traducciones y temporadas)\n"
            "OMDB: omdbapi.com (más fácil de obtener API key, títulos en inglés)"
        )
        self.cmb_provider.currentIndexChanged.connect(self._on_provider_changed)
        tmdb_layout.addWidget(self.cmb_provider, 0, 1)

        tmdb_layout.addWidget(QLabel("API Key:"), 1, 0)
        self.txt_api_key = QLineEdit()
        self.txt_api_key.setPlaceholderText("Tu API key de TVDB")
        self.txt_api_key.setEchoMode(QLineEdit.Password)
        tmdb_layout.addWidget(self.txt_api_key, 1, 1, 1, 2)

        self.lbl_tvdb_pin = QLabel("PIN TVDB:")
        tmdb_layout.addWidget(self.lbl_tvdb_pin, 2, 0)
        self.txt_tvdb_pin = QLineEdit()
        self.txt_tvdb_pin.setPlaceholderText("Opcional, si tu clave de TVDB lo requiere")
        self.txt_tvdb_pin.setEchoMode(QLineEdit.Password)
        tmdb_layout.addWidget(self.txt_tvdb_pin, 2, 1, 1, 2)

        self.lbl_language = QLabel("Idioma:")
        tmdb_layout.addWidget(self.lbl_language, 3, 0)
        self.cmb_language = QComboBox()
        self.cmb_language.addItems(["es-ES", "en-US", "pt-BR", "fr-FR", "de-DE", "it-IT"])
        tmdb_layout.addWidget(self.cmb_language, 3, 1)

        tmdb_layout.addWidget(QLabel("Buscar serie:"), 4, 0)
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Nombre de la serie...")
        self.txt_search.returnPressed.connect(self._on_search_tmdb)
        tmdb_layout.addWidget(self.txt_search, 4, 1)
        self.btn_search = QPushButton("Buscar")
        self.btn_search.clicked.connect(self._on_search_tmdb)
        tmdb_layout.addWidget(self.btn_search, 4, 2)

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

        # -- Online results --
        self.grp_results = QGroupBox("3. Seleccionar serie")
        res_layout = QVBoxLayout(self.grp_results)

        group_row = QHBoxLayout()
        self.lbl_series_group = QLabel("Grupo detectado:")
        self.cmb_series_group = QComboBox()
        self.cmb_series_group.currentIndexChanged.connect(self._on_series_group_changed)
        self.btn_next_unassigned = QPushButton("Siguiente sin asignar")
        self.btn_next_unassigned.setToolTip(
            "Ir al siguiente grupo que aún no esté confirmado online (TVDB/OMDB).\n"
            "Estados: ✓ confirmada online, ~ asignación manual, sin marca = provisional."
        )
        self.btn_next_unassigned.setShortcut("Ctrl+J")
        self.btn_next_unassigned.clicked.connect(self._on_next_unassigned_group)
        self.lbl_group_summary = QLabel("")
        self.lbl_group_summary.setToolTip("Resumen de grupos: online, manual y pendientes de confirmar online")
        group_row.addWidget(self.lbl_series_group)
        group_row.addWidget(self.cmb_series_group, stretch=1)
        group_row.addWidget(self.btn_next_unassigned)
        group_row.addWidget(self.lbl_group_summary)
        res_layout.addLayout(group_row)

        self.list_results = QListWidget()
        self.list_results.setMouseTracking(True)
        self.list_results.viewport().setMouseTracking(True)
        self.list_results.itemClicked.connect(self._on_series_selected)
        self.list_results.itemEntered.connect(self._on_series_hovered)
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
        grp_options = QGroupBox("5. Opciones")
        opt_layout = QVBoxLayout(grp_options)

        # Season override
        season_row = QHBoxLayout()
        self.chk_force_season = QCheckBox("Forzar temporada:")
        self.chk_force_season.setToolTip(
            "Marca esta casilla para aplicar la misma temporada a todos los episodios.\n"
            "Útil cuando la detección automática no acierta en una carpeta de una sola temporada.\n"
            "Déjala desactivada si el lote contiene varias temporadas."
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
        self.rb_move.setChecked(True)
        self.file_mode_group = QButtonGroup()
        self.file_mode_group.addButton(self.rb_copy, 0)
        self.file_mode_group.addButton(self.rb_move, 1)
        mode_file_row.addWidget(self.rb_copy)
        mode_file_row.addWidget(self.rb_move)
        mode_file_row.addStretch()
        opt_layout.addLayout(mode_file_row)

        self.chk_dry_run = QCheckBox("Modo simulación (dry-run, sin copiar/mover)")
        self.chk_dry_run.setToolTip(
            "Valida y muestra la operación final, pero no modifica archivos.\n"
            "Recomendado para una primera prueba real."
        )
        opt_layout.addWidget(self.chk_dry_run)

        self.chk_export_report = QCheckBox("Exportar reporte CSV al finalizar")
        self.chk_export_report.setChecked(True)
        self.chk_export_report.setToolTip(
            "Genera un CSV con origen, destino, serie asignada y estado final."
        )
        opt_layout.addWidget(self.chk_export_report)

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
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Archivo original", "Temporada", "Episodio", "Título episodio", "Serie asignada", "Nuevo nombre"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
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
        self.lbl_series_group.setVisible(False)
        self.cmb_series_group.setVisible(False)
        self.btn_next_unassigned.setVisible(False)
        self.lbl_group_summary.setVisible(False)
        self.lbl_single_suggestion.setVisible(False)
        self.cmb_single_suggestion.setVisible(False)
        self.btn_use_single_suggestion.setVisible(False)

    # ──────────────────────────── SETTINGS ────────────────────────────

    def _load_settings(self):
        provider = self.settings.value("api_provider", "TVDB")
        if provider == "TMDB":
            provider = "TVDB"
        idx_prov = self.cmb_provider.findText(provider)
        if idx_prov >= 0:
            self.cmb_provider.setCurrentIndex(idx_prov)
        # Ensure provider UI is synced (handles case where index didn't change)
        self._on_provider_changed(self.cmb_provider.currentIndex())

        lang = self.settings.value("tvdb_language", self.settings.value("tmdb_language", "es-ES"))
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
            self.settings.setValue("tvdb_api_key", self.txt_api_key.text().strip())
            self.settings.setValue("tvdb_pin", self.txt_tvdb_pin.text().strip())
        self.settings.setValue("tvdb_language", self.cmb_language.currentText())
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

    def _on_batch_mode_changed(self, button_id: int, checked: bool):
        """Toggle between single-series and multi-series batch modes."""
        if not checked:
            return

        is_multi = (button_id == 1)
        self._refresh_group_selector(keep_current=True)

        if not is_multi:
            self.active_group_key = None
            self._refresh_single_suggestion_selector(keep_current=True)
            self._apply_single_mode_suggestion()
        else:
            if self.episode_groups and self.active_group_key is None:
                self.active_group_key = next(iter(self.episode_groups.keys()))
            self._sync_inputs_with_active_group()
            self._refresh_single_suggestion_selector(keep_current=True)

        self.btn_execute.setEnabled(False)

    @staticmethod
    def _group_key(name: str) -> str:
        normalized = re.sub(r"\s+", " ", name.strip())
        normalized = re.sub(r"[^A-Za-zÀ-ÿ0-9 ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip().lower()
        return normalized or "serie-desconocida"

    def _build_episode_groups(self):
        self.episode_groups = {}
        self.group_labels = {}

        for ep in self.episodes:
            label = ep.series_guess.strip() if ep.series_guess.strip() else "Serie desconocida"
            key = self._group_key(label)
            if key not in self.group_labels:
                self.group_labels[key] = label
            self.episode_groups.setdefault(key, []).append(ep)

        ordered_keys = sorted(self.episode_groups.keys(), key=lambda k: self.group_labels.get(k, "").lower())
        self.episode_groups = {key: self.episode_groups[key] for key in ordered_keys}

        if self.active_group_key not in self.episode_groups:
            self.active_group_key = ordered_keys[0] if ordered_keys else None

        self._build_single_mode_suggestions()

    def _build_single_mode_suggestions(self):
        suggestions: List[str] = []
        scores: Dict[str, int] = {}
        if self.episode_groups:
            total = max(sum(len(items) for items in self.episode_groups.values()), 1)
            ordered = sorted(
                self.episode_groups.keys(),
                key=lambda key: (-len(self.episode_groups[key]), self.group_labels.get(key, "").lower()),
            )
            for key in ordered:
                label = self.group_labels.get(key, "").strip()
                if label and label not in suggestions:
                    suggestions.append(label)
                    score = int(round((len(self.episode_groups[key]) / total) * 100))
                    scores[label] = max(1, min(score, 100))

        if not suggestions and self.source_folder:
            fallback = guess_series_name_from_path(self.source_folder).strip()
            if fallback:
                suggestions.append(fallback)
                scores[fallback] = 100

        self.single_mode_suggestions = suggestions
        self.single_mode_suggestion_scores = scores
        self._refresh_single_suggestion_selector(keep_current=True)

    def _refresh_single_suggestion_selector(self, keep_current: bool = False):
        current = self.cmb_single_suggestion.currentData(Qt.UserRole) if keep_current else ""
        self.cmb_single_suggestion.blockSignals(True)
        self.cmb_single_suggestion.clear()

        for name in self.single_mode_suggestions:
            score = self.single_mode_suggestion_scores.get(name)
            if score is None:
                text = name
            else:
                text = f"{name} ({score}%)"
            self.cmb_single_suggestion.addItem(text)
            idx = self.cmb_single_suggestion.count() - 1
            self.cmb_single_suggestion.setItemData(idx, name, Qt.UserRole)

        if current:
            for idx in range(self.cmb_single_suggestion.count()):
                if self.cmb_single_suggestion.itemData(idx, Qt.UserRole) == current:
                    self.cmb_single_suggestion.setCurrentIndex(idx)
                    break

        self.cmb_single_suggestion.blockSignals(False)

        visible = self.rb_single_batch.isChecked() and self.cmb_single_suggestion.count() > 0
        self.lbl_single_suggestion.setVisible(visible)
        self.cmb_single_suggestion.setVisible(visible)
        self.btn_use_single_suggestion.setVisible(visible)
        self.btn_use_single_suggestion.setEnabled(visible)

    def _on_single_suggestion_changed(self, index: int):
        if index < 0:
            return
        if not self.rb_single_batch.isChecked():
            return
        self._apply_selected_single_suggestion()

    def _on_use_single_suggestion(self):
        self._apply_selected_single_suggestion()

    def _apply_selected_single_suggestion(self):
        if not self.rb_single_batch.isChecked():
            return
        suggestion = self.cmb_single_suggestion.currentData(Qt.UserRole)
        suggestion = str(suggestion).strip() if suggestion else ""
        if suggestion:
            self.txt_search.setText(suggestion)
            self.txt_manual_name.setText(suggestion)
            self._log(f"Sugerencia aplicada: <b>{suggestion}</b>")

    def _single_mode_guess(self) -> str:
        """Pick the best single-series suggestion using detected file-based groups."""
        selected = self.cmb_single_suggestion.currentData(Qt.UserRole)
        selected = str(selected).strip() if selected else ""
        if selected:
            return selected

        if self.episode_groups:
            best_key = max(
                self.episode_groups,
                key=lambda key: (len(self.episode_groups[key]), self.group_labels.get(key, "").lower()),
            )
            return self.group_labels.get(best_key, "")

        if self.source_folder:
            return guess_series_name_from_path(self.source_folder)
        return ""

    def _apply_single_mode_suggestion(self):
        if not self.rb_single_batch.isChecked():
            return
        if not self.single_mode_suggestions:
            self._build_single_mode_suggestions()

        guess = self._single_mode_guess().strip()
        if guess:
            self.txt_search.setText(guess)
            self.txt_manual_name.setText(guess)

        if self.episode_groups and len(self.episode_groups) > 1:
            suggestions = ", ".join(
                f"{self.group_labels.get(key, key)} ({len(self.episode_groups[key])})"
                for key in self.episode_groups
            )
            self._log(
                "Sugerencias por nombre de fichero (modo único): "
                f"<b>{suggestions}</b>"
            )

    def _refresh_group_selector(self, keep_current: bool = False):
        was_key = self.active_group_key if keep_current else None
        self.cmb_series_group.blockSignals(True)
        self.cmb_series_group.clear()

        for key, episodes in self.episode_groups.items():
            label = self.group_labels.get(key, "Serie")
            assignment_type = self.series_assignment_type.get(key, "")
            if assignment_type == "online":
                assigned = " ✓"
            elif assignment_type == "manual":
                assigned = " ~"
            else:
                assigned = ""
            self.cmb_series_group.addItem(f"{label} ({len(episodes)}){assigned}", key)

            # Apply a soft per-item color status in the dropdown.
            idx = self.cmb_series_group.count() - 1
            if assignment_type == "online":
                self.cmb_series_group.setItemData(idx, QColor("#a6e3a1"), Qt.ForegroundRole)
                self.cmb_series_group.setItemData(idx, QColor("#1e2a20"), Qt.BackgroundRole)
            elif assignment_type == "manual":
                self.cmb_series_group.setItemData(idx, QColor("#f9e2af"), Qt.ForegroundRole)
                self.cmb_series_group.setItemData(idx, QColor("#2d2618"), Qt.BackgroundRole)
            else:
                self.cmb_series_group.setItemData(idx, QColor("#cdd6f4"), Qt.ForegroundRole)
                self.cmb_series_group.setItemData(idx, QColor("#1e1e2e"), Qt.BackgroundRole)

        if was_key and was_key in self.episode_groups:
            idx = self.cmb_series_group.findData(was_key)
            if idx >= 0:
                self.cmb_series_group.setCurrentIndex(idx)
                self.active_group_key = was_key
        elif self.cmb_series_group.count() > 0:
            self.cmb_series_group.setCurrentIndex(0)
            self.active_group_key = self.cmb_series_group.currentData()

        self.cmb_series_group.blockSignals(False)
        visible = self.rb_multi_batch.isChecked() and bool(self.episode_groups)
        self.lbl_series_group.setVisible(visible)
        self.cmb_series_group.setVisible(visible)
        self.btn_next_unassigned.setVisible(visible)
        self.lbl_group_summary.setVisible(visible)

        online_count, manual_count, provisional_count, pending_count = self._assignment_counts()
        self.btn_next_unassigned.setEnabled(bool(self._next_unassigned_group_key()))
        self.btn_next_unassigned.setText(f"Siguiente sin confirmar ({pending_count})")
        self.lbl_group_summary.setText(
            f"Online: {online_count} | Manual: {manual_count} | Pendientes: {provisional_count}"
        )

    def _assignment_counts(self) -> tuple[int, int, int, int]:
        online_count = 0
        manual_count = 0
        provisional_count = 0

        for key in self.episode_groups.keys():
            assignment_type = self.series_assignment_type.get(key, "")
            if assignment_type == "online":
                online_count += 1
            elif assignment_type == "manual":
                manual_count += 1
            else:
                provisional_count += 1

        pending_count = provisional_count + manual_count
        return online_count, manual_count, provisional_count, pending_count

    def _next_unassigned_group_key(self) -> Optional[str]:
        for key in self.episode_groups.keys():
            if self.series_assignment_type.get(key) != "online":
                return key
        return None

    def _on_next_unassigned_group(self):
        key = self._next_unassigned_group_key()
        if not key:
            QMessageBox.information(self, "Todo confirmado", "Todos los grupos están confirmados online.")
            return

        idx = self.cmb_series_group.findData(key)
        if idx >= 0:
            self.cmb_series_group.setCurrentIndex(idx)
            label = self.group_labels.get(key, key)
            self._log(f"Grupo pendiente seleccionado: <b>{label}</b>")

    def _sync_inputs_with_active_group(self):
        if not self.rb_multi_batch.isChecked():
            return
        if not self.active_group_key:
            return
        label = self.group_labels.get(self.active_group_key, "")
        if label:
            self.txt_search.setText(label)
            self.txt_manual_name.setText(label)

    def _episodes_for_current_context(self) -> List[EpisodeFile]:
        if not self.rb_multi_batch.isChecked():
            return self.episodes
        if not self.active_group_key:
            return []
        return self.episode_groups.get(self.active_group_key, [])

    def _on_series_group_changed(self, index: int):
        if index < 0:
            return
        key = self.cmb_series_group.itemData(index)
        self.active_group_key = key
        self._sync_inputs_with_active_group()

        assigned = self.series_assignments.get(key)
        assignment_type = self.series_assignment_type.get(key, "provisional")

        if assigned and assignment_type == "online":
            self.selected_series = assigned
            year = assigned.first_air_date[:4] if assigned.first_air_date else "?"
            self.lbl_series_info.setText(f"<b>{assigned.name}</b> ({year})")
            self.info_frame.setVisible(True)
            self._load_poster(assigned.poster_url)
            self.statusBar().showMessage("Grupo confirmado online")
        else:
            self.selected_series = None
            self.lbl_series_info.setText("")
            self.lbl_poster.clear()
            self.lbl_poster.setText("")
            self.info_frame.setVisible(False)
            if assignment_type == "manual":
                self.statusBar().showMessage("Grupo asignado manualmente (pendiente de confirmar online)")
            else:
                self.statusBar().showMessage("Grupo pendiente de confirmar online")

    def _on_apply_manual(self):
        """Apply a manually entered series name."""
        name = self.txt_manual_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Nombre vacío", "Escribe el nombre de la serie.")
            return

        # Create a minimal TMDBSeries with just the name (no episode titles)
        chosen_series = TMDBSeries(
            tmdb_id=0,
            name=name,
        )
        self.selected_series = chosen_series

        if self.rb_multi_batch.isChecked() and self.active_group_key:
            self.series_assignments[self.active_group_key] = chosen_series
            self.series_assignment_type[self.active_group_key] = "manual"
            self._refresh_group_selector(keep_current=True)

        self.lbl_series_info.setText("")
        self.btn_preview.setEnabled(True)
        if self.rb_multi_batch.isChecked() and self.active_group_key:
            group_label = self.group_labels.get(self.active_group_key, self.active_group_key)
            self._log(f"Nombre manual aplicado al grupo <b>{group_label}</b>: <b>{name}</b>")
        else:
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
            # Auto-fill search / manual name with an intelligent guess from the inner structure
            guess = guess_series_name_from_path(self.source_folder)
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
        self.series_assignments = {}
        self.series_assignment_type = {}
        self.selected_series = None
        self._pending_assignment_group = None
        self._build_episode_groups()
        self._maybe_prompt_batch_mode_switch()
        self._refresh_group_selector()
        if self.rb_multi_batch.isChecked():
            self._sync_inputs_with_active_group()
        else:
            self._apply_single_mode_suggestion()

        # Build provisional assignments so preview is always available right after scan.
        if self.rb_multi_batch.isChecked():
            for key, label in self.group_labels.items():
                self.series_assignments[key] = TMDBSeries(tmdb_id=0, name=label)
                self.series_assignment_type[key] = "provisional"
            self._refresh_group_selector(keep_current=True)
        else:
            guess = self._single_mode_guess().strip()
            if guess:
                self.selected_series = TMDBSeries(tmdb_id=0, name=guess)

        self.btn_preview.setEnabled(bool(self.episodes))

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

        if self.episode_groups:
            detected = ", ".join(
                f"{self.group_labels[key]} ({len(self.episode_groups[key])})"
                for key in self.episode_groups
            )
            self._log(f"Series detectadas por afinidad: <b>{detected}</b>")

            if self.rb_multi_batch.isChecked():
                self._log(
                    "Asignación provisional aplicada en modo múltiple. "
                    "Puedes previsualizar ya y luego confirmar online cada grupo."
                )
                self._log("Leyenda grupos: <b>✓</b> online, <b>~</b> manual, sin marca = provisional")
            else:
                selected_name = self.selected_series.name if self.selected_series else ""
                if selected_name:
                    self._log(
                        "Asignación provisional aplicada en modo único: "
                        f"<b>{selected_name}</b>"
                    )

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
                unique_seasons = sorted(set(seasons))
                dominant = Counter(seasons).most_common(1)[0][0]
                self.spn_season.setValue(dominant)
                if len(unique_seasons) == 1:
                    self._log(f"Temporada detectada: <b>{dominant}</b> "
                              f"(puedes cambiarla en Opciones o editar la tabla)")
                else:
                    seasons_text = ", ".join(f"T{s:02d}" for s in unique_seasons)
                    self._log(
                        "Temporadas detectadas automáticamente: "
                        f"<b>{seasons_text}</b>"
                    )
                    self._log(
                        "La carpeta parece contener varias temporadas. "
                        "MediaClean mantendrá cada episodio en su temporada detectada "
                        "mientras no actives 'Forzar temporada'."
                    )

    def _on_scan_error(self, msg: str):
        self.btn_scan.setEnabled(True)
        self._log_error(msg)
        self.statusBar().showMessage("Error al escanear")

    def _maybe_prompt_batch_mode_switch(self):
        """Suggest switching batch mode when scan results strongly indicate the opposite mode."""
        group_count = len(self.episode_groups)
        if group_count == 0:
            return

        is_multi_selected = self.rb_multi_batch.isChecked()

        # Multiple detected groups strongly suggests multi-series mode.
        if not is_multi_selected and group_count > 1:
            reply = QMessageBox.question(
                self,
                "Tipo de lote detectado",
                "MediaClean detectó varias series probables en la carpeta escaneada.\n\n"
                "¿Quieres cambiar automáticamente a 'Múltiples series'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self.rb_multi_batch.setChecked(True)
                self._log("Cambio automático de tipo de lote: <b>Múltiples series</b>")
            return

        # A single detected group suggests single-series mode.
        if is_multi_selected and group_count == 1:
            reply = QMessageBox.question(
                self,
                "Tipo de lote detectado",
                "MediaClean detectó una sola serie probable en la carpeta escaneada.\n\n"
                "¿Quieres cambiar automáticamente a 'Serie única'?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                self.rb_single_batch.setChecked(True)
                self._log("Cambio automático de tipo de lote: <b>Serie única</b>")

    def _on_provider_changed(self, index: int):
        """Toggle UI elements based on selected API provider."""
        is_omdb = (self.cmb_provider.currentText() == "OMDB")
        # Save current key before switching
        current_key = self.txt_api_key.text().strip()
        current_pin = self.txt_tvdb_pin.text().strip()
        if current_key:
            if is_omdb:
                self.settings.setValue("tvdb_api_key", current_key)
                self.settings.setValue("tvdb_pin", current_pin)
            else:
                self.settings.setValue("omdb_api_key", current_key)

        if is_omdb:
            self.txt_api_key.setPlaceholderText("Tu API key de OMDB (omdbapi.com/apikey.aspx)")
            stored_key = self.settings.value("omdb_api_key", "")
            self.lbl_tvdb_pin.setVisible(False)
            self.txt_tvdb_pin.setVisible(False)
            self.lbl_language.setVisible(False)
            self.cmb_language.setVisible(False)
        else:
            self.txt_api_key.setPlaceholderText("Tu API key de TVDB")
            stored_key = self.settings.value("tvdb_api_key", self.settings.value("tmdb_api_key", ""))
            stored_pin = self.settings.value("tvdb_pin", "")
            self.lbl_tvdb_pin.setVisible(True)
            self.txt_tvdb_pin.setVisible(True)
            self.lbl_language.setVisible(True)
            self.cmb_language.setVisible(True)

        self.txt_api_key.setText(stored_key if stored_key else "")
        if is_omdb:
            self.txt_tvdb_pin.setText("")
        else:
            self.txt_tvdb_pin.setText(stored_pin if stored_pin else "")
        # Reset client so it gets re-created with the right provider
        self.tmdb_client = None

    def _ensure_tmdb_client(self) -> bool:
        api_key = self.txt_api_key.text().strip()
        provider = self.cmb_provider.currentText()
        tvdb_pin = self.txt_tvdb_pin.text().strip()

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
                    "Introduce tu API Key de TVDB.\n\n"
                    "Si tu clave requiere PIN, complétalo también en el campo 'PIN TVDB'."
                )
            return False

        lang = self.cmb_language.currentText()
        if provider == "OMDB":
            self.tmdb_client = OMDBClient(api_key, language=lang)
        else:
            self.tmdb_client = TVDBClient(api_key, language=lang, pin=tvdb_pin)
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
            label = f"{s.name} ({year})"
            if s.original_name and s.original_name != s.name:
                label += f" — {s.original_name}"
            item = QListWidgetItem(label)
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

    def _find_series_by_id(self, tmdb_id: int) -> Optional[TMDBSeries]:
        for s in self.tmdb_results:
            if s.tmdb_id == tmdb_id:
                return s
        return None

    def _show_search_series_preview(self, series: TMDBSeries):
        year = series.first_air_date[:4] if series.first_air_date else "?"
        overview = series.overview
        overview_text = f"<br><i>{overview[:200]}{'…' if len(overview) > 200 else ''}</i>" if overview else ""
        self.lbl_series_info.setText(
            f"<b>{series.name}</b> ({year}){overview_text}"
        )
        self.info_frame.setVisible(True)
        self._load_poster(series.poster_url)

    def _on_series_hovered(self, item: QListWidgetItem):
        tmdb_id = item.data(Qt.UserRole)
        hovered = self._find_series_by_id(tmdb_id)
        if not hovered:
            return
        self._show_search_series_preview(hovered)
        provider = self.cmb_provider.currentText()
        self.statusBar().showMessage(f"Previsualizando resultado en {provider} (clic para asignar)")

    def _on_series_selected(self, item: QListWidgetItem):
        tmdb_id = item.data(Qt.UserRole)
        # Find the series object
        self.selected_series = self._find_series_by_id(tmdb_id)

        if not self.selected_series:
            return

        if self.rb_multi_batch.isChecked():
            if not self.active_group_key:
                QMessageBox.information(self, "Grupo no seleccionado", "Selecciona un grupo de serie primero.")
                return
            self._pending_assignment_group = self.active_group_key
        else:
            self._pending_assignment_group = None

        # Keep the same preview behavior when assigning via click.
        self._show_search_series_preview(self.selected_series)

        # Now load episodes
        if not self._ensure_tmdb_client():
            return

        provider = self.cmb_provider.currentText()
        self.statusBar().showMessage(f"Cargando episodios desde {provider}…")

        # Determine which seasons we need
        seasons_needed = set()
        requested_episodes = {}
        for ep in self._episodes_for_current_context():
            if ep.season is not None:
                seasons_needed.add(ep.season)
                if ep.episode is not None:
                    requested_episodes.setdefault(ep.season, set()).add(ep.episode)
        if not seasons_needed:
            seasons_needed = None  # Load all
            requested_episodes = None
        else:
            seasons_needed = sorted(seasons_needed)
            requested_episodes = {
                season: sorted(episodes)
                for season, episodes in requested_episodes.items()
                if episodes
            }

        self._load_worker = TMDBLoadEpisodesWorker(
            self.tmdb_client,
            self.selected_series,
            seasons_needed,
            requested_episodes,
        )
        self._load_worker.finished.connect(self._on_episodes_loaded)
        self._load_worker.error.connect(self._on_episodes_load_error)
        self._load_worker.start()

    def _on_episodes_loaded(self, series: TMDBSeries):
        self.selected_series = series
        if self.rb_multi_batch.isChecked() and self._pending_assignment_group:
            self.series_assignments[self._pending_assignment_group] = series
            self.series_assignment_type[self._pending_assignment_group] = "online"
            group_label = self.group_labels.get(self._pending_assignment_group, self._pending_assignment_group)
            self._log_success(f"Asignada '{series.name}' al grupo '{group_label}'")
            self._refresh_group_selector(keep_current=True)

            next_key = self._next_unassigned_group_key()
            if next_key:
                next_idx = self.cmb_series_group.findData(next_key)
                if next_idx >= 0:
                    self.cmb_series_group.setCurrentIndex(next_idx)
                    next_label = self.group_labels.get(next_key, next_key)
                    self._log(f"Siguiente pendiente: <b>{next_label}</b>")
        self._pending_assignment_group = None

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
        TVDB usually returns full URLs. OMDB often returns Amazon-hosted images.
        Legacy TMDB URLs still use path segments like /w200/ which we swap to /w92/.
        """
        if not url:
            return url
        # Amazon (OMDB): replace SX300 → SX100
        if "media-amazon.com" in url or "_V1_" in url:
            import re
            url = re.sub(r'_V1_.*\.jpg', '_V1_SX100.jpg', url)
        # Legacy TMDB URLs: use smallest profile
        elif "/w200/" in url:
            url = url.replace("/w200/", "/w92/")
        return url

    def _load_poster(self, url: str):
        """Download and display the series poster thumbnail."""
        if not url:
            self._last_poster_url = ""
            self.lbl_poster.clear()
            self.lbl_poster.setText("")
            return

        thumb_url = self._thumbnail_url(url)
        if thumb_url == self._last_poster_url and self.lbl_poster.pixmap() is not None:
            return

        self._last_poster_url = thumb_url
        self._poster_request_seq += 1
        request_seq = self._poster_request_seq
        self.lbl_poster.setText("⏳")

        from mediaclean.ui.workers import PosterWorker
        worker = PosterWorker(thumb_url)
        self._poster_worker = worker
        self._active_poster_workers.append(worker)
        worker.finished.connect(lambda data, w=worker, seq=request_seq: self._on_poster_loaded(data, seq, w))
        worker.error.connect(lambda msg, w=worker, seq=request_seq: self._on_poster_error(msg, seq, w))
        worker.start()

    def _release_poster_worker(self, worker):
        try:
            if worker in self._active_poster_workers:
                self._active_poster_workers.remove(worker)
        except Exception:
            pass

    def _on_poster_loaded(self, image_data: bytes, request_seq: int, worker):
        """Display the downloaded poster thumbnail."""
        self._release_poster_worker(worker)
        if request_seq != self._poster_request_seq:
            return

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

    def _on_poster_error(self, msg: str, request_seq: int, worker):
        self._release_poster_worker(worker)
        if request_seq != self._poster_request_seq:
            return
        # Keep the last visible image if we had one; avoid flashing blank on transient failures.
        if self.lbl_poster.pixmap() is None:
            self.lbl_poster.setText("")

    def _on_preview(self):
        if not self.episodes:
            QMessageBox.information(
                self, "Faltan datos",
                "Necesitas escanear una carpeta primero."
            )
            return

        if self.rb_multi_batch.isChecked():
            if not self.episode_groups:
                QMessageBox.information(self, "Sin grupos", "Escanea una carpeta primero.")
                return

            output_base = self._get_output_path()
            for ep in self.episodes:
                ep.new_name = None
                ep.new_path = None

            missing_groups = []
            for key, group_episodes in self.episode_groups.items():
                assigned_series = self.series_assignments.get(key)
                if not assigned_series:
                    missing_groups.append(self.group_labels.get(key, key))
                    continue
                plan_renames(group_episodes, assigned_series, output_base)

            self._update_table()

            if missing_groups:
                self.btn_execute.setEnabled(False)
                missing_text = ", ".join(missing_groups)
                self._log_error(f"Faltan asignaciones de serie para: {missing_text}")
                self._on_next_unassigned_group()
                QMessageBox.information(
                    self,
                    "Asignaciones incompletas",
                    "Debes asignar una serie a cada grupo detectado antes de ejecutar.\n\n"
                    "Usa 'Siguiente sin asignar' para saltar al siguiente grupo pendiente."
                )
                self.statusBar().showMessage("Previsualización parcial (faltan asignaciones)")
                return

            duplicate_targets, existing_targets = self._detect_plan_conflicts()
            if duplicate_targets or existing_targets:
                self.btn_execute.setEnabled(False)
                self._show_conflict_summary(duplicate_targets, existing_targets)
                self.statusBar().showMessage("Previsualización con conflictos")
                return

            self.btn_execute.setEnabled(True)
            self._log("Previsualización multi-serie generada. Puedes ejecutar cuando quieras.")
            self.statusBar().showMessage("Previsualización multi-serie lista")
            return

        if not self.selected_series:
            # Fallback: always try a local/manual suggestion so preview can proceed.
            name = self.txt_manual_name.text().strip() or self.txt_search.text().strip() or self._single_mode_guess().strip()
            if name:
                self.selected_series = TMDBSeries(tmdb_id=0, name=name)
                self._log(
                    "Previsualización con asignación provisional. "
                    f"Serie usada: <b>{name}</b>"
                )
            else:
                QMessageBox.information(
                    self, "Faltan datos",
                    "Selecciona una serie online o escribe un nombre manual para previsualizar."
                )
                return

        output_base = self._get_output_path()
        plan_renames(self.episodes, self.selected_series, output_base)
        self._update_table()

        duplicate_targets, existing_targets = self._detect_plan_conflicts()
        if duplicate_targets or existing_targets:
            self.btn_execute.setEnabled(False)
            self._show_conflict_summary(duplicate_targets, existing_targets)
            self.statusBar().showMessage("Previsualización con conflictos")
            return

        self.btn_execute.setEnabled(True)
        self._log("Previsualización generada. Revisa los nombres y pulsa 'Ejecutar' para procesar los archivos.")
        self.statusBar().showMessage("Previsualización lista")

    def _on_execute(self):
        planned = [e for e in self.episodes if e.new_path]
        if not planned:
            QMessageBox.warning(self, "Nada que hacer", "No hay archivos para procesar.")
            return

        duplicate_targets, existing_targets = self._detect_plan_conflicts()
        if duplicate_targets or existing_targets:
            self._show_conflict_summary(duplicate_targets, existing_targets)
            self.btn_execute.setEnabled(False)
            self.statusBar().showMessage("Ejecución bloqueada por conflictos")
            return

        if self.chk_dry_run.isChecked():
            self._run_dry_run(planned)
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
            source_root=self.source_folder,
        )
        self._rename_worker.progress.connect(self._on_progress)
        self._rename_worker.finished.connect(self._on_execute_finished)
        self._rename_worker.error.connect(self._on_execute_error)
        self._rename_worker.start()

    def _run_dry_run(self, planned: List[EpisodeFile]):
        """Strict simulation mode: validate and show the final plan without touching files."""
        op = "MOVE" if self.rb_move.isChecked() else "COPY"
        self._log("Simulación (dry-run) iniciada. No se modificará ningún archivo.")
        for ep in planned:
            src_name = ep.original_path.name
            dst_name = ep.new_name or ""
            self._log(f"DRY-RUN {op}: {src_name}  -->  {dst_name}")

        self._log_success(f"Simulación completada: {len(planned)} archivo(s) planificado(s), 0 cambios reales.")

        if self.chk_export_report.isChecked():
            report_path = self._export_report_csv(mode="dry-run", logs=[])
            if report_path:
                self._log_success(f"Reporte CSV generado: {report_path}")

        self.statusBar().showMessage("Simulación completada")
        QMessageBox.information(
            self,
            "Simulación completada",
            "Dry-run completado correctamente.\n\n"
            "No se ha copiado ni movido ningún archivo."
        )

    def _episode_source_desc(self, ep: EpisodeFile) -> str:
        source_desc = ep.original_path.name
        if ep.archive_member:
            source_desc = f"{source_desc}:{Path(ep.archive_member).name}"
        return source_desc

    def _build_status_map_from_logs(self, logs: List[str]) -> Dict[str, Dict[str, str]]:
        status_map: Dict[str, Dict[str, str]] = {}
        prefixes = ("MOVE:", "COPY:", "EXTRACT:", "ERROR:", "SKIP:")

        for raw in logs:
            msg = str(raw).strip()
            if not msg.startswith(prefixes):
                continue

            if "-->" in msg:
                left, right = msg.split("-->", 1)
            else:
                left, right = msg, ""

            if left.startswith("MOVE:"):
                status = "OK"
                source = left[len("MOVE:"):].strip()
            elif left.startswith("COPY:"):
                status = "OK"
                source = left[len("COPY:"):].strip()
            elif left.startswith("EXTRACT:"):
                status = "OK"
                source = left[len("EXTRACT:"):].strip()
            elif left.startswith("ERROR:"):
                status = "ERROR"
                source = left[len("ERROR:"):].strip()
            else:
                status = "SKIP"
                source = left[len("SKIP:"):].strip()

            status_map[source] = {
                "status": status,
                "message": right.strip() if right.strip() else msg,
            }

        return status_map

    def _export_report_csv(self, mode: str, logs: List[str]) -> Optional[Path]:
        try:
            output_base = self._get_output_path()
            output_base.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = output_base / f"MediaClean_report_{mode}_{timestamp}.csv"

            status_map = self._build_status_map_from_logs(logs)

            with report_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow([
                    "timestamp",
                    "mode",
                    "status",
                    "source_path",
                    "source_desc",
                    "series_guess",
                    "assigned_series",
                    "season",
                    "episode",
                    "new_name",
                    "target_path",
                    "message",
                ])

                now_iso = datetime.now().isoformat(timespec="seconds")
                for ep in self.episodes:
                    source_desc = self._episode_source_desc(ep)
                    assigned_series = self._assigned_series_for_episode(ep)
                    assigned_name = assigned_series.name if assigned_series else ""

                    if mode == "dry-run":
                        status = "DRY-RUN" if ep.new_path else "SKIP"
                        message = "Planned only" if ep.new_path else "No season/episode info"
                    else:
                        row_state = status_map.get(source_desc)
                        if row_state:
                            status = row_state.get("status", "UNKNOWN")
                            message = row_state.get("message", "")
                        elif ep.new_path is None:
                            status = "SKIP"
                            message = "No season/episode info"
                        else:
                            status = "UNKNOWN"
                            message = "No explicit worker log entry"

                    writer.writerow([
                        now_iso,
                        mode,
                        status,
                        str(ep.original_path),
                        source_desc,
                        ep.series_guess,
                        assigned_name,
                        ep.season if ep.season is not None else "",
                        ep.episode if ep.episode is not None else "",
                        ep.new_name or "",
                        str(ep.new_path) if ep.new_path else "",
                        message,
                    ])

            return report_path
        except Exception as exc:
            self._log_error(f"No se pudo exportar el reporte CSV: {exc}")
            return None

    def _detect_plan_conflicts(self) -> tuple[list[Path], list[Path]]:
        """Return duplicate target paths and already-existing target paths."""
        planned_paths = [ep.new_path for ep in self.episodes if ep.new_path is not None]
        seen: dict[Path, int] = {}
        duplicates: list[Path] = []

        for path in planned_paths:
            seen[path] = seen.get(path, 0) + 1

        for path, count in seen.items():
            if count > 1:
                duplicates.append(path)

        existing = [path for path in planned_paths if path.exists()]
        duplicates.sort(key=lambda p: str(p).lower())
        existing.sort(key=lambda p: str(p).lower())
        return duplicates, existing

    def _show_conflict_summary(self, duplicate_targets: List[Path], existing_targets: List[Path]):
        """Show and log destination conflicts found in the current plan."""
        if duplicate_targets:
            self._log_error(
                f"Conflicto: {len(duplicate_targets)} destino(s) duplicado(s) en el plan."
            )
            for path in duplicate_targets[:8]:
                self._log_error(f"  DUPLICADO: {path}")

        if existing_targets:
            self._log_error(
                f"Conflicto: {len(existing_targets)} destino(s) ya existen en disco."
            )
            for path in existing_targets[:8]:
                self._log_error(f"  EXISTE: {path}")

        details = []
        if duplicate_targets:
            details.append(f"- Destinos duplicados en el plan: {len(duplicate_targets)}")
        if existing_targets:
            details.append(f"- Destinos ya existentes: {len(existing_targets)}")

        QMessageBox.warning(
            self,
            "Conflictos detectados",
            "Se detectaron conflictos en la previsualización y se bloqueó la ejecución.\n\n"
            + "\n".join(details)
            + "\n\nRevisa la tabla y/o ajusta temporada/episodio antes de continuar."
        )

    def _on_progress(self, current: int, total: int):
        self.progress_bar.setValue(current)

    def _on_execute_finished(self, log_messages: list):
        logs = list(log_messages or [])
        error_count = 0

        for raw_msg in logs:
            msg = str(raw_msg)
            if msg.startswith("ERROR"):
                error_count += 1
                self._log_error(msg)
            elif msg.startswith("WARN"):
                self._log(f'<span style="color:#f9e2af;">{msg}</span>')
            elif msg.startswith("SKIP"):
                self._log(f'<span style="color:#fab387;">{msg}</span>')
            else:
                self._log_success(msg)

        self.progress_bar.setValue(self.progress_bar.maximum())
        self.progress_bar.setVisible(False)
        self.btn_execute.setEnabled(True)
        self.btn_scan.setEnabled(True)
        self.btn_preview.setEnabled(True)
        self._rename_worker = None

        if self.chk_export_report.isChecked():
            report_path = self._export_report_csv(mode="execute", logs=logs)
            if report_path:
                self._log_success(f"Reporte CSV generado: {report_path}")

        if error_count:
            self.statusBar().showMessage(f"Proceso completado con {error_count} error(es)")
            self._log_error(f"═══ Proceso completado con {error_count} error(es) ═══")
            QMessageBox.warning(
                self, "Completado con errores",
                "Algunos archivos no se pudieron procesar.\n"
                "Revisa el log para ver el detalle de cada error."
            )
        else:
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
        self.btn_preview.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._rename_worker = None
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
        if self.selected_series or (self.rb_multi_batch.isChecked() and self.series_assignments):
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
        self.table.setItem(row, 5, QTableWidgetItem(""))
        self.table.blockSignals(False)

    def _get_output_path(self) -> Path:
        custom = self.txt_output.text().strip()
        if custom:
            return Path(custom)
        if self.source_folder:
            return self.source_folder.parent / DEFAULT_OUTPUT_FOLDER
        return Path.home() / DEFAULT_OUTPUT_FOLDER

    def _assigned_series_for_episode(self, ep: EpisodeFile) -> Optional[TMDBSeries]:
        if self.rb_multi_batch.isChecked():
            group_key = self._group_key(ep.series_guess or "")
            return self.series_assignments.get(group_key)
        return self.selected_series

    def _series_label_for_episode(self, ep: EpisodeFile) -> str:
        assigned = self._assigned_series_for_episode(ep)
        if assigned and assigned.name:
            return assigned.name
        fallback = (ep.series_guess or "Serie desconocida").strip()
        return fallback or "Serie desconocida"

    def _episode_sort_key(self, ep: EpisodeFile) -> tuple:
        series_name = self._series_label_for_episode(ep).lower()
        season = ep.season if ep.season is not None else 10_000
        episode = ep.episode if ep.episode is not None else 10_000
        return (series_name, season, episode, ep.original_path.name.lower())

    def _update_table(self):
        self.episodes.sort(key=self._episode_sort_key)
        self.table.blockSignals(True)  # Prevent cellChanged during population
        self.table.setRowCount(len(self.episodes))
        for row, ep in enumerate(self.episodes):
            # Original filename (read-only)
            label = ep.original_path.name
            if ep.needs_extract:
                label = f"📦 {label}"
                if ep.archive_member:
                    label = f"{label} :: {Path(ep.archive_member).name}"
            item_orig = QTableWidgetItem(label)
            item_orig.setFlags(item_orig.flags() & ~Qt.ItemIsEditable)
            if ep.needs_extract:
                tooltip = "Archivo comprimido (RAR) — se extraerá automáticamente"
                if ep.archive_member:
                    tooltip += f"\nContenido detectado: {ep.archive_member}"
                item_orig.setToolTip(tooltip)
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

            # Episode title from assigned series metadata (read-only)
            tmdb_name = ""
            assigned_series = self._assigned_series_for_episode(ep)
            if assigned_series and ep.season is not None and ep.episode is not None:
                tmdb_ep = assigned_series.get_episode(ep.season, ep.episode)
                if tmdb_ep:
                    tmdb_name = tmdb_ep.name
            item_tmdb = QTableWidgetItem(tmdb_name)
            item_tmdb.setFlags(item_tmdb.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 3, item_tmdb)

            assigned_name = assigned_series.name if assigned_series else ""
            if self.rb_multi_batch.isChecked() and not assigned_name:
                assigned_name = self.group_labels.get(self._group_key(ep.series_guess or ""), ep.series_guess or "Serie desconocida")
            item_assigned = QTableWidgetItem(assigned_name)
            item_assigned.setFlags(item_assigned.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 4, item_assigned)

            # New filename (read-only)
            new_name = ep.new_name if ep.new_name else ""
            item_new = QTableWidgetItem(new_name)
            item_new.setFlags(item_new.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 5, item_new)

        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        # Re-stretch first and last columns
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
