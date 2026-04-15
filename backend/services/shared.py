"""
shared.py — Cache, HTTP client, and common utilities
Used by both functions.py and vm.py
"""

import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from cachetools import TTLCache
import threading

# ── Cache ─────────────────────────────────────────────────────────────────────
_price_cache  = TTLCache(maxsize=100, ttl=3600)
_meta_cache   = TTLCache(maxsize=10,  ttl=86400)
_cache_lock   = threading.Lock()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://azure.microsoft.com/en-us/pricing/calculator/",
}

# ── HTTP session with retry + connection pooling ──────────────────────────────
def _make_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,          # waits 1s, 2s, 4s between retries
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update(HEADERS)
    return session

_session = _make_session()
_session_lock = threading.Lock()


def fetch_url(url, cache, cache_key, timeout=45):
    """Fetch URL with cache and retry. Thread-safe."""
    with _cache_lock:
        if cache_key in cache:
            return cache[cache_key], None
    try:
        with _session_lock:
            resp = _session.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        with _cache_lock:
            cache[cache_key] = data
        return data, None
    except Exception as e:
        return None, str(e)


def fetch_pricing(url, cache_key):
    return fetch_url(url, _price_cache, cache_key)


def fetch_metadata(url, cache_key):
    return fetch_url(url, _meta_cache, cache_key)


# ── Graduated price walker ────────────────────────────────────────────────────
def get_graduated_price(tiers_list, quantity):
    """Walk graduated tiers like tax brackets and return total cost."""
    prev_limit = 0
    total = 0.0
    remaining = quantity
    for tier in tiers_list:
        limit = tier["limit"] if tier["limit"] < 1e300 else float("inf")
        bucket = min(remaining, limit - prev_limit) if limit != float("inf") else remaining
        if bucket <= 0:
            break
        total += bucket * tier["price"]["value"]
        remaining -= bucket
        prev_limit = limit
        if remaining <= 0:
            break
    return total