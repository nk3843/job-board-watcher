import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = "job-watch/1.0 (personal job-search script; checks public job boards once a day)"


class FetchError(Exception):
    pass


class Http:
    """Small wrapper around requests with retries and one session per thread."""

    def __init__(self, timeout: int = 20):
        self.timeout = timeout
        self._local = threading.local()

    @property
    def session(self) -> requests.Session:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=frozenset(["GET", "POST"]), raise_on_status=False)
            adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            s.headers.update({"User-Agent": UA})
            self._local.session = s
        return s

    def _json(self, r: requests.Response, url: str):
        if r.status_code != 200:
            raise FetchError(f"HTTP {r.status_code} from {url}")
        try:
            return r.json()
        except ValueError:
            raise FetchError(f"Non-JSON response from {url}")

    def get_json(self, url: str, params=None):
        try:
            r = self.session.get(url, params=params, timeout=self.timeout, headers={"Accept": "application/json"})
        except requests.RequestException as e:
            raise FetchError(f"{type(e).__name__} for {url}") from e
        return self._json(r, url)

    def post_json(self, url: str, payload: dict):
        try:
            r = self.session.post(url, json=payload, timeout=self.timeout,
                                  headers={"Accept": "application/json", "Content-Type": "application/json"})
        except requests.RequestException as e:
            raise FetchError(f"{type(e).__name__} for {url}") from e
        return self._json(r, url)

    def get_text(self, url: str):
        """Returns (final_url, html) after redirects."""
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True,
                                 headers={"Accept": "text/html,application/xhtml+xml"})
        except requests.RequestException as e:
            raise FetchError(f"{type(e).__name__} for {url}") from e
        if r.status_code >= 400:
            raise FetchError(f"HTTP {r.status_code} from {url}")
        return r.url, r.text[:3_000_000]
