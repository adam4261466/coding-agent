"""Minimal LinkedIn profile reader used by the conversation assistant.

It opens one supplied LinkedIn profile in a dedicated persistent Chrome profile,
reads text that is actually visible on the page, and returns that text to the
local assistant. It does not send messages, connect with people, or click
outreach controls.
"""
from __future__ import annotations

import os
from typing import Any

from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_DIR = os.path.join(BASE_DIR, ".linkedin-browser-profile")


def _first_text(page: Any, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible():
                text = locator.inner_text(timeout=2000).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def fetch_profile(url: str, timeout_ms: int = 30000) -> dict[str, str]:
    url = url.strip()
    if not url.startswith("https://www.linkedin.com/"):
        raise ValueError("Please provide a valid LinkedIn profile URL starting with https://www.linkedin.com/")

    os.makedirs(PROFILE_DIR, exist_ok=True)

    with sync_playwright() as pw:
        try:
            context = pw.chromium.launch_persistent_context(
                PROFILE_DIR,
                channel="chrome",
                headless=False,
                viewport={"width": 1440, "height": 900},
                args=["--disable-notifications"],
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not open Chrome with Playwright. Install the Python package with "
                "'pip install playwright' and make sure Google Chrome is installed."
            ) from exc

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(2000)

            title = page.title().strip()
            name = _first_text(page, ["h1"])
            headline = _first_text(page, [
                ".text-body-medium.break-words",
                "div[data-generated-suggestion-target]",
            ])
            location = _first_text(page, [".text-body-small.inline.t-black--light.break-words"])

            try:
                body_text = page.locator("body").inner_text(timeout=5000)
            except Exception as exc:
                raise RuntimeError(f"Could not read LinkedIn profile content: {exc}") from exc

            if not body_text.strip():
                raise RuntimeError(
                    "LinkedIn returned an empty page. Check that you are logged in in the assistant's Chrome window."
                )

            # Keep the context useful without storing the entire page indefinitely.
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
            context.close()
