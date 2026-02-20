"""
Wikidata client: translates IMDB IDs to Spanish (Castilian) titles
using the Wikidata SPARQL endpoint.

OMDB only returns titles in English.  Wikidata stores multilingual
labels for TV series and episodes indexed by IMDB ID (property P345).
By batch-querying Wikidata we can replace English titles with their
Spanish equivalents.

Endpoint documentation:
    https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service
"""

import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List

from mediaclean.constants import WIKIDATA_SPARQL_URL

log = logging.getLogger(__name__)

# Maximum IMDB IDs per SPARQL query (to stay within payload limits).
_BATCH_SIZE = 200


class WikidataClient:
    """Queries Wikidata to obtain Spanish-language titles for IMDB entities."""

    def __init__(self, language: str = "es"):
        # Primary language for labels.  Falls back to English when the
        # requested language is not available.
        self.language = language

    # ── public API ───────────────────────────────────────────────────

    def get_labels(self, imdb_ids: List[str]) -> Dict[str, str]:
        """
        Given a list of IMDB IDs (e.g. ``["tt0903747", "tt0959621"]``),
        return a dict mapping each IMDB ID to its label in the
        configured language.

        Missing entries (no Wikidata item or no label) are silently
        omitted from the result.
        """
        if not imdb_ids:
            return {}

        labels: Dict[str, str] = {}
        for start in range(0, len(imdb_ids), _BATCH_SIZE):
            batch = imdb_ids[start : start + _BATCH_SIZE]
            labels.update(self._query_batch(batch))
        return labels

    # ── internals ────────────────────────────────────────────────────

    def _query_batch(self, imdb_ids: List[str]) -> Dict[str, str]:
        """Run a single SPARQL query for a batch of IMDB IDs."""
        values = " ".join(f'"{iid}"' for iid in imdb_ids)

        sparql = (
            "SELECT ?imdbId ?itemLabel WHERE {\n"
            f"  VALUES ?imdbId {{ {values} }}\n"
            "  ?item wdt:P345 ?imdbId .\n"
            "  SERVICE wikibase:label { bd:serviceParam wikibase:language "
            f'"{self.language},en" . }}\n'
            "}\n"
        )

        rows = self._sparql_request(sparql)

        labels: Dict[str, str] = {}
        for row in rows:
            imdb_id = row.get("imdbId", {}).get("value", "")
            label = row.get("itemLabel", {}).get("value", "")
            if not imdb_id or not label:
                continue
            # When Wikidata has no label at all it returns the Q-id
            # (e.g. "Q12345").  Skip those.
            if label.startswith("Q") and label[1:].isdigit():
                continue
            labels[imdb_id] = label

        return labels

    def _sparql_request(self, sparql: str) -> list:
        """Execute a SPARQL query via POST against the Wikidata Query Service."""
        body = urllib.parse.urlencode({"query": sparql}).encode("utf-8")

        req = urllib.request.Request(
            WIKIDATA_SPARQL_URL, data=body, method="POST"
        )
        req.add_header("Accept", "application/sparql-results+json")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        req.add_header("User-Agent", "MediaClean/1.0 (TV series renamer)")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", {}).get("bindings", [])
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            log.warning("Wikidata query failed: %s", exc)
            return []
        except (json.JSONDecodeError, KeyError) as exc:
            log.warning("Wikidata response parse error: %s", exc)
            return []


class WikidataError(Exception):
    """Custom exception for Wikidata errors."""
    pass
