# I Built an AI App That Audits Your Website — And It Was a Disaster Before It Was Great

*A story about three architecture collapses, one red circle that wouldn't stay still, and what happens when you build an AI tool with AI.*

---

There is a specific kind of hubris that strikes software engineers at 2 AM during a hackathon. It sounds like this:

*"This is brilliant. I'll have it done by noon."*

That was me before the weekend leading up to my latest Gemini hackathon submission. In the 24 hours that followed, I would go on to redo the core thesis of my entire project.

This is the story of **AuditMySite** — a tool that lets you paste any URL, pick simulated user personas, and watch AI agents browse your website and file a full UX report in real time. It is, I think, pretty good now. Getting there required three full architecture rewrites, a philosophical deep dive into subagent behavior, and a red circle that haunted me for two weeks.

Let's start at the beginning.

---

## The Idea

While in an airport in Montana I was talking to my buddy about this hackathon and ran through my two ideas. The first was a mobile QA agent that could test my app before I pushed to the App Store. The other: a simulated UX research audit. While the former was bolder, I settled on the latter after seeing a plethora of overpriced AI UX research products on the market that I thought I could simplify and offer for free.

The premise is genuinely useful: most founders and product teams have never watched a real user struggle through their website. User testing is expensive. Recruiting participants takes time. And most people just skip it, ship something confusing, and wonder why their conversion rate is 1.3%.

What if AI could simulate users — different kinds of users — and file a UX report the way a real researcher would? A first-time visitor who knows nothing about your product. A mobile user squinting at your pricing page on a phone. An accessibility-conscious user trying to navigate without mouse precision.

You paste a URL. You pick your personas. AI does the rest.

The idea is clean. The execution was not.

---

## The Original Plan (Act I: Hubris)

I entered the [Gemini Live Agent Challenge](https://googlegemini.devpost.com/) — Track 3: UI Navigator — with a plan that looked like this on a whiteboard:

```
┌─────────────────────────────────────────────────────────┐
│                  THE DREAM ARCHITECTURE                 │
│                                                         │
│  User pastes URL                                        │
│         │                                               │
│         ▼                                               │
│   SequentialAgent (root)                               │
│         │                                               │
│         ├── ParallelAgent                               │
│         │     ├── 🤖 Persona 1 (browsing + reporting)  │
│         │     ├── 🤖 Persona 2 (browsing + reporting)  │
│         │     └── 🤖 Persona 3 (browsing + reporting)  │
│         │           │                                   │
│         │           └── Each runs a real browser,       │
│         │               clicks around, logs issues      │
│         │                                               │
│         └── 📋 Consolidator Agent                       │
│               └── synthesizes findings → report         │
└─────────────────────────────────────────────────────────┘

Tech: Google ADK + ComputerUseToolset + gemini-3.1-pro-preview
Timeline estimate: a week for testing and iteration
```

This is what engineers call a "napkin architecture." It looks clean because you haven't tried to build it yet.

I was using **Google's Agent Development Kit (ADK)**, which is a framework for building multi-agent AI systems. The plan was to give each persona agent a `ComputerUseToolset` — essentially, the ability to control a browser like a human — and let them loose on any URL.

Forty-eight hours later, the napkin was on fire.

---

## Act II: The ADK Collapses (Three Times)

### Collapse #1: The Model Said No

The first problem appeared the moment I tried to actually run an agent.

`gemini-2.5-computer-use-preview-10-2025` (the model I needed for browser control) threw:

```
400 INVALID_ARGUMENT: UI actions are not enabled for this project
```

Google restricts computer-use capabilities at the *enterprise project level*. My project wasn't allowlisted. I couldn't use Vertex AI for this at all.

Fine. I pivoted to Google AI Studio instead — same models, different API key. This worked! Until I tried to add my custom `log_issue` function alongside the browser tool. The ADK threw a new error:

```
400 INVALID_ARGUMENT: Tool use is not supported with this model configuration
```

The ADK strictly prohibits combining `ComputerUseToolset` with *any* custom function tools in specialized models. You can have browser control or custom tools. Not both. My entire architecture required both.

### Collapse #2: The Rate Limit Bloodbath

I found a workaround: split each persona into two separate ADK agents. One for browsing, one for reporting. It was clunky but theoretically functional.

I ran three personas simultaneously.

Twenty-five seconds later:

```
429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric
'generate_requests_per_minute_per_project_per_base_model'
```

The computer-use model has a **25 requests-per-minute rate limit on the free tier**. Three parallel agents, each making multiple API calls per navigation step, hit that ceiling almost instantly. The agents deadlocked. Nothing finished. The audit hung forever.

### Collapse #3: The Vanishing Screenshots

By this point, I'd rearchitected twice. On my third attempt, something subtler went wrong: ADK was *swallowing screenshots*.

The multimodal state passing — the mechanism that's supposed to attach screenshot images to findings — worked differently than the documentation suggested. I couldn't reliably attach visual evidence to individual findings. The screenshots existed. They just weren't making it to where they needed to go.

It was around this time I looped in my engineering friends Arjun and Stefan for a spirited debate about how to efficiently break down massive requests into subagent loads. Memory, API call limits, and agent-to-agent communication were all up for debate. It became clear that agents trying to scroll, take pictures, and give UX opinions simultaneously was never going to work. Arjun had just done a recent Capture the Flag challenge where his subagents kept hitting doom loops — the agents needed more keypress inputs to continue, but refused to ask for them. Same class of problem: an agent that tries to do too much at once will grind itself to a halt.

Three collapses. One conclusion:

> **ADK was not the right choice for this project.**

---

## The First Rebuild: Native Everything

With the ADK out of the picture, I wrote my own execution loop from scratch using Python's `asyncio` and the standard Google GenAI SDK. No framework. Just direct API calls, raw Playwright browser control, and an `asyncio.gather()` to run everything in parallel.

```
┌─────────────────────────────────────────────────────────┐
│                 ARCHITECTURE V2                         │
│                                                         │
│  asyncio.gather()                                       │
│     ├── 🤖 Persona 1 (browsing loop in native Python)  │
│     ├── 🤖 Persona 2 (browsing loop in native Python)  │
│     └── 🤖 Persona 3 (browsing loop in native Python)  │
│           │                                             │
│           └── Each agent: navigate → screenshot →       │
│               log finding → navigate → ...              │
│                                                         │
│  Then → 📋 Consolidator (gemini-2.5-pro)                │
└─────────────────────────────────────────────────────────┘

Model: gemini-2.5-pro (not the newest, but it works)
Rate limit: much more manageable
Timeline estimate: 1 more day... right?
```

This actually worked. Agents were browsing. Screenshots were saving to Firebase Storage. Findings were streaming to the frontend in real time. It was exciting.

Then I ran it against a real website.

---

## Act III: The False Positive Spiral

The agents were *technically accurate*. They were also completely wrong.

Here's what happened when I audited a real app:

- The agent flagged the main CTA button as a **dead link** that goes nowhere.  
  *(It opens the App Store on a real device. Headless Chromium doesn't know that.)*

- The agent reported the interactive product demo as **completely broken**.  
  *(It's a touch-based interaction. The headless browser has no fingers.)*

- The agent gave the site a score of **15/100**.  
  *(Based entirely on "broken core functionality" — none of which was real.)*

One audit of uxcon.com produced a consolidated report that read, essentially, "this website does not function." The website, when opened in a real browser, functioned perfectly.

This is the core tension in any headless browser testing: **the simulation is not the reality.** A headless Chromium instance can't tap a mobile menu. It can't authenticate into an app store. It can't trigger hover states that only appear with a real cursor.

I had built a tool that was filing bug reports about its own limitations.

### The Hard Product Decision

I had two options:

**Option A: Fix the simulation.** Real mobile device testing. Smarter link-following logic. Retry strategies for clicks that "should" have worked. This path is very hard, very expensive, and still imperfect.

**Option B: Narrow the scope deliberately.** Accept that headless browsers can't reliably evaluate interactivity. Lean into what they *can* do reliably: read copy, assess visual layout, evaluate information hierarchy, measure readability.

I chose B.

The agents' system prompts were rewritten with a section that, in retrospect, is one of the more unusual things I've ever written:

```
YOUR ONLY JOB IS TO EVALUATE CONTENT AND PRESENTATION.

✗ ABSOLUTE RULES — NEVER FLAG THESE:
  - Broken links or dead CTAs
  - Non-functional demos or video players  
  - App Store links that don't open
  - Any interactive element that requires touch
  
If a click fails: simply scroll to read the next section of content.

✓ WHAT YOU ARE HERE TO EVALUATE:
  - Copy clarity and information hierarchy
  - Visual layout and readability
  - Emotional journey through the page
  - Messaging gaps and confusing language
```

"If a click fails, simply scroll to read the next section of content."

I wrote this to an AI. The AI started listening to it. The false positives dropped dramatically. The product became dramatically more trustworthy.

---

## Act IV: The Red Circle Disaster

This one is embarrassing, and I'm sharing it anyway.

Early versions of the UI had a feature that felt very polished: when an agent logged a finding, a **red circle would appear on the screenshot** at the exact coordinates where the issue was. Click through the findings, see little red circles pointing at the problems. Elegant.

Here's the thing: the circles were almost never in the right place.

I tried fixing the math. Introduced separate X and Y scale factors:

```typescript
const scaleX = 100 / viewportWidth;   // X% is relative to width
const scaleY = 100 / viewportHeight;  // Y% is relative to height
```

The circles moved. Still wrong positions.

I fixed the `isMobile` detection, which was computing device type by checking whether the agent's *name* contained the word "mobile." (This is the kind of heuristic that works exactly once and breaks every time thereafter.) Still wrong.

I spent a meaningful portion of a hackathon trying to get CSS circles to land in the right places on screenshots.

The root cause, which I eventually discovered: **Gemini internally resamples images before processing them.** When the model reports `x=200, y=400`, those are coordinates in the model's internal image space — not the original Playwright viewport. There is no documented way to know what resolution Gemini uses internally. The coordinates are accurate to a reality I can't access.

The circles were removed. The screenshots are better evidence on their own.

---

## Act V: Rate Limit Roulette

A new problem appeared during heavy testing: **agents were silently freezing**.

Terminal logs would show `Sending state to model...` and then nothing. No error. No timeout. Just silence, forever, until I killed the process manually.

The fix was obvious in retrospect: async API calls with no timeouts will wait forever if the network drops. I wrapped everything in `asyncio.wait_for()` with a 180-second guardrail.

```python
response = await asyncio.wait_for(
    chat.send_message(state_payload),
    timeout=180.0
)
```

Now the agents cleanly crashed after precisely 180 seconds instead of hanging indefinitely. This is an upgrade. A crash you can see is better than a hang you can't.

But the crashes revealed what was actually causing the stalls: **rate limits**. The parallel persona agents were exhausting their model quotas in the first minute of any multi-persona audit. Every model I tried hit a different wall:

| Model | Problem |
|---|---|
| `gemini-2.5-pro` | 2 RPM on free tier — 3 agents → immediate 429 |
| `gemini-3.1-pro-preview-customtools` | Same 2 RPM cap |
| `gemini-3-flash-preview` | Throws 400 errors on screenshot inputs |
| `gemini-2.5-flash` | ✅ 15 RPM, multimodal support, actually works |

The browser agents are now on `gemini-2.5-flash`. It's not the newest model on the spec sheet, but it doesn't explode when you give it screenshots and ask for parallel execution.

---

## The Architecture That Actually Shipped

After all of this, here's what the final system looks like. It's simpler than any intermediate version, and it works.

```
┌─────────────────────────────────────────────────────────────────┐
│                   AUDITMY SITE — FINAL ARCHITECTURE            │
│                                                                 │
│  ┌─────────────┐    POST /api/run_audit    ┌─────────────────┐ │
│  │  Next.js    │ ────────────────────────► │  FastAPI        │ │
│  │  Frontend   │                           │  Backend        │ │
│  │             │ ◄── Firestore onSnapshot ─ │                 │ │
│  └─────────────┘                           └────────┬────────┘ │
│                                                     │           │
│                          ┌──────────────────────────┘           │
│                          │ asyncio background task               │
│                          ▼                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  PHASE 1: CRAWLER  (crawler.py)                           │  │
│  │                                                           │  │
│  │  Two Playwright browsers run in parallel:                 │  │
│  │  🖥️  Desktop (1280×800)   📱 Mobile (390×844)             │  │
│  │                                                           │  │
│  │  Each navigates: homepage + top 3 nav subpages            │  │
│  │  3 scrolling screenshots per page → Firebase Storage      │  │
│  └───────────────────────────┬───────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  PHASE 2: VISION QA GATE  (screenshot_reviewer.py)        │  │
│  │                                                           │  │
│  │  gemini-2.5-flash reviews every screenshot:               │  │
│  │  ✓ Clear, loaded page content → approved                  │  │
│  │  ✗ Blank frames, loading spinners, broken embeds → rejected│  │
│  └───────────────────────────┬───────────────────────────────┘  │
│                              │ only approved screenshots         │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  PHASE 3: PARALLEL PERSONA REVIEW  (native_persona.py)   │  │
│  │                                                           │  │
│  │  asyncio.gather()    EXAMPLE PERSONAS:                    │  │
│  │  ├── 👤 First-Time Visitor  → desktop screenshots only    │  │
│  │  ├── 📱 Mobile User         → mobile screenshots only     │  │
│  │  ├── ♿ Accessibility User  → desktop screenshots only    │  │
│  │  └── 👤 [custom personas]   → device-matched screenshots  │  │
│  │                                                           │  │
│  │  Each agent: gemini-3.1-pro-preview, batch multimodal     │  │
│  │  Findings stream to Firestore in real time as they write  │  │
│  └───────────────────────────┬───────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  PHASE 4: CONSOLIDATOR  (native_consolidator.py)          │  │
│  │                                                           │  │
│  │  gemini-2.5-pro reads only evidence-backed findings       │  │
│  │  → score/100, critical issues, recommendations, positives │  │
│  └───────────────────────────┬───────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  PHASE 5: PRESENTATION  (audit_recap.py)                  │  │
│  │                                                           │  │
│  │  gemini-3.1-pro-preview (Vertex AI) — slide authoring     │  │
│  │  gemini-2.5-flash-preview-tts — per-slide narration audio │  │
│  │  Real audit screenshots assigned to every slide           │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

Communication layer: Firestore as shared message bus
Frontend updates: onSnapshot (real-time, no polling)
```

Notice what's elegant here: the frontend and backend never talk to each other after the initial POST. The frontend subscribes to Firestore and watches findings appear in real time. The backend writes to Firestore as it goes. **Firestore is the message bus.** Neither side needs to know the other exists — they're both just reading and writing to a shared database.

---

## The Three Pivots (In Plain English)

Here's the honest summary of how the architecture evolved:

**Pivot 1 — SDK to Native:**  
Started with Google's ADK framework. Hit walls with model restrictions, rate limits, and broken state passing. Threw it out and wrote my own execution loop with native Python `asyncio` and raw Playwright. Less magic, more control.

**Pivot 2 — Browsing Agents to Crawl-and-Review (the big one):**  
Even with native Playwright, asking an AI to *simultaneously* navigate a browser, reason about UX, and log findings was too much. It was like asking someone to drive, read a map, and write a book report at the same time. My friend Bhavya was the one who crystallized the fix: pull the crawler out into its own dedicated agent that does nothing but drive the browser and take screenshots, then hand the photos off to separate reviewer agents who only read and write. I split the jobs exactly that way — a dedicated crawler does all the driving and screenshot-taking first, then the persona reviewers look at the photos and write the report. Much better.

**Pivot 3 — Static Report to Interactive Presentation:**  
The original output was a text report. It was useful, but it didn't feel like something you'd want to share. I added a presentation layer: a full slide deck with per-slide audio narration, grounded in real screenshots from the audit. Every slide shows the actual product, not AI-generated placeholder art.

---

## What Got Built

AuditMySite lets you paste any URL, provide optional auth credentials, and pick from built-in personas — First-Time Visitor, Mobile User, Accessibility User — or describe your own:

> *"A 45-year-old project manager at a mid-sized logistics company evaluating our B2B product for the first time."*

Hit Generate. Gemini creates a full persona card. The persona gets added to your selection and saved for future audits.

Behind the scenes (over the next 90 seconds or so):

1. A headless browser visits the site in both desktop and mobile viewports, capturing screenshots at multiple scroll depths across the homepage and key subpages.

2. A vision model reviews every screenshot for quality — rejecting loading spinners, blank frames, and broken embeds before any persona sees them. This is the step that keeps garbage out of the final report.

3. Each persona agent receives only the screenshots appropriate for their device type. They evaluate copy clarity, visual hierarchy, information gaps, emotional tone, and messaging — and stream findings to your screen as they're written.

4. A consolidator reads only the evidence-backed findings and produces a scored executive report.

5. A presentation layer rewrites the report into a slide deck with narration audio you can step through.

The whole thing runs in the background. You can watch findings appear in real time from the live agent feeds, then flip to the consolidated report when it's ready, then step through the presentation.

---

## The Meta-Layer: Building an AI Tool, With AI

I want to be honest about something: this was built in close collaboration with AI coding assistants. Cursor. Claude. Gemini itself. The architecture decisions, the debugging sessions, the "I need to completely rethink this" moments — all of it happened in conversation with AI.

There's something a little recursive about that. I built an AI that audits websites, using AI, while debugging AI models, for a competition run by the company that makes the AI models I was using.

The docs became part of the system. Architecture decisions, bugs found and fixed, scope changes, the reasoning behind every pivot — all logged in a format that let the next session continue without losing momentum. I did this via a living document throughout the duration of the project called `AGENT_HANDOFF.md`, updated regularly via an `/updatedoc` skill after every major change.

In less AI-sounding words: running `/updatedoc` after the context window hit 50% kept multi-session work moving smoothly, made publishing the blog and the GitHub repo trivial, and meant that debugging and system redesigns never left orphaned legacy context behind.

This is, I think, what collaborative development with AI actually looks like in practice: not "the AI writes all the code" but "the human and AI maintain a shared understanding of a system that's too complex to hold in either one's working memory alone."

---

## What Surprised Me Most

**Scope discipline is the hardest problem.** The technical architecture was difficult. But the hardest decision wasn't choosing which model to use — it was deciding what the tool was *not* allowed to do. Telling an AI agent "you are not allowed to report broken links" and then watching it still report broken links, and having to make the prompt louder and more emphatic until it finally stuck — that's a product problem, not an engineering problem. The engineering is downstream of scope.

**Rate limits are the real infrastructure.** I spent more time routing around API rate limits than around any other technical constraint. The choice of model for each phase of the pipeline was almost entirely determined by rate limits — not by capability. `gemini-2.5-flash` runs the browser agents not because it's the most capable model but because it's the only one that doesn't immediately 429 when three instances run in parallel.

**Firestore as a message bus is underrated.** The frontend/backend communication pattern — where both sides just read and write to Firestore and never talk to each other directly — turned out to be extremely robust. The backend can restart without the frontend caring. The frontend can connect and disconnect without the backend caring. Real-time streaming works without WebSockets. For an async multi-agent workflow where individual steps can take varying amounts of time, it's the right architecture.

---

## The Tagline

Early in the build, the UI text said:

> *"Automated UX testing powered by multiple AI personas running in parallel..."*

This is accurate. It is also the kind of thing only an engineer could love.

I changed it to:

> **"Real Feedback from Almost Real Users."**

The "almost" is doing a lot of work. It's honest about what AI simulation can and can't do. It's also, I think, the right frame for the whole project: not a replacement for real user testing, but a useful approximation that you can run in 90 seconds instead of 90 days.

---

## Post-Submission Bugs Found and Fixed

After the initial submission and live testing, a few real bugs surfaced:

**Silent backend trigger failure (Vercel serverless)**  
The `POST /api/audit/start` route was calling `fetch(cloudRunUrl)` without `await`. Vercel's serverless functions terminate as soon as a response is returned, killing any unawaited Promises. Audits stuck at `pending` forever because Cloud Run was never actually reached. Fix: changed to `await fetch(...)` with a 10-second `AbortSignal.timeout` and proper `try/catch` that updates Firestore to `status: error` and returns a `502` if the backend can't be reached.

**Python stdout buffering hid Cloud Run logs**  
`print()` statements from the Python backend weren't showing up in Cloud Run logs during live runs. Python buffers stdout by default when not attached to a TTY. Fix: added `ENV PYTHONUNBUFFERED=1` to the `Dockerfile`.

**Presentation slides showing generic placeholder images instead of real screenshots**
The `persona_reports` dict built in `main.py` only included `{personaId, summary, findings[]}`. The `_attach_supporting_screenshots` function in `audit_recap.py` built its fallback raw screenshot pool (`all_raw_screenshots`) from `report.pageScreenshots` and `report.latestScreenshot` — fields that were never included in that dict. So the pool was always empty. With only 2-3 unique screenshot URLs across all findings (the same composite image gets cited by every finding on a given page), the `used_urls` set exhausted all unique options after the first two slides. Every remaining slide fell through to `_generate_presentation_visual_asset`, which generates a generic AI placeholder image. Fix: after the `pageScreenshots`/`latestScreenshot` pass, if `all_raw_screenshots` is still empty, populate it from the `screenshotUrl` fields on the findings themselves. The cyclic reuse path at the end of the fallback chain then has a real pool to draw from, so every slide gets an actual audit screenshot.

**Duplicate screenshot cards in the Screenshots tab**  
The `BrowserDriver` (used internally by the crawler) calls `create_screenshot_upload()` on every page navigation, writing individual `shot_TIMESTAMP.png` files to `agentReports/crawler_desktop.latestScreenshot` and `agentReports/crawler_mobile.latestScreenshot`. These `shot_` URLs are different from the stitched `composite_TIMESTAMP.png` URLs stored in `crawledPages`. The frontend `screenshotGroups` useMemo processed all persona reports — including `crawler_desktop` and `crawler_mobile` — and created a new card for each unrecognized `latestScreenshot` URL. This caused the last page visited by the crawler to appear twice in the Screenshots tab (one individual `shot_` card, one composite card). Fix: filter `crawler_*` reports out of the `screenshotGroups` loop. The crawler's composites still render correctly via `crawledPages`.

---

## What's Next

The hackathon submission is in. Whether it places or not, the thing I built is real, works on real websites, and tells you something genuinely useful that you didn't know before you ran it.

The architecture has more room to grow:

- **Authenticated audits** work for standard username/password sites. MFA and OTP are still out of scope.
- **The crawler is capped at ~4 pages** right now. More thorough site crawls are possible but require smarter navigation logic.
- **The presentation layer** is where the most polish lives — and where the most polish can still be added.

The three pivots taught me more about building production AI systems than any amount of reading would have. You learn where the framework boundaries are by hitting them. You learn what a model can't do by watching it fail confidently.

And lastly, you learn what your product actually *is* by deciding what it isn't.

---

*Built for the Gemini Live Agent Challenge — Track 3: UI Navigator.*  
*Stack: Next.js 15, FastAPI, Python asyncio, Google AI Studio, Vertex AI, Firebase Firestore + Storage + Auth, Playwright.*
