"""
TMDB client: searches for TV series and retrieves episode metadata
using The Movie Database (TMDB) API v3.
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from mediaclean.constants import TMDB_API_BASE, TMDB_IMAGE_BASE


@dataclass
class TMDBEpisode:
    """Episode metadata from TMDB."""
    season: int
    episode: int
    name: str
    overview: str = ""
    air_date: str = ""
    still_path: str = ""


@dataclass
class TMDBSeries:
    """TV series metadata from TMDB."""
    tmdb_id: int
    name: str
    original_name: str = ""
    overview: str = ""
    first_air_date: str = ""
    poster_path: str = ""
    seasons_count: int = 0
    episodes: Dict[str, TMDBEpisode] = field(default_factory=dict)

    @property
    def poster_url(self) -> str:
        if self.poster_path:
            # OMDB stores full URLs; TMDB stores relative paths
            if self.poster_path.startswith("http"):
                return self.poster_path
            return f"{TMDB_IMAGE_BASE}{self.poster_path}"
        return ""

    def get_episode(self, season: int, episode: int) -> Optional[TMDBEpisode]:
        key = f"S{season:02d}E{episode:02d}"
        return self.episodes.get(key)


class TMDBClient:
    """Client to interact with TMDB API v3."""

    def __init__(self, api_key: str, language: str = "es-ES"):
        self.api_key = api_key
        self.language = language

    def _request(self, endpoint: str, params: Optional[Dict[str, str]] = None) -> Any:
        """Make a GET request to the TMDB API."""
        if params is None:
            params = {}
        params["api_key"] = self.api_key
        params["language"] = self.language

        url = f"{TMDB_API_BASE}{endpoint}?{urllib.parse.urlencode(params)}"

        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except urllib.error.HTTPError as e:
            raise TMDBError(f"HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise TMDBError(f"Connection error: {e.reason}") from e

    def search_series(self, query: str) -> List[TMDBSeries]:
        """Search for TV series by name. Returns a list of matches."""
        data = self._request("/search/tv", {"query": query})
        results: List[TMDBSeries] = []

        for item in data.get("results", []):
            series = TMDBSeries(
                tmdb_id=item["id"],
                name=item.get("name", ""),
                original_name=item.get("original_name", ""),
                overview=item.get("overview", ""),
                first_air_date=item.get("first_air_date", ""),
                poster_path=item.get("poster_path", ""),
            )
            results.append(series)

        return results

    def get_series_details(self, tmdb_id: int) -> TMDBSeries:
        """Get detailed information about a TV series."""
        data = self._request(f"/tv/{tmdb_id}")

        series = TMDBSeries(
            tmdb_id=tmdb_id,
            name=data.get("name", ""),
            original_name=data.get("original_name", ""),
            overview=data.get("overview", ""),
            first_air_date=data.get("first_air_date", ""),
            poster_path=data.get("poster_path", ""),
            seasons_count=data.get("number_of_seasons", 0),
        )
        return series

    def get_season_episodes(self, tmdb_id: int, season_number: int) -> List[TMDBEpisode]:
        """Get all episodes for a specific season."""
        data = self._request(f"/tv/{tmdb_id}/season/{season_number}")
        episodes: List[TMDBEpisode] = []

        for item in data.get("episodes", []):
            ep = TMDBEpisode(
                season=item.get("season_number", season_number),
                episode=item.get("episode_number", 0),
                name=item.get("name", ""),
                overview=item.get("overview", ""),
                air_date=item.get("air_date", ""),
                still_path=item.get("still_path", ""),
            )
            episodes.append(ep)

        return episodes

    def load_episodes_for_series(self, series: TMDBSeries, seasons: Optional[List[int]] = None):
        """
        Load episode metadata for the given seasons into the series object.
        If seasons is None, loads all seasons.
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
            except TMDBError:
                # Season might not exist or have issues – skip
                continue


class TMDBError(Exception):
    """Custom exception for TMDB API errors."""
    pass
