import asyncio
import os
import time
import uuid
import urllib.parse
from typing import Literal
from typing import Optional

import firebase_admin
from google.adk.tools.computer_use.base_computer import BaseComputer
from google.adk.tools.computer_use.base_computer import ComputerEnvironment
from google.adk.tools.computer_use.base_computer import ComputerState
from playwright.async_api import async_playwright
import termcolor
from typing_extensions import override

# Define a mapping from the user-friendly key names to Playwright's expected key names.
# Playwright is generally good with case-insensitivity for these, but it's best to be canonical.
# See: https://playwright.dev/docs/api/class-keyboard#keyboard-press
# Keys like 'a', 'b', '1', '$' are passed directly.
PLAYWRIGHT_KEY_MAP = {
 "backspace": "Backspace",
 "tab": "Tab",
 "return": "Enter",  # Playwright uses 'Enter'
 "enter": "Enter",
 "shift": "Shift",
 "control": "Control",  # Or 'ControlOrMeta' for cross-platform Ctrl/Cmd
 "alt": "Alt",
 "escape": "Escape",
 "space": "Space",  # Can also just be " "
 "pageup": "PageUp",
 "pagedown": "PageDown",
 "end": "End",
 "home": "Home",
 "left": "ArrowLeft",
 "up": "ArrowUp",
 "right": "ArrowRight",
 "down": "ArrowDown",
 "insert": "Insert",
 "delete": "Delete",
 "semicolon": ";",  # For actual character ';'
 "equals": "=",  # For actual character '='
 "multiply": "Multiply",  # NumpadMultiply
 "add": "Add",  # NumpadAdd
 "separator": "Separator",  # Numpad specific
 "subtract": "Subtract",  # NumpadSubtract, or just '-' for character
 "decimal": "Decimal",  # NumpadDecimal, or just '.' for character
 "divide": "Divide",  # NumpadDivide, or just '/' for character
 "f1": "F1",
 "f2": "F2",
 "f3": "F3",
 "f4": "F4",
 "f5": "F5",
 "f6": "F6",
 "f7": "F7",
 "f8": "F8",
 "f9": "F9",
 "f10": "F10",
 "f11": "F11",
 "f12": "F12",
 "command": "Meta",  # 'Meta' is Command on macOS, Windows key on Windows
}


class PlaywrightComputer(BaseComputer):
 """Computer that controls Chromium via Playwright."""

 def __init__(
 self,
 screen_size: tuple[int, int],
 initial_url: str = "https://www.google.com",
 search_engine_url: str = "https://www.google.com",
 highlight_mouse: bool = False,
 user_data_dir: Optional[str] = None,
 audit_id: Optional[str] = None,
 persona_id: Optional[str] = None,
 login_url: Optional[str] = None,
 login_email: Optional[str] = None,
 login_password: Optional[str] = None,
 ):
  self._initial_url = initial_url
  self._screen_size = screen_size
  self._search_engine_url = search_engine_url
  self._highlight_mouse = highlight_mouse
  self._user_data_dir = user_data_dir
  self._audit_id = audit_id
  self._persona_id = persona_id
  self._login_url = login_url
  self._login_email = login_email
  self._login_password = login_password
  # Screenshot throttle / cap state
  self._last_screenshot_page: str = ""
  self._last_screenshot_ts: float = 0.0
  self._screenshot_count: int = 0

 @override
 async def initialize(self):
  print("Creating session...")
  self._playwright = await async_playwright().start()

  # Define common arguments for both launch types
  browser_args = [
  "--disable-blink-features=AutomationControlled",
  "--disable-gpu",
  # Required for Google Cloud Run
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-dev-shm-usage",
  "--single-process",
  ]

  if self._user_data_dir:
   termcolor.cprint(
   f"Starting playwright with persistent profile: {self._user_data_dir}",
   color="yellow",
   attrs=["bold"],
   )
   # Use a persistent context if user_data_dir is provided
   self._context = await self._playwright.chromium.launch_persistent_context(
   self._user_data_dir,
   headless=False,
   args=browser_args,
   )
   self._browser = self._context.browser
  else:
   termcolor.cprint(
   "Starting playwright with a temporary profile.",
   color="yellow",
   attrs=["bold"],
   )
   # Launch a temporary browser instance if user_data_dir is not provided
   self._browser = await self._playwright.chromium.launch(
   args=browser_args,
   headless=True,
   )
   self._context = await self._browser.new_context()

  if not self._context.pages:
   self._page = await self._context.new_page()
   await self._page.goto(self._initial_url)
  else:
   self._page = self._context.pages[0]  # Use existing page if any

  await self._page.set_viewport_size({
  "width": self._screen_size[0],
  "height": self._screen_size[1],
  })
  termcolor.cprint(
  f"Started local playwright.",
  color="green",
  attrs=["bold"],
  )

  # If credentials were provided, log in before the agent starts browsing
  if self._login_url and self._login_email and self._login_password:
   await self._login()

 @override
 async def environment(self):
  return ComputerEnvironment.ENVIRONMENT_BROWSER

 @override
 async def close(self, exc_type, exc_val, exc_tb):
  if self._context:
   await self._context.close()
  try:
   await self._browser.close()
  except Exception as e:
   # Browser was already shut down because of SIGINT or such.
   if (
   "Browser.close: Connection closed while reading from the driver"
   in str(e)
   ):
    pass
   else:
    raise

  await self._playwright.stop()

 async def open_web_browser(self) -> ComputerState:
  print(f"[{self._persona_id}] open_web_browser called", flush=True)
  return await self.current_state()

 async def click_at(self, x: int, y: int):
  print(f"[{self._persona_id}] click_at called: ({x},{y})", flush=True)
  await self.highlight_mouse(x, y)
  await self._page.mouse.click(x, y)
  await self._page.wait_for_load_state(timeout=5000)
  return await self.current_state()

 async def hover_at(self, x: int, y: int):
  await self.highlight_mouse(x, y)
  await self._page.mouse.move(x, y)
  await self._page.wait_for_load_state()
  return await self.current_state()

 async def type_text_at(
 self,
 x: int,
 y: int,
 text: str,
 press_enter: bool = True,
 clear_before_typing: bool = True,
 ) -> ComputerState:
  await self.highlight_mouse(x, y)
  await self._page.mouse.click(x, y)
  await self._page.wait_for_load_state()

  if clear_before_typing:
   await self.key_combination(["Control", "A"])
   await self.key_combination(["Delete"])

  await self._page.keyboard.type(text)
  await self._page.wait_for_load_state()

  if press_enter:
   await self.key_combination(["Enter"])
   await self._page.wait_for_load_state()
  return await self.current_state()

 async def _horizontal_document_scroll(
 self, direction: Literal["left", "right"]
 ) -> ComputerState:
  # Scroll by 50% of the viewport size.
  horizontal_scroll_amount = await self.screen_size()[0] // 2
  if direction == "left":
   sign = "-"
  else:
   sign = ""
  scroll_argument = f"{sign}{horizontal_scroll_amount}"
  # Scroll using JS.
  await self._page.evaluate(f"window.scrollBy({scroll_argument}, 0); ")
  await self._page.wait_for_load_state()
  return await self.current_state()

 async def scroll_document(
 self, direction: Literal["up", "down", "left", "right"]
 ) -> ComputerState:
  if direction == "down":
   return await self.key_combination(["PageDown"])
  elif direction == "up":
   return await self.key_combination(["PageUp"])
  elif direction in ("left", "right"):
   return await self._horizontal_document_scroll(direction)
  else:
   raise ValueError("Unsupported direction: ", direction)

 async def scroll_at(
 self,
 x: int,
 y: int,
 direction: Literal["up", "down", "left", "right"],
 magnitude: int,
 ) -> ComputerState:
  await self.highlight_mouse(x, y)

  await self._page.mouse.move(x, y)
  await self._page.wait_for_load_state()

  dx = 0
  dy = 0
  if direction == "up":
   dy = -magnitude
  elif direction == "down":
   dy = magnitude
  elif direction == "left":
   dx = -magnitude
  elif direction == "right":
   dx = magnitude
  else:
   raise ValueError("Unsupported direction: ", direction)

  await self._page.mouse.wheel(dx, dy)
  await self._page.wait_for_load_state()
  return await self.current_state()

 async def wait(self, seconds: int) -> ComputerState:
  await asyncio.sleep(seconds)
  return await self.current_state()

 async def go_back(self) -> ComputerState:
  await self._page.go_back()
  await self._page.wait_for_load_state()
  return await self.current_state()

 async def go_forward(self) -> ComputerState:
  await self._page.go_forward()
  await self._page.wait_for_load_state()
  return await self.current_state()

 async def search(self) -> ComputerState:
  return await self.navigate(self._search_engine_url)

 async def navigate(self, url: str) -> ComputerState:
  print(f"[{self._persona_id}] navigate called: {url[:80]}", flush=True)
  await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
  print(f"[{self._persona_id}] navigate goto done", flush=True)
  return await self.current_state()

 async def key_combination(self, keys: list[str]) -> ComputerState:
  # Normalize all keys to the Playwright compatible version.
  keys = [PLAYWRIGHT_KEY_MAP.get(k.lower(), k) for k in keys]

  for key in keys[:-1]:
   await self._page.keyboard.down(key)

  await self._page.keyboard.press(keys[-1])

  for key in reversed(keys[:-1]):
   await self._page.keyboard.up(key)

  return await self.current_state()

 async def drag_and_drop(
 self, x: int, y: int, destination_x: int, destination_y: int
 ) -> ComputerState:
  await self.highlight_mouse(x, y)
  await self._page.mouse.move(x, y)
  await self._page.wait_for_load_state()
  await self._page.mouse.down()
  await self._page.wait_for_load_state()

  await self.highlight_mouse(destination_x, destination_y)
  await self._page.mouse.move(destination_x, destination_y)
  await self._page.wait_for_load_state()
  await self._page.mouse.up()
  return await self.current_state()

 async def current_state(self) -> ComputerState:
  with open("/tmp/screenshot_debug_REAL.log", "a") as f: f.write(f"[{self._persona_id}] current_state ENTER\n")
  try:
   await self._page.wait_for_load_state(timeout=5000)
  except Exception:
   pass  # Timeout is fine — just take the screenshot of wherever we are
  time.sleep(0.5)
  current_url = self._page.url
  with open("/tmp/screenshot_debug_REAL.log", "a") as f: f.write(f"[{self._persona_id}] URL: {current_url[:80]}\n")
  # Viewport-only screenshot for Gemini — tall full-page images confuse the model
  viewport_screenshot = await self._page.screenshot(type="png", full_page=False)
  # Full-page screenshot for Storage — shows the whole page in the UI
  try:
   full_page_screenshot = await self._page.screenshot(type="png", full_page=True)
  except Exception:
   full_page_screenshot = viewport_screenshot
  # asyncio.to_thread properly ties the thread lifecycle to the event loop,
  # unlike ThreadPoolExecutor(wait=False) which gets GC'd before completing.
  try:
   await asyncio.to_thread(self._upload_screenshot, full_page_screenshot, current_url)
  except Exception as e:
   print(f"[{self._persona_id}] current_state upload error: {e}", flush=True)
  return ComputerState(screenshot=viewport_screenshot, url=current_url)

 async def _login(self) -> None:
  """
  Navigates to the login page and fills in credentials before the browsing agent starts.
  Tries common field selectors in priority order to handle most login forms.
  """
  termcolor.cprint(f"Attempting login at {self._login_url} ...", color="cyan", attrs=["bold"])
  try:
   await self._page.goto(self._login_url)
   await self._page.wait_for_load_state("networkidle", timeout=10000)

   # --- Fill email / username ---
   email_selectors = [
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input[name="user"]',
    'input[id="email"]',
    'input[id="username"]',
    'input[autocomplete="email"]',
    'input[autocomplete="username"]',
   ]
   email_filled = False
   for sel in email_selectors:
    try:
     await self._page.fill(sel, self._login_email, timeout=2000)
     email_filled = True
     termcolor.cprint(f"  Filled email with selector: {sel}", color="cyan")
     break
    except Exception:
     continue

   if not email_filled:
    termcolor.cprint("  WARNING: Could not find email/username field — login may fail.", color="yellow")

   # --- Fill password ---
   try:
    await self._page.fill('input[type="password"]', self._login_password, timeout=3000)
    termcolor.cprint("  Filled password field.", color="cyan")
   except Exception:
    termcolor.cprint("  WARNING: Could not find password field — login may fail.", color="yellow")

   # --- Submit ---
   submit_selectors = [
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Sign in")',
    'button:has-text("Log in")',
    'button:has-text("Login")',
    'button:has-text("Sign In")',
    'button:has-text("Continue")',
   ]
   submitted = False
   for sel in submit_selectors:
    try:
     await self._page.click(sel, timeout=2000)
     submitted = True
     termcolor.cprint(f"  Submitted with selector: {sel}", color="cyan")
     break
    except Exception:
     continue

   if not submitted:
    # Last resort: press Enter on the password field
    try:
     await self._page.keyboard.press("Enter")
     submitted = True
     termcolor.cprint("  Submitted via Enter key.", color="cyan")
    except Exception:
     termcolor.cprint("  WARNING: Could not find submit button — login may fail.", color="yellow")

   if submitted:
    await self._page.wait_for_load_state("networkidle", timeout=10000)
    termcolor.cprint(f"  Login complete — now at: {self._page.url}", color="green", attrs=["bold"])

  except Exception as e:
   termcolor.cprint(f"  Login failed: {e}", color="red", attrs=["bold"])
   # Don't crash the agent — proceed without auth and let it do its best

 MAX_SCREENSHOTS = 10

 def _upload_screenshot(self, screenshot_bytes: bytes, current_url: str) -> None:
  """
  Called via asyncio.to_thread() from current_state() — safe to do blocking I/O here.
  Throttled by URL change or 30s elapsed. Capped at MAX_SCREENSHOTS per session.
  Prints a diagnostic line for every early-exit so failures are visible in the logs.
  """
  tag = f"[{self._persona_id or '?'}]"
  now = time.time()
  with open("/tmp/screenshot_debug.log", "a") as f:
   f.write(f"[{now}] {tag} upload start for {current_url}\n")
  
  if not self._audit_id or not self._persona_id:
   with open("/tmp/screenshot_debug.log", "a") as f: f.write(f"{tag} skip: no id\n")
   return
  if not firebase_admin._apps:
   with open("/tmp/screenshot_debug.log", "a") as f: f.write(f"{tag} skip: no firebase\n")
   return
  if self._screenshot_count >= self.MAX_SCREENSHOTS:
   with open("/tmp/screenshot_debug.log", "a") as f: f.write(f"{tag} skip: cap\n")
   return

  url_changed = current_url != self._last_screenshot_page
  time_elapsed = (now - self._last_screenshot_ts) >= 30

  if not (url_changed or time_elapsed):
   with open("/tmp/screenshot_debug.log", "a") as f: f.write(f"{tag} skip: throttle\n")
   return  # silent — just throttle noise

  self._last_screenshot_page = current_url
  self._last_screenshot_ts = now

  with open("/tmp/screenshot_debug.log", "a") as f: f.write(f"{tag} proceeding to upload to storage...\n")
  try:
   from firebase_admin import storage as fb_storage, firestore

   bucket = fb_storage.bucket()
   ts_ms = int(now * 1000)
   token = str(uuid.uuid4())
   blob_name = f"screenshots/{self._audit_id}/{self._persona_id}/{ts_ms}.png"
   blob = bucket.blob(blob_name)
   blob.metadata = {"firebaseStorageDownloadTokens": token}
   blob.upload_from_string(screenshot_bytes, content_type="image/png")

   encoded = urllib.parse.quote(blob_name, safe="")
   download_url = (
    f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}"
    f"/o/{encoded}?alt=media&token={token}"
   )

   self._screenshot_count += 1
   print(f"{tag} screenshot {self._screenshot_count}/{self.MAX_SCREENSHOTS} OK: {blob_name}")

   db = firestore.client()
   doc_ref = db.collection("audits").document(self._audit_id) \
    .collection("agentReports").document(self._persona_id)
   
   doc_ref.set({
    "latestScreenshot": download_url,
    "latestScreenshotPage": current_url,
    "pageScreenshots": {current_url: download_url},
   }, merge=True)
   with open("/tmp/screenshot_debug.log", "a") as f: f.write(f"{tag} SUCCESS: db updated\n")

  except Exception as e:
   with open("/tmp/screenshot_debug.log", "a") as f: f.write(f"{tag} screenshot UPLOAD FAILED: {e}\n")

 async def get_page_text(self) -> str:
  """Returns the visible text content of the current page, stripped of HTML."""
  await self._page.wait_for_load_state()
  try:
   text = await self._page.inner_text('body')
   lines = [line.strip() for line in text.splitlines() if line.strip()]
   return f"[URL: {self._page.url}]\n\n" + "\n".join(lines)
  except Exception as e:
   return f"[Could not extract page text: {e}]"

 async def screen_size(self) -> tuple[int, int]:
  return self._screen_size

 async def highlight_mouse(self, x: int, y: int):
  if not self._highlight_mouse:
   return
  await self._page.evaluate(f"""
  () => {{
   const element_id = "playwright-feedback-circle";
   const div = document.createElement('div');
   div.id = element_id;
   div.style.pointerEvents = 'none';
   div.style.border = '4px solid red';
   div.style.borderRadius = '50%';
   div.style.width = '20px';
   div.style.height = '20px';
   div.style.position = 'fixed';
   div.style.zIndex = '9999';
   document.body.appendChild(div);

   div.hidden = false;
   div.style.left = {x} - 10 + 'px';
   div.style.top = {y} - 10 + 'px';

   setTimeout(() => {{
   div.hidden = true;
   }}, 2000);
  }}
  """)
  # Wait a bit for the user to see the cursor.
  time.sleep(1)