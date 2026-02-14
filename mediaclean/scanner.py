"""
Scanner module: discovers video files inside a series folder structure.

Expected input structure:
    SeriesFolder/
        Episode01_subfolder/
            video.mkv
            subtitle.srt
            ...
        Episode02_subfolder/
            video.avi
            ...
        maybe_a_video_here.mp4
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from mediaclean.constants import VIDEO_EXTENSIONS, EPISODE_PATTERNS, JUNK_PATTERNS, RAR_EXTENSIONS


@dataclass
class EpisodeFile:
    """Represents a discovered video file that belongs to a TV episode."""
    original_path: Path
    season: Optional[int] = None
    episode: Optional[int] = None
    series_guess: str = ""
    extension: str = ""
    new_name: Optional[str] = None
    new_path: Optional[Path] = None
    needs_extract: bool = False  # True if source is a RAR archive

    def __post_init__(self):
        self.extension = self.original_path.suffix.lower()


def is_video_file(path: Path) -> bool:
    """Check if a given path is a video file based on its extension."""
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_rar_first_volume(path: Path) -> bool:
    """
    Check if a path is the first volume of a RAR archive set.

    Recognised patterns:
      - file.rar               (single or first volume)
      - file.part1.rar / file.part01.rar  (first part, new style)

    We skip:
      - file.r00, .r01, ...    (old-style continuation volumes)
      - file.part2.rar, ...    (continuation volumes, new style)
    """
    name = path.name.lower()
    if not name.endswith(".rar"):
        return False
    # new-style split: file.part2.rar, file.part03.rar  → skip
    m = re.search(r'\.part(\d+)\.rar$', name)
    if m:
        return int(m.group(1)) == 1
    # If it ends with .rar and is not a partN.rar with N>1, it's the first
    return True


def _find_rar_video_extension(rar_path: Path) -> str:
    """
    Peek inside a RAR archive to find the video file extension.
    Returns the extension (e.g. '.mkv') or '' if none found.
    Uses the rarfile module if available, otherwise guesses '.mkv'.
    """
    try:
        import rarfile
        with rarfile.RarFile(str(rar_path)) as rf:
            for info in rf.infolist():
                ext = Path(info.filename).suffix.lower()
                if ext in VIDEO_EXTENSIONS:
                    return ext
    except Exception:
        pass
    # Default guess — will be corrected after extraction
    return ".mkv"


def parse_episode_info(filename: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Try to extract season and episode numbers from a filename.
    Returns (season, episode) or (None, None) if not found.

    Strategy:
      1. Normalise the filename (clean up separators)
      2. Try explicit season+episode patterns first (patterns 0-4)
         → returns (season, episode) directly
      3. Try episode-only patterns (patterns 5-9)
         → single number passed through fuzzy logic to guess season
      4. Filter out false positives (years, resolutions, etc.)
    """
    # Work on stem (no extension) to avoid ".mkv" etc. confusing patterns
    name = Path(filename).stem if "." in filename and filename.rsplit(".", 1)[-1].isalpha() else filename

    # ── Phase 1: explicit season + episode (two capture groups) ──
    for pattern in EPISODE_PATTERNS[:5]:
        m = re.search(pattern, name)
        if m and m.lastindex and m.lastindex >= 2:
            season, episode = int(m.group(1)), int(m.group(2))
            if season >= 0 and episode >= 0:
                return season, episode

    # ── Phase 2: episode-only patterns (one capture group) ──
    for pattern in EPISODE_PATTERNS[5:]:
        m = re.search(pattern, name)
        if m:
            raw_num = m.group(1)
            # Skip false positives: years (1950-2030), resolutions, codecs
            num = int(raw_num)
            if _is_false_positive(num, raw_num, name, m.start()):
                continue
            return _fuzzy_parse_cap_number(raw_num)

    return None, None


def _is_false_positive(num: int, raw: str, full_name: str, match_pos: int) -> bool:
    """
    Check if a captured number is actually a year, resolution, or
    other non-episode metadata.
    """
    # Years 1950–2030
    if 1950 <= num <= 2030 and len(raw) == 4:
        return True
    # Resolution numbers: 720, 1080, 2160, 480, 576
    if num in (480, 576, 720, 1080, 2160, 4320):
        return True
    # Codec-like numbers: 264, 265
    if num in (264, 265):
        return True
    return False


def _fuzzy_parse_cap_number(raw_num: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Fuzzy parsing of a bare number from Cap.NNN or similar patterns.

    Heuristics:
      - 4 digits (e.g. 1205): first 2 = season, last 2 = episode  → S12E05
      - 3 digits (e.g. 401):  first 1 = season, last 2 = episode  → S04E01
        BUT if the episode part > 50 (unlikely), treat as absolute  → S01E401
      - 1-2 digits (e.g. 01, 5): episode only, season unknown      → (None, N)
    """
    num = int(raw_num)
    length = len(raw_num)

    if length == 4:
        # 1205 → S12E05
        season = num // 100
        episode = num % 100
        if season >= 1 and episode >= 1:
            return season, episode

    if length == 3:
        # 401 → S4E01; but 401 could also be absolute episode 401
        season = num // 100
        episode = num % 100
        if season >= 1 and 1 <= episode <= 50:
            return season, episode
        # Unlikely season/episode split — treat as absolute
        return None, num

    # 1-2 digits: just episode number, season unknown
    if num >= 1:
        return None, num

    return None, None


def guess_series_name(folder_name: str) -> str:
    """
    Try to extract a clean series name from a folder name by stripping
    common junk patterns (quality, codec, group tags, etc.).
    """
    name = folder_name

    # Remove everything starting from SxxExx, TxxExx, or similar
    name = re.split(r"[Ss]\d{1,2}[\s._-]*[Ee]\d", name)[0]
    name = re.split(r"[Tt]\d{1,2}[\s._-]*[Ee]\d", name)[0]
    name = re.split(r"\d{1,2}[xX]\d{1,3}", name)[0]

    # Remove Temporada/Cap markers and everything after
    name = re.split(r"[Tt](?:emporada|emp)[\s._-]*\d", name, flags=re.IGNORECASE)[0]
    name = re.split(r"[Cc]ap(?:i(?:tulo)?)?[\s._-]*\d", name, flags=re.IGNORECASE)[0]

    # Remove standalone season markers like "S02", "S1", "Season 2", "T2"
    name = re.sub(r"\b[Ss]\d{1,2}\b", "", name)
    name = re.sub(r"\b[Ss]eason\s*\d{1,2}\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\b[Tt]\d{1,2}\b", "", name)

    # Remove junk patterns
    for pattern in JUNK_PATTERNS:
        name = re.sub(pattern, "", name, flags=re.IGNORECASE)

    # Replace dots, underscores with spaces
    name = name.replace(".", " ").replace("_", " ")

    # Remove extra whitespace and trailing dashes
    name = re.sub(r"\s+", " ", name).strip(" -–—")

    return name


def scan_folder(root_path: Path) -> List[EpisodeFile]:
    """
    Scan a series root folder and discover all video files,
    extracting season/episode info from folder and file names.

    Searches recursively: videos can be at the root level or inside
    subfolders (one level or more).
    """
    episodes: List[EpisodeFile] = []
    root_path = Path(root_path)

    if not root_path.is_dir():
        return episodes

    series_guess = guess_series_name(root_path.name)

    for dirpath, dirnames, filenames in os.walk(root_path):
        dir_has_video = False
        dir_rar_first = None  # first RAR volume in this directory

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if is_video_file(fpath):
                dir_has_video = True
                ep = EpisodeFile(original_path=fpath, series_guess=series_guess)

                season, episode = parse_episode_info(fname)
                if season is None and episode is None:
                    parent_name = fpath.parent.name
                    season, episode = parse_episode_info(parent_name)

                ep.season = season
                ep.episode = episode
                episodes.append(ep)

            elif dir_rar_first is None and is_rar_first_volume(fpath):
                dir_rar_first = fpath

        # If this directory has RAR files but NO video files, treat the
        # RAR as a compressed episode
        if not dir_has_video and dir_rar_first is not None:
            # Try to determine the video extension inside the archive
            vid_ext = _find_rar_video_extension(dir_rar_first)

            ep = EpisodeFile(
                original_path=dir_rar_first,
                series_guess=series_guess,
                needs_extract=True,
            )
            ep.extension = vid_ext  # override the .rar extension

            # Parse episode info from the RAR filename or folder
            season, episode = parse_episode_info(dir_rar_first.name)
            if season is None and episode is None:
                season, episode = parse_episode_info(dir_rar_first.parent.name)

            ep.season = season
            ep.episode = episode
            episodes.append(ep)

    # Post-process: try to infer seasons for episodes that have None
    infer_seasons(episodes, root_path)

    # Sort by season, then episode
    episodes.sort(key=lambda e: (e.season or 0, e.episode or 0))
    return episodes


def infer_seasons(episodes: List[EpisodeFile], root_path: Path):
    """
    Post-processing: try to infer season numbers for episodes where
    the season is None using multiple heuristics:

    1. Folder name analysis: look for season clues in parent folder names
       (e.g. "Temporada 4", "Season 4", "T4", "S4")
    2. Root folder name: check if the root folder itself has a season hint
       (e.g. "Serie.S04.720p")
    3. Consensus: if all other episodes in the same batch have the same
       season, assign that season to unknowns
    4. Fallback: default to season 1
    """
    for ep in episodes:
        if ep.season is not None:
            continue

        # Heuristic 1: check parent folder(s) for season markers
        inferred = _season_from_path(ep.original_path, root_path)
        if inferred is not None:
            ep.season = inferred
            continue

        # Heuristic 2: check root folder name
        inferred = _extract_season_from_string(root_path.name)
        if inferred is not None:
            ep.season = inferred
            continue

    # Heuristic 3: consensus from siblings
    known_seasons = {ep.season for ep in episodes if ep.season is not None}
    if len(known_seasons) == 1:
        consensus = known_seasons.pop()
        for ep in episodes:
            if ep.season is None:
                ep.season = consensus

    # Heuristic 4: fallback to 1
    for ep in episodes:
        if ep.season is None:
            ep.season = 1


def _season_from_path(file_path: Path, root_path: Path) -> Optional[int]:
    """
    Walk up from the file's parent to the root, looking for season
    markers in each folder name.
    """
    current = file_path.parent
    while current != root_path and current != current.parent:
        result = _extract_season_from_string(current.name)
        if result is not None:
            return result
        current = current.parent
    return None


def _extract_season_from_string(text: str) -> Optional[int]:
    """
    Try to extract a season number from a string using common patterns.
    """
    patterns = [
        # "Season 4", "Temporada 4", "Temp 4", "Temp.4"
        r"(?:season|temporada|temp)[\s.]*?(\d{1,2})",
        # "S04", "S4" (standalone)
        r"\b[Ss](\d{1,2})\b",
        # "T04", "T4" (Spanish: Temporada 4)
        r"\b[Tt](\d{1,2})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            val = int(m.group(1))
            if 1 <= val <= 50:  # sanity check
                return val
    return None


def override_season(episodes: List[EpisodeFile], new_season: int):
    """
    Override the season number for ALL episodes in the list.
    Used when the user manually sets the season from the UI.
    """
    for ep in episodes:
        ep.season = new_season
