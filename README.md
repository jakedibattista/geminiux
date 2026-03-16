# AuditMySite — Real Feedback from Almost Real Users

AuditMySite is an open-source web app that runs multiple AI personas against any URL and returns a live UX audit with a score, findings, recommendations, and a narrated slide presentation. Built for the [Gemini Live Agent Challenge](https://ai.google.dev/) (Track 3: UI Navigator).

**[Read the build story →](docs/build-story.md)**

---

## How It Works

1. **Crawl** — A dedicated Playwright crawler visits the site on both desktop (1280×800) and mobile (390×844), capturing composite scroll screenshots of the homepage and top navigation pages.
2. **Vision Filter** — `gemini-2.5-flash` reviews every screenshot, rejecting loading placeholders, blank frames, and other headless render artifacts before personas see anything.
3. **Persona Review** — Multiple persona agents (`gemini-3.1-pro-preview`) receive the approved screenshots in parallel. Each agent must log at least 2 distinct first-person findings per screenshot.
4. **Consolidation** — `gemini-2.5-pro` synthesises all evidence-backed findings into a scored executive report.
5. **Presentation** — A boardroom-style slide deck is generated with per-slide narration audio, grounded in real audit screenshots wherever possible.

## What Agents Evaluate

All built-in personas are **content and presentation auditors**. Functional QA (broken links, form failures) was intentionally removed — headless browsers produce too many false positives for interactivity testing to be trustworthy.

Personas focus on:
- Copy clarity and value proposition messaging
- Missing information — what questions does this persona have that the page doesn't answer?
- Information architecture and content hierarchy
- Visual hierarchy and readability
- Emotional journey through the content
- Messaging consistency across sections

## Key Features

- **Parallel AI Personas** — Multiple agents review concurrently, each with a distinct UX perspective
- **Dual Viewport Capture** — Every page is captured in both desktop and mobile viewports; personas receive the view matching their device type
- **Composite Screenshots** — Scrolled viewport frames are stitched into a single image per page, so findings always attach to the right visual context
- **Live Streaming** — Findings appear in the UI as agents run via Firestore `onSnapshot`
- **Screenshot QA Pass** — A separate multimodal reviewer checks every screenshot for visual fitness before it's used as evidence
- **Custom AI Persona Builder** — Describe any persona in natural language, pick Desktop or Mobile, and Gemini generates a structured persona saved to your account
- **Executive Report** — Scored report with summary, critical issues, recommendations, and positive findings
- **Founder Presentation** — Narrated slide deck with per-slide audio, grounded in real site screenshots
- **Optional Auth Support** — Provide login credentials to audit password-protected experiences

---

## Project Structure

```
/
├── src/                              # Next.js 15 App Router frontend
│   ├── app/
│   │   ├── (protected)/
│   │   │   ├── audit/new/            # New audit page (persona selection, auth)
│   │   │   └── audit/[auditId]/     # Live audit report (findings, report, presentation)
│   │   └── api/
│   │       ├── audit/start/          # Creates Firestore doc, triggers Python backend
│   │       ├── personas/generate/    # AI persona generation endpoint
│   │       └── session/              # Firebase auth cookie
│   ├── components/audit/             # PersonaSelector, PersonaBuilder, PersonaEditorDialog
│   └── lib/                          # Firebase client + admin SDK init
│
└── agent-backend/                    # Python FastAPI + Google GenAI SDK
    ├── main.py                        # Entry point, /api/run_audit endpoint
    └── agents/
        ├── crawler.py                 # Playwright crawler — dual viewport, composite screenshots
        ├── browser_driver.py          # BrowserDriver: Playwright wrapper + Firebase uploads
        ├── native_persona.py          # Persona reviewer — batch multimodal, gemini-3.1-pro-preview
        ├── screenshot_reviewer.py     # Vision QA gate — gemini-2.5-flash
        ├── native_consolidator.py     # Report synthesis — gemini-2.5-pro
        └── audit_recap.py            # Presentation + narration audio layer
```

---

## Getting Started

### Prerequisites

- Node.js 18+
- Python 3.12+
- Google AI Studio API key — [aistudio.google.com](https://aistudio.google.com)
- Firebase project with Firestore, Authentication, and Storage enabled
- Google Cloud ADC configured: `gcloud auth application-default login`

### 1. Frontend

```bash
cp .env.local.example .env.local   # fill in your Firebase + backend values
npm install
npm run dev
```

See `.env.local.example` for all required variables.

### 2. Agent Backend

```bash
cd agent-backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env               # fill in GEMINI_API_KEY, AGENT_API_SECRET, etc.
```

Start the backend:
```bash
# Local dev (hot reload — do NOT use while running live audits)
uvicorn main:app --reload --port 8080

# Production / stable (safe to run audits)
PYTHONUNBUFFERED=1 nohup uvicorn main:app --port 8080 > /tmp/audit_backend.log 2>&1 &
```

> **Note:** `--reload` kills all in-flight audits on any file save. Only use it when you're not running live audits.

### Key env vars

| Variable | Where | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | backend | Google AI Studio key — used for all agents |
| `GOOGLE_GENAI_USE_VERTEXAI` | backend | Must be `FALSE` — browsing agents don't work on Vertex |
| `GOOGLE_CLOUD_PROJECT` | backend | Your GCP project ID — used for Vertex presentation layer |
| `FIREBASE_STORAGE_BUCKET` | backend | e.g. `your-project.firebasestorage.app` |
| `AGENT_API_SECRET` | both | Shared secret — generate with `openssl rand -hex 32` |
| `AGENT_BACKEND_URL` | frontend | e.g. `http://localhost:8080` for local dev |

---

## Firebase Storage Rules

```
rules_version = '2';
service firebase.storage {
  match /b/{bucket}/o {
    match /screenshots/{allPaths=**} {
      allow read;
      allow write: if false;  // Backend writes via Admin SDK (bypasses rules)
    }
  }
}
```

---

## Deploying to Production

### Agent Backend (Cloud Run)

The backend needs at least **2 GiB memory** because Playwright runs Chromium inside the container.

```bash
cd agent-backend

# Build and push the image
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/audit-agent

# Deploy
gcloud run deploy audit-agent \
  --image gcr.io/YOUR_PROJECT_ID/audit-agent \
  --platform managed \
  --region us-central1 \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 5 \
  --min-instances 1 \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,FIREBASE_STORAGE_BUCKET=YOUR_PROJECT_ID.firebasestorage.app,ALLOWED_ORIGINS=*" \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,AGENT_API_SECRET=agent-api-secret:latest"
```

Key settings:
- `--min-instances 1` — audits run as background tasks after the HTTP response returns. If Cloud Run scales to zero, the container is killed mid-audit.
- `--memory 2Gi` — minimum for Playwright + Chromium. Use 4 GiB if you see OOM crashes.
- The default 300s request timeout is fine — `/api/run_audit` returns 202 immediately and the work runs as a `BackgroundTask`.

After deploying the frontend, lock down CORS:
```bash
gcloud run services update audit-agent \
  --region us-central1 \
  --update-env-vars "ALLOWED_ORIGINS=https://your-app.vercel.app"
```

### Frontend (Vercel)

1. Push to GitHub
2. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your repo
3. Add all variables from `.env.local.example` in the Vercel project settings
4. Set `AGENT_BACKEND_URL` to your Cloud Run URL
5. Deploy

---

## Documentation

| File | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full architecture, Firestore schema, critical constraints, model split, known issues, and full change log |
| [docs/build-story.md](docs/build-story.md) | Narrative write-up of the build — three architecture collapses, the red circle problem, and the design decisions that shaped the final product |
| [docs/hackathon_report.md](docs/hackathon_report.md) | Detailed technical build log from the hackathon |

---

## License

MIT
