# ADK to Native GenAI SDK Migration (COMPLETED)

This document outlines the decision and implications of moving away from the Google Agent Development Kit (ADK) to directly using the Google GenAI SDK to control headless browsers (Playwright) and orchestrate agents.

---

## Why We Migrated

1. `gemini-2.5-computer-use-preview-10-2025` could not be mixed with custom callable tools (like `log_issue`) inside the ADK — it threw `400 INVALID_ARGUMENT: Tool use is not supported with this model configuration`.
2. Even the two-stage workaround (separate BrowsingAgent + ReportingAgent per persona) hit the model's 25 RPM rate limit when 3+ personas ran concurrently, causing `429 RESOURCE_EXHAUSTED` crashes.
3. ADK swallowed intermediate screenshots — the multimodal state passing worked differently from what we expected and we couldn't reliably attach evidence screenshots to individual findings.

---

## The Native Architecture (Map-Reduce / Crawl-and-Review)

As of March 16, 2026, the architecture has evolved from a continuous browsing loop into a two-phase "Map-Reduce" system to improve reliability and screenshot quality.

### 1. Standalone Crawler (`crawler.py`)
Site navigation is now decoupled from UX reasoning. A dedicated `CrawlerAgent` uses `BrowserDriver` (Playwright) to:
- Visit the target URL and extract primary navigation links.
- Capture dual Desktop (1280x800) and Mobile (390x844) screenshots of the homepage and top 3 subpages.
- Handle authentication (login) before the crawl starts.
- Upload all screenshots to Firebase Storage and return the structured `crawledPages` payload.

### 2. Vision QA Gate (`screenshot_reviewer.py`)
Before persona agents see any images, a dedicated vision model (`gemini-2.5-flash`) reviews every captured URL. It rejects "messy" screenshots (blank images, empty device frames, loading skeletons) to ensure the final audit only contains "presentation-ready" evidence.

### 3. Pure Multimodal Reviewers (`native_persona.py`)
Instead of driving a browser, persona agents are now **pure batch reviewers**. They:
- Receive only the "eligible" (approved) screenshots for their target device type.
- Use **`gemini-3.1-pro-preview`** to evaluate the entire batch in a single pass.
- Are required to provide at least one first-person finding for **every single screenshot** they receive.
- Log findings via the `log_issue` tool, which streams directly to Firestore.

### 4. Parallel Execution
`asyncio.gather()` in `main.py` orchestrates the entire pipeline: Crawler → Vision Filter → Parallel Persona Reviewers → Consolidator. Exceptions are caught per-agent so one failure doesn't kill the rest.

---

---

## Scope Philosophy Migration (Post-Launch Iteration)

## Scope Philosophy Migration (Post-Launch Iteration)

After running real audits against production sites, a second major design shift happened in March 2026. The agents were generating high volumes of false positives — findings about broken CTAs, dead links, non-functional App Store buttons, and broken interactive demos. All of these were headless simulation artifacts, not real bugs.

**The final decision:** As of March 16, 2026, the architecture was pivoted to **pure static Reviewers**. The original `QA Agent` was sunset, and the remaining personas are now **pure Content & UX Researchers**. They no longer interact with the page; they only evaluate static screenshots. Their system prompts now:

1. Focus entirely on copy quality, information gaps, visual hierarchy, readability, and emotional journey
2. Are prohibited from commenting on broken links, interactivity, or transient simulation failures
3. Treat blank media or empty device frames as "messy" artifacts to be rejected during the Vision QA phase

This is documented at length in `hackathon_report.md` under "33. Vision-Model-Driven 'Eligible Screenshot' Refinement (Mar 16, 2026)."

---

## Coordinate Overlay Removal (Mar 9, 2026)

The original system had `(x, y)` coordinates logged per-finding and rendered as CSS red circles overlaid on screenshots. This was removed because:

- The Gemini model internally resizes images before processing, so coordinates it reports are in the model's internal image pixel space — not the actual Playwright viewport pixel space (1280×800 or 390×844)
- Despite multiple attempts to correct the scaling math (separate `scaleX`/`scaleY` per axis, propagating `isMobile` correctly through the rendering pipeline), the circles continued to land in the wrong positions
- The screenshot itself provides sufficient visual context; the circles added noise and confusion

The `(x, y)` values are still stored in Firestore and still logged by agents (the backend is unchanged), but the frontend no longer renders them.
