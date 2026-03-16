import asyncio
import uuid
import time
import urllib.parse
from typing import Any

from playwright.async_api import async_playwright
import firebase_admin
from firebase_admin import storage, firestore

PLAYWRIGHT_KEY_MAP = {
    "backspace": "Backspace", "tab": "Tab", "return": "Enter", "enter": "Enter",
    "shift": "Shift", "control": "Control", "alt": "Alt", "escape": "Escape",
    "space": "Space", "pageup": "PageUp", "pagedown": "PageDown", "end": "End",
    "home": "Home", "left": "ArrowLeft", "up": "ArrowUp", "right": "ArrowRight",
    "down": "ArrowDown", "delete": "Delete", "command": "Meta",
}

def _normalize_url_for_compare(url: str | None) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"

class BrowserDriver:
    def __init__(self, screen_size=(1280, 800), initial_url="https://google.com", 
                 audit_id=None, persona_id=None, auth=None):
        self.screen_size = screen_size
        self.initial_url = initial_url
        self.audit_id = audit_id
        self.persona_id = persona_id
        self.auth = auth
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.screenshot_count = 0
        self.MAX_SCREENSHOTS = 15
        self.last_screenshot_url = ""
        self.last_upload_time = 0.0
        self.auth_attempted = False
        self.auth_succeeded = False
        self.auth_error = ""
        self.login_url = (auth or {}).get("loginUrl", "")

    async def _settle_after_action(self, wait_ms: int = 750):
        try:
            await self.page.wait_for_load_state(timeout=5000)
        except Exception:
            pass
        await self.page.wait_for_timeout(wait_ms)

    def _looks_like_auth_url(self, url: str) -> bool:
        normalized = (url or "").lower()
        auth_tokens = [
            "/login", "/signin", "/sign-in", "/auth", "/session",
            "client_id=", "returnurl=", "redirect_uri=",
        ]
        return any(token in normalized for token in auth_tokens)

    async def _page_has_login_form(self) -> bool:
        try:
            return await self.page.evaluate("""
                () => {
                    const password = document.querySelector('input[type="password"]');
                    const email = document.querySelector('input[type="email"], input[name="email"], input[name="username"], input[id="email"]');
                    return Boolean(password || email);
                }
            """)
        except Exception:
            return False

    async def is_on_auth_page(self) -> bool:
        if self._looks_like_auth_url(self.page.url):
            return True
        return await self._page_has_login_form()

    async def initialize(self):
        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                args=[
                    "--disable-blink-features=AutomationControlled", 
                    "--disable-gpu",
                    "--autoplay-policy=no-user-gesture-required",
                    # Required for Google Cloud Run
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--single-process",
                ],
                headless=True
            )
            
            context_args = {
                "viewport": {"width": self.screen_size[0], "height": self.screen_size[1]},
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            }
            if self.screen_size[0] < 600:
                context_args["is_mobile"] = True
                context_args["has_touch"] = True
                context_args["user_agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1"
                
            self.context = await self.browser.new_context(**context_args)
            self.page = await self.context.new_page()
            
            if self.auth:
                await self._login()

            if not (self.auth and self.auth_succeeded):
                print(f"[{self.persona_id}] Navigating to {self.initial_url}")
                try:
                    await self.page.goto(self.initial_url, wait_until="networkidle", timeout=20000)
                except Exception as e:
                    try:
                        print(f"[{self.persona_id}] Warning: networkidle timeout on initial goto: {e}, falling back to domcontentloaded")
                        await self.page.goto(self.initial_url, wait_until="domcontentloaded", timeout=15000)
                    except Exception as e2:
                        print(f"[{self.persona_id}] Error: Initial navigation failed entirely: {e2}")
                        # Don't crash the whole agent if the site is misbehaving/slow
                        # Instead, let the agent continue and naturally report that the page couldn't be loaded
                        return
            else:
                print(f"[{self.persona_id}] Login succeeded; staying on authenticated page {self.page.url}")
                    
            if "chrome-error://" in self.page.url:
                print(f"[{self.persona_id}] Hit chrome-error on startup. Site is actively blocking us.")
                
            try:
                await self.page.wait_for_timeout(4000)
                await self.create_screenshot_upload(page_url=self.page.url, persist_for_page=False)
            except Exception as e:
                print(f"[{self.persona_id}] Warning: Error during startup settle/screenshot: {e}")
                
        except Exception as e:
            # Clean up what we can before bubbling the error up
            print(f"[{self.persona_id}] Failed during initialize: {e}")
            await self.close()
            raise e
        
    async def _login(self):
        login_url = self.auth.get('loginUrl')
        if not login_url:
            return
        self.auth_attempted = True
        self.auth_succeeded = False
        self.auth_error = ""
        print(f"[{self.persona_id}] Attempting login...")
        try:
            try:
                await self.page.goto(login_url, wait_until="networkidle", timeout=15000)
            except Exception:
                await self.page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
            
            email_selectors = ['input[type="email"]', 'input[name="email"]', 'input[name="username"]', 'input[id="email"]']
            for sel in email_selectors:
                try:
                    await self.page.fill(sel, self.auth.get('loginEmail', ''), timeout=2000)
                    break
                except: pass
                
            try:
                await self.page.fill('input[type="password"]', self.auth.get('loginPassword', ''), timeout=3000)
            except: pass
            
            submit_selectors = ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Log in")']
            submitted = False
            for sel in submit_selectors:
                try:
                    await self.page.click(sel, timeout=2000)
                    submitted = True
                    break
                except: pass
                
            if not submitted:
                try: await self.page.keyboard.press("Enter")
                except: pass

            try:
                await self.page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                try:
                    await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
            await self.page.wait_for_timeout(2500)

            on_auth_page = await self.is_on_auth_page()
            self.auth_succeeded = not on_auth_page
            if self.auth_succeeded:
                print(f"[{self.persona_id}] Login appears successful — now at {self.page.url}")
            else:
                self.auth_error = "Still on login/auth page after submitting credentials."
                print(f"[{self.persona_id}] Login did not leave auth page; still at {self.page.url}")
        except Exception as e:
            self.auth_error = str(e)
            print(f"[{self.persona_id}] Login error: {e}")

    async def get_state(self) -> dict:
        try:
            await self.page.wait_for_load_state(timeout=5000)
        except: pass
        await asyncio.sleep(0.5)
        
        url = self.page.url
        screenshot_bytes = await self.page.screenshot(type="png", full_page=False, timeout=15000)
        scroll_y = await self.page.evaluate("() => window.scrollY")
        viewport_height = await self.page.evaluate("() => window.innerHeight")
        text = await self._get_clean_text()
        media_state = await self._get_visible_media_state()
        return {
            "url": url,
            "screenshot_bytes": screenshot_bytes,
            "text": text,
            "scroll_y": scroll_y,
            "viewport_height": viewport_height,
            "on_auth_page": await self.is_on_auth_page(),
            "media_state": media_state,
            "primary_nav_links": await self.get_primary_nav_links(),
        }

    async def _get_clean_text(self):
        try:
            texts = await self.page.evaluate("""
                () => {
                    const allowedTags = new Set([
                        'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
                        'P', 'LI', 'A', 'BUTTON', 'SPAN', 'LABEL',
                        'DT', 'DD', 'TD', 'TH'
                    ]);

                    const elements = Array.from(document.querySelectorAll('body *'));
                    const raw = [];

                    for (const el of elements) {
                        if (!(el instanceof HTMLElement)) continue;

                        const style = window.getComputedStyle(el);
                        if (
                            style.display === 'none' ||
                            style.visibility === 'hidden' ||
                            style.opacity === '0'
                        ) {
                            continue;
                        }

                        const rect = el.getBoundingClientRect();
                        if (
                            rect.width < 2 ||
                            rect.height < 2 ||
                            rect.bottom < 0 ||
                            rect.top > window.innerHeight ||
                            rect.right < 0 ||
                            rect.left > window.innerWidth
                        ) {
                            continue;
                        }

                        if (!allowedTags.has(el.tagName) && el.children.length > 0) {
                            continue;
                        }

                        const text = (el.innerText || '')
                            .replace(/\s+/g, ' ')
                            .trim();
                        if (!text || text.length < 2) continue;

                        raw.push(text.slice(0, 400));
                    }

                    const deduped = [];
                    for (const text of raw) {
                        if (deduped.includes(text)) continue;
                        if (deduped.some(existing => existing.includes(text))) continue;
                        deduped.push(text);
                    }

                    return deduped.slice(0, 80);
                }
            """)
            return "\n".join(texts)
        except:
            return ""

    async def _get_visible_media_state(self) -> dict[str, Any]:
        try:
            return await self.page.evaluate("""
                () => {
                    const selectors = [
                        'video',
                        'iframe',
                        'canvas',
                        'svg',
                        '[data-testid*="video"]',
                        '[class*="video"]',
                        '[class*="player"]',
                        '[class*="demo"]',
                        '[class*="animation"]',
                        '[class*="hotspot"]',
                        '[id*="video"]',
                        '[id*="player"]',
                        '[id*="demo"]',
                    ];

                    const isVisible = (el) => {
                        if (!(el instanceof Element)) return false;
                        const style = window.getComputedStyle(el);
                        if (
                            style.display === 'none' ||
                            style.visibility === 'hidden' ||
                            style.opacity === '0'
                        ) {
                            return false;
                        }

                        const rect = el.getBoundingClientRect();
                        return !(
                            rect.width < 8 ||
                            rect.height < 8 ||
                            rect.bottom < 0 ||
                            rect.top > window.innerHeight ||
                            rect.right < 0 ||
                            rect.left > window.innerWidth
                        );
                    };

                    const nodes = Array.from(new Set(
                        selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                    )).filter(isVisible);

                    const counts = {
                        video: 0,
                        iframe: 0,
                        canvas: 0,
                        svg: 0,
                        mediaLikeContainer: 0,
                        lazyMedia: 0,
                        posterlessVideo: 0,
                    };

                    for (const el of nodes) {
                        const tag = el.tagName.toLowerCase();
                        if (tag === 'video') {
                            counts.video += 1;
                            if (!el.getAttribute('poster')) counts.posterlessVideo += 1;
                        } else if (tag === 'iframe') {
                            counts.iframe += 1;
                            if ((el.getAttribute('loading') || '').toLowerCase() === 'lazy') counts.lazyMedia += 1;
                        } else if (tag === 'canvas') {
                            counts.canvas += 1;
                        } else if (tag === 'svg') {
                            counts.svg += 1;
                        } else {
                            counts.mediaLikeContainer += 1;
                        }
                    }

                    const totalVisibleMedia =
                        counts.video +
                        counts.iframe +
                        counts.canvas +
                        counts.svg +
                        counts.mediaLikeContainer;

                    return {
                        counts,
                        totalVisibleMedia,
                        hasEmbeddedMediaRisk: totalVisibleMedia > 0,
                    };
                }
            """)
        except Exception:
            return {
                "counts": {
                    "video": 0,
                    "iframe": 0,
                    "canvas": 0,
                    "svg": 0,
                    "mediaLikeContainer": 0,
                    "lazyMedia": 0,
                    "posterlessVideo": 0,
                },
                "totalVisibleMedia": 0,
                "hasEmbeddedMediaRisk": False,
            }

    async def get_primary_nav_links(self) -> list[dict[str, str]]:
        try:
            return await self.page.evaluate(r"""
                () => {
                    const containers = Array.from(document.querySelectorAll('header, nav, [role="navigation"]'));
                    const links = containers.flatMap(container => Array.from(container.querySelectorAll('a[href]')));
                    const seen = new Set();
                    const results = [];

                    const isVisible = (el) => {
                        if (!(el instanceof HTMLElement)) return false;
                        const style = window.getComputedStyle(el);
                        if (
                            style.display === 'none' ||
                            style.visibility === 'hidden' ||
                            style.opacity === '0'
                        ) {
                            return false;
                        }
                        const rect = el.getBoundingClientRect();
                        return rect.width >= 12 && rect.height >= 12 && rect.bottom > 0 && rect.top < 220;
                    };

                    for (const link of links) {
                        if (!(link instanceof HTMLAnchorElement) || !isVisible(link)) continue;

                        const rawHref = (link.getAttribute('href') || '').trim();
                        if (!rawHref || rawHref.startsWith('#') || rawHref.startsWith('javascript:')) continue;
                        if (rawHref.startsWith('mailto:') || rawHref.startsWith('tel:')) continue;

                        let resolved;
                        try {
                            resolved = new URL(rawHref, window.location.href);
                        } catch {
                            continue;
                        }

                        if (resolved.origin !== window.location.origin) continue;

                        const path = resolved.pathname.replace(/\/+$/, '') || '/';
                        const fullUrl = `${resolved.origin}${path}${resolved.search}`;
                        const text = (
                            link.innerText ||
                            link.getAttribute('aria-label') ||
                            link.getAttribute('title') ||
                            ''
                        ).replace(/\s+/g, ' ').trim();
                        if (!text || text.length > 40) continue;

                        const dedupeKey = `${text.toLowerCase()}::${fullUrl.toLowerCase()}`;
                        if (seen.has(dedupeKey)) continue;
                        seen.add(dedupeKey);

                        results.push({ text, url: fullUrl });
                        if (results.length >= 8) break;
                    }

                    return results;
                }
            """)
        except Exception:
            return []

    async def create_screenshot_upload(
        self,
        page_url: str | None = None,
        *,
        persist_for_page: bool = True,
        force_new: bool = False,
    ) -> str:
        if not firebase_admin._apps or not self.audit_id:
            return None
            
        try:
            target_page_url = page_url or self.page.url
            page_key = _normalize_url_for_compare(target_page_url)
            db = firestore.client()
            doc_ref = db.collection("audits").document(self.audit_id).collection("agentReports").document(self.persona_id)

            page_screenshots: dict[str, str] = {}
            try:
                snap = doc_ref.get()
                if snap.exists:
                    page_screenshots = snap.to_dict().get("pageScreenshots", {}) or {}
            except Exception:
                page_screenshots = {}

            existing_url = page_screenshots.get(page_key) or page_screenshots.get(target_page_url)
            if existing_url and persist_for_page and not force_new:
                doc_ref.set({
                    "latestScreenshot": existing_url,
                    "latestScreenshotPage": target_page_url,
                }, merge=True)
                return existing_url

            screenshot_bytes = await self.page.screenshot(type="png", full_page=False, timeout=15000)
            
            now = time.time()
            bucket = storage.bucket()
            ts_ms = int(now * 1000)
            token = str(uuid.uuid4())
            blob_name = f"screenshots/{self.audit_id}/{self.persona_id}/shot_{ts_ms}.png"
            blob = bucket.blob(blob_name)
            blob.metadata = {"firebaseStorageDownloadTokens": token}
            
            await asyncio.to_thread(blob.upload_from_string, screenshot_bytes, content_type="image/png", timeout=20)
            
            encoded = urllib.parse.quote(blob_name, safe="")
            download_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{encoded}?alt=media&token={token}"
            
            self.screenshot_count += 1
            print(f"[{self.persona_id}] Uploaded screenshot {self.screenshot_count}")
            
            if persist_for_page and page_key:
                page_screenshots[page_key] = download_url
            doc_ref.set({
                "latestScreenshot": download_url,
                "latestScreenshotPage": target_page_url,
                **({"pageScreenshots": page_screenshots} if persist_for_page else {}),
            }, merge=True)
            
            return download_url
        except Exception as e:
            print(f"[{self.persona_id}] Screenshot upload error: {e}")
            return None

    async def click(self, x: int, y: int):
        previous_url = self.page.url
        await self.page.mouse.move(x, y)
        await self.page.mouse.click(x, y)
        await self._settle_after_action()
        if _normalize_url_for_compare(previous_url) != _normalize_url_for_compare(self.page.url):
            await self.create_screenshot_upload(page_url=self.page.url, persist_for_page=False)
        return "Clicked"

    async def hover(self, x: int, y: int):
        await self.page.mouse.move(x, y)
        await self._settle_after_action(wait_ms=500)
        return "Hovered"

    async def type_text(self, text: str):
        await self.page.keyboard.type(text)
        await self.page.keyboard.press("Enter")
        await self._settle_after_action()
        return f"Typed {text}"

    async def press_key(self, key: str):
        resolved = PLAYWRIGHT_KEY_MAP.get(key.lower(), key)
        await self.page.keyboard.press(resolved)
        await self._settle_after_action(wait_ms=500)
        return f"Pressed {resolved}"

    async def scroll(self, direction: str):
        if direction == "down":
            await self.page.keyboard.press("PageDown")
        elif direction == "up":
            await self.page.keyboard.press("PageUp")
        await self._settle_after_action(wait_ms=500)
        return f"Scrolled {direction}"

    async def wait(self, seconds: float):
        seconds = max(1, min(float(seconds), 10))
        await self.page.wait_for_timeout(int(seconds * 1000))
        return f"Waited {seconds:.1f}s"

    async def go_back(self):
        previous_url = self.page.url
        try:
            await self.page.go_back(wait_until="domcontentloaded", timeout=15000)
        except Exception:
            await self.page.go_back()
        await self._settle_after_action()
        if _normalize_url_for_compare(previous_url) != _normalize_url_for_compare(self.page.url):
            await self.create_screenshot_upload(page_url=self.page.url, persist_for_page=False)
        return f"Went back to {self.page.url}"

    async def navigate(self, url: str):
        if url and "://" not in url and not url.startswith(("about:", "chrome-error://")):
            url = f"https://{url}"
        try:
            response = await self.page.goto(url, wait_until="networkidle", timeout=20000)
            if response and not response.ok:
                print(f"[{self.persona_id}] Warning: Received HTTP {response.status} for {url}")
        except Exception as e:
            try:
                print(f"[{self.persona_id}] Warning: networkidle failed for {url} ({e}), falling back to domcontentloaded...")
                response = await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception as e2:
                print(f"[{self.persona_id}] Error: Navigation failed entirely for {url}: {e2}")
                return f"Error: Navigation failed to load {url}. The site may be blocking automated traffic. Try a different task or finish the report."
                
        current_url = self.page.url
        if "chrome-error://" in current_url:
            return f"Error: Navigation blocked. The site refused to connect (likely anti-bot protection). Stop trying to navigate here and try a different task or finish the report."
            
        await self.page.wait_for_timeout(3000)
        await self.create_screenshot_upload(page_url=self.page.url, persist_for_page=False)
        return f"Navigated to {url}"

    async def close(self):
        try:
            if self.context: await self.context.close()
        except Exception:
            pass
            
        try:
            if self.browser: await self.browser.close()
        except Exception:
            pass
            
        try:
            if self.playwright: await self.playwright.stop()
        except Exception:
            pass
