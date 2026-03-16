# Architecture Reference

**AuditMySite** is a web app that runs multiple AI personas against a user-provided URL, streams findings live to the UI, and synthesises a consolidated UX audit report with a narrated slide presentation. Built for the Gemini Live Agent Challenge (Track 3: UI Navigator).

**Core design decision:** All agents are **content and presentation auditors only**. The functional `QA Agent` was removed in March 2026 because static screenshot review cannot reliably test interactivity — headless browsers produce too many false positives for CTA clicks, embedded video, and external deep links.

---

## Architecture

### Frontend — Next.js 15 App Router (`/src`)
- **UI:** React + Tailwind CSS + shadcn/ui (neutral monochrome design system — no hardcoded color accents)
- **Auth:** Firebase Client SDK → `/api/session` route sets an HTTP-only `__session` cookie via Firebase Admin SDK. Middleware at `src/proxy.ts` guards the `/(protected)` route group.
- **Live updates:** `onSnapshot` listeners on `audits/{auditId}` and `audits/{auditId}/agentReports/{personaId}` drive all real-time UI updates.
- **Audit trigger:** `src/app/api/audit/start/route.ts` creates the Firestore doc then POSTs to `http://localhost:8080/api/run_audit` with `X-Api-Secret`.
- **Custom personas:** Saved per-user in Firestore at `users/{uid}/customPersonas/{personaId}`. Loaded via `onSnapshot` in `src/app/(protected)/audit/new/page.tsx`, and can now be edited in-place from the new-audit screen.

### Backend — Python FastAPI + Google GenAI SDK (`/agent-backend`)
- **Entry point:** `agent-backend/main.py` — initialises Firebase Admin, mounts the FastAPI app, exposes `POST /api/run_audit`.
- **Agent Orchestration (Map-Reduce Architecture):**
  ```
  asyncio.gather (run_audit_background in main.py)
  ├── run_crawler_agent (crawler.py) [Dual BrowserDriver: Desktop & Mobile]
  │   └── Captures ~4 pages, scrolls N frames, stitches into 1 composite PNG per page/device
  │
  ├── run_screenshot_reviewer [gemini-2.5-flash]
  │   └── Vision QA gate — rejects blank/messy screenshots before personas see them
  │
  ├── run_persona_agent (persona 1) [gemini-3.1-pro-preview]
  │   └── Receives 1 composite image per page; must log ≥2 findings per image
  │
  ├── run_persona_agent (persona 2) [gemini-3.1-pro-preview]
  │   └── ...
  │
  └── run_native_consolidator [gemini-2.5-pro]
  ```
- **Crawler:** `agent-backend/agents/crawler.py` handles the navigation phase. It spins up two parallel `BrowserDriver` instances (1280x800 desktop and 390x844 mobile) to ensure every page is captured for both device types.
- **Reviewers:** `agent-backend/agents/native_persona.py` is now a pure multimodal reviewer. It no longer controls a browser. It receives the crawled screenshots and selects the set (desktop or mobile) that matches the persona's `deviceType`.
- **Screenshot QA:** `agent-backend/agents/screenshot_reviewer.py` reviews every screenshot *before* persona execution. It rejects "messy" artifacts (blank images, empty frames, placeholders), stores results in `mediaArtifacts.screenshotReview`, and filters the set of URLs persona agents will receive.

---

## Critical Constraints & Workarounds

### 1. The Computer-Use Model API Restrictions (Why we abandoned ADK for browsing)
The specialized `gemini-2.5-computer-use-preview-10-2025` model **cannot** be mixed with custom callable tools (like `log_issue`). The ADK threw `400 INVALID_ARGUMENT` when combining them. Furthermore, the preview model has a 25 RPM rate limit that crashes parallel agents.

**Solution:** We use the standard `gemini-2.5-pro` model with a custom native `asyncio` execution loop in `native_persona.py`. It manually defines `click`, `type_text`, `scroll`, `navigate`, `log_issue`, and `finish` as `types.Tool(function_declarations=[...])` and handles the while-loop state machine itself.

### 2. Core Browsing Uses Google AI Studio; Presentation Uses Vertex AI Preview Models
Vertex AI throws `400 INVALID_ARGUMENT: UI actions are not enabled for this project` for computer-use style browsing workflows. The backend `.env` must therefore keep the core agent runtime on the Google AI Studio path:
```
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GEMINI_API_KEY=...   # From aistudio.google.com
```

However, the **post-audit presentation layer** is now intentionally split onto Vertex AI preview models because it does not use browser tools. It relies on ADC + project/location configuration:
```
GOOGLE_CLOUD_PROJECT=auditmysite-61bd1
GOOGLE_CLOUD_LOCATION=us-central1
```

Current model split:
- `gemini-3.1-pro-preview` via AI Studio — persona agents (pure screenshot review phase)
- `gemini-2.5-flash-preview-tts` via AI Studio — narration audio
- `gemini-3.1-pro-preview` via Vertex AI — slide-authoring / boardroom-style presentation rewrite
- `gemini-3-pro-image-preview` via Vertex AI — fallback visuals only for slides that do not have strong screenshot evidence

Important runtime note: on this specific project, the image preview path is accessible, but the Vertex text preview models may return `404 NOT_FOUND` if the project has not been granted access yet. `audit_recap.py` now tries the preview text models first and automatically falls back to `gemini-2.5-pro` for slide authoring instead of failing the presentation.

### 3. Do NOT use `--reload` when running the backend
`uvicorn --reload` or `fastapi dev` watches the directory. **Any file save kills the server process and all in-flight audits.** Production start command:
```bash
cd agent-backend
source venv/bin/activate
PYTHONUNBUFFERED=1 nohup uvicorn main:app --port 8080 > /tmp/audit_backend.log 2>&1 &
```

### 4. Model Names — Do Not Invent Them
Confirmed working models as of March 2026:
- `gemini-2.5-pro` — persona agents and consolidator
- `gemini-2.5-pro` via AI Studio — consolidator
- `gemini-2.5-computer-use-preview-10-2025` — kept in `persona_agent.py` (ADK path, not primary)
- `gemini-2.5-flash-preview-tts` — per-slide narration audio for the presentation
- `gemini-3.1-pro-preview` — post-audit presentation authoring on Vertex AI
- `gemini-3-pro-image-preview` — generated slide visuals on Vertex AI

Do NOT move the **browser agents** onto `gemini-3.*` or Vertex AI. The preview stack is currently reserved for the post-audit presentation layer only.

### 5. Custom Personas Always Use `native_persona.py`
Both built-in and custom personas route through `run_persona_agent` in `native_persona.py`. The two-stage ADK pipeline (`persona_agent.py`) exists as a fallback but is not the active code path.

---

## Agent Scope Philosophy

This is the most important design decision made post-launch. Headless browser simulation has fundamental limitations that produce systematic false positives:

**What headless browsers cannot reliably test:**
- Whether a button or link actually navigates somewhere (JS handlers, App Store deep links, external redirects all appear broken)
- Embedded video players, interactive demos, touch-gesture interactions
- Any CTA that requires a real device or logged-in session context

**What we discovered in production:** Agents were consistently flagging things like "Get Puck Buddy button is dead" and "interactive demo is completely broken" — findings that were wrong when tested on a real device. These dominated the reports and made them untrustworthy.

**The original decision:** We narrowed agent scope to **content and presentation only**. Both `native_persona.py` and `persona_agent.py` were hardened around:
- An absolute prohibition (`✗ NEVER log...`) on any interactivity findings
- A redirection (`✓`) toward copy quality, information architecture, visual hierarchy, readability, emotional journey, and content completeness
- The framing "you are a Content & UX Researcher" — not a functional tester

**Current refinement:** The original built-in `QA Agent` was completely removed on Mar 16, 2026, as part of a shift toward a static map-reduce "screenshot review" architecture. Because static reviewing cannot click buttons or follow links, functional bug testing was dropped entirely to preserve the high-signal content/taste feedback. All built-in personas are now exclusively UX/content focused.

---

## Authentication Flow (Per-Audit, Optional)

Users can provide `loginUrl`, `loginEmail`, and `loginPassword` in the new audit form. When provided:

1. The `BrowserDriver` is initialized at `loginUrl` (not `target_url`) so login happens before any browsing
2. `_login()` fills the email/password fields using flexible CSS selectors and submits the form
3. After login, the agent lands on the authenticated experience (dashboard, etc.)
4. The system instruction tells the agent: **"You are already logged in. Do NOT navigate back to the public marketing page. Explore the authenticated experience."**

**Previous bug (fixed Mar 9 2026):** The initial_url was always `target_url` (the public site). Even after login succeeded and redirected to the dashboard, the system prompt's first instruction was "Navigate to {target_url}" — sending the agent straight back to the marketing homepage. Fixed by setting `initial_url = auth.get('loginUrl', target_url) if auth else target_url` and branching the system instruction.

---

## Screenshot System

### How It Works
1. **Crawl**: `crawler.py` runs two parallel `BrowserDriver` instances (Desktop 1280×800 and Mobile 390×844). For each page, it captures N viewport frames while scrolling (3 for the homepage, 2 for subpages) and **stitches them into a single composite PNG** using Pillow. One composite URL is stored per page per device.
2. **Vision QA**: `screenshot_reviewer.py` reviews every composite for visual quality (blank frames, loading skeletons, broken device shells) before personas see anything.
3. **Filter**: Rejected screenshots are marked; their URLs are nullified on findings but the raw `crawledPages` entry is preserved as a last-resort fallback.
4. **Distribute**: Persona agents receive only the composites matching their `deviceType`.
5. **Consolidate**: The consolidator synthesizes evidence-backed findings into the final report.
6. **Results**: Review metadata is stored in `mediaArtifacts.screenshotReview` and within each persona's Firestore doc.

### Why One Composite Per Page (not multiple scrolled shots)
The persona agent calls `log_issue` with an explicit `screenshot_url`. When multiple scrolled images existed for the same page URL, the model had to recall the exact opaque Firebase Storage token for whichever viewport frame its observation came from — it almost never got it right, causing all findings to pile up on the first screenshot and leaving the others blank. Stitching into one composite per page gives the model exactly one URL to cite, so every finding for that page correctly attaches to the full-page visual.

### Screenshot-to-Finding Grouping (important)
Findings are mapped to screenshots through a prioritized fallback chain:

1. `finding.screenshotUrl` — the explicit URL the agent logged when calling `log_issue`
2. `report.pageScreenshots[pageKey]` — the approved screenshot stored for that page
3. `report.latestScreenshot` — if it belongs to the same page
4. `crawledPageKeyToImgUrl[pageKey]` — the first raw crawled screenshot for that page (last resort)

The last-resort fallback is important because the screenshot reviewer nullifies both `finding.screenshotUrl` and the matching `pageScreenshots` entry when it rejects a screenshot. Without step 4, those findings would be silently dropped and the screenshot would render with no persona quotes at all. The raw `crawledPages` data is never filtered by the reviewer, so it always provides a stable fallback image for the correct page.

### Screenshot Review Rules
The dedicated screenshot reviewer is intentionally stricter than the browsing agents. It approves screenshots only when they are presentation-safe evidence:
- legible and visually clear
- representative of a real page state
- free of blank or obviously broken media / device frames
- polished enough to reuse in the founder presentation

If a screenshot looks like a headless rendering artifact, missing gallery image, empty device shell, broken embed, or generally poor slide material, it is rejected and downstream consumers must fall back to another approved screenshot or generated filler art.

### Important Limitation
Screenshot capture is still coarse at the storage layer: `BrowserDriver.create_screenshot_upload()` dedupes by normalized page URL. That means the reviewer can reject bad screenshots, but it cannot recover a better section-specific screenshot if the system only stored one image for that page. The next reliability step would be section-aware screenshot storage rather than page-only reuse.

### Coordinate Overlays — Removed
We previously rendered CSS red circles on screenshots based on `(x, y)` coordinates logged by the agent. These were removed on Mar 9, 2026 because:
- The Gemini model internally scales/resizes images before processing them
- Coordinates reported by the model refer to the image as *the model sees it*, not the actual Playwright viewport pixel space
- This produced circles that were consistently in the wrong location
- The screenshot itself is sufficient visual context; circles added confusion

### Storage Path
`screenshots/{audit_id}/{persona_id}/shot_{timestamp_ms}.png`

### Firestore Schema Per Finding
```json
{
  "text": "The headline copy is too vague...",
  "screenshotUrl": "https://firebasestorage.googleapis.com/...",
  "pageUrl": "https://example.com/pricing",
  "x": 640,
  "y": 300
}
```
Note: `x` and `y` are still stored in Firestore (the backend still logs them) but are no longer rendered by the frontend.

---

## Frontend Key Files

| File | Purpose |
|------|---------|
| `src/app/(protected)/audit/new/page.tsx` | New audit page — persona selection, auth fields, custom persona create/edit/delete |
| `src/app/(protected)/audit/[auditId]/page.tsx` | Audit report — live progress, tabs (Presentation / Consolidated / Screenshots / Live Feeds), Send to Agent button. Live feed shows agent findings without internal status metrics. |
| `src/components/audit/PersonaSelector.tsx` | Persona card grid with edit + delete controls for custom personas |
| `src/components/audit/PersonaBuilder.tsx` | AI persona generator with Desktop/Mobile toggle |
| `src/components/audit/PersonaEditorDialog.tsx` | Manual editor for saved custom persona name, description, goals, and device type |
| `src/app/api/audit/start/route.ts` | Creates Firestore doc, POSTs to Python backend |
| `src/app/api/personas/generate/route.ts` | Calls Gemini to generate persona JSON |
| `src/lib/firebase.ts` | Firebase Client SDK init |
| `src/lib/firebase-admin.ts` | Firebase Admin SDK init (server-side) |
| `src/proxy.ts` | Middleware: guards `/(protected)` routes |

## Backend Key Files

| File | Purpose |
|------|---------|
| `agent-backend/main.py` | FastAPI app, Firebase init, `/api/run_audit`, `run_audit_background` with `asyncio.gather` |
| `agent-backend/agents/native_persona.py` | Pure multimodal reviewer (no browser execution loop). Uses `gemini-3.1-pro-preview` to evaluate a batch of crawled screenshots and log findings. |
| `agent-backend/agents/screenshot_reviewer.py` | Post-persona screenshot QA pass: multimodal image review, Firestore review metadata, evidence filtering |
| `agent-backend/agents/native_consolidator.py` | Reads evidence-backed findings from Firestore, treats persona summaries as secondary context only, writes final JSON report |
| `agent-backend/agents/audit_recap.py` | Post-audit media layer: structured presentation deck, per-slide narration, approved-screenshot assignment, and generated visuals for non-evidence slides |

---

## Firestore Data Schema

```
audits/{auditId}
  status: "pending" | "running" | "completed" | "error"
  url: string
  createdAt: timestamp
  userId: string
  selectedPersonaIds: string[]
  customPersonas: Array<{id, name, description, goals, deviceType}>
  consolidatedReport: {
    summary: string
    score: number
    title: string                     ← format: "UX Audit of <Company>"
    criticalIssues: string[]          ← format: "Title (Persona A, Persona B): Description"
    recommendations: string[]         ← same format
    positives: string[]               ← same format
  }
  mediaArtifacts: {
    screenshotReview: {
      status: "reviewing" | "ready" | "error"
      totalScreenshots: number
      reviewedCount: number
      approvedCount: number
      rejectedCount: number
      sampleIssues: string[]
    }
    presentation: {
      status: "generating" | "ready" | "error"
      title: string
      subtitle: string
      score: number
      slides: Array<{
        id: string
        eyebrow: string
        title: string
        bodyLines: string[]            ← max 3 concise bullets
        narration: string
        screenshotUrl?: string         ← real evidence screenshot OR generated visual asset
        pageUrl?: string
        personaName?: string
        audioUrl?: string
        visualSource?: "evidence" | "generated" | "none"
      }>
    }
  }

  agentReports/{personaId}
    personaName: string
    status: "running" | "completed" | "error"
    currentAction: string
    summary: string                   ← optional persona context written on finish; NOT source of truth for consolidation
    findingsCount: number             ← count of accepted evidence-backed findings
    findings: Array<{
      text: string
      screenshotUrl: string | null
      pageUrl: string | null
      evidenceBacked: boolean         ← true only for accepted findings used by consolidator
      category?: string               ← UX personas use content categories; `p_qa` uses functional categories
      sentiment?: "positive" | "negative"
      screenshotReview?: {
        approved: boolean
        qualityScore: number
        visualAppeal: "high" | "medium" | "low"
        missingImagesOrFrames: boolean
        issues: string[]
        summary: string
      }
      x: number                       ← stored but not rendered in UI
      y: number                       ← stored but not rendered in UI
    }>
    lastActionEvent: { ... }          ← most recent structured tool event for debugging
    actionEvents: Array<{ ... }>      ← action timeline (tool name, args, status, urls, result)
    latestScreenshot: string
    latestScreenshotPage: string
    pageScreenshots: {[pageUrl]: downloadUrl}
    screenshotReview: {
      status: "ready"
      reviewedCount: number
      approvedCount: number
      rejectedCount: number
      reviews: Array<{ ... }>
    }
```

---

## Environment Variables

### Frontend (`/src/.env.local`)
```
NEXT_PUBLIC_FIREBASE_API_KEY=...
NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=...
NEXT_PUBLIC_FIREBASE_PROJECT_ID=auditmysite-61bd1
NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET=auditmysite-61bd1.firebasestorage.app
NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=...
NEXT_PUBLIC_FIREBASE_APP_ID=...
FIREBASE_ADMIN_PROJECT_ID=auditmysite-61bd1
FIREBASE_ADMIN_CLIENT_EMAIL=...
FIREBASE_ADMIN_PRIVATE_KEY=...
AGENT_BACKEND_URL=http://localhost:8080
AGENT_API_SECRET=...
```

### Backend (`/agent-backend/.env`)
```
GEMINI_API_KEY=...          # From Google AI Studio — NOT Vertex AI
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_CLOUD_PROJECT=auditmysite-61bd1
FIREBASE_STORAGE_BUCKET=auditmysite-61bd1.firebasestorage.app
AGENT_API_SECRET=...        # Must match frontend
```

---

## Current Status (as of Mar 16, 2026)

### Working
- Full audit pipeline: persona selection → Playwright browsing → findings streaming → consolidation → report display
- Built-in `QA Agent` persona for broken links, failed interactions, form issues, and visible error states
- Custom persona creation (AI-generated), storage per-user in Firestore, editing, and deletion from UI
- Device type selection (Desktop / Mobile) with accurate Playwright emulation (touch, user-agent, viewport)
- Authentication flow for password-protected sites — agents now land on and explore the authenticated experience
- Consolidated report: Executive Summary, Key Recommendations, Critical Issues, Positive Findings, UX Score
- "Send to Agent" copy button — formats full report for AI coding agents (Cursor, Claude Code, etc.)
- Simulated progress bar heartbeat (avoids 0% stall at start, asymptotes at 85% while agents run)
- Screenshot tab: groups screenshots by image URL, shows associated findings alongside each screenshot
- `isMobile` is now propagated correctly through the screenshot group pipeline — custom mobile personas are no longer treated as desktop for image sizing
- Native persona runner now records structured `actionEvents` and `lastActionEvent` for each tool call, including blocked actions
- `finish` is now hard-blocked until a persona logs at least 3 accepted evidence-backed findings
- `finish` is also coverage-gated: at least 1 positive finding, 2 negative findings, 2 distinct pages/sections, and 2 different finding categories
- Simulator-artifact findings and summaries (broken demos, dead links, render failures, empty phone frames, etc.) are rejected before they reach Firestore
- `QA Agent` is the only built-in persona allowed to keep same-site functional failures such as broken links or failed interactions; the other personas still reject those findings
- Media-heavy viewports (videos, iframes, demo containers, lazy embeds) now get an extra guardrail so "blank/missing demo" observations are blocked as likely headless render artifacts
- Consolidation is now grounded in evidence-backed `findings`; persona `summary` is treated as secondary context only
- A dedicated screenshot-review pass now runs between persona completion and consolidation, removing screenshots with blank/missing frames or poor presentation quality before they reach the final deck
- Completed audits now generate a founder-friendly presentation artifact with per-slide audio and more visual storytelling than the raw report
- The presentation layer now avoids reusing the same approved screenshot twice; if no unused approved evidence fits, it falls back to generated visuals instead of duplicating a slide image
- Audit progress now stays visible through the presentation handoff instead of briefly showing a false "fully complete" state between report completion and presentation initialization
- **Standardized Branded Titles:** Presentation and report headers now follow the format: "UX Audit of <company or product name>". A `getFriendlySiteName` helper is used in both Python and TypeScript to reliably extract brand names from any URL.

### Known Issues / Tradeoffs
- Agent findings used to reference off-screen sections because the model saw `body.innerText` while only receiving a viewport screenshot. This is now mitigated by sending only visible viewport text plus scroll metadata in `native_persona.py`.
- Screenshot-to-finding matching only works when the agent explicitly passes `page_url` to `log_issue`. If `page_url` is missing, the finding won't appear in the Screenshots tab.
- Personas can still waste too many steps on navigation before logging findings. This is now less dangerous because early `finish` is blocked, but it can still reduce audit breadth on difficult sites.
- If the model keeps proposing simulator-artifact language, the finding is rejected and the agent must continue. Check `actionEvents` to see exactly which `log_issue` or `finish` calls were blocked and why.
- Presentation generation is intentionally slower than the report because each slide gets its own audio clip, and some low-evidence slides may still request fallback visuals from Vertex AI preview image models.
- Screenshot quality is now reviewed after persona execution, but screenshot capture is still page-level deduped upstream. A rejected screenshot cannot be replaced automatically with a better section-level crop unless that second screenshot was actually stored.

### Mar 10, 2026 — Presentation Layer Pivot: From Audio Recap To Boardroom Deck
**The Problem:** The first founder recap shipped as a plain audio summary and then as a text-heavy slide deck. It proved the pipeline worked, but it still looked too much like a dashboard report: raw URLs as titles, long copied report text, visible narration blocks, and placeholder visuals that did not feel presentation-ready.

**The Decision:** We kept the **core audit** stable on `gemini-2.5-pro` via AI Studio and moved only the **post-audit presentation layer** onto explicit Vertex AI preview models. This split lets the browsing agents stay reliable while the finished output aims for much higher polish.

**The Solution:**
- `audit_recap.py` now treats the deck as its own artifact, not as a reformatted report
- `gemini-3.1-pro-preview` on Vertex AI rewrites the completed audit into a boardroom-style slide JSON with concise titles, 1-3 short bullets, and presentation-friendly narration
- `gemini-3-pro-image-preview` is used selectively only when a product-facing slide does not have a strong grounded screenshot
- Real audit screenshots are now the preferred visual source across product, evidence, and recommendation slides whenever a good grounded image exists

**Product Direction:** The end-state is now clearly:
- `Presentation` — the primary guided experience
- `Report` — the detailed reference artifact

The standalone `Listen to Recap` surface has now been removed. The presentation is the single guided audio experience, while the report remains the detailed reference.

### Mar 10, 2026 — Presentation Progress Briefly Looked Complete Too Early
**The Problem:** After the consolidated report finished, the audit page could briefly show a fully complete state before the presentation artifact appeared. Roughly 10 seconds later, the UI would switch back into "building presentation," which made the completion state feel untrustworthy.

**Root Cause:** There was a handoff gap between the consolidator writing the report and `audit_recap.py` initializing `mediaArtifacts.presentation.status = "generating"`. During that gap, the frontend had no signal that the fourth tab was still in progress.

**The Solution:** We fixed the issue in both layers:
- `audit_recap.py` now writes a lightweight `presentation: generating` state immediately before the heavier authoring pass starts
- `src/app/(protected)/audit/[auditId]/page.tsx` now keeps the loading UI alive during the report-to-presentation handoff instead of briefly dropping to 100%

**Resulting Rule:** The audit should not look fully complete until the presentation tab is either ready or has explicitly failed.

### Mar 10, 2026 — Presentation Visuals Now Prefer Real Screenshots
**The Problem:** Some slides looked too obviously AI-generated, and product-focused slides could end up with weak filler art or no image at all even though the audit already had grounded screenshots.

**Root Cause:** The presentation authoring prompt explicitly reserved real screenshots for evidence slides only. Summary and recommendation slides were therefore biased toward generated visuals, even when a real audit screenshot would have been more trustworthy.

**The Solution:** We changed the visual strategy:
- the authoring prompt now prefers real audit screenshots for any slide discussing the product, UX findings, or recommendations
- `audit_recap.py` now runs a screenshot-assignment pass after authoring, matching slides to supporting findings and attaching grounded screenshots wherever possible
- generated visuals are now fallback filler only when no strong screenshot exists

**Resulting Rule:** If a slide is talking about the real product experience, prefer a real audit screenshot over synthetic art.

### Mar 10, 2026 — Summary vs. Evidence Divergence Bug
**The Problem:** A real `buddysports.app` audit exposed a trust gap between the Live Agent Feed and the final executive report. The mobile persona logged 0 findings and the first-time visitor logged only 1, yet the consolidator still produced multiple critical issues such as "broken demo video" and "empty phone frames." These did not appear in the live feed because they came from persona finish summaries, not from accepted findings.

**Root Cause:** `main.py` was passing only each persona's `summary` field into `native_consolidator.py`. The consolidator prompt claimed it was reading "findings," but the actual payload was just free-form summaries. This let unsupported claims and headless rendering artifacts leak into the final report.

**The Solution:** We hardened the pipeline in three places:
- `native_persona.py`: blocks `finish` until at least 3 accepted findings exist, and rejects simulator-artifact language in both `log_issue` and `finish`
- `persona_agent.py`: `report_finding()` now rejects simulator artifacts and tags accepted findings with `evidenceBacked: true`
- `main.py` + `native_consolidator.py`: the consolidator now receives evidence-backed findings as the primary payload and treats persona summaries as secondary context only

**Resulting Rule:** If an issue is not in the persona's accepted `findings` array, it should not appear in the consolidated report.

### Mar 10, 2026 — Media-Heavy Viewports Causing False Negative Findings
**The Problem:** On `buddysports.app`, some screenshots showed empty-looking video or demo containers even though the live site clearly renders motion media for real users. That created a new risk: the agent could stay within scope on paper but still log false negative content findings such as "no video is visible" or "the demo area is empty," even when the blank state was just headless rendering lag.

**Root Cause:** We had already blocked explicit simulator-artifact phrases like broken demos and failed players, but the model could still phrase the same issue more softly as a content observation. The runtime also was not looking at the viewport structure itself, so it could not tell when the current section was dominated by media surfaces that commonly render poorly in headless Chromium.

**The Solution:** We hardened the runner in two places:
- `native_persona.py`: each step now computes a lightweight viewport media scan (visible `video`, `iframe`, `canvas`, `svg`, and media-like containers) and includes that context in the model prompt
- `native_persona.py` + `persona_agent.py`: `log_issue` now blocks findings that read like "missing video / blank demo / no hotspots visible" when the viewport clearly contains embedded or animated media surfaces
- The prompt now tells personas to focus on surrounding headlines, captions, labels, and explanatory copy when a media-heavy section may not render correctly

**Resulting Rule:** If a section is media-heavy, the audit should critique the surrounding context and messaging, not treat an empty render as evidence that the product experience is broken.

### Mar 10, 2026 — Coverage-Gated Finish Logic
**The Problem:** Even after blocking early finish and rejecting simulator artifacts, a persona could still satisfy the runner with a shallow audit: for example 3 very similar negative findings from the same page, or a one-sided report with no positives at all.

**The Solution:** `native_persona.py` now tracks audit coverage in-memory and in Firestore. A persona cannot finish unless it has:
- at least 3 accepted findings total
- at least 1 positive finding
- at least 2 negative findings
- findings spanning at least 2 distinct pages/sections
- findings covering at least 2 different audit categories

**Implementation Detail:** Each accepted finding now includes structured `sentiment` and `category` metadata. Duplicate findings are also blocked so the model has to produce genuinely new observations instead of rephrasing the same issue.

### Mar 10, 2026 — Screenshot/Advice Mismatch From Full-Page Text Leakage
**The Problem:** During live testing, screenshot evidence often showed the same hero or top-of-page region while the written advice referred to pricing, lower sections, or other off-screen content. The feedback quality was often good, but the evidence image felt obviously mismatched.

**Root Cause:** `native_persona.py` was sending a viewport screenshot but also passing `page.inner_text('body')`, which effectively exposed the entire page's text to the model. That let the model make correct observations about content far below the fold without ever scrolling the screenshot to that section before calling `log_issue`.

**The Solution:** The state payload is now viewport-grounded:
- `_get_clean_text()` collects only text from elements currently visible in the viewport
- the prompt now includes `scroll_y` and `viewport_height`
- the instruction explicitly tells the model to reason only about content visible in the screenshot + visible viewport text

**Resulting Rule:** If a section is not visible in the current screenshot and viewport text, the agent should scroll before commenting on it.

**Validation:** A follow-up rerun on `buddysports.app` produced materially better screenshot relevance than earlier attempts. The written advice still needs normal audit scrutiny, but the screenshot evidence now tracks the discussed section much more closely instead of clustering at the hero/top-of-page region.

### Mar 10, 2026 — Auth Flow Regressing Back To Login Screen
**The Problem:** A LegalZoom authenticated audit showed screenshots of the login page even when the finding text claimed the agent had reached protected pages like `/my/dashboard` and `/my/documents`.

**Root Cause:** Two issues combined:
- `BrowserDriver.initialize()` called `_login()` and then still navigated to `initial_url`. In auth mode, `initial_url` was the login URL, so a successful login could immediately be bounced back to sign-in.
- The agent was allowed to log findings for claimed protected `page_url` values even when the real browser state was still on the login/auth page.

**The Solution:** `native_persona.py` now:
- tracks `auth_attempted`, `auth_succeeded`, and `auth_error`
- skips the post-login redirect back to `loginUrl` when auth succeeds
- includes `on_auth_page` in the model state payload
- blocks `log_issue` calls that claim protected pages while the real browser is still on an auth page
- persists `authStatus` and `authError` into Firestore for debugging

**Resulting Rule:** In authenticated audits, the browser's real URL is the source of truth. If the browser is still on auth, the agent may not claim to be evaluating the dashboard.

**Validation:** A follow-up authenticated run against a non-MFA ESPN flow successfully cleared the previous "snap back to login" behavior and entered the signed-in experience. This suggests the current auth approach is viable for standard username/password flows that do not require a second factor.

**Current Boundary:** MFA / OTP / verification-code checkpoints are still unsupported. When a site requests a one-time security code, the audit should be treated as blocked on auth rather than expecting the agent to complete the challenge.

### Mar 15, 2026 — Relaxing Completion Rules to Unblock Audits
**The Problem:** Audits were frequently getting stalled or completing with zero findings. Models were burning through their `max_steps = 20` budget without successfully saving findings because they were getting blocked by strict validation rules (like "Make this quote more specific" or missing coverage requirements).

**The Solution:** 
- Coverage requirements were stripped out (`MIN_FINDINGS_BEFORE_FINISH` reduced to 1, no minimum positives/negatives).
- Strict quote style validation filters (`get_quote_style_reason`, `get_persona_relevance_reason`, `get_simulator_artifact_reason`) were removed from the `log_issue` path.
- Screenshot generation during `log_issue` was made less brittle by attempting to reuse page-level cached screenshots instead of forcing synchronous fresh uploads every time.

**Resulting Rule:** The pipeline now prioritizes getting raw quotes and screenshots into Firestore first. Downstream steps (screenshot QA, consolidator) act as the filters to clean up the data, rather than crashing the audit via upfront validation.

### Mar 15, 2026 — Fixing Firestore QUIC Protocol Errors
**The Problem:** Long-running audits caused the Next.js frontend to throw `ERR_QUIC_PROTOCOL_ERROR.QUIC_TOO_MANY_RTOS` from Firestore `Listen/channel` requests, leading to dropped real-time connections.

**The Solution:** The Firebase client configuration in `src/lib/firebase.ts` was updated to `experimentalForceLongPolling: true` instead of auto-detecting. While long polling has a slight performance cost, it is significantly more reliable for long-lived listener streams across unstable network conditions (VPNs, proxies, etc.).

### Mar 15, 2026 — Upgrading to Gemini 2.5 Flash for Browser Agents
**The Problem:** The agents were frequently timing out on `chat.send_message` and crashing the audit. This was because launching 3 parallel `gemini-2.5-pro` agents instantly exhausted the 2 RPM (Requests Per Minute) free tier rate limit on Google AI Studio, causing the `tenacity` library to infinitely backoff until our 120-second timeout killed the run. We briefly attempted using `gemini-3.1-pro-preview-customtools` (also 2 RPM) and `gemini-3-flash-preview` (which threw 400 errors for image inputs).

**The Solution:**
- Switched the core execution loop in `native_persona.py` from `gemini-2.5-pro` to `gemini-2.5-flash`.
- Flash operates on a 15 RPM free tier limit, easily accommodating parallel multi-agent runs without stalling, while still providing sufficient capability for browsing and DOM reading, and fully supporting multimodal inputs.
- Also increased the `asyncio.wait_for` timeout from 120s to 180s to account for the fact that sometimes the model needs more than 2 minutes to evaluate a dense screenshot and DOM tree.

**Resulting Rule:** The browser loop MUST use `gemini-2.5-flash` or a model with at least a 15 RPM quota and stable multimodal support to prevent immediate 429 rate limit deadlocks when multiple personas are selected. The final `native_consolidator.py` remains on `gemini-2.5-pro` since it only executes once at the very end of the run.

### Mar 16, 2026 — Presentation Polish: Real Screenshots & Audio Auto-play
**The Problem:** The presentation layer occasionally used synthetic "AI-looking" generated visuals even when real site context was available. Additionally, the user experience was disjointed because users had to manually play the narration for every slide.

**The Solution:**
- **Strict Screenshot Enforcement:** Updated the slide authoring prompt to strictly demand real audit screenshots for *every* slide (even summary and recommendation slides).
- **Global Fallback Matching:** Updated `audit_recap.py` to search across *all* raw crawler screenshots if no evidence-backed finding was a strong keyword match. This ensures that even high-level slides remain grounded in the site's real visual context.
- **Audio Auto-play:** Added a `useEffect` hook in the frontend `PresentationTab` that automatically loads and plays the slide's narration whenever the user navigates to a new slide.

**Resulting Rule:** Presentations should always prefer real visual evidence over generated art, and narration should play automatically to create a "guided movie" experience.

### Mar 16, 2026 — Dual Viewport Support & Frontend Hook Fix
**The Implementation:** 
- `run_crawler_agent` now initializes two parallel `BrowserDriver` instances (1280x800 desktop and 390x844 mobile).
- Captured pages now include `desktop_screenshots` and `mobile_screenshots` fields.
- `run_persona_agent` intelligently selects device-specific screenshots based on the persona's `deviceType`.
- Frontend screenshot feed now groups by Page URL, then Device (Desktop first), then scroll timestamp, ensuring a predictable top-to-bottom story for both views.
- Fixed a major React "Rules of Hooks" violation where `useMemo` was called after early returns in `AuditPage`.

### Mar 16, 2026 — Production Hardening for GitHub + GCP Cloud Run
**Changes made:**
- **`.gitignore`** — added explicit patterns for Firebase service account JSON files (`*-firebase-adminsdk-*.json`, `*service-account*.json`). The `.env*` pattern already covered env files, but the JSON key file sitting in the repo root was not covered. It would have been committed on `git init`.
- **CORS** — `main.py` changed from `allow_origins=["*"]` to a `ALLOWED_ORIGINS` env var (comma-separated). Default is `http://localhost:3000` for local dev. In production, set it to the actual frontend URL(s).
- **`.dockerignore`** added to `agent-backend/` — prevents `.env` files and service account JSON from being baked into the Docker image during `gcloud builds submit`.
- **`agent-backend/.gitignore`** added — covers `__pycache__`, `venv`, `.adk/`, test images, and all `.json` files so local credential files are never accidentally committed.
- **`.env.example` files** — added for both frontend and backend with all required variable names, safe placeholder values, and inline documentation.
- **Secrets rotation recommended** — before making the GitHub repo public, rotate `GEMINI_API_KEY`, `AGENT_API_SECRET`, and the Firebase service account key in the GCP console. The current secrets were used in local development only but should be considered compromised if the repo is pushed publicly without rotation.

**Cloud Run deployment constraints:**
- Memory: minimum 2 GiB for Playwright + Chromium; 4 GiB preferred for concurrent audits.
- Timeout: set to 3600s (1 hour) — Cloud Run's default 300s will kill most audits mid-run.
- Concurrency: keep low (1–10). Each audit spawns multiple browser processes.
- Firebase Admin SDK uses Application Default Credentials on Cloud Run automatically — no service account JSON file needed in production, just the `GOOGLE_CLOUD_PROJECT` env var and the correct IAM roles on the Cloud Run service account.

### Mar 16, 2026 — Screenshots Tab: Persona Quotes Missing For Rejected Screenshots
**The Problem:** When the screenshot reviewer rejected a screenshot, persona quotes and thoughts would disappear entirely from that screenshot card in the UI. The screenshot itself still rendered but with an empty right panel — no quotes, no findings.

**Root Cause:** The screenshot reviewer does two things on rejection: it nullifies `finding.screenshotUrl` and it removes the URL from `report.pageScreenshots`. The `screenshotGroups` logic in `audit/[auditId]/page.tsx` resolved a finding's target screenshot via those two fields first, then `latestScreenshot`. When all three were empty or non-matching, `imgUrl` resolved to null and the finding was silently skipped via `if (!imgUrl) return`. The screenshot (initialized from raw `crawledPages`) stayed in the grid but with an empty `agents` array.

**The Fix:** Added a `crawledPageKeyToImgUrl` reverse map built from raw `crawledPages` before the persona-report loop. This map is never touched by the screenshot reviewer. It is now the final fallback in the `imgUrl` resolution chain. Findings that have had their explicit screenshot nullified will still appear under the correct page's first crawled screenshot rather than disappearing.

**Resulting Rule:** The `crawledPages` data is the ground truth for which screenshots exist per page. Always keep it as a last-resort fallback for screenshot-to-finding mapping.

### Mar 16, 2026 — Execution of Architecture Pivot: Removal of QA Agent and Legacy ADK Code
- **QA Agent (`p_qa`) Sunset:** Since static screenshots cannot reliably test broken links or form submissions, the `QA Agent` was entirely removed from the frontend (PersonaSelector) and backend (Consolidator). Audits are now strictly 100% content and UX presentation focused.
- **Legacy ADK Cleanup:** `agent-backend/agents/persona_agent.py`, the original fallback file that relied on the restrictive `ComputerUseToolset`, was deleted. All necessary configurations (like the `PERSONAS` dict and quote normalizer) were inlined into `native_persona.py`.
- **Live Feed UI Cleanup:** Removed the "Audit Snapshot" (collapsible status/coverage metrics) from each agent's card in the Live Agent Feed. This keeps the live experience focused on findings rather than internal state.
- **"Eligible Screenshot" Feedback Requirement:** Persona agents (`native_persona.py`) are now required to provide at least one first-person finding (positive or negative) for **every single screenshot** they receive. This ensures that the user's "Screenshots" tab is populated with relevant, context-aware advice for every visual artifact of the audit.
- **Vision Model Pre-Filtering:** A dedicated `review_urls` pass (using `gemini-2.5-flash`) now runs immediately after the crawler captures screenshots. This phase identifies and rejects "messy" screenshots (blank images, empty frames, loading skeletons) *before* persona agents see them, ensuring the final report is based only on "presentation-ready" evidence.

### Mar 16, 2026 — TTS Bug Fix: `response_modalities` Must Be Uppercase
**The Problem:** Presentation audio generation was silently failing with `"Gemini TTS response did not include audio bytes."` The error was caught and logged but the presentation continued without any slide narration.

**Root Cause:** `_generate_tts_response` in `audit_recap.py` passed `response_modalities=["audio"]` (lowercase). The Gemini TTS API requires `["AUDIO"]` (uppercase). Lowercase silently caused the model to return a response with no audio parts at all.

**The Fix:** Changed to `response_modalities=["AUDIO"]`. Also updated the retry loop in `_generate_tts_audio_asset` to retry on `"did not include audio bytes"` errors in addition to HTTP 500/429 errors, since TTS preview models can transiently return incomplete responses.

### Mar 16, 2026 — Persona Agents Were Too Shallow (≥2 Findings Per Screenshot Enforced)
**The Problem:** Despite the "at least once per screenshot" instruction, persona agents interpreted the minimum as permission to stop — producing one generic finding per screenshot and moving on.

**The Fix:** Three changes to `native_persona.py`:
1. Minimum raised from "at least once" to **"MINIMUM OF 2 TIMES"** per screenshot, with explicit instructions to reference specific copy, button labels, and section names visible in the image.
2. Temperature raised from `0.0` to `0.5` so quotes are more natural and varied across screenshots.
3. Each screenshot's inline prompt label now includes `"Log at least 2 findings for this screenshot before continuing"` — the reminder is embedded right before each image in the prompt, not just in the system instruction.

Two new evaluation categories were also added to the system instruction: **content completeness** and **messaging consistency**.

### Mar 16, 2026 — Public GitHub + GCP Cloud Run Deployment

**Live URLs:**
- Frontend: https://geminiux-buddy-tech.vercel.app
- Backend (Cloud Run): https://audit-agent-403481904256.us-central1.run.app
- Repo: https://github.com/jakedibattista/geminiux

**Build fixes required for Vercel:**

1. **TypeScript null guard** — `getPersonaProgress(report?: PersonaReport)` accessed `report.findingsCount` without checking if `report` was undefined. The `waiting` status guard did not satisfy the TypeScript compiler because `getPersonaStatus` itself accepts `undefined`. Fixed by adding `|| !report` to the early return guard.

2. **Firebase Admin lazy init** — `firebase-admin.ts` called `admin.auth()` and `admin.firestore()` at module load time. Next.js evaluates all server-side modules during the build's static analysis phase, before any request exists. If `FIREBASE_SERVICE_ACCOUNT` is missing or unparseable, `initializeApp()` throws (silently caught), then `admin.auth()` throws "default Firebase app does not exist." Fixed by replacing the top-level exports with `Proxy` objects that call `getAdminApp()` only when a property is first accessed — which only happens inside a live request handler, not at build time.

3. **`force-dynamic` on Firebase-dependent route groups** — Next.js 15 tries to statically pre-render all routes during the build, including client components. Pages in `/(auth)` (login, signup) and `/(protected)` (dashboard, audit) import the Firebase client SDK, which calls `initializeApp()` during static generation. If `NEXT_PUBLIC_FIREBASE_API_KEY` is undefined at build time, Firebase throws `auth/invalid-api-key`. Fixed by adding layout files to both route groups with `export const dynamic = 'force-dynamic'`, which opts the entire group out of static generation.

**Docker build fix:**

`playwright install-deps chromium` fails on `python:3.10-slim` (Debian Bookworm) because packages `ttf-unifont` and `ttf-ubuntu-font-family` no longer exist in that repo. Fixed by switching the Dockerfile base image to `mcr.microsoft.com/playwright/python:v1.52.0-jammy` — the official Playwright Python image, which ships with Chromium and all system dependencies pre-installed. No `install-deps` or `install chromium` steps needed.

**GCP deployment gotchas:**
- The default `gcloud` project may differ from the Firebase project. Always pass `--project YOUR_PROJECT_ID` explicitly to `gcloud builds submit` and `gcloud run deploy`.
- Required APIs are not enabled by default: enable `secretmanager`, `cloudbuild`, `run`, `containerregistry` before first deploy.
- The Cloud Run default compute service account (`PROJECT_NUMBER-compute@developer.gserviceaccount.com`) needs `secretmanager.secretAccessor` granted per-secret, plus `datastore.user`, `storage.objectAdmin`, and `aiplatform.user` at the project level for Firebase and Vertex AI access.
- CORS is managed via the `ALLOWED_ORIGINS` env var. Deploy with `ALLOWED_ORIGINS=*` first, then update to the Vercel URL after the frontend is deployed.

### Mar 16, 2026 — Composite Screenshots Eliminate Finding-to-Image Mismatch
**The Problem:** The crawler stored 3 separate scrolled screenshots for the homepage and 2 for each subpage. All shared the same `page_url`. When the persona agent called `log_issue`, it needed to cite the exact Firebase Storage token URL for whichever scroll frame its observation came from. The model almost never recalled the correct token, causing all findings for a page to pile up on the first screenshot URL and leaving the others blank in the UI.

**The Fix:** `crawler.py` now uses Pillow (`PIL`) to stitch the N viewport frames into a single composite PNG before uploading. Each page now has exactly one URL per device. The model can only cite that one URL, so every finding for the page attaches to the composite image showing the full scroll content.

- Homepage composites: 3 frames × 1280×800 = 1280×2400 desktop, 390×844×3 = 390×2532 mobile
- Subpage composites: 2 frames — 1280×1600 desktop, 390×1688 mobile
- After capture, the driver scrolls back to top so subsequent page navigation is unaffected
- `Pillow` added to `requirements.txt`
