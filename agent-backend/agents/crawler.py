import asyncio
import io
import uuid
import time
import urllib.parse
from typing import Any

from playwright.async_api import async_playwright
import firebase_admin
from firebase_admin import storage, firestore

from agents.browser_driver import BrowserDriver

try:
    from PIL import Image as PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


def _stitch_png_frames(frames: list[bytes]) -> bytes:
    """Vertically stitch viewport PNG frames into one composite image."""
    if not frames:
        return b""
    if len(frames) == 1 or not _PIL_AVAILABLE:
        return frames[0]

    images = [PILImage.open(io.BytesIO(f)).convert("RGB") for f in frames]
    width = max(img.width for img in images)
    total_height = sum(img.height for img in images)

    composite = PILImage.new("RGB", (width, total_height))
    y_offset = 0
    for img in images:
        composite.paste(img, (0, y_offset))
        y_offset += img.height

    buf = io.BytesIO()
    composite.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


async def _upload_png(audit_id: str, png_bytes: bytes, prefix: str) -> str | None:
    """Upload a PNG to Firebase Storage and return its download URL."""
    if not firebase_admin._apps or not png_bytes:
        return None
    try:
        bucket = storage.bucket()
        ts_ms = int(time.time() * 1000)
        token = str(uuid.uuid4())
        blob_name = f"screenshots/{audit_id}/crawler/{prefix}_{ts_ms}.png"
        blob = bucket.blob(blob_name)
        blob.metadata = {"firebaseStorageDownloadTokens": token}
        await asyncio.to_thread(blob.upload_from_string, png_bytes, content_type="image/png", timeout=30)
        encoded = urllib.parse.quote(blob_name, safe="")
        return f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{encoded}?alt=media&token={token}"
    except Exception as e:
        print(f"[Crawler {audit_id}] PNG upload error ({prefix}): {e}")
        return None


async def _capture_page_screenshots(
    driver: BrowserDriver,
    audit_id: str,
    url: str,
    label: str,
    max_frames: int,
) -> tuple[list[str], str | None]:
    """
    Capture enough viewport frames to reach the footer or page bottom, up to
    `max_frames`, then stitch them into a single composite image.
    """
    frames: list[bytes] = []
    try:
        await driver.page.evaluate("() => window.scrollTo(0, 0)")
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"[Crawler {audit_id}] Initial scroll failed on {url}: {e}")
        pass

    last_scroll_y = -1
    for i in range(max_frames):
        try:
            png_bytes = await driver.page.screenshot(type="png", full_page=False, timeout=15000)
            frames.append(png_bytes)
        except Exception as e:
            print(f"[Crawler {audit_id}] Frame capture error on {url} (frame {i + 1}): {e}")
            # If screenshot fails, browser context is likely dead
            break

        try:
            metrics = await driver.page.evaluate(
                """() => {
                    const doc = document.documentElement;
                    const body = document.body;
                    const footer = document.querySelector('footer');
                    const scrollY = window.scrollY;
                    const viewportHeight = window.innerHeight;
                    const scrollHeight = Math.max(
                        doc?.scrollHeight || 0,
                        body?.scrollHeight || 0,
                        doc?.offsetHeight || 0,
                        body?.offsetHeight || 0
                    );
                    const footerBottom = footer
                        ? (footer.getBoundingClientRect().bottom + window.scrollY)
                        : null;
                    return { scrollY, viewportHeight, scrollHeight, footerBottom };
                }"""
            )
        except Exception as e:
            print(f"[Crawler {audit_id}] Failed to read page metrics on {url}: {e}")
            break

        scroll_y = int(metrics.get("scrollY") or 0)
        viewport_height = max(1, int(metrics.get("viewportHeight") or 1))
        scroll_height = max(viewport_height, int(metrics.get("scrollHeight") or viewport_height))
        footer_bottom = metrics.get("footerBottom")
        footer_target = int(footer_bottom) if isinstance(footer_bottom, (int, float)) else scroll_height
        visible_bottom = scroll_y + viewport_height
        reached_footer = visible_bottom >= footer_target - 24
        reached_bottom = visible_bottom >= scroll_height - 24

        if reached_footer or reached_bottom:
            break

        next_scroll_y = min(
            scroll_y + max(1, int(viewport_height * 0.85)),
            max(0, scroll_height - viewport_height),
        )
        if next_scroll_y <= scroll_y or next_scroll_y == last_scroll_y:
            break

        try:
            await driver.page.evaluate("(y) => window.scrollTo(0, y)", next_scroll_y)
            last_scroll_y = next_scroll_y
            await asyncio.sleep(1)
        except Exception as e:
            print(f"[Crawler {audit_id}] Scroll error on {url} (frame {i + 1}): {e}")
            break

    # Scroll back to top so nav-link extraction and next-page navigation aren't
    # affected by a stale scroll position.
    try:
        await driver.page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass

    if not frames:
        return [], None

    composite_bytes = _stitch_png_frames(frames)
    composite_task = _upload_png(audit_id, composite_bytes, "composite")
    preview_task = _upload_png(audit_id, frames[0], "viewport")
    composite_url, preview_url = await asyncio.gather(composite_task, preview_task)
    return ([composite_url] if composite_url else []), preview_url


async def run_crawler_agent(audit_id: str, target_url: str, auth: dict = None):
    """
    Phase 1 map/reduce: Dedicated crawler agent.
    Spins up desktop and mobile browsers, explores the site, and takes screenshots
    to hand off to the persona reviewers.
    """
    print(f"[Crawler {audit_id}] Starting crawler for {target_url}")

    desktop_driver = BrowserDriver(screen_size=(1280, 800), initial_url=target_url, audit_id=audit_id, persona_id="crawler_desktop", auth=auth)
    mobile_driver = BrowserDriver(screen_size=(390, 844), initial_url=target_url, audit_id=audit_id, persona_id="crawler_mobile", auth=auth)

    db = firestore.client() if firebase_admin._apps else None
    if db:
        db.collection("audits").document(audit_id).set({
            "crawlerStatus": "running"
        }, merge=True)

    captured_pages = []

    try:
        # Initialize both drivers in parallel
        await asyncio.gather(desktop_driver.initialize(), mobile_driver.initialize())

        # 1. Capture Homepage
        print(f"[Crawler {audit_id}] Capturing homepage (desktop & mobile)...")
        desktop_state = await desktop_driver.get_state()
        homepage_url = desktop_state["url"]

        desktop_homepage_shots_task = _capture_page_screenshots(desktop_driver, audit_id, homepage_url, "Homepage", 7)
        mobile_homepage_shots_task = _capture_page_screenshots(mobile_driver, audit_id, homepage_url, "Homepage", 7)

        (desktop_homepage_shots, desktop_homepage_preview), (mobile_homepage_shots, mobile_homepage_preview) = await asyncio.gather(
            desktop_homepage_shots_task, mobile_homepage_shots_task
        )

        captured_pages.append({
            "url": homepage_url,
            "label": "Homepage",
            "desktop_screenshots": desktop_homepage_shots,
            "mobile_screenshots": mobile_homepage_shots,
            "desktop_presentation_screenshot": desktop_homepage_preview,
            "mobile_presentation_screenshot": mobile_homepage_preview,
            "screenshots": desktop_homepage_shots  # Backwards compatibility
        })

        # 2. Extract top nav links and visit up to 3 more pages
        nav_links = desktop_state.get("primary_nav_links", [])
        visited_urls = {homepage_url}
        pages_to_visit = []

        for link in nav_links:
            url = link.get("url")
            if url and url not in visited_urls:
                pages_to_visit.append(url)
                visited_urls.add(url)
                if len(pages_to_visit) >= 3:
                    break

        # 3. Visit and capture the subpages
        for url in pages_to_visit:
            print(f"[Crawler {audit_id}] Navigating to {url} (desktop & mobile)")
            await asyncio.gather(desktop_driver.navigate(url), mobile_driver.navigate(url))
            await asyncio.sleep(2)

            desktop_sub_task = _capture_page_screenshots(desktop_driver, audit_id, url, "Subpage", 7)
            mobile_sub_task = _capture_page_screenshots(mobile_driver, audit_id, url, "Subpage", 7)

            (desktop_sub_shots, desktop_sub_preview), (mobile_sub_shots, mobile_sub_preview) = await asyncio.gather(desktop_sub_task, mobile_sub_task)

            path = urllib.parse.urlparse(url).path.strip('/')
            label = path.split('/')[-1].replace('-', ' ').replace('_', ' ').title() if path else 'Page'

            captured_pages.append({
                "url": url,
                "label": label,
                "desktop_screenshots": desktop_sub_shots,
                "mobile_screenshots": mobile_sub_shots,
                "desktop_presentation_screenshot": desktop_sub_preview,
                "mobile_presentation_screenshot": mobile_sub_preview,
                "screenshots": desktop_sub_shots  # Backwards compatibility
            })

        print(f"[Crawler {audit_id}] Finished crawling. Captured {len(captured_pages)} pages with dual viewports.")

        if db:
            db.collection("audits").document(audit_id).set({
                "crawlerStatus": "completed",
                "crawledPages": captured_pages
            }, merge=True)

        return {"status": "success", "crawledPages": captured_pages}

    except Exception as e:
        print(f"[Crawler {audit_id}] Error: {e}")
        if db:
            db.collection("audits").document(audit_id).set({
                "crawlerStatus": "error",
                "crawlerError": str(e)
            }, merge=True)
        return {"status": "error", "reason": str(e)}

    finally:
        # Close the drivers safely so one failure doesn't prevent the other from closing
        await asyncio.gather(
            desktop_driver.close(), 
            mobile_driver.close(),
            return_exceptions=True
        )
