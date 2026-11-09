"""Open a real browser with HAR recording on, let a human use the site, save the traffic."""
from __future__ import annotations

from pathlib import Path


def capture_manual(start_url: str, har_path: Path, profile_dir: Path | None = None) -> Path:
    """Headed Chromium; the user browses, then presses Enter in the terminal to finish.

    profile_dir keeps cookies between runs, so you can log in once and reuse the session.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "Playwright is not installed. Run: pip install playwright && playwright install chromium"
        ) from exc

    har_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        har_opts = dict(record_har_path=str(har_path), record_har_content="embed")
        if profile_dir:
            context = p.chromium.launch_persistent_context(
                str(profile_dir), headless=False, **har_opts
            )
            browser = None
        else:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(**har_opts)

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(start_url)
        print("\nBrowser is open and recording.")
        print("Use the site the way your API should: run searches, open details, paginate, filter.")
        input("Press Enter here when you're done (keep the browser open until then)... ")

        context.close()  # the HAR file is written on close
        if browser:
            browser.close()
    return har_path
