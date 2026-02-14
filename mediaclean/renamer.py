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

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from mediaclean.scanner import EpisodeFile
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
    file_mode: str = "copy",
    progress_callback=None,
) -> List[str]:
    """
    Execute the planned renames by copying or moving files
    to their new paths. Returns a list of log messages.

    file_mode: "copy" or "move"
      - copy: files are duplicated (originals untouched)
      - move: files are moved (originals disappear from source)

    Episodes with needs_extract=True are extracted from RAR first.
    """
    log: List[str] = []
    total = len([e for e in episodes if e.new_path])

    for idx, ep in enumerate(episodes):
        if ep.new_path is None:
            log.append(f"SKIP: {ep.original_path.name} (no season/episode info)")
            continue

        # Create target directory
        ep.new_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            if ep.needs_extract:
                # ── Extract from RAR ──
                extracted = _extract_video_from_rar(ep.original_path, ep.new_path.parent)
                if extracted is None:
                    log.append(f"ERROR: {ep.original_path.name}  -->  no video found in RAR")
                    continue
                # Update extension if it was guessed wrong during scan
                real_ext = extracted.suffix.lower()
                if real_ext != ep.extension:
                    ep.extension = real_ext
                    ep.new_name = ep.new_name.rsplit(".", 1)[0] + real_ext
                    ep.new_path = ep.new_path.parent / ep.new_name
                # Move extracted file to final name
                final_target = ep.new_path
                if extracted != final_target:
                    if final_target.exists():
                        final_target.unlink()
                    shutil.move(str(extracted), str(final_target))
                log.append(f"EXTRACT: {ep.original_path.name}  -->  {ep.new_name}")
            elif file_mode == "move":
                shutil.move(str(ep.original_path), str(ep.new_path))
                log.append(f"MOVE: {ep.original_path.name}  -->  {ep.new_name}")
            else:
                shutil.copy2(str(ep.original_path), str(ep.new_path))
                log.append(f"COPY: {ep.original_path.name}  -->  {ep.new_name}")
        except Exception as e:
            log.append(f"ERROR: {ep.original_path.name}  -->  {e}")

        if progress_callback:
            progress_callback(idx + 1, total)

    return log


def _extract_video_from_rar(rar_path: Path, output_dir: Path) -> Optional[Path]:
    """
    Extract the video file from a RAR archive into output_dir.
    Tries multiple extraction methods:
      1. rarfile (pure Python, needs UnRAR DLL/binary)
      2. unrar command-line
      3. 7z command-line (7-Zip)
      4. WinRAR directly

    Returns the Path of the extracted video or None.
    """
    # ── Method 1: rarfile module ──
    try:
        import rarfile
        with rarfile.RarFile(str(rar_path)) as rf:
            for info in rf.infolist():
                ext = Path(info.filename).suffix.lower()
                if ext in VIDEO_EXTENSIONS:
                    rf.extract(info, str(output_dir))
                    extracted = output_dir / info.filename
                    # If extracted into a subfolder, move it up
                    if extracted.parent != output_dir:
                        dest = output_dir / extracted.name
                        shutil.move(str(extracted), str(dest))
                        extracted = dest
                    return extracted
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
                video = _find_video_in_dir(output_dir)
                if video:
                    return video
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    return None


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


def _find_video_in_dir(directory: Path) -> Optional[Path]:
    """Find the first video file in a directory (non-recursive)."""
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS:
            return f
    return None
