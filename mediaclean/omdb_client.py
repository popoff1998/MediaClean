"""
OMDB client: searches for TV series and retrieves episode metadata
using the Open Movie Database (OMDB) API.

OMDB is a free alternative to TMDB. Get a key at:
    https://www.omdbapi.com/apikey.aspx
"""

import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Optional, Dict, Any

from mediaclean.constants import OMDB_API_BASE
from mediaclean.tmdb_client import TMDBSeries, TMDBEpisode
from mediaclean.wikidata_client import WikidataClient

log = logging.getLogger(__name__)


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

        # Wikidata is used to translate English OMDB titles to Spanish.
        self._wikidata = WikidataClient(language=language.split("-")[0])
        # Temporary mapping: episode key ("S01E01") → IMDB ID
        self._episode_imdb_map: Dict[str, str] = {}

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
        imdb_ids: List[str] = []

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
            if imdb_id:
                imdb_ids.append(imdb_id)

        # Translate series names to Spanish via Wikidata
        self._translate_series_names(results, imdb_ids)

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
            ep_num = int(item.get("Episode", 0))
            ep = TMDBEpisode(
                season=season_number,
                episode=ep_num,
                name=title,
                overview="",
                air_date=item.get("Released", ""),
                still_path="",
            )
            episodes.append(ep)

            # Store IMDB ID for later Wikidata translation
            ep_imdb_id = item.get("imdbID", "")
            if ep_imdb_id:
                key = f"S{season_number:02d}E{ep_num:02d}"
                self._episode_imdb_map[key] = ep_imdb_id

        return episodes

    def load_episodes_for_series(
        self,
        series: TMDBSeries,
        seasons: Optional[List[int]] = None,
    ):
        """
        Load episode metadata for the given seasons into the series
        object.  If *seasons* is None, loads all seasons.

        After all OMDB data is fetched the method queries Wikidata to
        replace English titles with Spanish ones where available.
        """
        self._episode_imdb_map.clear()

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

        # Translate series name + episode titles to Spanish
        self._apply_wikidata_translations(series)

    # ── Wikidata translation helpers ─────────────────────────────────

    def _translate_series_names(
        self,
        series_list: List[TMDBSeries],
        imdb_ids: List[str],
    ):
        """
        Replace the *name* of each series in *series_list* with the
        Spanish label from Wikidata (when available).
        """
        try:
            labels = self._wikidata.get_labels(imdb_ids)
            for series_obj in series_list:
                iid = self._int_to_imdb_id(series_obj.tmdb_id)
                if iid in labels:
                    series_obj.name = labels[iid]
        except Exception:
            # Wikidata is best-effort; keep English names on failure.
            log.debug("Wikidata translation skipped for search results", exc_info=True)

    def _apply_wikidata_translations(self, series: TMDBSeries):
        """
        After all OMDB data is loaded, batch-query Wikidata for the
        Spanish labels of the series **and** every episode, then
        overwrite the English titles where a translation exists.
        """
        try:
            series_imdb = self._int_to_imdb_id(series.tmdb_id)
            all_ids = [series_imdb] + list(self._episode_imdb_map.values())

            labels = self._wikidata.get_labels(all_ids)

            # Series name
            if series_imdb in labels:
                series.name = labels[series_imdb]

            # Episode names
            for key, ep_imdb in self._episode_imdb_map.items():
                if ep_imdb in labels and key in series.episodes:
                    series.episodes[key].name = labels[ep_imdb]
        except Exception:
            # Wikidata is optional – keep English names on failure.
            log.debug("Wikidata translation skipped for episodes", exc_info=True)


class OMDBError(Exception):
    """Custom exception for OMDB API errors."""
    pass
