"""browser_dom.py - DOM-based browser control (the browser-use / Playwright strategy)
==================================================================================

Why this beats pixel-clicking for the web:
  - Web page CONTENT is not exposed through Windows UI Automation, which is why
    list_ui_elements() saw nothing on YouTube.
  - The DOM knows every interactive element by role/name: clicking "Like" is a
    button named "like this video along with..." - find it, click it, done.
  - Deterministic and fast: no vision model, no coordinate guessing.

How it works:
  - Launches REAL Chrome with --remote-debugging-port=9222 and connects to it
    over the Chrome DevTools Protocol (CDP) via Playwright.
  - browser_snapshot() runs JS in the page listing numbered elements with text.
  - browser_click(i) clicks element i directly in-page (JS el.click()).
  - browser_type(i, text) clicks the element and types real keystrokes.

IMPORTANT - Chrome profile:
  Chrome 136+ blocks remote debugging on your normal profile, so this launches
  Chrome with a separate clean profile at %TEMP%\\agent-chrome-profile.
  => Log into YouTube/Google ONCE inside that window; the login persists in the
     profile folder and future agent runs reuse it.

SETUP
  pip install playwright requests
  (NO 'playwright install' needed - it drives your installed Chrome, not its own)

INTEGRATION
  tools.py, at the bottom:
      from browser_dom import register_browser_tools
      register_browser_tools(TOOLS)
"""

import json
import os
import subprocess
import threading
import time

import requests

CDP_PORT = 9222

# IMPORTANT: Chrome 136+ HARD-BLOCKS --remote-debugging-port on your normal,
# everyday profile as a security measure - no amount of killing processes or
# clearing lock files works around this, Chrome just silently refuses to open
# the CDP port. So we must use a SEPARATE dedicated profile for the agent.
#
# This is a one-time cost: the first time you ask the agent to use a site
# (LinkedIn, Gmail, etc.) it'll open its own Chrome window, logged out. Log in
# there ONCE - the session is saved to disk in AGENT_PROFILE_DIR below (a
# permanent folder, not %TEMP%, so it survives reboots/disk cleanups) and every
# future run reuses it automatically.
USE_REAL_PROFILE = False
PROFILE_NAME = "Default"
AGENT_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".agent-chrome-profile")
REAL_USER_DATA_ROOT = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
PROFILE_DIR = REAL_USER_DATA_ROOT if USE_REAL_PROFILE else AGENT_PROFILE_DIR

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# Playwright state is kept PER THREAD (threading.local) for two reasons:
# 1) The sync API can only be entered ONCE per thread. A second
#    sync_playwright().start() raises "It looks like you are using Playwright
#    Sync API inside the asyncio loop" whenever the previous greenlet event
#    loop is still marked running - which happens when the last context wasn't
#    cleanly stopped (e.g. a page died mid-navigation). Keeping one long-lived
#    context per thread means start() is never called a second time.
# 2) The GUI is multi-threaded, so multiple threads can drive browser_* tools
#    concurrently - each needs its own independent connection.
_tls = threading.local()
_launch_lock = threading.Lock()   # serializes Chrome launch / CDP health checks


# ----------------------------------------------------------------------
# Chrome / CDP plumbing
# ----------------------------------------------------------------------

def _chrome_exe() -> str:
    for p in CHROME_PATHS:
        if os.path.exists(p):
            return p
    return "chrome"


def _cdp_up() -> bool:
    try:
        requests.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2)
        return True
    except requests.RequestException:
        return False


def _clear_profile_locks():
    """Chrome refuses to attach debugging to a profile it thinks is already
    open, based on lock files in the User Data root - not just live processes.
    Remove them after taskkill so the relaunch isn't silently ignored."""
    for fname in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        fpath = os.path.join(PROFILE_DIR, fname)
        try:
            if os.path.exists(fpath) or os.path.islink(fpath):
                os.remove(fpath)
        except Exception:
            pass


def _kill_existing_chrome():
    """Chrome ignores --remote-debugging-port if that profile is already
    running (or thinks it is, via lock files) under a process without the
    flag, so when using the REAL profile we must fully close Chrome first
    and clear its lock files before relaunching with debugging enabled."""
    try:
        subprocess.run(
            ["taskkill", "/IM", "chrome.exe", "/F", "/T"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
    # Give Windows time to fully release the process/file handles before
    # touching the profile directory.
    time.sleep(2.5)
    _clear_profile_locks()
    time.sleep(0.5)


def _launch_chrome_debug():
    exe = _chrome_exe()
    os.makedirs(PROFILE_DIR, exist_ok=True)
    cmd = [
        exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    if USE_REAL_PROFILE:
        cmd.append(f"--profile-directory={PROFILE_NAME}")
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        if _cdp_up():
            return True
    return False


def _ensure_chrome() -> str | None:
    """Return None if Chrome with debugging is reachable, else error string."""
    with _launch_lock:
        if _cdp_up():
            return None

        if USE_REAL_PROFILE:
            _kill_existing_chrome()

        if _launch_chrome_debug():
            return None

        if USE_REAL_PROFILE:
            # First attempt failed - most likely another Chrome process (or a
            # lingering lock file) grabbed the profile again in between. Retry
            # once with a harder kill before giving up.
            _kill_existing_chrome()
            if _launch_chrome_debug():
                return None

        exe_used = _chrome_exe()
        return (
            f"Chrome did not open port {CDP_PORT} (exe: {exe_used}, profile: {PROFILE_DIR}). "
            f"Check that chrome.exe exists at one of the paths in CHROME_PATHS, and that no "
            f"antivirus/EDR software is blocking the --remote-debugging-port flag."
        )


def _get_pw():
    """Return THIS thread's long-lived Playwright context, starting it once.

    start() is deliberately never called again for the same thread - doing so
    is what trips Playwright's "Sync API inside the asyncio loop" guard and
    permanently wedges the browser tools. Recovery uses reconnect below.
    """
    pw = getattr(_tls, "_pw", None)
    if pw is None:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        _tls._pw = pw
        _tls._browser = None
        _tls._page = None
    return pw


def _get_page(force_reconnect: bool = False):
    """Return a live Playwright Page connected via CDP (per-thread).

    Reuses the cached page when possible, but ALWAYS verifies it's truly
    responsive first (not just checking .url, which can look fine even when
    the page's execution context has died after an SPA route change) - this
    is what caused 'cannot switch to a different thread' errors before: a
    dead page object kept getting reused because .url didn't raise.

    IMPORTANT: we never recover by stop() + start() of the Playwright context
    (that re-entry is what raises "Playwright Sync API inside the asyncio
    loop"). Instead we drop the stale browser/page handles and reconnect them
    on the SAME long-lived context via connect_over_cdp.
    """
    err = _ensure_chrome()
    if err:
        raise RuntimeError(err)

    pw = _get_pw()

    if not force_reconnect and getattr(_tls, "_page", None) is not None:
        try:
            _tls._page.evaluate("1")  # real liveness check, not just .url
            return _tls._page
        except Exception:
            _tls._page = None
            _tls._browser = None

    if getattr(_tls, "_browser", None) is not None:
        try:
            if not _tls._browser.is_connected():
                _tls._browser = None
        except Exception:
            _tls._browser = None

    if _tls._browser is None:
        _tls._browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")

    context = _tls._browser.contexts[0]
    pages = [p for p in context.pages if not p.is_closed()]
    if not pages:
        _tls._page = context.new_page()
    else:
        # Prefer the most recently opened/active tab over a stale first tab.
        _tls._page = pages[-1]
    _tls._page.bring_to_front()
    return _tls._page


def _with_page(action):
    """Run `action(page)` against a live page; on ANY failure, reconnect
    fresh and retry exactly once before surfacing the error. This is what
    makes browser_* calls self-healing instead of dying on stale-context
    errors like SPA navigations invalidating the previous execution context."""
    try:
        page = _get_page()
        return action(page)
    except Exception:
        page = _get_page(force_reconnect=True)
        return action(page)


# ----------------------------------------------------------------------
# Element enumeration in-page (Set-of-Mark, but structural)
# ----------------------------------------------------------------------

_SNAPSHOT_JS = """
() => {
  const sel = 'a, button, input, textarea, select, [role="button"], [role="link"], [role="textbox"], [role="combobox"], [role="menuitem"], [contenteditable="true"]';
  window.__agentEls = [];
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= 500) break;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    if (r.bottom < -100 || r.top > innerHeight + 100) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    const txt = (el.getAttribute('aria-label')
              || el.innerText
              || el.value
              || el.getAttribute('placeholder')
              || el.getAttribute('title')
              || '').trim().replace(/\\s+/g, ' ').slice(0, 70);
    window.__agentEls.push(el);
    out.push({
      i: out.length,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      text: txt,
      href: el.href || el.getAttribute('href') || '',
    });
  }
  return out;
}
"""

_CLICK_JS = """
(i) => {
  const els = window.__agentEls || [];
  const el = els[i];
  if (!el) return null;
  el.scrollIntoView({block: 'center', inline: 'center'});
  el.click();
  return (el.getAttribute('aria-label') || el.innerText || el.value || '')
         .trim().replace(/\\s+/g, ' ').slice(0, 60);
}
"""

# Detect LinkedIn "Connect / Invite ... to connect" elements (search results AND
# profile pages render them as <a href="/preload/search-custom-invite/?...">).
# Clicking those anchors DOES NOT work under automation here (the click fires, a
# profile-preload XHR runs, but the invite dialog never opens). Navigating to the
# href instead reliably renders the "Add a note" invite dialog.
_CONNECT_DETECT_JS = """
(i) => {
  const els = window.__agentEls || [];
  const el = els[i];
  if (!el) return null;
  const aria = (el.getAttribute('aria-label') || '').trim();
  const txt = (el.innerText || '').trim().replace(/\\s+/g, ' ');
  const href = (el.getAttribute('href') || '').trim();
  const isInvite = /search-custom-invite/i.test(href)
    || /^invite .* to connect$/i.test(aria)
    || (/^connect$/i.test(txt) && /invite.*connect/i.test(aria));
  return {
    label: (aria || el.innerText || el.value || '')
           .trim().replace(/\\s+/g, ' ').slice(0, 60),
    isInvite,
    href: el.href || '',
  };
}
"""


def _fmt_snapshot(page, els: list, start: int = 0, limit: int = 80) -> str:
    lines = [f"PAGE: {page.title()}"[:90], f"URL: {page.url}"]
    shown = els[start:start + limit]
    for e in shown:
        kind = e["role"] or e["type"] or e["tag"]
        line = f"[{e['i']}] {kind}"
        if e["text"]:
            line += f" '{e['text']}'"
        lines.append(line)
    if not shown:
        lines.append("(no interactive elements found at/above the fold - try browser_scroll)")
    elif start + len(shown) < len(els):
        first = start + len(shown)
        lines.append(f"... {len(els) - first} more elements (indexes {first}..{len(els)-1}) - "
                     f"call browser_snapshot(start={first}) to see them")
    return "\n".join(lines)


def browser_goto(url: str, start: int = 0, limit: int = 80) -> str:
    """Open a URL in the agent-controlled Chrome tab, then show page elements."""
    def _do(page):
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        return _fmt_snapshot(page, page.evaluate(_SNAPSHOT_JS), start, limit)
    try:
        return _with_page(_do)
    except Exception as e:
        return f"Error: {e}"


def browser_snapshot(start: int = 0, limit: int = 80) -> str:
    """Re-scan the current page and list numbered interactive elements.
    Call this after every action - elements and their indexes change.
    If the page has more than `limit` elements, pass `start` to page
    through the list (indexes stay the same across pages)."""
    def _do(page):
        return _fmt_snapshot(page, page.evaluate(_SNAPSHOT_JS))
    try:
        return _with_page(_do)
    except Exception as e:
        return f"Error: {e}"


def browser_click(index: int) -> str:
    """Click element number N from the last browser_snapshot/browser_goto output.
    LinkedIn Connect/Invite links are handled specially: instead of clicking the
    anchor (which silently does nothing under automation), we navigate to its
    /preload/search-custom-invite href, which reliably opens the invite dialog.
    For everything else we click with Playwright's TRUSTED mouse events (not JS
    el.click()) so React dropdowns like LinkedIn's 'More ...' menu actually open."""
    def _do(page):
        info = page.evaluate(_CONNECT_DETECT_JS, int(index))
        if info is None:
            return f"Error: element {index} stale - call browser_snapshot again."
        if info["isInvite"] and info["href"]:
            page.goto(info["href"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            return (f"Opened the LinkedIn connect dialog for '{info['label']}' "
                    f"(via its invite link). Call browser_snapshot and click 'Add a note'.")
        handle = page.evaluate_handle("(i) => (window.__agentEls || [])[i]", int(index))
        el = handle.as_element()
        if not el:
            return f"Error: element {index} not an element - call browser_snapshot again."
        try:
            el.scroll_into_view_if_needed()
            el.click(timeout=8000)
        except Exception:
            page.evaluate(_CLICK_JS, int(index))  # fallback: synthetic click
        page.wait_for_timeout(2000)
        return f"Clicked [{index}] '{info['label']}'. Call browser_snapshot to see the new page state."
    try:
        return _with_page(_do)
    except Exception as e:
        return f"Error: {e}"


def browser_type(index: int, text: str, press_enter: bool = False) -> str:
    """Click element N (usually an input/textbox) then type text with real keyboard events."""
    def _do(page):
        handle = page.evaluate_handle("(i) => (window.__agentEls || [])[i]", int(index))
        el = handle.as_element()
        if not el:
            return f"Error: element {index} not an element - call browser_snapshot again."
        el.click()
        page.keyboard.type(text, delay=30)
        if press_enter:
            page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        return f"Typed into [{index}]: {text[:60]}{' + Enter' if press_enter else ''}. Call browser_snapshot for updated page."
    try:
        return _with_page(_do)
    except Exception as e:
        return f"Error: {e}"


def browser_press(key: str) -> str:
    """Press a keyboard key in the page (Enter, Escape, Tab, ArrowDown...)."""
    def _do(page):
        page.keyboard.press(key)
        page.wait_for_timeout(1000)
        return f"Pressed {key}. Call browser_snapshot if page changed."
    try:
        return _with_page(_do)
    except Exception as e:
        return f"Error: {e}"


def browser_scroll(dy: int = 600, start: int = 0, limit: int = 80) -> str:
    """Scroll the page (positive dy = down, in pixels), then show elements."""
    def _do(page):
        page.mouse.wheel(0, int(dy))
        page.wait_for_timeout(1200)
        return _fmt_snapshot(page, page.evaluate(_SNAPSHOT_JS), start, limit)
    try:
        return _with_page(_do)
    except Exception as e:
        return f"Error: {e}"


_BROWSER_TOOLS = {
    "browser_goto": {
        "description": "Open a URL in the agent's dedicated Chrome window and list the page's interactive elements. This is the ONLY way to open a website - it launches Chrome itself if needed, you don't need open_app first.",
        "function": browser_goto,
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to open"},
                "start": {"type": "integer", "default": 0, "description": "First element index to show (paging)"},
                "limit": {"type": "integer", "default": 80, "description": "How many elements to show"},
            },
            "required": ["url"],
        },
    },
    "browser_snapshot": {
        "description": "List numbered interactive elements (links, buttons, inputs) of the current web page. Call after every browser action - indexes change. If the output ends with '... more elements', page through with start=N to see the rest (indexes are stable).",
        "function": browser_snapshot,
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "integer", "default": 0, "description": "First element index to show (paging)"},
                "limit": {"type": "integer", "default": 80, "description": "How many elements to show"},
            },
        },
    },
    "browser_click": {
        "description": "Click element N from the last browser_snapshot output (by its DOM element, not pixels).",
        "function": browser_click,
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element index from browser_snapshot"},
            },
            "required": ["index"],
        },
    },
    "browser_type": {
        "description": "Click element N then type text with real keystrokes. Use for search boxes, comment fields.",
        "function": browser_type,
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Element index from browser_snapshot"},
                "text": {"type": "string", "description": "Text to type"},
                "press_enter": {"type": "boolean", "default": False, "description": "Press Enter afterwards"},
            },
            "required": ["index", "text"],
        },
    },
    "browser_press": {
        "description": "Press a keyboard key in the page: Enter, Escape, Tab, ArrowDown, etc.",
        "function": browser_press,
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Key name (Playwright format)"},
            },
            "required": ["key"],
        },
    },
    "browser_scroll": {
        "description": "Scroll the page down (positive) or up (negative) in pixels, then show page elements.",
        "function": browser_scroll,
        "parameters": {
            "type": "object",
            "properties": {
                "dy": {"type": "integer", "default": 600},
                "start": {"type": "integer", "default": 0, "description": "First element index to show (paging)"},
                "limit": {"type": "integer", "default": 80, "description": "How many elements to show"},
            },
        },
    },
}


def register_browser_tools(tools_dict: dict) -> None:
    """Merge the DOM browser tools into your existing TOOLS dict."""
    tools_dict.update(_BROWSER_TOOLS)