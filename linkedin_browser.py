"""Read a LinkedIn profile from the user's existing Chrome session.

The assistant intentionally does NOT launch a second LinkedIn browser profile.
It attaches to Chrome over Chrome DevTools Protocol (CDP), so the page uses the
user's existing logged-in session/cookies.

Setup on Chrome 144+:
    1. Open Chrome normally and make sure you are logged in to LinkedIn.
    2. Open chrome://inspect/#remote-debugging
    3. Enable Remote Debugging and allow the connection when Chrome asks.

The CDP endpoint defaults to http://127.0.0.1:9222 and can be overridden with
LINKEDIN_CDP_URL.
"""
from __future__ import annotations

import os
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

CDP_URL = os.environ.get("LINKEDIN_CDP_URL", "http://127.0.0.1:9222")


def _first_text(page: Any, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                text = locator.inner_text(timeout=2500).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def fetch_profile(url: str, timeout_ms: int = 30000) -> dict[str, str]:
    url = url.strip()
    if not url.startswith("https://www.linkedin.com/"):
        raise ValueError(
            "Please provide a valid LinkedIn URL starting with https://www.linkedin.com/"
        )

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            raise RuntimeError(
                "Could not attach to your existing Chrome session.\n\n"
                "Open Chrome normally, sign in to LinkedIn, then open:\n"
                "chrome://inspect/#remote-debugging\n\n"
                "Enable Remote Debugging and allow the connection.\n"
                f"The assistant is trying to connect to {CDP_URL}.\n\n"
                "No separate browser was opened."
            ) from exc

        try:
            contexts = browser.contexts
            if not contexts:
                raise RuntimeError("Chrome is connected, but no browser context is available.")

            context = contexts[0]
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightError:
                pass
            page.wait_for_timeout(2500)

            title = page.title().strip()
            name = _first_text(page, ["h1"])
            headline = _first_text(
                page,
                [
                    ".text-body-medium.break-words",
                    "div[data-generated-suggestion-target]",
                ],
            )
            location = _first_text(
                page,
                [".text-body-small.inline.t-black--light.break-words"],
            )

            try:
                body_text = page.locator("body").inner_text(timeout=5000)
            except Exception as exc:
                raise RuntimeError(f"Could not read the LinkedIn profile page: {exc}") from exc

            if not body_text.strip():
                raise RuntimeError(
                    "LinkedIn returned an empty page. Make sure you are logged in to LinkedIn "
                    "in the Chrome session you attached."
                )

            # Keep useful profile context without storing an unlimited page dump.
            body_text = body_text.strip()[:30000]
            return {
                "url": url,
                "title": title,
                "name": name,
                "headline": headline,
                "location": location,
                "profile_text": body_text,
            }
        finally:
            # Closing the Playwright connection must NOT close the user's Chrome.
            browser.close()
