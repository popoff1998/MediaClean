"""
OMDB client: searches for TV series and retrieves episode metadata
using the Open Movie Database (OMDB) API.

OMDB is a free alternative to TMDB. Get a key at:
    https://www.omdbapi.com/apikey.aspx
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Optional, Dict, Any

from mediaclean.constants import OMDB_API_BASE
from mediaclean.tmdb_client import TMDBSeries, TMDBEpisode


class OMDBClient:
    """
    Client to interact with the OMDB API.

    Returns TMDBSeries / TMDBEpisode objects so it can be used as a
    drop-in alternative to TMDBClient.  The ``tmdb_id`` field stores
    the numeric part of the IMDB id (e.g. tt0903747 → 903747) so the
    rest of the application works without changes.
    """

    def __init__(self, api_key: str, language: str = "es-ES"):
        self.api_key = api_key
        # OMDB doesn't support language selection, but we keep the
        # parameter for interface compatibility.
        self.language = language

    # ── low-level request ────────────────────────────────────────────

    def _request(self, params: Dict[str, str]) -> Any:
        """Make a GET request to the OMDB API."""
        params["apikey"] = self.api_key

        url = f"{OMDB_API_BASE}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise OMDBError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise OMDBError(f"Error de conexión: {e.reason}") from e

        # OMDB signals errors inside the JSON body
        if data.get("Response") == "False":
            raise OMDBError(data.get("Error", "Unknown OMDB error"))

        return data

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _imdb_id_to_int(imdb_id: str) -> int:
        """Convert 'tt0903747' → 903747."""
        return int(imdb_id.replace("tt", ""))

    @staticmethod
    def _int_to_imdb_id(num: int) -> str:
        """Convert 903747 → 'tt0903747'."""
        return f"tt{num:07d}"

    # ── public API (mirrors TMDBClient interface) ────────────────────

    def search_series(self, query: str) -> List[TMDBSeries]:
        """Search for TV series by name.  Returns a list of matches."""
        data = self._request({"s": query, "type": "series"})
        results: List[TMDBSeries] = []

        for item in data.get("Search", []):
            imdb_id = item.get("imdbID", "")
            poster = item.get("Poster", "")
            if poster == "N/A":
                poster = ""

            series = TMDBSeries(
                tmdb_id=self._imdb_id_to_int(imdb_id) if imdb_id else 0,
                name=item.get("Title", ""),
                original_name=item.get("Title", ""),
                overview="",
                first_air_date=item.get("Year", ""),
                poster_path=poster,  # OMDB gives full URL
            )
            results.append(series)

        return results

    def get_series_details(self, tmdb_id: int) -> TMDBSeries:
        """Get detailed information about a TV series by numeric id."""
        imdb_id = self._int_to_imdb_id(tmdb_id)
        data = self._request({"i": imdb_id, "type": "series"})

        poster = data.get("Poster", "")
        if poster == "N/A":
            poster = ""

        total_seasons = 0
        try:
            total_seasons = int(data.get("totalSeasons", 0))
        except (ValueError, TypeError):
            pass

        series = TMDBSeries(
            tmdb_id=tmdb_id,
            name=data.get("Title", ""),
            original_name=data.get("Title", ""),
            overview=data.get("Plot", ""),
            first_air_date=data.get("Year", ""),
            poster_path=poster,
            seasons_count=total_seasons,
        )
        return series

    def get_season_episodes(self, tmdb_id: int, season_number: int) -> List[TMDBEpisode]:
        """Get all episodes for a specific season."""
        imdb_id = self._int_to_imdb_id(tmdb_id)
        data = self._request({"i": imdb_id, "Season": str(season_number)})

        episodes: List[TMDBEpisode] = []
        for item in data.get("Episodes", []):
            title = item.get("Title", "")
            if title == "N/A":
                title = ""
            ep = TMDBEpisode(
                season=season_number,
                episode=int(item.get("Episode", 0)),
                name=title,
                overview="",
                air_date=item.get("Released", ""),
                still_path="",
            )
            episodes.append(ep)

        return episodes

    def load_episodes_for_series(
        self,
        series: TMDBSeries,
        seasons: Optional[List[int]] = None,
    ):
        """
        Load episode metadata for the given seasons into the series
        object.  If *seasons* is None, loads all seasons.
        """
        if seasons is None:
            details = self.get_series_details(series.tmdb_id)
            series.seasons_count = details.seasons_count
            seasons = list(range(1, details.seasons_count + 1))

        for s in seasons:
            try:
                eps = self.get_season_episodes(series.tmdb_id, s)
                for ep in eps:
                    key = f"S{ep.season:02d}E{ep.episode:02d}"
                    series.episodes[key] = ep
            except OMDBError:
                # Season might not exist – skip
                continue


class OMDBError(Exception):
    """Custom exception for OMDB API errors."""
    pass
