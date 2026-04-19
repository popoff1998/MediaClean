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

import importlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from mediaclean.constants import VIDEO_EXTENSIONS, EPISODE_PATTERNS, JUNK_PATTERNS, RAR_EXTENSIONS


SEASON_HINT_PATTERNS = (
    r"(?:season|temporada|temp)[\s.]*?(\d{1,2})",
    r"\b[Ss](\d{1,2})\b",
    r"\b[Tt](\d{1,2})\b",
)

SERIES_NAME_STOPWORDS = {
    "season",
    "temporada",
    "temp",
    "episode",
    "episodio",
    "capitulo",
    "capítulo",
    "sample",
    "samples",
    "subs",
    "subtitles",
    "extra",
    "extras",
    "special",
    "specials",
    "video",
    "videos",
    "archivo",
    "file",
}

GENERIC_CONTAINER_HINTS = {
    "series",
    "temporadas",
    "season",
    "pack",
    "downloaded",
    "complete",
    "completa",
    "collection",
    "coleccion",
    "colección",
    "generic",
    "generico",
    "genérico",
    "contenedor",
    "folder",
    "source",
    "downloads",
    "download",
    "descargas",
    "descarga",
    "torrent",
    "media",
    "output",
}


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
    archive_member: Optional[str] = None  # Internal member path for multi-video RARs

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
        rarfile = importlib.import_module("rarfile")
        with rarfile.RarFile(str(rar_path)) as rf:
            for info in rf.infolist():
                ext = Path(info.filename).suffix.lower()
                if ext in VIDEO_EXTENSIONS:
                    return ext
    except Exception:
        pass
    # Default guess — will be corrected after extraction
    return ".mkv"


def _list_rar_video_members(rar_path: Path) -> List[str]:
    """
    List video entries found inside a RAR archive.

    Returns member paths as stored inside the archive.
    If archive listing is unavailable (e.g. rarfile not installed),
    returns an empty list.
    """
    members: List[str] = []
    seen: set[str] = set()

    try:
        rarfile = importlib.import_module("rarfile")
        with rarfile.RarFile(str(rar_path)) as rf:
            for info in rf.infolist():
                member = str(info.filename)
                if not member or member.endswith(("/", "\\")):
                    continue

                ext = Path(member).suffix.lower()
                if ext not in VIDEO_EXTENSIONS:
                    continue

                key = member.replace("\\", "/").lower()
                if key in seen:
                    continue
                seen.add(key)
                members.append(member)
    except Exception:
        return []

    return members


def _parse_episode_range(text: str) -> List[Tuple[Optional[int], int]]:
    """
    Parse compact ranges like "Cap.101_107" or "E01-07".

    Returns a list of (season, episode) tuples.
    Season can be None when it cannot be inferred from the range itself.
    """
    patterns = [
        r"[Cc]ap(?:[ií]tulo)?[\s._-]*(\d{1,4})\s*[_\-]\s*(\d{1,4})",
        r"[Ee](?:p(?:isodio|isode)?)[\s._-]*(\d{1,4})\s*[_\-]\s*(\d{1,4})",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue

        start_raw, end_raw = m.group(1), m.group(2)
        start_season, start_episode = _fuzzy_parse_cap_number(start_raw)
        end_season, end_episode = _fuzzy_parse_cap_number(end_raw)

        if start_episode is None or end_episode is None:
            continue
        if end_episode < start_episode:
            continue
        if (end_episode - start_episode) > 200:
            continue

        season = start_season if start_season is not None else end_season
        if start_season is not None and end_season is not None and start_season != end_season:
            continue

        return [(season, ep) for ep in range(start_episode, end_episode + 1)]

    return []


def parse_episode_info(filename: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Try to extract season and episode numbers from a filename.
    Returns (season, episode) or (None, None) if not found.

    Strategy:
      1. Normalise the filename (clean up separators)
      2. Try explicit season+episode patterns first (patterns 0-4)
         → returns (season, episode) directly
    3. Try episode-only patterns (patterns 5+)
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
        for m in re.finditer(pattern, name):
            raw_num = m.group(1)
            # Skip false positives: years (1950-2030), resolutions, codecs
            num = int(raw_num)
            if _is_false_positive(num, raw_num, name, m.start(1), m.end(1)):
                continue
            return _fuzzy_parse_cap_number(raw_num)

    return None, None


def _is_false_positive(
    num: int,
    raw: str,
    full_name: str,
    match_start: int,
    match_end: int,
) -> bool:
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
    # Season folder names like "Season 02" / "Temporada 2" should not
    # be mistaken for episode numbers when we parse parent folders.
    if _match_overlaps_season_hint(full_name, match_start, match_end):
        return True
    return False


def _match_overlaps_season_hint(text: str, match_start: int, match_end: int) -> bool:
    """
    Return True when a numeric match overlaps the numeric part of a
    season marker such as "Season 02", "S02" or "T2".
    """
    for pattern in SEASON_HINT_PATTERNS:
        for season_match in re.finditer(pattern, text, re.IGNORECASE):
            start, end = season_match.span(1)
            if match_start < end and match_end > start:
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


def _strip_trailing_unbalanced_brackets(text: str) -> str:
    """Remove dangling suffixes that start with an unclosed bracket."""
    cleaned = text
    while True:
        updated = re.sub(r"\s*[\[\(\{][^\]\)\}]*$", "", cleaned).strip(" -–—._")
        if updated == cleaned:
            return updated
        cleaned = updated


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

    # Remove trailing fragments such as "Show Name [1080p".
    name = _strip_trailing_unbalanced_brackets(name)

    # Replace dots, underscores with spaces
    name = name.replace(".", " ").replace("_", " ")

    # Remove extra whitespace and trailing dashes
    name = re.sub(r"\s+", " ", name).strip(" -–—[](){}")

    return name


def guess_series_name_from_path(root_path: Path, max_samples: int = 250) -> str:
    """
    Guess a series name by looking inside the selected folder structure.

    Preference order:
      1. Repeated candidates found in inner folder names
      2. Repeated candidates found in video filenames
      3. Root folder name as a fallback
    """
    root_path = Path(root_path)
    root_guess_raw = guess_series_name(root_path.name)
    root_guess = _series_candidate_from_text(root_path.name) or root_guess_raw

    if not root_path.is_dir():
        return root_guess

    scores: Counter[str] = Counter()
    display_names: dict[str, str] = {}

    def remember_candidate(raw_text: str, weight: int):
        candidate = _series_candidate_from_text(raw_text)
        if not candidate:
            return
        key = _series_name_key(candidate)
        if not key:
            return
        scores[key] += weight
        best_display = display_names.get(key, "")
        if len(candidate) > len(best_display):
            display_names[key] = candidate

    try:
        for child in root_path.iterdir():
            if child.is_dir():
                remember_candidate(child.name, 6)
            elif child.is_file() and (is_video_file(child) or is_rar_first_volume(child)):
                remember_candidate(child.stem, 5 if parse_episode_info(child.stem)[1] is not None else 2)
    except OSError:
        pass

    sampled_files = 0
    for dirpath, dirnames, filenames in os.walk(root_path):
        current_dir = Path(dirpath)

        if current_dir != root_path:
            remember_candidate(current_dir.name, 4 if current_dir.parent == root_path else 2)

        for fname in filenames:
            fpath = current_dir / fname
            if not (is_video_file(fpath) or is_rar_first_volume(fpath)):
                continue

            stem = fpath.stem
            parsed = parse_episode_info(stem)
            remember_candidate(stem, 5 if parsed[1] is not None else 2)

            if current_dir != root_path:
                parent_weight = 5 if _extract_season_from_string(current_dir.name) is not None else 3
                remember_candidate(current_dir.name, parent_weight)

            sampled_files += 1
            if sampled_files >= max_samples:
                break

        if sampled_files >= max_samples:
            break

    if scores:
        best_key, best_score = max(
            scores.items(),
            key=lambda item: (item[1], len(display_names.get(item[0], ""))),
        )
        if best_score >= 5 or _looks_like_generic_container(root_guess):
            return display_names[best_key]

    if _looks_like_generic_container(root_guess):
        return ""

    return root_guess


def _series_candidate_from_text(raw_text: str) -> str:
    """Build a clean candidate series name from a raw folder/file name."""
    candidate = guess_series_name(raw_text)
    if not candidate:
        return ""

    candidate = re.sub(r"\s+", " ", candidate).strip(" -–—._[](){}")
    candidate = _strip_trailing_unbalanced_brackets(candidate)
    candidate = re.sub(r"\b(19[5-9]\d|20[0-3]\d)$", "", candidate).strip(" -–—._[](){}")

    words = candidate.split()
    while words and _series_name_key(words[0]) in GENERIC_CONTAINER_HINTS:
        words.pop(0)
    while words and _series_name_key(words[-1]) in GENERIC_CONTAINER_HINTS:
        words.pop()
    candidate = " ".join(words).strip(" -–—._[](){}")
    candidate = _strip_trailing_unbalanced_brackets(candidate)

    if not _is_valid_series_candidate(candidate):
        return ""

    return candidate


def _is_valid_series_candidate(candidate: str) -> bool:
    """Filter out obvious non-series labels."""
    if not candidate:
        return False
    if candidate[0].isdigit():
        return False
    if not re.search(r"[A-Za-zÀ-ÿ]", candidate):
        return False

    normalized = _series_name_key(candidate)
    if normalized in SERIES_NAME_STOPWORDS:
        return False

    letters_only = re.sub(r"[^A-Za-zÀ-ÿ]", "", candidate)
    return len(letters_only) >= 3


def _series_name_key(name: str) -> str:
    """Normalise a candidate name for scoring and comparison."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-zÀ-ÿ0-9]+", " ", name)).strip().lower()


def _looks_like_generic_container(name: str) -> bool:
    """Return True when a folder name looks like a generic container label."""
    normalized = _series_name_key(name)
    if not normalized:
        return True
    words = normalized.split()
    return all(word in GENERIC_CONTAINER_HINTS for word in words)


def _guess_series_name_for_file(file_path: Path, root_path: Path, fallback: str) -> str:
    """
    Guess a series name for a concrete file path using local folder affinity.

    This is used by multi-series mode to keep episodes grouped by the most
    representative nearby folder names instead of forcing one global guess.
    """
    scores: Counter[str] = Counter()
    display_names: dict[str, str] = {}

    def remember(raw_text: str, weight: int):
        candidate = _series_candidate_from_text(raw_text)
        if not candidate:
            return
        key = _series_name_key(candidate)
        if not key:
            return
        scores[key] += max(weight, 1)
        best_display = display_names.get(key, "")
        if len(candidate) > len(best_display):
            display_names[key] = candidate

    file_candidate = _series_candidate_from_text(file_path.stem)
    if file_candidate:
        # File stem should dominate when multiple shows are mixed in one folder.
        remember(file_candidate, 12)

    current = file_path.parent
    depth = 0
    while True:
        folder_name = current.name
        folder_weight = max(8 - depth * 2, 2)

        if current == root_path and file_candidate:
            # In flat mixed folders, the root label is often generic/noisy.
            folder_weight = min(folder_weight, 2)

        if _extract_season_from_string(folder_name) is not None:
            # "Season 01" contributes less than a real series folder.
            folder_weight = max(folder_weight - 3, 1)
        remember(folder_name, folder_weight)

        if current == root_path or current == current.parent:
            break
        current = current.parent
        depth += 1

    remember(file_path.stem, 6)

    if scores:
        best_key, _ = max(
            scores.items(),
            key=lambda item: (item[1], len(display_names.get(item[0], ""))),
        )
        return display_names[best_key]

    return fallback


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

    default_series_guess = guess_series_name_from_path(root_path)

    for dirpath, dirnames, filenames in os.walk(root_path):
        dir_has_video = False
        dir_rar_first_volumes: List[Path] = []

        for fname in filenames:
            fpath = Path(dirpath) / fname
            if is_video_file(fpath):
                dir_has_video = True
                series_guess = _guess_series_name_for_file(fpath, root_path, default_series_guess)
                ep = EpisodeFile(original_path=fpath, series_guess=series_guess)

                season, episode = parse_episode_info(fname)
                if season is None:
                    season = _extract_season_from_string(Path(fname).stem)
                if season is None and episode is None:
                    parent_name = fpath.parent.name
                    season, episode = parse_episode_info(parent_name)
                    if season is None:
                        season = _extract_season_from_string(parent_name)

                ep.season = season
                ep.episode = episode
                episodes.append(ep)

            elif is_rar_first_volume(fpath):
                dir_rar_first_volumes.append(fpath)

        # If this directory has RAR files but NO video files, treat each
        # first RAR volume as a compressed episode.
        if not dir_has_video and dir_rar_first_volumes:
            for rar_file in sorted(dir_rar_first_volumes, key=lambda p: p.name.lower()):
                members = _list_rar_video_members(rar_file)

                # Best case: archive listing is available and contains
                # one or more video files. Create one EpisodeFile per member.
                if members:
                    for member in members:
                        member_name = Path(member).name
                        member_ext = Path(member).suffix.lower() or _find_rar_video_extension(rar_file)

                        ep = EpisodeFile(
                            original_path=rar_file,
                            series_guess=_guess_series_name_for_file(rar_file, root_path, default_series_guess),
                            needs_extract=True,
                            archive_member=member,
                        )
                        ep.extension = member_ext

                        # Prefer episode parsing from the member filename.
                        season, episode = parse_episode_info(member_name)
                        if season is None:
                            season = _extract_season_from_string(member_name)

                        # Fallback to archive filename or parent folder.
                        if season is None and episode is None:
                            season, episode = parse_episode_info(rar_file.name)
                            if season is None:
                                season = _extract_season_from_string(rar_file.stem)
                        if season is None and episode is None:
                            season, episode = parse_episode_info(rar_file.parent.name)
                            if season is None:
                                season = _extract_season_from_string(rar_file.parent.name)

                        ep.season = season
                        ep.episode = episode
                        episodes.append(ep)
                    continue

                # Fallback when archive listing is unavailable (e.g. rarfile
                # not installed): expand compact ranges from archive filename.
                range_items = _parse_episode_range(rar_file.stem)
                if range_items:
                    vid_ext = _find_rar_video_extension(rar_file)
                    for season, episode in range_items:
                        ep = EpisodeFile(
                            original_path=rar_file,
                            series_guess=_guess_series_name_for_file(rar_file, root_path, default_series_guess),
                            needs_extract=True,
                        )
                        ep.extension = vid_ext
                        ep.season = season
                        ep.episode = episode
                        episodes.append(ep)
                    continue

                # Last resort: treat archive as a single unknown/partially
                # known episode.
                vid_ext = _find_rar_video_extension(rar_file)
                ep = EpisodeFile(
                    original_path=rar_file,
                    series_guess=_guess_series_name_for_file(rar_file, root_path, default_series_guess),
                    needs_extract=True,
                )
                ep.extension = vid_ext

                season, episode = parse_episode_info(rar_file.name)
                if season is None:
                    season = _extract_season_from_string(rar_file.stem)
                if season is None and episode is None:
                    season, episode = parse_episode_info(rar_file.parent.name)
                    if season is None:
                        season = _extract_season_from_string(rar_file.parent.name)

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
    for pattern in SEASON_HINT_PATTERNS:
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
