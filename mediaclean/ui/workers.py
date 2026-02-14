"""
Worker threads for long-running operations (scanning, TMDB lookup, file copy, poster download).
"""

import urllib.request
import urllib.error
from PySide6.QtCore import QThread, Signal
from pathlib import Path
from typing import List, Optional

from mediaclean.scanner import scan_folder, EpisodeFile
from mediaclean.tmdb_client import TMDBClient, TMDBSeries, TMDBError
from mediaclean.renamer import plan_renames, execute_renames


class ScanWorker(QThread):
    """Scans a folder for video files in a background thread."""
    finished = Signal(list)  # List[EpisodeFile]
    error = Signal(str)

    def __init__(self, folder_path: Path, parent=None):
        super().__init__(parent)
        self.folder_path = folder_path

    def run(self):
        try:
            episodes = scan_folder(self.folder_path)
            self.finished.emit(episodes)
        except Exception as e:
            self.error.emit(str(e))


class TMDBSearchWorker(QThread):
    """Searches TMDB for a series in a background thread."""
    finished = Signal(list)   # List[TMDBSeries]
    error = Signal(str)

    def __init__(self, client: TMDBClient, query: str, parent=None):
        super().__init__(parent)
        self.client = client
        self.query = query

    def run(self):
        try:
            results = self.client.search_series(self.query)
            self.finished.emit(results)
        except TMDBError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))


class TMDBLoadEpisodesWorker(QThread):
    """Loads episode metadata for a series from TMDB."""
    finished = Signal(object)  # TMDBSeries with episodes populated
    error = Signal(str)

    def __init__(self, client: TMDBClient, series: TMDBSeries,
                 seasons: Optional[List[int]] = None, parent=None):
        super().__init__(parent)
        self.client = client
        self.series = series
        self.seasons = seasons

    def run(self):
        try:
            self.client.load_episodes_for_series(self.series, self.seasons)
            self.finished.emit(self.series)
        except TMDBError as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))


class RenameWorker(QThread):
    """Executes file copy/move operations in a background thread."""
    progress = Signal(int, int)   # current, total
    finished = Signal(list)       # List[str] log messages
    error = Signal(str)

    def __init__(self, episodes: List[EpisodeFile], file_mode: str = "copy", parent=None):
        super().__init__(parent)
        self.episodes = episodes
        self.file_mode = file_mode

    def run(self):
        try:
            log = execute_renames(
                self.episodes,
                file_mode=self.file_mode,
                progress_callback=lambda cur, tot: self.progress.emit(cur, tot),
            )
            self.finished.emit(log)
        except Exception as e:
            self.error.emit(str(e))


class PosterWorker(QThread):
    """Downloads a poster image in a background thread."""
    finished = Signal(bytes)   # raw image data
    error = Signal(str)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            req = urllib.request.Request(self.url)
            req.add_header("User-Agent", "MediaClean/1.0")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            self.finished.emit(data)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            self.error.emit(str(e))
        except Exception as e:
            self.error.emit(str(e))
