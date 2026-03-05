"""LinkedIn session management — cookie-based authentication."""

from __future__ import annotations


class LinkedInSession:
    """Manages a Playwright browser context with saved LinkedIn cookies."""

    def __init__(self, cookies_path: str = "data/cookies.json") -> None:
        self.cookies_path = cookies_path

    async def login(self) -> None:
        raise NotImplementedError

    async def save_cookies(self) -> None:
        raise NotImplementedError

    async def load_cookies(self) -> None:
        raise NotImplementedError

