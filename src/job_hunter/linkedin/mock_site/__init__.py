"""Mock LinkedIn site — local HTTP server serving HTML fixtures."""

from __future__ import annotations

import logging
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

logger = logging.getLogger("job_hunter.linkedin.mock_site")

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Route mapping: URL path → fixture file name
_ROUTES: dict[str, str] = {
    "/jobs/search": "job_list.html",
    "/jobs/view/mock-001": "job_detail.html",
    "/jobs/view/mock-002": "job_detail_002.html",
    "/jobs/view/mock-003": "job_detail_003.html",
}


class _MockHandler(SimpleHTTPRequestHandler):
    """Route LinkedIn-style paths to local fixture files."""

    def __init__(self, *args, fixtures_dir: Path = FIXTURES_DIR, **kwargs) -> None:  # type: ignore[override]
        self._fixtures_dir = fixtures_dir
        super().__init__(*args, directory=str(fixtures_dir), **kwargs)

    def do_GET(self) -> None:  # noqa: N802
        # Strip query string for matching
        clean_path = self.path.split("?")[0].rstrip("/")
        fixture = _ROUTES.get(clean_path)

        if fixture is not None:
            fixture_path = self._fixtures_dir / fixture
            # Fall back to default detail page if specific fixture doesn't exist
            if not fixture_path.exists():
                fixture_path = self._fixtures_dir / "job_detail.html"
            self._serve_file(fixture_path)
        else:
            super().do_GET()

    def _serve_file(self, path: Path) -> None:
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.debug(format, *args)


class MockLinkedInServer:
    """A lightweight local HTTP server for mock LinkedIn fixtures.

    Usage::

        server = MockLinkedInServer()
        base_url = server.start()   # e.g. "http://127.0.0.1:54321"
        # … run Playwright against base_url …
        server.stop()
    """

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR) -> None:
        self._fixtures_dir = fixtures_dir
        self._httpd: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """Start the server on a random free port and return the base URL."""
        handler = partial(_MockHandler, fixtures_dir=self._fixtures_dir)
        self._httpd = HTTPServer(("127.0.0.1", 0), handler)
        port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        base_url = f"http://127.0.0.1:{port}"
        logger.info("Mock LinkedIn server started at %s", base_url)
        return base_url

    def stop(self) -> None:
        """Shut down the server."""
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            logger.info("Mock LinkedIn server stopped")
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self) -> str:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
