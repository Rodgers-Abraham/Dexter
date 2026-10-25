import asyncio
import threading
import concurrent.futures
import platform
import shutil
import subprocess
import random
import time
import urllib.parse
from pathlib import Path


# ── Detect user's real default browser ────────────────────────────────────────

def _get_default_browser_cmd() -> str | None:
    """Get the default browser command on Linux."""
    try:
        result = subprocess.run(
            ["xdg-settings", "get", "default-web-browser"],
            capture_output=True, text=True, timeout=5,
        )
        desktop_name = result.stdout.strip()  # e.g. "brave-browser.desktop"
        if desktop_name:
            # Strip .desktop to get the command name
            cmd = desktop_name.replace(".desktop", "")
            if shutil.which(cmd):
                return cmd
            # Try common variations
            for variant in [cmd, cmd.lower(), cmd.replace(".", "-")]:
                if shutil.which(variant):
                    return variant
    except Exception:
        pass

    # Fallback: check common browsers
    for cmd in ["brave-browser", "google-chrome-stable", "google-chrome", "firefox", "chromium-browser"]:
        if shutil.which(cmd):
            return cmd
    return None


def _open_url_in_browser(url: str) -> str:
    """Open a URL in the user's real default browser (not Playwright)."""
    if not url.startswith("http"):
        url = "https://" + url

    system = platform.system()
    try:
        if system == "Linux":
            # Try xdg-open first (respects default browser)
            subprocess.Popen(
                ["xdg-open", url],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        elif system == "Darwin":
            subprocess.Popen(["open", url])
        elif system == "Windows":
            subprocess.Popen(["start", url], shell=True)
        else:
            subprocess.Popen(["xdg-open", url])

        time.sleep(1.0)
        return f"Opened in browser: {url}"
    except Exception as e:
        return f"Failed to open URL: {e}"


def _search_in_browser(query: str, engine: str = "google") -> str:
    """Open a search query in the user's real browser."""
    encoded = urllib.parse.quote_plus(query)
    engines = {
        "google":     f"https://www.google.com/search?q={encoded}",
        "bing":       f"https://www.bing.com/search?q={encoded}",
        "duckduckgo": f"https://duckduckgo.com/?q={encoded}",
        "youtube":    f"https://www.youtube.com/results?search_query={encoded}",
    }
    url = engines.get(engine.lower(), engines["google"])
    return _open_url_in_browser(url)


# ── Playwright stealth browser (kept as optional fallback for automation) ─────

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False
    PlaywrightTimeout = Exception

try:
    from playwright_stealth import Stealth
    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False


class _BrowserThread:
    """Playwright-based browser thread for advanced automation tasks only."""

    def __init__(self):
        self._loop       = None
        self._thread     = None
        self._ready      = threading.Event()
        self._playwright = None
        self._browser    = None
        self._context    = None
        self._page       = None

    def start(self):
        if not _HAS_PLAYWRIGHT:
            print("[Browser] ⚠️ Playwright not installed — automation features disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="BrowserThread"
        )
        self._thread.start()
        self._ready.wait(timeout=15)

    def _run_loop(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init())
        self._ready.set()
        self._loop.run_forever()

    async def _init(self):
        self._playwright = await async_playwright().start()

    def run(self, coro, timeout: int = 30):
        if not self._loop:
            raise RuntimeError("BrowserThread not started.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _launch_browser_if_needed(self):
        if self._browser and self._browser.is_connected():
            return

        engine = self._playwright.chromium
        chromium_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ]

        try:
            self._browser = await engine.launch(
                headless=False,
                args=chromium_args,
                ignore_default_args=["--enable-automation"],
            )
        except Exception as e:
            print(f"[Browser] ⚠️ Playwright launch failed: {e}")
            raise

    async def _get_page(self):
        await self._launch_browser_if_needed()

        if self._context is None:
            self._context = await self._browser.new_context(
                viewport=None,
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            )

        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()
            if _HAS_STEALTH:
                stealth_bot = Stealth()
                await stealth_bot.apply_stealth_async(self._page)

        return self._page

    async def _go_to(self, url: str) -> str:
        if not url.startswith("http"):
            url = "https://" + url
        page = await self._get_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            return f"Opened: {page.url}"
        except PlaywrightTimeout:
            return f"Timeout loading: {url}"
        except Exception as e:
            return f"Navigation error: {e}"

    async def _click(self, selector=None, text=None) -> str:
        page = await self._get_page()
        try:
            if text:
                await page.get_by_text(text, exact=False).first.click(timeout=8000)
                return f"Clicked: '{text}'"
            elif selector:
                await page.click(selector, timeout=8000)
                return f"Clicked: {selector}"
            return "No selector or text provided."
        except PlaywrightTimeout:
            return "Element not found or not clickable."
        except Exception as e:
            return f"Click error: {e}"

    async def _type(self, selector=None, text: str = "", clear_first: bool = True) -> str:
        page = await self._get_page()
        try:
            element = page.locator(selector).first if selector else page.locator(":focus")
            if clear_first:
                await element.clear()
            delay = random.randint(50, 150)
            await element.type(text, delay=delay)
            return "Text typed."
        except Exception as e:
            return f"Type error: {e}"

    async def _scroll(self, direction: str = "down", amount: int = 500) -> str:
        page = await self._get_page()
        try:
            y = amount if direction == "down" else -amount
            await page.mouse.wheel(0, y)
            return f"Scrolled {direction}."
        except Exception as e:
            return f"Scroll error: {e}"

    async def _press(self, key: str) -> str:
        page = await self._get_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed: {key}"
        except Exception as e:
            return f"Key error: {e}"

    async def _get_text(self) -> str:
        page = await self._get_page()
        try:
            text = await page.inner_text("body")
            return text[:4000] if len(text) > 4000 else text
        except Exception as e:
            return f"Could not get page text: {e}"

    async def _fill_form(self, fields: dict) -> str:
        page    = await self._get_page()
        results = []
        for selector, value in fields.items():
            try:
                el = page.locator(selector).first
                await el.clear()
                delay = random.randint(50, 150)
                await el.type(str(value), delay=delay)
                results.append(f"✓ {selector}")
            except Exception as e:
                results.append(f"✗ {selector}: {e}")
        return "Form filled: " + ", ".join(results)

    async def _smart_click(self, description: str) -> str:
        page       = await self._get_page()
        desc_lower = description.lower()

        role_hints = {
            "button":    ["button", "btn"],
            "link":      ["link"],
            "searchbox": ["search"],
            "textbox":   ["input", "field"],
        }
        for role, keywords in role_hints.items():
            if any(k in desc_lower for k in keywords):
                try:
                    await page.get_by_role(role).first.click(timeout=5000)
                    return f"Clicked ({role}): '{description}'"
                except Exception:
                    pass

        try:
            await page.get_by_text(description, exact=False).first.click(timeout=5000)
            return f"Clicked (text): '{description}'"
        except Exception:
            pass

        try:
            await page.get_by_placeholder(description, exact=False).first.click(timeout=5000)
            return f"Clicked (placeholder): '{description}'"
        except Exception:
            pass

        return f"Could not find: '{description}'"

    async def _smart_type(self, description: str, text: str) -> str:
        page = await self._get_page()

        for method, locator in [
            ("placeholder", page.get_by_placeholder(description, exact=False)),
            ("label",       page.get_by_label(description, exact=False)),
            ("role",        page.get_by_role("textbox")),
        ]:
            try:
                el = locator.first
                await el.clear()
                delay = random.randint(50, 150)
                await el.type(text, delay=delay)
                return f"Typed into ({method}): '{description}'"
            except Exception:
                continue

        return f"Could not find input: '{description}'"

    async def _close_browser(self) -> str:
        if self._browser:
            await self._browser.close()
            self._browser = None
            self._context = None
            self._page    = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        return "Browser closed."


# ── Singleton (lazy init — only starts when automation actions are used) ──────

_bt         = _BrowserThread()
_bt_started = False
_bt_lock    = threading.Lock()


def _ensure_playwright_started():
    global _bt_started
    if not _HAS_PLAYWRIGHT:
        raise RuntimeError(
            "Playwright is not installed. Basic browser actions (open URL, search) "
            "still work via your real browser. For advanced automation, run: pip install playwright && playwright install"
        )
    with _bt_lock:
        if not _bt_started:
            _bt.start()
            _bt_started = True


# ── Public API ───────────────────────────────────────────────────────────────

def browser_control(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None
) -> str:
    """
    Browser controller.

    For 'go_to' and 'search' actions: opens in the user's REAL default browser
    (Brave, Chrome, Firefox, etc.) via xdg-open — no CAPTCHAs, no cookie popups,
    all your logins and extensions are available.

    For advanced automation actions (click, type, fill_form, smart_click, etc.):
    uses a Playwright-controlled browser instance.

    parameters:
        action      : go_to | search | click | type | scroll | fill_form |
                      smart_click | smart_type | get_text | press | close
        url         : URL for go_to
        query       : search query
        engine      : google | bing | duckduckgo | youtube (default: google)
        selector    : CSS selector for click/type
        text        : text to click or type
        description : element description for smart_click/smart_type
        direction   : up | down for scroll
        amount      : scroll amount in pixels (default: 500)
        key         : key name for press (e.g. Enter, Escape, Tab)
        fields      : {selector: value} dict for fill_form
        clear_first : bool, clear input before typing (default: True)
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()
    result = "Unknown action."

    try:
        # ── Actions that use the REAL browser (no CAPTCHA / cookies issues) ──
        if action == "go_to":
            url = params.get("url", "")
            if not url:
                result = "No URL provided."
            else:
                result = _open_url_in_browser(url)

        elif action == "search":
            query = params.get("query", "")
            engine = params.get("engine", "google")
            if not query:
                result = "No search query provided."
            else:
                result = _search_in_browser(query, engine)

        # ── Actions that need Playwright automation ──────────────────────────
        elif action == "click":
            _ensure_playwright_started()
            result = _bt.run(_bt._click(
                selector=params.get("selector"),
                text=params.get("text"),
            ))

        elif action == "type":
            _ensure_playwright_started()
            result = _bt.run(_bt._type(
                selector=params.get("selector"),
                text=params.get("text", ""),
                clear_first=params.get("clear_first", True),
            ))

        elif action == "scroll":
            _ensure_playwright_started()
            result = _bt.run(_bt._scroll(
                direction=params.get("direction", "down"),
                amount=params.get("amount", 500),
            ))

        elif action == "fill_form":
            _ensure_playwright_started()
            result = _bt.run(_bt._fill_form(params.get("fields", {})))

        elif action == "smart_click":
            _ensure_playwright_started()
            result = _bt.run(_bt._smart_click(params.get("description", "")))

        elif action == "smart_type":
            _ensure_playwright_started()
            result = _bt.run(_bt._smart_type(
                params.get("description", ""),
                params.get("text", ""),
            ))

        elif action == "get_text":
            _ensure_playwright_started()
            result = _bt.run(_bt._get_text())

        elif action == "press":
            _ensure_playwright_started()
            result = _bt.run(_bt._press(params.get("key", "Enter")))

        elif action == "close":
            if _bt_started:
                result = _bt.run(_bt._close_browser())
            else:
                result = "No automation browser is open."

        else:
            result = f"Unknown action: {action}"

    except concurrent.futures.TimeoutError:
        result = "Browser action timed out."
    except RuntimeError as e:
        result = str(e)
    except Exception as e:
        result = f"Browser error: {e}"

    print(f"[Browser] {result[:80]}")
    if player:
        player.write_log(f"[browser] {result[:60]}")

    return result