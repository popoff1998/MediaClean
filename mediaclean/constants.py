"""Constants used across the application."""

# Supported video file extensions
VIDEO_EXTENSIONS = {
    ".mkv", ".avi", ".mp4", ".m4v", ".wmv", ".flv", ".mov",
    ".mpg", ".mpeg", ".ts", ".webm", ".ogv", ".divx", ".xvid",
    ".3gp", ".asf", ".vob", ".rm", ".rmvb",
}

# RAR archive extensions (first volume patterns)
RAR_EXTENSIONS = {
    ".rar",
}

# Common junk patterns to ignore when parsing filenames
JUNK_PATTERNS = [
    r"\b(720p|1080p|2160p|4k|uhd)\b",
    r"\b(bluray|bdrip|brrip|webrip|web-dl|webdl|hdtv|dvdrip|hdrip)\b",
    r"\b(x264|x265|h\.?264|h\.?265|hevc|avc|aac|ac3|dts|flac|mp3)\b",
    r"\b(proper|repack|internal|real)\b",
    r"\b(multi|dual|spanish|english|latino|castellano|spa|eng)\b",
    r"\[.*?\]",
    r"\(.*?\)",
    r"\{.*?\}",
]

# Regex patterns to extract season/episode from filenames.
# Ordered by specificity: most explicit patterns first.
# Group 1 = season (when present), Group 2 = episode (or Group 1 = episode if no season).
EPISODE_PATTERNS = [
    # ── Explicit Season + Episode (unambiguous) ──

    # S01E01, s01e01, S1E1  (also S01.E01, S01-E01, S01_E01, S01 E01)
    r"[Ss](\d{1,2})[\s._-]*[Ee](\d{1,3})",
    # Multi-episode: S01E01E02, S01E01-E02  → capture first episode
    # (already covered by the pattern above since it stops at first match)

    # 1x01, 01x01
    r"(\d{1,2})[xX](\d{1,3})",

    # T01E01, T1E01  (Spanish: Temporada / Episodio)
    r"[Tt](\d{1,2})[\s._-]*[Ee](\d{1,3})",

    # Season 1 Episode 1, Season.1.Episode.1, Season 1 - Episode 1
    r"[Ss]eason[\s._-]*(\d{1,2})[\s._-]*(?:[-–—]?[\s._-]*)[Ee]pisode[\s._-]*(\d{1,3})",

    # Temporada 1 Capitulo 5, Temporada.1.Capitulo.5, Temp 1 Cap 5,
    # Temporada 1 - Capitulo 5, Temporada 1 Episodio 5, Temp.1.Ep.5
    r"[Tt](?:emporada|emp)[\s._-]*(\d{1,2})[\s._-]*(?:[-–—]?[\s._-]*)(?:[Cc]ap(?:itulo)?|[Ee](?:p(?:isodio)?)?)[\s._-]*(\d{1,3})",

    # ── Episode only (season inferred later) ──

    # Capitulo 01, Cap 01, Cap.01, Cap.401, Cap.1205, Capítulo 5
    r"[Cc]ap(?:[ií]tulo)?[\s._-]*(\d{1,4})",

    # Episode 01, Episodio 01, Ep.01, Ep 01, Ep01
    r"[Ee](?:p(?:isodio|isode)?)[\s._-]*(\d{1,4})",

    # Standalone "E01" not preceded by S/T/season marker
    r"(?<![SsTt\d])[Ee](\d{2,3})(?=[\s._\-\[\(]|$)",

    # Bare episode number after a separator: "- 01", "- 001"
    r"[\-–—]\s*(\d{2,4})(?=[\s._\[\(]|$)",

    # Bare 2-4 digit number surrounded by separators (last resort)
    # e.g. "Series.Name.01.720p" or "Series Name - 01 - Title"
    r"(?<=[\s._\-])(\d{2,4})(?=[\s._\-\[\(]|$)",
]

# Default output folder name
DEFAULT_OUTPUT_FOLDER = "_MediaClean_Output"

# TMDB base URLs
TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w200"

# OMDB base URL
OMDB_API_BASE = "https://www.omdbapi.com/"

# Wikidata SPARQL endpoint (used to translate IMDB titles to Spanish)
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
