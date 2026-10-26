"""Загрузка страниц: паузы между запросами, кэш на диске, повторы и распознавание блокировки.

Бэкенды по убыванию «похожести на браузер»:
  * Playwright (настоящий Chromium) — network.browser: true / --browser;
  * curl_cffi с TLS-отпечатком Chrome — используется автоматически, если установлен;
  * requests — запасной вариант, Авито блокирует его чаще всего.
"""

from __future__ import annotations

import hashlib
import random
import time
from pathlib import Path

from .config import Network

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.6,en;q=0.5",
}
_BLOCK_MARKERS = (
    "доступ ограничен", "проблема с ip", "firewall-title", "captcha", "подозрительная активность", "geetest",
    "доступ временно заблокирован",
)

BLOCK_HINT = (
    "Сайт временно ограничил доступ (HTTP 403/429 или капча). Что можно сделать:\n"
    "  • подождать 15–30 минут и запустить снова (страницы из кэша повторно не скачиваются);\n"
    "  • увеличить паузу между запросами: network.delay_min / delay_max в config.yaml;\n"
    "  • включить браузерный режим: --browser (нужен Playwright, как установить — README, «Запуск командой»);\n"
    "  • указать прокси (--proxy http://user:pass@host:port) или Cookie из своего браузера (network.cookies)."
)


class FetchError(Exception):
    pass


class BlockedError(FetchError):
    pass


class DiskCache:
    def __init__(self, directory: str | Path, ttl_minutes: float):
        self.dir = Path(directory) / "http"
        self.ttl = ttl_minutes * 60

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def get(self, url: str) -> str | None:
        if self.ttl <= 0:
            return None
        p = self._path(url)
        if p.exists() and time.time() - p.stat().st_mtime < self.ttl:
            return p.read_text(encoding="utf-8")
        return None

    def set(self, url: str, text: str) -> None:
        if self.ttl <= 0:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        self._path(url).write_text(text, encoding="utf-8")


def looks_blocked(status: int, text: str) -> bool:
    if status in (403, 429):
        return True
    # Страница блокировки маленькая; у настоящей выдачи сотни килобайт.
    return len(text) < 30000 and any(m in text.lower() for m in _BLOCK_MARKERS)


class _RequestsBackend:
    def __init__(self, net: Network):
        import requests

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _UA, **_HEADERS})
        self.timeout = net.timeout
        self.proxies = {"http": net.proxy, "https": net.proxy} if net.proxy else None

    def fetch(self, url: str, headers: dict) -> tuple[int, str]:
        r = self.session.get(url, headers=headers, timeout=self.timeout, proxies=self.proxies)
        r.encoding = r.encoding or "utf-8"
        return r.status_code, r.text

    def close(self) -> None:
        self.session.close()


class _CurlBackend(_RequestsBackend):
    def __init__(self, net: Network):
        from curl_cffi import requests as cffi_requests

        # User-Agent не переопределяем: он должен совпадать с TLS-отпечатком.
        self.session = cffi_requests.Session(impersonate="chrome")
        self.session.headers.update(_HEADERS)
        self.timeout = net.timeout
        self.proxies = {"http": net.proxy, "https": net.proxy} if net.proxy else None


class _BrowserBackend:
    def __init__(self, net: Network):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise FetchError(
                "Браузерный режим требует Playwright: python -m pip install playwright && "
                "python -m playwright install chromium (тем же Python, что запускает парсер; см. README)"
            ) from e
        self._pw = sync_playwright().start()
        launch = {"headless": net.headless}
        if net.proxy:
            launch["proxy"] = {"server": net.proxy}
        self._browser = self._pw.chromium.launch(**launch)
        self._context = self._browser.new_context(locale="ru-RU", user_agent=_UA)
        self._page = self._context.new_page()
        self.timeout = net.timeout * 1000

    def fetch(self, url: str, headers: dict) -> tuple[int, str]:
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
        self._page.wait_for_timeout(1500)
        return (resp.status if resp else 200), self._page.content()

    def close(self) -> None:
        self._context.close()
        self._browser.close()
        self._pw.stop()


class Fetcher:
    def __init__(self, net: Network, use_cache: bool = True, log=None):
        self.net = net
        self.cache = DiskCache(net.cache_dir, net.cache_ttl_minutes if use_cache else 0)
        self.log = log or (lambda msg: None)
        self._backend = None
        self._last_request = 0.0
        self.requests_made = 0

    def _get_backend(self):
        if self._backend is None:
            if self.net.browser:
                self._backend = _BrowserBackend(self.net)
            else:
                try:
                    self._backend = _CurlBackend(self.net) if self.net.impersonate else _RequestsBackend(self.net)
                except ImportError:
                    self._backend = _RequestsBackend(self.net)
        return self._backend

    def _throttle(self) -> None:
        wait = random.uniform(self.net.delay_min, max(self.net.delay_min, self.net.delay_max))
        left = self._last_request + wait - time.monotonic()
        if left > 0:
            time.sleep(left)

    def get(self, url: str, referer: str | None = None) -> str:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        headers = {}
        if referer:
            headers["Referer"] = referer
        if self.net.cookies:
            headers["Cookie"] = self.net.cookies
        backend = self._get_backend()
        last_error: Exception | None = None
        for attempt in range(self.net.retries + 1):
            if self.requests_made:
                self._throttle()
            self._last_request = time.monotonic()
            self.requests_made += 1
            try:
                status, text = backend.fetch(url, headers)
            except Exception as e:  # сетевые ошибки разных бэкендов
                last_error = e
                if attempt < self.net.retries:
                    self.log(f"Ошибка сети ({e.__class__.__name__}), повтор {attempt + 1}/{self.net.retries}")
                    time.sleep(5 * (attempt + 1))
                continue
            if status == 429 and attempt < self.net.retries:
                self.log(f"Слишком много запросов (429), пауза {30 * (attempt + 1)} с")
                time.sleep(30 * (attempt + 1))
                continue
            if looks_blocked(status, text):
                raise BlockedError(f"{url}\n{BLOCK_HINT}")
            if status == 404:
                raise FetchError(f"Страница не найдена (404): {url}")
            if status >= 500:
                last_error = FetchError(f"HTTP {status}: {url}")
                if attempt < self.net.retries:
                    time.sleep(5 * (attempt + 1))
                continue
            if status >= 400:
                raise FetchError(f"HTTP {status}: {url}")
            self.cache.set(url, text)
            return text
        raise FetchError(f"Не удалось загрузить {url}: {last_error}")

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
