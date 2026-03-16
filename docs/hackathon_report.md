# AuditMySite — Hackathon Build Report

**Event:** Gemini Live Agent Challenge — Track 3: UI Navigator  
**Project:** AuditMySite  
**Date:** March 4, 2026

---

## What We Built

AuditMySite is a web application that runs multiple AI-driven "user personas" against any URL and streams a live UX audit back to the user in real time. You paste a URL, pick which types of users you want to simulate (a first-time visitor, a mobile user, an accessibility user, etc.), and AI agents browse the site as each of those people — clicking around, scrolling, noting confusing UI patterns, and reporting findings as they go. When all the agents finish, a consolidator agent synthesizes everything into an executive UX report with a score, critical issues, recommendations, and what actually worked well.

The whole thing runs against a live website, uses real browser automation, and streams agent activity to the frontend as it happens.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15 (App Router), React, Tailwind CSS, shadcn/ui |
| Auth | Firebase Auth (client) + Firebase Admin SDK (session cookies) |
| Real-time updates | Firestore `onSnapshot` listeners |
| Backend | Python, FastAPI, Google ADK |
| AI Models | Gemini model split: `gemini-2.5-pro` + TTS on Google AI Studio for core audits, Vertex AI preview models (`gemini-3.1-pro-preview`, `gemini-3-pro-image-preview`) for the post-audit presentation layer |
| Browser automation | Playwright (headless Chromium) via ADK's `ComputerUseToolset` |
| Storage | Firebase Firestore (data) |
| Hosting target | Cloud Run (backend), Vercel (frontend) |

---

## Architecture

The system is split into two services that talk through Firestore as a shared message bus:

**Frontend (Next.js)**
- Route groups: `/(auth)` for login/signup, `/(protected)` for the app itself
- `src/proxy.ts` — middleware that guards all protected routes by verifying the `__session` cookie
- `POST /api/audit/start` — creates the Firestore audit document, then fire-and-forgets a POST to the Python backend
- `GET /audit/[auditId]` — pure real-time listener; the page subscribes to `onSnapshot` on both the audit document and the `agentReports` subcollection

**Backend (Python + Google GenAI SDK) — The Pivot**

We had to significantly pivot our agent architecture twice due to model access, tool compatibility constraints, and rate limits. Here is what we originally planned versus what we successfully built:

### 1. Planned Agent Tree
Our initial plan was to use Vertex AI and the newest `gemini-3.1-pro-preview` model for every single agent, combining browser automation and custom function calling into single steps.

```
SequentialAgent (root)
├── ParallelAgent
│   ├── LlmAgent (persona 1) [gemini-3.1-pro-preview via Vertex AI + ComputerUseToolset + log_issue]
│   ├── LlmAgent (persona 2) [gemini-3.1-pro-preview via Vertex AI + ComputerUseToolset + log_issue]
│   └── ...
└── Consolidator LlmAgent [gemini-3.1-pro-preview via Vertex AI]
```

### 2. Intermediate ADK Architecture
Due to Google Cloud allowlisting issues and the ADK's strict rejection of combining `ComputerUseToolset` with custom tools in specialized models, we pivoted to using Google AI Studio. We used `gemini-2.5-computer-use-preview` for browsing and a separate `gemini-2.5-pro` agent for reporting.

### 3. Actual Final Architecture (Native GenAI SDK)
Ultimately, the computer-use model had severe rate limits (25 RPM) and ADK swallowed the screenshots. We dropped the ADK entirely for the browser agents and wrote a custom execution loop (`native_persona.py`). 

```
Asyncio.gather (root)
├── run_persona_agent (persona 1) [gemini-2.5-pro + Native Playwright Tools + log_issue]
├── run_persona_agent (persona 2) [gemini-2.5-pro + Native Playwright Tools + log_issue]
└── ...

Then...
└── Consolidator LlmAgent [gemini-3.1-pro-preview via AI Studio]
```

---

## Features Shipped Together During Our Session

- **Dynamic Persona Builder:** Added a feature allowing users to type a plain English description of a target user. Gemini then automatically generates a new persona card and adds it to the user's selectable list, making the tool highly customizable for different products.
- **Custom Persona Prop Drilling:** We implemented a robust data flow to pass `customPersonas` from the frontend all the way down to the ADK `ParallelAgent` so that dynamically created personas would actually use their generated descriptions as their system prompts during the browser automation step.
- **Real-time Audit Dashboard Enhancements:** We improved the dashboard with live agent feeds (`AuditProgress.tsx`), a consolidated report view, and real-time Firestore `onSnapshot` listeners that smoothly transition states from `pending` to `running` to `completed` or `error`.
- **Delete Audit Functionality:** Added the ability to delete stuck or erroneous audit runs directly from the dashboard using Firestore's `deleteDoc`.
- **Agent Handoff Documentation:** Crafted `README.md` and `docs/AGENT_HANDOFF.md` to ensure the next AI agent can seamlessly understand our nuanced architecture decisions without exhausting the context window.

---

## Roadbumps, Design Decisions & Key Learnings

### 1. The "Computer Use" Model API Restrictions (Why we abandoned ADK)
**The Problem:** We attempted to use the highly specialized `gemini-2.5-computer-use-preview-10-2025` model for our persona agents. First, routing through Google Cloud Vertex AI threw `400 INVALID_ARGUMENT: UI actions are not enabled for this project` because Google restricts this preview capability at the enterprise project level. Second, when we bypassed Vertex AI using AI Studio, the model threw an error complaining about Automatic Function Calling (AFC) compatibility because the ADK strictly blocks combining custom user tools with its `ComputerUseToolset`. Third, we hit a 25 RPM rate limit which crashed parallel agents.
**The Solution:** Rather than building a complex workaround to proxy function calling inside the ADK, we made the critical architectural decision to drop the ADK entirely for the browser agents. We wrote a native `asyncio` execution loop using the standard **`gemini-2.5-pro`** model, which natively supports both Playwright tools (manual `types.Tool` declarations) and custom function calling simultaneously, while avoiding rate limits.

### 2. Strategic Application of Gemini 3.1 Pro
**The Context:** You requested that we leverage the newest model, `gemini-3.1-pro-preview`. However, 3.1 has stricter rate limits on the free tier and struggles to run 3+ concurrent browser agents.
**The Solution:** We split the intelligence. The fast, parallel browser agents use `gemini-2.5-pro`, but we upgraded the `Consolidator` agent (`agents/consolidator.py`) to use **`gemini-3.1-pro-preview`**. Because this agent's sole job is to read massive amounts of text logs from the previous agents and synthesize a final executive JSON report, it is the perfect place to leverage 3.1's advanced reasoning and text synthesis capabilities!

### 3. Local Browser Instances Spawning vs. Production
**The Problem:** During testing, you noticed three Chromium instances suddenly popping up on the screen and taking over your desktop, and hanging when the API failed.
**The Solution:** We had to clarify the intended user experience. While it was helpful for local debugging to see the agents working live (`headless=False`), we updated the code to `headless=True`. This ensures the application correctly mirrors its future production state on Google Cloud Run, where the agents run invisibly in the background while the user watches the progress bar on the web app.

### 4. Context Window Exhaustion & Handoffs
**The Problem:** As the project grew in complexity and we debugged the ADK source code, the AI agent hit its context window limits. 
**The Solution:** We learned the absolute necessity of modular documentation. We paused to synthesize our architecture decisions, workarounds, and setup instructions into a `README.md` and a specific `docs/AGENT_HANDOFF.md` file. This allows you to successfully hand off context to new agent sessions without losing the hard-earned knowledge about the ADK constraints.

### 5. Firebase Admin Vertex AI Errors
**The Problem:** Encountered build errors regarding `@google/genai` vs `@google-cloud/vertexai` when building the Persona Generator.
**The Solution:** We ultimately utilized the `@google-cloud/vertexai` SDK authorized via our Firebase Admin service account credentials, successfully bypassing the Generative Language API restrictions for the project.

### 6. Messaging and Jargon
**The Problem:** The initial UI text was far too technical ("Automated UX testing powered by multiple AI personas running in parallel...").
**The Solution:** We refined the copy to be much more accessible ("Real Feedback from Almost Real Users"), realizing that the end-user doesn't need to know about the complex agent architecture behind the scenes to get value from the tool.

### 7. Resolving Firestore Update Errors
**The Problem:** During testing, you hit `404 No document to update: projects/...` errors on the backend because of race conditions where the Python script tried to `.update()` a Firestore document before the Next.js frontend had finished creating it.
**The Solution:** We learned to avoid `.update()` entirely in distributed async workflows, and switched all status modifications in `main.py` to use `.set({...}, merge=True)` which acts as an upsert, safely creating the document if the frontend was too slow.

### 8. Screenshot Strategy Pivot: Ephemeral State & React Overlays
**The Problem:** Originally, our agents captured zoomed-in, tightly cropped screenshots of elements they found issues with. This caused a loss of full-page context (just showing a red circle in a dark box). The `gemini-2.5-pro` model natively swallows Playwright states when used with ADK, making it tricky to manage precise image timing. We considered building a separate "Screenshot Agent" that would revisit URLs to take pictures after issues were found, but realized this breaks on "Ephemeral State" (e.g. drop-down menus or modals that only appear after specific click paths).
**The Solution:** We moved to a "React UI Overlay" strategy. Instead of injecting `<div>` elements into the DOM via Playwright to draw red circles (which sometimes broke layout or scrolling), the backend now simply uploads the raw, unadulterated screenshot at the exact moment the agent observes an issue. It then saves the `(x, y)` coordinates of the finding to Firestore. The React frontend is now responsible for absolutely positioning a red CSS circle over the image at those coordinates. This perfectly preserves transient UI states while maintaining full page context.

### 9. The Headless Browser vs. Real Device Fidelity Problem
**The Problem:** After deploying and running real audits against live sites, a fundamental tension emerged: the agents were generating findings that were technically accurate from the perspective of a headless Chromium browser, but flat-out wrong when a real user tried the same thing on their actual device. Specific examples caught in production:

- The agent flagged the main CTA ("Get Puck Buddy") as a dead link — but it correctly navigates to the App Store on a real device.
- The agent reported the interactive product demo as completely broken — but it works fine via real mobile touch.
- The agent flagged a pricing section finding, but the screenshot evidence attached to it was the hero section of the homepage, creating a misleading and confusing report.
- Red circle coordinate overlays were rendering in the wrong positions because the scaling math used viewport **width** as the denominator for both the X and Y axes, when CSS `top: Y%` is relative to the element's **height**, not width.

**The Crossroads:** This exposed a core product question — what is the right scope for this kind of AI audit tool? We had two paths:

*Option A: Try to fix the simulation* — use real mobile devices, improve link-following logic, add retry/fallback strategies to detect when a click "should" have worked on a real device. This is very hard, expensive, and likely still imperfect.

*Option B: Narrow the scope deliberately* — accept that headless browser simulation cannot reliably evaluate interactivity, and lean into what it **can** reliably do: read and evaluate copy, assess visual layout and hierarchy, measure readability, and model the user's emotional journey through content alone.

**The Decision:** We chose Option B. The agents are now explicitly instructed to skip any finding about broken links, dead CTAs, non-functional demos, App Store URLs, or touch-based interactions. The focus shifts entirely to what a simulated browser can evaluate with high accuracy and zero false positives: **content quality, visual hierarchy, messaging clarity, and user sentiment**. This produces a smaller but far more trustworthy report.

**The Solutions Shipped (initial pass):**
- Updated both the `browsing_instruction` and `reporting_instruction` in `persona_agent.py` with a "KNOWN SIMULATOR LIMITATIONS — DO NOT FLAG THESE" block, explicitly listing App Store links, interactive demos, video players, and navigation clicks as off-limits.
- Added a matching "DISCARD THESE — KNOWN SIMULATOR ARTIFACTS" block in the reporting agent so anything the browsing agent slips through gets filtered before hitting Firestore.
- Fixed the coordinate overlay math: introduced separate `scaleX = 100 / viewportWidth` and `scaleY = 100 / viewportHeight` so red circle overlays map correctly to both axes.
- Fixed a screenshot-to-finding misattribution bug where findings without an explicit `screenshotUrl` were being incorrectly shown under unrelated page screenshots (the "pricing finding shown under hero image" bug).
- Added then removed a **false positive dismiss button** — decided it was band-aid UX on top of a fixable source problem, and removing it kept the UI cleaner.

### 10. Escalating Agent Scope Control — From Soft Hints to Hard Rules
**The Problem:** Even after adding the "KNOWN SIMULATOR LIMITATIONS" block, agents continued flagging broken CTAs and non-functional navigation as their primary findings. Running an audit of a conference site (uxcon.com) produced a score of 15/100 and a consolidated report entirely about "broken core functionality" — none of which was real. The "soft" phrasing ("may require real device testing", "never flag these") wasn't strong enough.

**What We Learned:** LLMs respond much more reliably to explicit structural formatting (✗/✓ symbols, ALL-CAPS headers, explicit alternative actions) than to prose instructions. The model was pattern-matching against its training data about UX testing, which heavily emphasizes interactivity.

**The Solution:** Both `native_persona.py` and `persona_agent.py` were rewritten with:
- A framing shift: agents are now called "Content & UX Researchers", not "UX Researchers" — this signals scope before any rules are read
- A prominently boxed header: `YOUR ONLY JOB IS TO EVALUATE CONTENT AND PRESENTATION`
- ✗ ABSOLUTE RULES with specific prohibited phrases ("broken", "dead", "non-functional", "unusable")
- An explicit alternative action for when clicks fail: "simply scroll to read the next section of content"
- ✓ WHAT YOU ARE HERE TO EVALUATE — a positive list of in-scope observations
- The same rules applied at **both** stages (browsing + reporting) so nothing slips through

### 11. Custom Persona Agents Completing Without Any Findings
**The Problem:** Two custom personas ("Toronto's Team Manager Mom" and "The SaaS Sales Veteran") consistently completed their audits with 0 logged findings, despite the Live Agent Feed showing they successfully explored multiple pages. Terminal logs revealed they were calling `finish()` directly without ever calling `log_issue()` — even when their finish summaries mentioned real observations ("significant gaps in B2B strategy").

**Root Cause:** The `log_issue` tool description said "call this whenever you notice an issue" — too passive. The model treated it as optional and saved all observations for the finish summary instead.

**The Solution:**
- Made `log_issue` explicitly mandatory: "You MUST call this for EVERY specific observation. Aim for at least 3-5 logged findings before finishing."
- Changed the `finish` description to: "Call ONLY after you have logged all findings via `log_issue`. The summary should be 2-3 sentences max — all detail must be in the logged findings."
- Added the same guidance to the system instruction's WORKFLOW section with explicit minimum-findings language.

### 12. The `isMobile` Detection Bug for Custom Personas
**The Problem:** Red circle coordinate overlays (before they were removed) and the screenshot image container width were being determined by checking if the agent's display name contained the word "mobile". This correctly identified the built-in "Mobile User" persona but failed for any custom persona with a mobile `deviceType` whose name didn't include the word "mobile" (e.g., "Toronto's Team Manager Mom").

**Root Cause:** At the screenshot grouping render stage, `isMobile` was computed as:
```tsx
const isMobile = imgUrl.includes('mobile') || agents.some(a => a.name.toLowerCase().includes('mobile'));
```
This is a heuristic that breaks on custom naming. The correct `isMobile` was already computed earlier in the `personaReports.forEach()` loop using the actual `deviceType` from `audit.customPersonas`.

**The Solution:** Added `isMobile: boolean` to the `PageGroup` type. Set it during the map-building phase using the correct persona config, and destructured it from the group at render time. The name-sniffing heuristic was eliminated entirely.

### 13. Coordinate Overlays — Removal After Repeated Failures
**The Problem:** Despite fixing the y-axis scaling math (separate `scaleX`/`scaleY`), fixing the `isMobile` detection bug for custom personas, and instructing agents to scroll to the relevant section before logging, the red circles continued to appear in incorrect positions across multiple test audits.

**Root Cause (final determination):** The Gemini model internally scales/resamples images before processing them. When the model reports `x=200, y=400`, those coordinates correspond to the image *as the model perceives it* — not the original Playwright viewport pixel space (1280×800 or 390×844). There is no reliable way to know what internal resolution Gemini uses without access to model internals.

**The Decision:** Removed coordinate overlays entirely from the frontend. The `getFindingCoordinates` helper was deleted. Coordinates are still stored in Firestore (no backend changes) but are not rendered. The screenshot itself provides sufficient visual context for the finding.

### 14. Authentication Flow Bug — Agent Returning to Public Site After Login
**The Problem:** When users provided login credentials, the agent would successfully log in via `_login()` and land on the authenticated dashboard — then immediately navigate away from it. The system instruction said "Navigate to {target_url}" as its first workflow step, which sent the agent straight back to the public marketing homepage.

**Root Cause:** `initial_url` in `BrowserDriver` was always set to `target_url` (the public URL). After login redirected to the dashboard, the system prompt's step 1 undid this by explicitly navigating back to the marketing site.

**The Solution:**
```python
initial_url = auth.get('loginUrl', target_url) if auth else target_url
driver = BrowserDriver(..., initial_url=initial_url, ...)
```
Plus a branched system instruction:
```python
auth_context = """
You have been pre-logged in. Do NOT navigate back to the public marketing page.
Explore the authenticated experience only.
""" if auth else f"Start by navigating to {target_url}."
```

### 15. Consolidator Hallucinating Issues That Never Appeared In Live Agent Feeds
**The Problem:** A real `buddysports.app` audit revealed a trust-breaking mismatch: the Mobile User agent logged 0 findings and the First-Time Visitor logged only 1, yet the final consolidated report still surfaced multiple extra issues like a "broken demo video" and "empty phone frames." Those claims never appeared in the Live Agent Feeds and were likely caused by headless rendering artifacts, not actual UX problems.

**Root Cause:** We discovered the final consolidator was not actually reading the persona `findings` array from Firestore. `main.py` was only passing each persona's free-form `summary` into `native_consolidator.py`. That let unsupported claims from the finish summary leak into the executive report even when they were never logged as findings with screenshot evidence.

**The Solution:**
- Added hard runtime enforcement in `native_persona.py`: `finish` is blocked until the agent logs at least 3 accepted findings.
- Added simulator-artifact filtering in both `log_issue` and `finish` paths so phrases like broken demos, dead links, rendering failures, and empty phone frames are rejected before saving.
- Tagged accepted findings with `evidenceBacked: true` in Firestore.
- Changed `main.py` + `native_consolidator.py` so consolidation is driven by evidence-backed `findings`, with persona `summary` used only as optional secondary context.

**The New Rule:** If an issue was not accepted into the persona's `findings` array, it should not appear in the final consolidated report.

### 16. Shallow Audits Passing With Too Little Variety
**The Problem:** After the first hardening pass, personas could no longer finish with 0 findings, but they could still technically complete an audit with a shallow set of observations: e.g. several near-duplicate negative findings from one page, or no positive findings at all.

**Root Cause:** The runner was enforcing minimum count, but not minimum coverage. We had not taught the runtime to distinguish between "enough findings" and "enough variety of evidence."

**The Solution:**
- Extended `log_issue` to require structured `sentiment` (`positive` or `negative`) and `category`
- Added a coverage gate in `native_persona.py` so `finish` is blocked unless the persona has:
  - at least 3 accepted findings
  - at least 1 positive finding
  - at least 2 negative findings
  - findings from at least 2 distinct pages/sections
  - findings covering at least 2 categories
- Added duplicate-finding rejection so the model must log genuinely new observations instead of paraphrasing the same issue
- Persisted a `coverage` object and `findingsCount` in Firestore for easier debugging

**The Goal:** The audit loop should now behave more like a real researcher: collect a balanced set of observations across multiple parts of the site before being allowed to conclude.

### 17. Screenshot Evidence Showing The Wrong Part Of The Page
**The Problem:** In a later `buddysports.app` test, the advice quality improved a lot, but the screenshot evidence still looked wrong: multiple findings pointed to the same top-of-page image even when the written feedback referred to pricing or lower sections.

**Root Cause:** The native runner was giving Gemini a viewport screenshot but also passing `page.inner_text('body')`, which exposed the entire page's text. The model could therefore make a valid observation about a lower section while the screenshot still showed the hero area.

**The Solution:**
- Replaced full-page `body.innerText` with a viewport-only visible-text extractor in `native_persona.py`
- Added `scroll_y` and `viewport_height` to the state payload
- Updated the system instruction so the model must only comment on content visible in the current screenshot + visible viewport text

**The Goal:** Screenshot evidence and written advice should now stay grounded to the same visible section of the page.

**Validation Result:** A rerun against `buddysports.app` immediately showed the strongest screenshot-to-advice alignment we had seen so far. The fix did not make the audit "perfect," but it materially reduced the earlier pattern where multiple findings reused the same hero-area screenshot for lower-page observations.

### 18. Authenticated Audit Snapping Back To The Login Screen
**The Problem:** During a LegalZoom authenticated-user test, the findings referenced post-login pages like `/my/dashboard` and `/my/documents`, but the screenshots in the UI still showed the sign-in page. The result was confusing and undermined trust in the authenticated flow.

**Root Cause:** The native runner was doing two bad things:
- After `_login()` it still navigated to `initial_url`, and in auth mode `initial_url` was the login URL itself. A successful login could therefore be followed by an immediate bounce back to sign-in.
- The agent could log a finding with a claimed protected `page_url` even when the actual browser URL was still an auth page.

**The Solution:**
- Added explicit auth state tracking in `native_persona.py` (`auth_attempted`, `auth_succeeded`, `auth_error`)
- If login succeeds, the driver now stays on the authenticated page instead of revisiting `loginUrl`
- Added `on_auth_page` to the model state payload
- Blocked `log_issue` when the claimed `page_url` does not match the real browser state and the browser is still on auth
- Persisted `authStatus` and `authError` to Firestore for easier debugging

**The Goal:** Authenticated audits should now either stay grounded in the real signed-in app state or clearly expose that login failed, rather than mixing dashboard findings with login-page screenshots.

**Validation Result:** A later authenticated test against a non-MFA ESPN flow successfully entered the signed-in experience, which suggests the current auth path is now workable for standard username/password sites that do not require a second factor.

**Current Limitation:** MFA / OTP / verification-code checkpoints are still out of scope for the current implementation. Sites like LegalZoom that demand a one-time code should be treated as auth-blocked rather than expecting the agent to complete the challenge.

### 19. Turning The Audit Into A Boardroom Presentation
**The Problem:** After shipping the consolidated report and founder audio recap, the next step was obvious: make the result feel like a presentation a UX researcher would confidently show to executives. Our first pass proved the mechanics worked, but it still looked too much like a dashboard: raw URLs as titles, long report fragments, visible narration text, and placeholder graphics that felt redundant rather than premium.

**The Crossroads:** We had two ways to improve polish:

*Option A: Keep deterministic slide-building* — fast and predictable, but prone to stiff copy, truncated bullets, and weak visual storytelling.

*Option B: Treat the deck as its own authored artifact* — use a stronger reasoning model to rewrite the report into presentation-first slides, and selectively generate visuals when a real screenshot is not the best lead asset.

**The Decision:** We chose Option B, but only for the **post-audit presentation layer**. The core browsing audit remains on the stable AI Studio runtime, while the deck generator now targets explicit Vertex AI preview models. This preserves reliability where we need it most and spends the higher-capability models only on the final storytelling surface.

**The Solution:**
- Added a new `mediaArtifacts.presentation` artifact with slide JSON, per-slide audio, and visual metadata
- Kept evidence slides grounded in real screenshots captured during the audit
- Introduced a Vertex-backed presentation authoring path using `gemini-3.1-pro-preview` to rewrite the report into concise slide titles, 1-3 executive bullets, and cleaner narration
- Added selective visual generation using `gemini-3-pro-image-preview`, with real screenshots preferred whenever a strong grounded image exists
- Reworked the frontend to make the visual the hero, hide narration text, and present the deck as a guided experience rather than a text-heavy report clone

**What We Verified In Practice:** The current project can access the preview image model path, but the Vertex text preview models returned `404 NOT_FOUND` on this project during live validation. Instead of abandoning the presentation upgrade, we shipped a resilient fallback chain: try the preview text models first, and if access is unavailable, fall back to `gemini-2.5-pro` for the slide-authoring pass while still using the preview image model when available.

**The Product Goal:** Long-term, the output surface becomes:
- `Presentation` — primary guided experience
- `Report` — detailed reference

That transition is now complete: the standalone `Listen to Recap` button has been removed, and the presentation is the single guided audio-first artifact.

### 20. Media-Heavy Sections Creating Fake "Empty Demo" Findings
**The Problem:** When we reviewed `buddysports.app`, some audit screenshots showed media containers before their video or animation visibly rendered. That exposed a subtler failure mode than the earlier "broken demo" bug: even if the agent avoided saying the player was "broken," it could still log softer but still-wrong findings like "no video is visible" or "the demo area is empty."

**Root Cause:** Our first simulator-artifact pass mostly filtered specific phrases. It did not inspect the actual viewport structure, so the runtime could not tell when the current section was full of videos, iframes, and animation containers that are especially likely to appear blank in headless Chromium.

**The Solution:**
- Added a viewport media scan in `native_persona.py` so each step now tracks visible `video`, `iframe`, `canvas`, `svg`, and media-like containers
- Fed that media scan back into the prompt so the model is reminded to treat blank media as a possible simulator artifact
- Added an extra rejection layer in `native_persona.py` + `persona_agent.py` that blocks findings phrased like missing video, blank demo, empty frame, or no visible hotspots when the current viewport is clearly media-heavy
- Tightened the intended evaluation behavior: in media-heavy sections, the audit should focus on surrounding headlines, captions, labels, and explanatory copy rather than punishing the site for a headless render gap

**The Goal:** If a rich media section does not fully render in the simulator, the audit should still extract useful UX insight from the surrounding context without inventing a fake product problem.

### 21. Making Custom Personas Editable Instead of Disposable
**The Problem:** Custom personas were useful once generated, but they were awkward to refine. If the generated name, goals, or description were close but not quite right, the only available action was delete-and-recreate.

**Root Cause:** The persona workflow only supported two states: create a new persona document in Firestore, or delete it. There was no UI path to update the saved persona fields in place.

**The Solution:**
- Added an edit action to custom persona cards in `PersonaSelector.tsx`
- Added `PersonaEditorDialog.tsx` so saved personas can be updated directly
- Wired `src/app/(protected)/audit/new/page.tsx` to save edited persona fields back into `users/{uid}/customPersonas/{personaId}`

**The Goal:** Custom personas should behave like reusable test assets that can be refined over time instead of thrown away and rebuilt.

### 22. Preventing a False "100% Complete" State Before Presentation Finished
**The Problem:** The audit page could briefly look fully complete after the consolidated report appeared, then switch back into a loading state once presentation generation became visible. That made the final step feel glitchy even though the backend was still working.

**Root Cause:** The report completed before the presentation artifact was initialized in Firestore. For a short window, the frontend saw a finished report but no active presentation status, so it assumed the whole workflow was done.

**The Solution:**
- Updated `audit_recap.py` to mark `mediaArtifacts.presentation.status = "generating"` immediately when the presentation phase starts
- Updated the audit page UI to keep the loading bar and "Building presentation" state alive through that handoff

**The Goal:** The user should see one continuous progress story from audit start through final presentation readiness.

### 23. Replacing AI-Looking Slide Art With Real Audit Screenshots
**The Problem:** Some presentation slides looked too obviously AI-generated, and at least one product-focused slide ended up without a strong image even though the audit already had relevant screenshots.

**Root Cause:** We had told the presentation authoring model to use real screenshots only for explicit evidence slides. That unintentionally pushed recommendation and summary slides toward generated filler art.

**The Solution:**
- Rewrote the presentation prompt so product, UX, and recommendation slides prefer real audit screenshots
- Added a runtime screenshot-matching pass in `audit_recap.py` that attaches supporting screenshots to slides after authoring
- Kept generated imagery only as a fallback when no grounded screenshot is a strong fit

**The Goal:** The presentation should feel boardroom-polished without losing trust. When a slide talks about the real product, it should show the real product whenever possible.

### 24. Splitting Functional QA Out From Persona UX Feedback
**The Problem:** After we hardened the UX personas against simulator-artifact bug reports, we lost a clean place for real same-site functional issues such as broken links, dead-end navigation, failed form submissions, and visible error states. At the same time, we did not want every persona drifting back into low-signal bug/error reporting.

**The Decision:** We kept the content-only rule for the regular personas and introduced a new built-in standard persona: **`QA Agent`**.

**What Changed:**
- Added `p_qa` as a built-in persona in the frontend selector and audit report UI
- Made new audits include `QA Agent` in the default built-in selection set
- Gave `QA Agent` a dedicated functional-testing prompt in both `native_persona.py` and the fallback `persona_agent.py`
- Added functional finding categories: `broken_link`, `broken_interaction`, `form_error`, `navigation_issue`, `unexpected_error`, and `working_flow`
- Kept the original UX personas on the stricter content/presentation-only path so they no longer own bug/error reporting

**Why It Matters:** This keeps the audit output cleaner. The UX personas can stay focused on taste, copy, clarity, and emotional journey, while the `QA Agent` becomes the single built-in owner for broken interactions and obvious site errors.

### 25. Unblocking Stalled Audits by Relaxing Completion Rules
**The Problem:** Audits were frequently getting stalled or completing with zero findings, despite agents successfully exploring the site. The models would continually receive rejection errors like "Make this quote more specific" or "Log at least 2 negative findings" and would burn through their maximum tool calls (`max_steps = 20`) without successfully saving findings to Firestore.

**Root Cause:** The runtime execution loop in `native_persona.py` and the `report_finding()` helper in `persona_agent.py` had very strict validation requirements. An agent could not finish until it hit exact counts (minimum findings, minimum positives, minimum negatives, minimum distinct pages) and its quotes had to pass rigid style filters (first-person, non-generic, non-artifact). This created too much friction for the LLM to successfully log its thoughts.

**The Solution:**
- Stripped the strict coverage requirements (`MIN_FINDINGS_BEFORE_FINISH` reduced from 3 to 1, other minimums set to 0).
- Removed the strict quote validation filters (`get_quote_style_reason`, `get_persona_relevance_reason`, `get_simulator_artifact_reason`) from the logging path so that quotes and screenshots are accepted best-effort.
- Re-architected screenshot capture to avoid forcing a brand-new screenshot upload synchronously on every `log_issue`, relying instead on the existing page-level cache when possible to reduce latency.

**The Goal:** Prioritize getting raw findings and screenshots into the database so that downstream processes (screenshot reviewer, consolidator) can clean up the data, rather than crashing the pipeline with overly strict upfront validation.

### 26. Fixing Firestore QUIC Protocol Errors in the Browser
**The Problem:** During long-running audits, the browser console would throw `ERR_QUIC_PROTOCOL_ERROR.QUIC_TOO_MANY_RTOS` for the Firestore `Listen/channel` requests, leading to the UI losing live updates from the agents.

**Root Cause:** The default Firestore WebChannel transport, combined with `experimentalAutoDetectLongPolling: true`, was unstable over longer durations on certain network environments (like VPNs, proxies, or flaky Wi-Fi) which interfered with the HTTP/3 QUIC connection.

**The Solution:**
- Changed the Firestore client configuration in `src/lib/firebase.ts` to use `experimentalForceLongPolling: true`.

**The Goal:** While long polling is slightly less performant than streaming WebChannels, it is significantly more reliable for long-lived, continuous real-time listeners, preventing the UI from abruptly disconnecting from the backend audit progress.

### 27. Fixing Persona Card UI Overlap
**The Problem:** The custom persona cards in the audit creation screen had an overlapping UI bug. The edit and delete action buttons, which were absolutely positioned in the top-right, were overlapping with the "mobile/desktop" device pill badge.

**The Solution:** 
- Refactored `PersonaSelector.tsx` to use shadcn/ui's `CardAction` instead of absolute positioning.
- Placed the action buttons and the device badge inside a structured flex container within the `CardHeader`, allowing the grid layout system to position them cleanly without overlapping text or icons.

### 28. Adding Timeout Guardrails to Prevent Silent Agent Stalls
**The Problem:** We discovered that agents were occasionally freezing entirely after logging a few findings. Terminal logs showed the process hanging on `Sending state to model...` or `Uploaded screenshot...` without ever throwing an error.

**Root Cause:** The `asyncio` execution loop in `native_persona.py` relied on network-heavy calls (like `chat.send_message` and Firebase Storage uploads) that lacked explicit timeouts. If the Google GenAI SDK or Firebase hit a dropped connection or rate-limit stall, the promise would wait indefinitely, permanently hanging that specific agent.

**The Solution:**
- Wrapped all model interactions (`chat.send_message`) in an `asyncio.wait_for(..., timeout=180.0)` guardrail.
- Added explicit `timeout=20` kwargs to the Firebase Storage `blob.upload_from_string` thread execution.
- Added explicit `timeout=15000` to the `page.screenshot()` calls to prevent Chromium from locking up the Python event loop.

**The Goal:** If a network drop or API stall happens, the agent will now catch a `TimeoutError` and gracefully abort rather than silently freezing the backend and locking up the audit indefinitely.

### 29. Upgrading to Gemini 2.5 Flash for Browser Agents
**The Problem:** After adding the `120s` timeout guardrails, the agents cleanly crashed after precisely 120 seconds instead of hanging. This exposed the real root cause of the network stalls: rate limits. Running 3 parallel agents on a "Pro" model instantly exhausted the 2 RPM (Requests Per Minute) free tier quota on Google AI Studio, causing the `tenacity` library to infinitely backoff until our timeout killed the run. We tried upgrading to `gemini-3.1-pro-preview-customtools` but it hit the exact same 2 RPM cap. We also tried `gemini-3-flash-preview`, but those preview models currently throw 400 errors when fed screenshot images.

**The Solution:**
- Switched the core execution loop in `native_persona.py` to `gemini-2.5-flash`.
- Flash 2.5 operates on a 15 RPM free tier limit, easily accommodating parallel multi-agent runs without stalling, and fully supports multimodal image inputs without erroring out.

**The Goal:** Ensure the browser loop uses a model with a high enough rate limit to avoid immediate 429 deadlocks when multiple personas are selected, while maintaining stable image processing. The final `native_consolidator.py` remains on `gemini-2.5-pro` since it only executes once at the very end of the run.

### 30. Fixing Presentation Generation Dict Unpacking Bug
**The Problem:** After audits successfully completed, the final step would occasionally crash with `TypeError: agents.audit_recap._set_presentation_state() got multiple values for keyword argument 'status'`.
**Root Cause:** In `audit_recap.py`, `_set_presentation_state(..., **base_state, status="error")` was called in the exception handler. Since `base_state` already contained a `"status": "generating"` key, passing it alongside an explicit `status="error"` keyword argument caused a Python dictionary unpacking collision.
**The Solution:** Properly merged the dictionaries before passing them to the function using `error_state = {**base_state, "status": "error", "error": str(exc)}`.

### 31. Proposed Architectural Shift: Separating Crawling from Reviewing
**The Problem:** Persona agents still experience "burnout" during runtime tasks. Even with flash models, navigating, reading the DOM, and reasoning about UX in a continuous loop can lead to rate limits, context exhaustion, or endless tool-call loops.
**The Proposed Solution:** Split the workload into two distinct phases:
1. **The Crawler/Screenshot Agent:** A single agent or script dedicated purely to browsing the site, handling auth, and capturing a comprehensive set of screenshots. To manage context windows, this will initially be capped at ~5 pages and max 3 images per page.
2. **The Persona Reviewers:** Instead of browsing, the persona agents simply receive the batch of screenshots and provide multimodal UX feedback in a single pass. The "emotional journey" of UX will be simulated via prompt engineering.
**Scope Adjustments for this Pivot:**
- **Ephemeral States:** To solve for missing drop-downs or modals, the crawler will need explicit instructions (or simple JS scripts) to interact with and expand UI elements before snapping pictures.
- **Functional QA Removed:** Since personas won't be clicking links, the built-in `QA Agent` (which looks for 404s and broken flows) will be eliminated for now to simplify the architecture.
**Status:** Approved for future roadmap.

---

### 33. The Three Major Architectural Realizations
**The Journey:** Over the course of the hackathon, our architecture and product vision underwent three major evolutions based on real-world constraints and testing:

1. **The SDK Pivot (ADK to Native AI SDK):** We started with the Google Agentic Developer Kit (ADK) using the `ComputerUseToolset`, but hit severe rate limits (25 RPM) and rigid tool restrictions that blocked custom function calling. We dropped ADK for the browser agents, writing a native `asyncio` execution loop with `gemini-2.5-pro` and raw Playwright, immediately unblocking parallel persona runs.
2. **The Execution Pivot (Sequential Browsing to Crawl-and-Review):** Even with native Playwright, persona agents experienced "burnout" when trying to navigate, read the DOM, and reason about UX simultaneously. We shifted to a Map-Reduce model: a dedicated Crawler agent captures the site structure and screenshots first, then pure multimodal Reviewer agents process those screenshots in batch using `gemini-3.1-pro-preview`.
3. **The Output Pivot (Static Report to Interactive Presentation):** The final output started as a static markdown-like "Consolidated Report." Realizing the need for a more polished, executive-ready artifact, we upgraded the output layer to build an interactive, audio-narrated Founder Presentation, leveraging `gemini-3.1-pro-preview` for slide authoring and `gemini-3-pro-image-preview` for fallback visuals, while tightly linking slides to real screenshot evidence.

### 32. Implementing the Crawler/Reviewer Architecture & Bug Fixes
**The Shift:** We implemented the map-reduce architecture. The `BrowserDriver` was extracted into its own file, a dedicated crawler agent handles site navigation and takes screenshots, and `native_persona.py` was refactored into a pure multimodal reviewer using `gemini-3.1-pro-preview` that processes screenshots in batch. The legacy `persona_agent.py` was deleted and the `QA Agent` was completely removed.
**Subsequent Fixes:**
- **Screenshot Reviewer Bug:** The screenshot review pass previously set `screenshotUrl: None` for rejected or unclear screenshots. This caused rendering crashes in the frontend and the `audit_recap.py` presentation generator, which expected either a valid URL or gracefully handled empty strings/missing keys, but choked on explicit `None` mutation within the existing findings list. Fixed by preserving the `screenshotUrl` but properly mapping the approved/rejected state.
- **Presentation Visual Prompt Fix:** The `_make_slide` helper was missing the `visualPrompt` key, causing the Vertex AI fallback image generator to fail silently or crash when it tried to generate placeholder art for slides lacking approved screenshot evidence. Fixed by explicitly adding `visual_prompt` support to the slide authoring function.
- **Presentation Screenshot Linking & Page Labels:** Sometimes screenshots weren't matching up properly with presentation slides or the frontend was showing raw URLs instead of human-friendly page names. Fixed by updating the Crawler to generate `label` attributes for captured pages. Then, updated the pure Reviewer tools to require logging a `page_label` along with findings. Finally, updated the Presentation builder (`audit_recap.py`) and the Next.js Frontend to correctly parse and display these clean page labels alongside the screenshot evidence.

---

### 34. Dual Viewport Capture (Desktop & Mobile) and Frontend Stabilization (Mar 16, 2026)
**The Problem:** The screenshot reviewer agent was originally hardcoded to desktop views, making the mobile persona's findings less accurate. Furthermore, the frontend was "flickering" due to unstable screenshot sorting and React Hook errors.
**The Solution:** 
- Updated `crawler.py` to launch two parallel `BrowserDriver` instances (1280x800 desktop and 390x844 mobile).
- `native_persona.py` now receives device-specific screenshots based on the persona's `device_type`.
- In the frontend (`AuditPage`), the screenshot grouping logic was wrapped in `useMemo` to stabilize rendering.
- Fixed a "Rules of Hooks" violation by moving `useMemo` to the top level of the component, before early returns.
- Implemented a stable multi-level sort for screenshots: Page URL (lexicographical) -> Device Type (Desktop first) -> Capture Timestamp (oldest to newest).
**Result:** The audit now provides high-fidelity mobile and desktop views side-by-side, with a stable and performant UI.

### 35. Eliminating Synthetic Slide Art & Adding Audio Auto-play (Mar 16, 2026)
**The Problem:** Despite having hundreds of real screenshots from the audit, the presentation agent occasionally generated synthetic "AI-looking" placeholder art for non-evidence slides (like summaries or recommendations). Additionally, the user experience was disjointed because users had to manually hit "Play" on every single slide's audio narration.

**The Solution:**
- **Strict Screenshot Instruction:** Updated the Gemini 3.1 Pro presentation authoring prompt to demand real audit screenshots for *every* slide.
- **Global Fallback Logic:** Updated `audit_recap.py` to search through *all* raw crawler screenshots if no evidence-backed finding was a perfect keyword match. This ensures that even high-level summary slides show real site context rather than generated visuals.
- **Audio auto-play:** Implemented a `useEffect` hook in the `PresentationTab` that monitors the `currentSlideIndex`. When the slide changes (via Next/Previous buttons or slide numbers), the audio narration automatically loads and plays.
- **Resilient Playback:** Included a `.load()` call and a silent error catch for browser auto-play restrictions, ensuring a seamless experience for users who have already interacted with the page.

**The Result:** The presentation now feels like a polished, automated "movie" of the audit findings, grounded entirely in real visual evidence from the site.

### 36. Live Feed UI Cleanup (Mar 16, 2026)
**The Problem:** Each agent card in the Live Agent Feed included a collapsible "Audit Snapshot" that displayed internal metrics like findings count, authentication status, and coverage details. While useful for debugging, this added visual clutter and distracted from the live stream of actual findings.

**The Solution:**
- **Removed Audit Snapshot Display:** Edited `src/app/(protected)/audit/[auditId]/page.tsx` to remove the `<details>` element containing the "Audit Snapshot" from the live feed cards.
- **Simplified Content:** The agent cards now focus exclusively on the identity of the persona and the live-streamed findings, creating a cleaner and more direct real-time feedback experience.

**The Result:** A more focused and boardroom-ready live feed that highlights the agent's insights without exposing the internal tracking metrics.

### 37. Standardized "UX Audit of [Company]" Title Format (Mar 16, 2026)
**The Problem:** The presentation and consolidated report headers were inconsistent and sometimes used generic titles like "Founder Presentation" or "Audit Report" instead of reflecting the specific brand being audited.

**The Solution:**
- **Backend Enforcment:** Updated the `audit_recap.py` prompt for `gemini-3.1-pro-preview` with a strict rule: "The overall presentation `title` MUST follow the format: 'UX Audit of <company or product name>'".
- **Dynamic Helper:** Implemented `getFriendlySiteName` in both Python and TypeScript to reliably extract a capitalized company name from any URL (e.g., "Example" from `example-site.com`).
- **Global Header Update:** Updated the Next.js `AuditPage` and the report copy-to-clipboard markdown to use the new brand-aware title format.
- **Resilient Fallbacks:** Ensured that while the presentation is still "generating," the UI displays the correct branded title immediately instead of a generic "Presentation" placeholder.

**The Result:** A more professional, boardroom-ready artifact that feels tailored to the specific product or company being audited from the moment the report page loads.

---

## File Map (Relevant to our session)

| File | What It Does |
|---|---|
| `agent-backend/main.py` | FastAPI entry point, `/api/run_audit` endpoint, startup orphan cleanup |
| `agent-backend/agents/native_persona.py` | Pure multimodal reviewer (no browser execution loop). Uses `gemini-3.1-pro-preview` to evaluate a batch of crawled screenshots and log findings. |
| `agent-backend/agents/audit_recap.py` | Presentation layer: generates executive slide deck, slide audio (TTS), and attaches grounded screenshots |
| `agent-backend/agents/native_consolidator.py` | Consolidator: reads evidence-backed findings from Firestore, uses persona summaries only as secondary context, writes final JSON report |
| `src/app/api/audit/start/route.ts` | Creates Firestore doc, triggers backend, passes custom personas and auth |
| `src/app/api/personas/generate/route.ts` | AI persona generation |
| `src/app/(protected)/audit/new/page.tsx` | New audit page — persona selection, custom persona builder, auth fields |
| `src/app/(protected)/audit/[auditId]/page.tsx` | Live audit report — Presentation (with auto-play audio) / Consolidated Report / Screenshots / Live Agent Feeds tabs. No internal status metrics in live feeds. |
| `src/components/audit/PersonaBuilder.tsx` | AI persona generator UI |
| `docs/AGENT_HANDOFF.md` | Full architecture reference, constraints, schema, and known issues |
| `docs/ADK_MIGRATION_TRADEOFFS.md` | Why we moved from ADK to native SDK, and why coordinate overlays were removed |
| `hackathon_report.md` | Running log of every major decision, pivot, bug, and fix made during the build |
