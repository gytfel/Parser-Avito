"""Загрузка страниц: паузы между запросами, кэш на диске, повторы и распознавание блокировки.

Бэкенды по убыванию «похожести на браузер»:
  * Playwright — настоящий Chrome / Edge / Chromium (network.browser: true или --browser).
    Профиль сохраняется в .cache/browser-profile, поэтому пройденная однажды проверка
    («капча») запоминается; окно видно, и проверку можно пройти вручную;
  * curl_cffi с TLS-отпечатком Chrome — используется автоматически, если установлен;
  * requests — запасной вариант, Авито блокирует его чаще всего.
"""

from __future__ import annotations

import hashlib
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from .config import Network

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
_LANG = "ru-RU,ru;q=0.9,en-US;q=0.6,en;q=0.5"
# Надписи страниц блокировки; «captcha» встречается и в обычных страницах, поэтому учитывается только
# у маленьких страниц (см. looks_blocked).
_STRONG_MARKERS = (
    "доступ ограничен", "проблема с ip", "firewall-title", "подозрительная активность",
    "доступ временно заблокирован",
)
_WEAK_MARKERS = ("captcha", "geetest")

BLOCK_HINT = (
    "Что можно сделать:\n"
    "  • выключить VPN или прокси на компьютере — Авито часто не пускает такие адреса;\n"
    "  • искать через браузер: в меню ответьте «да» на предложение или добавьте к команде --browser;\n"
    "  • подождать 15–30 минут (уже скачанные страницы берутся из кэша);\n"
    "  • увеличить паузу между запросами: network.delay_min / delay_max в config.yaml."
)


def playwright_hint() -> str:
    return (
        "Для поиска через браузер нужен Playwright. Установите его командой:\n"
        f'  "{sys.executable}" -m pip install playwright\n'
        "(в меню это делается автоматически). Если на компьютере нет Google Chrome или Microsoft Edge, "
        f'ещё и:\n  "{sys.executable}" -m playwright install chromium'
    )


# Код выхода команды, когда сайт не пустил: меню по нему предлагает повторить поиск через браузер.
EXIT_BLOCKED = 3


class FetchError(Exception):
    pass


class BlockedError(FetchError):
    def __init__(self, message: str, status: int | None = None, reason: str = ""):
        super().__init__(message)
        self.status = status
        self.reason = reason


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
    return len(text) < 30000 and any(m in text.lower() for m in _STRONG_MARKERS + _WEAK_MARKERS)


def block_reason(status: int, text: str) -> str:
    """«HTTP 403, «Доступ ограничен: проблема с IP»» — что именно ответил сайт."""
    parts = [f"HTTP {status}"]
    for pattern in (r"<h[12][^>]*>(.*?)</h[12]>", r"<title[^>]*>(.*?)</title>"):
        m = re.search(pattern, text, re.S | re.I)
        if m:
            caption = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip()
            if caption:
                parts.append(f"«{caption[:120]}»")
                break
    return ", ".join(parts)


def _content_ready(url: str, html: str) -> bool:
    """Открылась ли нужная страница после проверки в браузере."""
    low = html.lower()
    if any(m in low for m in _STRONG_MARKERS):
        return False
    if "avito.ru" in urlsplit(url).netloc:
        return any(m in low for m in ('data-marker="item', 'data-marker="catalog-serp"', "data-mfe-state"))
    return not looks_blocked(200, html)


class _RequestsBackend:
    def __init__(self, net: Network, log=None):
        import requests

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _UA, "Accept": _ACCEPT, "Accept-Language": _LANG})
        self.timeout = net.timeout
        self.proxies = {"http": net.proxy, "https": net.proxy} if net.proxy else None

    def fetch(self, url: str, headers: dict) -> tuple[int, str]:
        r = self.session.get(url, headers=headers, timeout=self.timeout, proxies=self.proxies)
        r.encoding = r.encoding or "utf-8"
        return r.status_code, r.text

    def close(self) -> None:
        self.session.close()


class _CurlBackend(_RequestsBackend):
    def __init__(self, net: Network, log=None):
        from curl_cffi import requests as cffi_requests

        # Остальные заголовки (User-Agent, sec-ch-ua, Accept) curl_cffi ставит сам — в том порядке,
        # что и настоящий Chrome; переопределять их нельзя, иначе отпечаток перестанет совпадать.
        self.session = cffi_requests.Session(impersonate="chrome")
        self.session.headers.update({"Accept-Language": _LANG})
        self.timeout = net.timeout
        self.proxies = {"http": net.proxy, "https": net.proxy} if net.proxy else None


class _BrowserBackend:
    # None — Chromium, скачанный командой `playwright install chromium`.
    CHANNELS = ("chrome", "msedge", None)

    def __init__(self, net: Network, log=None):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise FetchError(playwright_hint()) from e
        self.log = log or (lambda msg: None)
        self.headless = net.headless
        self.captcha_wait = net.captcha_wait
        self.timeout = net.timeout * 1000
        profile = Path(net.cache_dir) / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True)
        opts: dict = {
            "headless": net.headless,
            "locale": "ru-RU",
            "viewport": {"width": 1366, "height": 900},
            # Без этих флагов Chrome помечает себя как «управляемый автоматически» (navigator.webdriver),
            # и сайт отвечает «Доступ ограничен», хотя в обычном браузере всё открывается.
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if net.proxy:
            opts["proxy"] = {"server": net.proxy}
        channels = [net.browser_channel] if net.browser_channel else list(self.CHANNELS)
        self._pw = sync_playwright().start()
        self._context = None
        errors = []
        for channel in channels:
            kw = dict(opts)
            if channel and channel != "chromium":
                kw["channel"] = channel
            try:
                self._context = self._pw.chromium.launch_persistent_context(str(profile), **kw)
                self.log(f"Браузер: {channel or 'chromium'}")
                break
            except Exception as e:  # браузер не установлен или профиль занят другим окном
                errors.append(f"  {channel or 'chromium'}: {str(e).strip().splitlines()[0]}")
        if self._context is None:
            self._pw.stop()
            raise FetchError(
                "Не удалось запустить браузер. Закройте другие окна парсера; если Google Chrome или "
                f'Microsoft Edge не установлены, выполните:\n  "{sys.executable}" -m playwright install chromium\n'
                + "\n".join(errors)
            )
        self._context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def _open(self, url: str) -> tuple[int, str]:
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
        self._page.wait_for_timeout(1500)
        return (resp.status if resp else 200), self._page.content()

    def fetch(self, url: str, headers: dict) -> tuple[int, str]:
        status, html = self._open(url)
        if not looks_blocked(status, html) or self.headless or self.captcha_wait <= 0:
            return status, html
        self.log(f"Сайт просит пройти проверку — сделайте это в открывшемся окне браузера "
                 f"(жду до {int(self.captcha_wait)} с)…")
        deadline = time.monotonic() + self.captcha_wait
        while time.monotonic() < deadline:
            self._page.wait_for_timeout(2000)
            if _content_ready(url, self._page.content()):
                self.log("Проверка пройдена, продолжаю.")
                # После проверки сайт обычно сам возвращает на страницу; на всякий случай открываем её заново.
                status, html = self._open(url)
                return (200 if not looks_blocked(status, html) else status), html
        return status, self._page.content()

    def close(self) -> None:
        try:
            self._context.close()
        finally:
            self._pw.stop()


class Fetcher:
    def __init__(self, net: Network, use_cache: bool = True, log=None):
        self.net = net
        self.cache = DiskCache(net.cache_dir, net.cache_ttl_minutes if use_cache else 0)
        self.log = log or (lambda msg: None)
        self._backend = None
        self._last_request = 0.0
        self._warmed: set[str] = set()
        self.requests_made = 0

    def _get_backend(self):
        if self._backend is None:
            if self.net.browser:
                self._backend = _BrowserBackend(self.net, self.log)
            else:
                try:
                    self._backend = (_CurlBackend if self.net.impersonate else _RequestsBackend)(self.net, self.log)
                except ImportError:
                    self._backend = _RequestsBackend(self.net, self.log)
        return self._backend

    def _throttle(self) -> None:
        wait = random.uniform(self.net.delay_min, max(self.net.delay_min, self.net.delay_max))
        left = self._last_request + wait - time.monotonic()
        if left > 0:
            time.sleep(left)

    def warm_up(self, home_url: str) -> None:
        """Сначала открыть главную, как это делает человек: сайт выдаёт cookies сессии.

        В браузерном режиме не нужно — у браузера свой сохранённый профиль.
        """
        if self.net.browser or home_url in self._warmed:
            return
        self._warmed.add(home_url)
        try:
            self._request(home_url, None, cache=False)
        except BlockedError:
            raise
        except FetchError as e:
            self.log(f"Главная страница не открылась ({e}), продолжаю")

    def get(self, url: str, referer: str | None = None) -> str:
        cached = self.cache.get(url)
        if cached is not None:
            return cached
        return self._request(url, referer, cache=True)

    def _request(self, url: str, referer: str | None, cache: bool) -> str:
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
                reason = block_reason(status, text)
                raise BlockedError(f"{url}\nОтвет сайта: {reason}\n{BLOCK_HINT}", status, reason)
            if status == 404:
                raise FetchError(f"Страница не найдена (404): {url}")
            if status >= 500:
                last_error = FetchError(f"HTTP {status}: {url}")
                if attempt < self.net.retries:
                    time.sleep(5 * (attempt + 1))
                continue
            if status >= 400:
                raise FetchError(f"HTTP {status}: {url}")
            if cache:
                self.cache.set(url, text)
            return text
        raise FetchError(f"Не удалось загрузить {url}: {last_error}")

    def save_debug(self, name: str, text: str) -> Path:
        """Сохранить страницу для разбора проблемы (её можно прислать разработчику)."""
        path = Path(self.net.cache_dir) / "debug" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None
