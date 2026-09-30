from jobwatch.http import FetchError


class FakeHttp:
    """Maps URLs to canned JSON so tests run offline. Unknown URLs behave like a 404."""

    def __init__(self, get=None, post=None, pages=None):
        self.get = get or {}
        self.post = post or {}
        self.pages = pages or {}
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append(("GET", url, params))
        if url in self.get:
            value = self.get[url]
            return value(params) if callable(value) else value
        raise FetchError(f"HTTP 404 from {url}")

    def post_json(self, url, payload):
        self.calls.append(("POST", url, payload))
        if url in self.post:
            value = self.post[url]
            return value(payload) if callable(value) else value
        raise FetchError(f"HTTP 404 from {url}")

    def get_text(self, url):
        if url in self.pages:
            return self.pages[url]
        raise FetchError(f"HTTP 404 from {url}")
