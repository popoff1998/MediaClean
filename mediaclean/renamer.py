"""
Renamer module: builds Plex-compatible filenames and creates 
the output folder with hard links or copies of the video files.

Plex naming convention:
    Show Name - SxxExx - Episode Title.ext

Output structure:
    <source_folder>/_MediaClean_Output/
        Show Name/
            Season 01/
                Show Name - S01E01 - Episode Title.mkv
                Show Name - S01E02 - Episode Title.avi
            Season 02/
                ...
"""

import importlib
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from mediaclean.scanner import EpisodeFile, parse_episode_info
from mediaclean.tmdb_client import TMDBSeries
from mediaclean.constants import DEFAULT_OUTPUT_FOLDER, VIDEO_EXTENSIONS


def sanitize_filename(name: str) -> str:
    """Remove or replace characters that are invalid in file/folder names."""
    # Replace characters not allowed on Windows: \ / : * ? " < > |
    name = re.sub(r'[\\/:*?"<>|]', "", name)
    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_plex_name(
    series_name: str,
    season: int,
    episode: int,
    episode_title: Optional[str],
    extension: str,
) -> str:
    """
    Build a Plex-compatible filename.
    Format: Show Name - S01E01 - Episode Title.ext
    """
    safe_series = sanitize_filename(series_name)
    code = f"S{season:02d}E{episode:02d}"

    if episode_title:
        safe_title = sanitize_filename(episode_title)
        return f"{safe_series} - {code} - {safe_title}{extension}"
    else:
        return f"{safe_series} - {code}{extension}"


def plan_renames(
    episodes: List[EpisodeFile],
    series: TMDBSeries,
    output_base: Path,
) -> List[EpisodeFile]:
    """
    Assign new_name and new_path to each EpisodeFile based on TMDB metadata.
    Does NOT perform any file operations.
    """
    series_name = series.name if series.name else episodes[0].series_guess if episodes else "Unknown"

    for ep in episodes:
        if ep.season is None or ep.episode is None:
            # Can't rename without season/episode info
            continue

        tmdb_ep = series.get_episode(ep.season, ep.episode)
        ep_title = tmdb_ep.name if tmdb_ep else None

        ep.new_name = build_plex_name(
            series_name, ep.season, ep.episode, ep_title, ep.extension
        )

        season_folder = output_base / sanitize_filename(series_name) / f"Season {ep.season:02d}"
        ep.new_path = season_folder / ep.new_name

    return episodes


def execute_renames(
    episodes: List[EpisodeFile],
    file_mode: str = "move",
    source_root: Optional[Path] = None,
    progress_callback=None,
) -> List[str]:
    """
    Execute the planned renames by copying or moving files
    to their new paths. Returns a list of log messages.

    file_mode: "copy" or "move"
      - copy: files are duplicated (originals untouched)
      - move: files are moved (originals disappear from source)

        source_root:
            - folder selected by the user; never removed by auto-cleanup.

    Episodes with needs_extract=True are extracted from RAR first.
    """
    log: List[str] = []
    total = len([e for e in episodes if e.new_path])
    processed = 0
    archive_remaining: dict[Path, List[Path]] = {}
    archive_temp_dirs: dict[Path, tempfile.TemporaryDirectory] = {}
    pending_per_container: dict[Path, int] = {}
    failed_containers: set[Path] = set()

    if file_mode == "move":
        for ep in episodes:
            container = ep.original_path.parent
            if ep.new_path is None:
                # Keep folders with skipped items so they remain visible for manual fixing.
                failed_containers.add(container)
                continue
            pending_per_container[container] = pending_per_container.get(container, 0) + 1

    try:
        for ep in episodes:
            if ep.new_path is None:
                log.append(f"SKIP: {ep.original_path.name} (no season/episode info)")
                continue

            processed += 1
            success = False
            container = ep.original_path.parent

            source_desc = ep.original_path.name
            if ep.archive_member:
                source_desc = f"{source_desc}:{Path(ep.archive_member).name}"

            try:
                # Create target directory for this file only.
                ep.new_path.parent.mkdir(parents=True, exist_ok=True)

                if ep.needs_extract:
                    if ep.original_path not in archive_remaining:
                        tmp_dir = tempfile.TemporaryDirectory(prefix="mediaclean_rar_")
                        archive_temp_dirs[ep.original_path] = tmp_dir
                        archive_remaining[ep.original_path] = _extract_videos_from_rar(
                            ep.original_path,
                            Path(tmp_dir.name),
                        )

                    extracted = _pick_extracted_video(archive_remaining[ep.original_path], ep)
                    if extracted is None:
                        log.append(f"ERROR: {source_desc}  -->  no video found in RAR")
                    else:
                        # Update extension if it was guessed wrong during scan
                        real_ext = extracted.suffix.lower()
                        if real_ext != ep.extension:
                            ep.extension = real_ext
                            base_name = ep.new_name.rsplit(".", 1)[0] if ep.new_name else ep.new_path.stem
                            ep.new_name = base_name + real_ext
                            ep.new_path = ep.new_path.parent / ep.new_name

                        final_target = ep.new_path
                        if final_target.exists():
                            final_target.unlink()
                        shutil.move(str(extracted), str(final_target))
                        log.append(f"EXTRACT: {source_desc}  -->  {ep.new_name}")
                        success = True

                elif file_mode == "move":
                    shutil.move(str(ep.original_path), str(ep.new_path))
                    log.append(f"MOVE: {ep.original_path.name}  -->  {ep.new_name}")
                    success = True
                else:
                    shutil.copy2(str(ep.original_path), str(ep.new_path))
                    log.append(f"COPY: {ep.original_path.name}  -->  {ep.new_name}")
                    success = True
            except Exception as e:
                msg = str(e).strip()
                detail = f"{e.__class__.__name__}: {msg}" if msg else e.__class__.__name__
                log.append(f"ERROR: {source_desc}  -->  {detail}")
                failed_containers.add(container)
            finally:
                if file_mode == "move":
                    if success:
                        remaining = max(pending_per_container.get(container, 0) - 1, 0)
                        pending_per_container[container] = remaining
                        if remaining == 0 and container not in failed_containers:
                            if source_root is None or not _is_same_path(container, source_root):
                                _remove_source_container(container, log)
                    else:
                        failed_containers.add(container)

                if progress_callback:
                    try:
                        progress_callback(processed, total)
                    except Exception:
                        # Never abort the processing loop because of UI callback failures.
                        pass
    finally:
        for temp_dir in archive_temp_dirs.values():
            try:
                temp_dir.cleanup()
            except Exception as e:
                msg = str(e).strip()
                detail = f"{e.__class__.__name__}: {msg}" if msg else e.__class__.__name__
                log.append(f"WARN: failed to clean temporary extraction folder ({detail})")

    return log


def _extract_videos_from_rar(rar_path: Path, output_dir: Path) -> List[Path]:
    """
    Extract all video files from a RAR archive into output_dir.
    Tries multiple extraction methods:
      1. rarfile (pure Python, needs UnRAR DLL/binary)
      2. unrar command-line
      3. 7z command-line (7-Zip)
      4. WinRAR directly

    Returns extracted videos sorted by filename.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Method 1: rarfile module ──
    try:
        rarfile = importlib.import_module("rarfile")
        extracted_any = False
        with rarfile.RarFile(str(rar_path)) as rf:
            for info in rf.infolist():
                member = str(info.filename)
                if not member or member.endswith(("/", "\\")):
                    continue

                ext = Path(member).suffix.lower()
                if ext in VIDEO_EXTENSIONS:
                    rf.extract(info, str(output_dir))
                    extracted_any = True

        if extracted_any:
            videos = _collect_video_files(output_dir)
            if videos:
                return videos
    except ImportError:
        pass  # rarfile not installed
    except Exception:
        pass  # extraction failed, try other methods

    # ── Method 2-4: command-line tools ──
    # Try to extract with the first available tool
    commands = _build_extract_commands(rar_path, output_dir)
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=600,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                videos = _collect_video_files(output_dir)
                if videos:
                    return videos
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return []


def _pick_extracted_video(remaining: List[Path], ep: EpisodeFile) -> Optional[Path]:
    """
    Select the best extracted file for one episode and remove it from
    the remaining pool to avoid duplicate assignment.
    """
    if not remaining:
        return None

    # 1) Prefer exact basename match when we know the archive member.
    if ep.archive_member:
        wanted = Path(ep.archive_member).name.lower()
        for idx, candidate in enumerate(remaining):
            if candidate.name.lower() == wanted:
                return remaining.pop(idx)

    # 2) Try matching by parsed season/episode from extracted filename.
    if ep.season is not None and ep.episode is not None:
        for idx, candidate in enumerate(remaining):
            cand_season, cand_episode = parse_episode_info(candidate.name)
            if cand_episode is None:
                continue
            if cand_season is None:
                cand_season = ep.season
            if cand_season == ep.season and cand_episode == ep.episode:
                return remaining.pop(idx)

    # 3) Fallback: consume in deterministic order.
    return remaining.pop(0)


def _build_extract_commands(rar_path: Path, output_dir: Path):
    """Build a list of possible extraction commands to try."""
    rar_str = str(rar_path)
    out_str = str(output_dir) + os.sep
    commands = []

    # unrar (standalone or bundled with WinRAR)
    for unrar in ["unrar", r"C:\Program Files\WinRAR\UnRAR.exe",
                   r"C:\Program Files (x86)\WinRAR\UnRAR.exe"]:
        commands.append([unrar, "e", "-o+", "-y", rar_str, out_str])

    # 7z / 7za
    for sz in ["7z", "7za",
                r"C:\Program Files\7-Zip\7z.exe",
                r"C:\Program Files (x86)\7-Zip\7z.exe"]:
        commands.append([sz, "e", f"-o{out_str}", "-y", rar_str])

    return commands


def _collect_video_files(directory: Path) -> List[Path]:
    """Collect extracted video files recursively in deterministic order."""
    videos: List[Path] = []
    for path in sorted(directory.rglob("*"), key=lambda p: str(p).lower()):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(path)
    return videos


def _remove_source_container(container: Path, log: List[str]):
    """Remove a source container folder after all its files were moved successfully."""
    if not container.exists() or not container.is_dir():
        return

    try:
        shutil.rmtree(container)
        log.append(f"CLEANUP: {container}")
    except Exception as e:
        msg = str(e).strip()
        detail = f"{e.__class__.__name__}: {msg}" if msg else e.__class__.__name__
        log.append(f"WARN: failed to remove source folder {container} ({detail})")


def _is_same_path(a: Path, b: Path) -> bool:
    """Compare two paths robustly across relative forms and Windows case-insensitivity."""
    return _path_key(a) == _path_key(b)


def _path_key(path: Path) -> str:
    """Normalised key for path comparisons."""
    try:
        normalized = path.resolve(strict=False)
    except Exception:
        normalized = path.absolute()
    return os.path.normcase(os.path.normpath(str(normalized)))
