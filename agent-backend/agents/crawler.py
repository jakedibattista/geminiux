import asyncio
import uuid
import time
import urllib.parse
from typing import Any

from playwright.async_api import async_playwright
import firebase_admin
from firebase_admin import storage, firestore

from agents.browser_driver import BrowserDriver

async def _capture_page_screenshots(driver: BrowserDriver, url: str, label: str, count: int) -> list[str]:
    """Helper to capture multiple screenshots of a page by scrolling."""
    shots = []
    for i in range(count):
        shot_url = await driver.create_screenshot_upload(page_url=url, persist_for_page=False, force_new=True)
        if shot_url:
            shots.append(shot_url)
        if i < count - 1:
            await driver.scroll("down")
            await asyncio.sleep(1)
    return shots

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
        
        # Capture homepage shots in parallel for both devices
        desktop_homepage_shots_task = _capture_page_screenshots(desktop_driver, homepage_url, "Homepage", 3)
        mobile_homepage_shots_task = _capture_page_screenshots(mobile_driver, homepage_url, "Homepage", 3)
        
        desktop_homepage_shots, mobile_homepage_shots = await asyncio.gather(
            desktop_homepage_shots_task, mobile_homepage_shots_task
        )
            
        captured_pages.append({
            "url": homepage_url,
            "label": "Homepage",
            "desktop_screenshots": desktop_homepage_shots,
            "mobile_screenshots": mobile_homepage_shots,
            "screenshots": desktop_homepage_shots # Backwards compatibility
        })
        
        # 2. Extract top nav links (using desktop state) and visit up to 3 more pages
        nav_links = desktop_state.get("primary_nav_links", [])
        visited_urls = {homepage_url}
        pages_to_visit = []
        
        for link in nav_links:
            url = link.get("url")
            if url and url not in visited_urls:
                pages_to_visit.append(url)
                visited_urls.add(url)
                if len(pages_to_visit) >= 3: # Reduced to 3 subpages (4 total) to avoid too many screenshots
                    break
                    
        # 3. Visit and capture the subpages
        for url in pages_to_visit:
            print(f"[Crawler {audit_id}] Navigating to {url} (desktop & mobile)")
            await asyncio.gather(desktop_driver.navigate(url), mobile_driver.navigate(url))
            await asyncio.sleep(2)
            
            desktop_sub_task = _capture_page_screenshots(desktop_driver, url, "Subpage", 2)
            mobile_sub_task = _capture_page_screenshots(mobile_driver, url, "Subpage", 2)
            
            desktop_sub_shots, mobile_sub_shots = await asyncio.gather(desktop_sub_task, mobile_sub_task)
                
            # Basic path extraction for label
            path = urllib.parse.urlparse(url).path.strip('/')
            label = path.split('/')[-1].replace('-', ' ').replace('_', ' ').title() if path else 'Page'
                
            captured_pages.append({
                "url": url,
                "label": label,
                "desktop_screenshots": desktop_sub_shots,
                "mobile_screenshots": mobile_sub_shots,
                "screenshots": desktop_sub_shots # Backwards compatibility
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
        await asyncio.gather(desktop_driver.close(), mobile_driver.close())
