"""
TVDB client: searches for TV series and retrieves episode metadata
using TheTVDB API v4.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from mediaclean.constants import TVDB_API_BASE
from mediaclean.tmdb_client import TMDBEpisode, TMDBSeries


TVDB_LANGUAGE_MAP = {
    "es-ES": "spa",
    "en-US": "eng",
    "pt-BR": "por",
    "fr-FR": "fra",
    "de-DE": "deu",
    "it-IT": "ita",
}


class TVDBClient:
    """Client to interact with TheTVDB API v4."""

    def __init__(self, api_key: str, language: str = "es-ES", pin: str = ""):
        self.api_key = api_key.strip()
        self.language = language
        self.pin = pin.strip()
        self.language_code = TVDB_LANGUAGE_MAP.get(language, "eng")

        self._token: Optional[str] = None
        self._season_map_cache: Dict[int, Dict[int, int]] = {}
        self._series_translation_cache: Dict[tuple[int, str], Dict[str, str]] = {}
        self._episode_translation_cache: Dict[tuple[int, str], Dict[str, str]] = {}

    # ── low-level HTTP ──────────────────────────────────────────────

    def _login(self):
        payload = {"apikey": self.api_key}
        if self.pin:
            payload["pin"] = self.pin

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{TVDB_API_BASE}/login",
            data=body,
            method="POST",
        )
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "MediaClean/1.0")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise TVDBError(self._format_http_error(e)) from e
        except urllib.error.URLError as e:
            raise TVDBError(f"Error de conexión con TVDB: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise TVDBError("TVDB devolvió una respuesta inválida al iniciar sesión.") from e

        token = payload.get("data", {}).get("token")
        if not token:
            raise TVDBError("TVDB no devolvió un token válido.")
        self._token = token

    def _request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        retry_auth: bool = True,
    ) -> Any:
        if self._token is None:
            self._login()

        url = f"{TVDB_API_BASE}{endpoint}"
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean_params:
                url = f"{url}?{urllib.parse.urlencode(clean_params)}"

        req = urllib.request.Request(url)
        req.add_header("Accept", "application/json")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("User-Agent", "MediaClean/1.0")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 401 and retry_auth:
                self._token = None
                return self._request(endpoint, params=params, retry_auth=False)
            raise TVDBError(self._format_http_error(e)) from e
        except urllib.error.URLError as e:
            raise TVDBError(f"Error de conexión con TVDB: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise TVDBError("TVDB devolvió una respuesta JSON inválida.") from e

        if isinstance(payload, dict):
            return payload.get("data")
        return payload

    @staticmethod
    def _format_http_error(exc: urllib.error.HTTPError) -> str:
        message = exc.reason
        try:
            body = exc.read().decode("utf-8", errors="ignore")
            if body:
                data = json.loads(body)
                message = data.get("message") or data.get("status") or message
        except Exception:
            pass
        return f"HTTP {exc.code}: {message}"

    # ── public API ──────────────────────────────────────────────────

    def search_series(self, query: str) -> List[TMDBSeries]:
        data = self._request(
            "/search",
            {
                "query": query,
                "type": "series",
                "language": self.language_code,
                "limit": 20,
            },
        )

        results: List[TMDBSeries] = []
        seen_ids: set[int] = set()

        for item in self._as_list(data):
            record_type = str(item.get("type", "")).lower()
            if record_type and record_type != "series":
                continue

            series_id = self._extract_numeric_id(
                item.get("tvdb_id"),
                item.get("id"),
                item.get("objectID"),
            )
            if series_id is None or series_id in seen_ids:
                continue
            seen_ids.add(series_id)

            translated_name = self._first_non_empty(
                item.get("name_translated"),
                item.get("name"),
                item.get("title"),
            )
            original_name = self._first_non_empty(
                item.get("name"),
                item.get("name_translated"),
                translated_name,
            )
            overview = self._extract_overview(item)
            poster = self._first_non_empty(
                item.get("image_url"),
                item.get("thumbnail"),
                item.get("poster"),
            )
            first_air = self._first_non_empty(
                item.get("year"),
                item.get("first_air_time"),
                item.get("firstAired"),
            )

            results.append(
                TMDBSeries(
                    tmdb_id=series_id,
                    name=translated_name or original_name or str(series_id),
                    original_name=original_name or translated_name or "",
                    overview=overview,
                    first_air_date=first_air or "",
                    poster_path=poster or "",
                )
            )

        return results

    def get_series_details(self, tvdb_id: int) -> TMDBSeries:
        data = self._request(f"/series/{tvdb_id}/extended", {"meta": "translations"})
        if not isinstance(data, dict):
            raise TVDBError("TVDB devolvió un detalle de serie inesperado.")

        self._cache_season_map(tvdb_id, data.get("seasons"))
        translation = self._get_series_translation(tvdb_id)

        original_name = self._first_non_empty(data.get("name"), translation.get("name"))
        translated_name = self._first_non_empty(translation.get("name"), data.get("name"), original_name)
        overview = self._first_non_empty(translation.get("overview"), data.get("overview"), "")

        season_numbers = {
            season_number
            for season_number in self._season_map_cache.get(tvdb_id, {})
            if season_number >= 1
        }

        return TMDBSeries(
            tmdb_id=tvdb_id,
            name=translated_name or original_name or str(tvdb_id),
            original_name=original_name or translated_name or "",
            overview=overview,
            first_air_date=self._first_non_empty(data.get("firstAired"), data.get("year"), ""),
            poster_path=self._pick_series_image(data),
            seasons_count=len(season_numbers),
        )

    def get_season_episodes(self, tvdb_id: int, season_number: int) -> List[TMDBEpisode]:
        season_map = self._season_map_cache.get(tvdb_id)
        if not season_map or season_number not in season_map:
            self.get_series_details(tvdb_id)
            season_map = self._season_map_cache.get(tvdb_id, {})

        raw_episodes: List[Dict[str, Any]] = []
        season_id = season_map.get(season_number)

        if season_id is not None:
            season_data = self._request(f"/seasons/{season_id}/extended")
            if isinstance(season_data, dict):
                raw_episodes = self._as_list(season_data.get("episodes"))

        if not raw_episodes:
            raw_episodes = self._get_series_episodes_fallback(tvdb_id, season_number)

        episodes: List[TMDBEpisode] = []
        seen_numbers: set[tuple[int, int]] = set()

        for item in raw_episodes:
            ep_num = self._extract_numeric_id(item.get("number"), item.get("episodeNumber"))
            ep_season = self._extract_numeric_id(item.get("seasonNumber"))
            ep_id = self._extract_numeric_id(item.get("id"))

            if ep_num is None or ep_num <= 0:
                continue
            if ep_season is None:
                ep_season = season_number
            if ep_season != season_number:
                continue

            key = (ep_season, ep_num)
            if key in seen_numbers:
                continue
            seen_numbers.add(key)

            name = self._first_non_empty(item.get("name"), "")
            overview = self._first_non_empty(item.get("overview"), "")
            if ep_id is not None:
                translation = self._get_episode_translation(ep_id)
                name = self._first_non_empty(translation.get("name"), name)
                overview = self._first_non_empty(translation.get("overview"), overview)

            episodes.append(
                TMDBEpisode(
                    season=ep_season,
                    episode=ep_num,
                    name=name or f"Episode {ep_num}",
                    overview=overview,
                    air_date=self._first_non_empty(item.get("aired"), item.get("year"), ""),
                    still_path=self._first_non_empty(item.get("image"), ""),
                )
            )

        episodes.sort(key=lambda ep: ep.episode)
        return episodes

    def load_episodes_for_series(self, series: TMDBSeries, seasons: Optional[List[int]] = None):
        details = self.get_series_details(series.tmdb_id)

        if details.name:
            series.name = details.name
        if details.original_name:
            series.original_name = details.original_name
        if details.overview:
            series.overview = details.overview
        if details.first_air_date:
            series.first_air_date = details.first_air_date
        if details.poster_path:
            series.poster_path = details.poster_path
        if details.seasons_count:
            series.seasons_count = details.seasons_count

        if seasons is None:
            available = sorted(
                season_number
                for season_number in self._season_map_cache.get(series.tmdb_id, {})
                if season_number >= 1
            )
            seasons = available or list(range(1, max(series.seasons_count, 0) + 1))

        for season_number in seasons:
            try:
                for episode in self.get_season_episodes(series.tmdb_id, season_number):
                    key = f"S{episode.season:02d}E{episode.episode:02d}"
                    series.episodes[key] = episode
            except TVDBError:
                continue

    # ── helpers ─────────────────────────────────────────────────────

    def _get_series_episodes_fallback(self, tvdb_id: int, season_number: int) -> List[Dict[str, Any]]:
        episodes: List[Dict[str, Any]] = []
        seen_ids: set[int] = set()
        page = 0

        while True:
            data = self._request(
                f"/series/{tvdb_id}/episodes/official",
                {
                    "page": page,
                    "season": season_number,
                },
            )
            if not isinstance(data, dict):
                break

            page_items = self._as_list(data.get("episodes"))
            if not page_items:
                break

            new_items = 0
            for item in page_items:
                ep_id = self._extract_numeric_id(item.get("id"))
                if ep_id is not None and ep_id in seen_ids:
                    continue
                if ep_id is not None:
                    seen_ids.add(ep_id)
                episodes.append(item)
                new_items += 1

            if new_items == 0:
                break
            page += 1

        return episodes

    def _get_series_translation(self, tvdb_id: int) -> Dict[str, str]:
        cache_key = (tvdb_id, self.language_code)
        if cache_key not in self._series_translation_cache:
            self._series_translation_cache[cache_key] = self._safe_translation_request(
                f"/series/{tvdb_id}/translations/{self.language_code}"
            )
        return self._series_translation_cache[cache_key]

    def _get_episode_translation(self, episode_id: int) -> Dict[str, str]:
        cache_key = (episode_id, self.language_code)
        if cache_key not in self._episode_translation_cache:
            self._episode_translation_cache[cache_key] = self._safe_translation_request(
                f"/episodes/{episode_id}/translations/{self.language_code}"
            )
        return self._episode_translation_cache[cache_key]

    def _safe_translation_request(self, endpoint: str) -> Dict[str, str]:
        if self.language_code == "eng":
            return {}
        try:
            data = self._request(endpoint)
        except TVDBError:
            return {}
        if not isinstance(data, dict):
            return {}
        return {
            "name": self._first_non_empty(data.get("name"), ""),
            "overview": self._first_non_empty(data.get("overview"), ""),
        }

    def _cache_season_map(self, tvdb_id: int, seasons: Any):
        season_map: Dict[int, int] = {}
        for item in self._as_list(seasons):
            season_id = self._extract_numeric_id(item.get("id"))
            season_number = self._extract_numeric_id(item.get("number"), item.get("seasonNumber"))
            if season_id is None or season_number is None:
                continue
            season_map.setdefault(season_number, season_id)
        self._season_map_cache[tvdb_id] = season_map

    @staticmethod
    def _pick_series_image(data: Dict[str, Any]) -> str:
        image = TVDBClient._first_non_empty(
            data.get("image"),
            data.get("image_url"),
            data.get("thumbnail"),
        )
        if image:
            return image

        for artwork in TVDBClient._as_list(data.get("artworks")):
            art_image = TVDBClient._first_non_empty(artwork.get("image"), artwork.get("thumbnail"))
            if art_image:
                return art_image

        return ""

    @staticmethod
    def _extract_overview(item: Dict[str, Any]) -> str:
        overview = TVDBClient._first_non_empty(item.get("overview"), "")
        if overview:
            return overview

        translated = item.get("overview_translated")
        if isinstance(translated, str):
            return translated
        if isinstance(translated, list):
            for entry in translated:
                if isinstance(entry, str) and entry.strip():
                    return entry.strip()

        overviews = item.get("overviews")
        if isinstance(overviews, dict):
            for value in overviews.values():
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return ""

    @staticmethod
    def _first_non_empty(*values: Any) -> str:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _as_list(value: Any) -> List[Dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _extract_numeric_id(*values: Any) -> Optional[int]:
        for value in values:
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str):
                match = re.search(r"\d+", value)
                if match:
                    return int(match.group(0))
        return None


class TVDBError(Exception):
    """Custom exception for TVDB API errors."""

    pass
