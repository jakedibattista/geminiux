# AuditMySite — Real Feedback from Almost Real Users

> **This project has been sunset.** The Cloud Run backend has been decommissioned and the app is no longer accepting new audits. The source code remains available here for reference.

AuditMySite is an open-source web app that ran multiple AI personas against any URL and returned a live UX audit with a score, findings, recommendations, and a narrated slide presentation. Built for the [Gemini Live Agent Challenge](https://ai.google.dev/) (Track 3: UI Navigator).

**[Read the build story →](docs/build-story.md)**

**🎥 [Watch the demo →](https://youtu.be/VstSF9ii0cU)**

---

## How It Works

1. **Crawl** — A dedicated Playwright crawler visits the site on both desktop (1280×800) and mobile (390×844), capturing composite scroll screenshots of the homepage and top navigation pages.
2. **Vision Filter** — `gemini-2.5-flash` reviews every screenshot and scores whether it is clean enough for the founder presentation. This review no longer gates persona coverage.
3. **Persona Review** — Multiple persona agents (`gemini-3.1-pro-preview`) review the full crawled screenshot set in parallel, using the screenshot review pass only as presentation-quality metadata. Each agent must log at least 2 distinct first-person findings per screenshot.
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
- **Screenshot QA Pass** — A separate multimodal reviewer checks every screenshot for founder-deck quality without narrowing persona audit coverage
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

## Deploying to Production (Historical Reference)

> **Note:** The production backend has been decommissioned. These instructions are preserved for reference only.

<details>
<summary>Original deployment steps</summary>

Live deployment (when active):
- **Frontend:** https://geminiux.vercel.app
- **Frontend alias:** https://geminiux-buddy-tech.vercel.app
- **Backend (decommissioned):** https://audit-agent-403481904256.us-central1.run.app
- **Repo:** https://github.com/jakedibattista/geminiux

#### Step 1 — Enable GCP APIs

Do this once per project:
```bash
gcloud services enable \
  secretmanager.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  containerregistry.googleapis.com \
  --project YOUR_PROJECT_ID
```

#### Step 2 — Create secrets

```bash
echo -n "your-gemini-key" | gcloud secrets create gemini-api-key --data-file=- --project YOUR_PROJECT_ID
echo -n "your-api-secret" | gcloud secrets create agent-api-secret --data-file=- --project YOUR_PROJECT_ID
```

#### Step 3 — Build and deploy the agent backend (Cloud Run)

Always pass `--project` explicitly — your default `gcloud` project may differ from your Firebase project.

```bash
cd agent-backend

gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/audit-agent --project YOUR_PROJECT_ID

gcloud run deploy audit-agent \
  --image gcr.io/YOUR_PROJECT_ID/audit-agent \
  --platform managed \
  --region us-central1 \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 5 \
  --min-instances 1 \
  --allow-unauthenticated \
  --project YOUR_PROJECT_ID \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,GOOGLE_CLOUD_LOCATION=us-central1,FIREBASE_STORAGE_BUCKET=YOUR_PROJECT_ID.firebasestorage.app,ALLOWED_ORIGINS=*" \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,AGENT_API_SECRET=agent-api-secret:latest"
```

#### Step 4 — Grant IAM permissions

The Cloud Run default service account (`PROJECT_NUMBER-compute@developer.gserviceaccount.com`) needs:

```bash
SA="PROJECT_NUMBER-compute@developer.gserviceaccount.com"
PROJECT="YOUR_PROJECT_ID"

gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" --project $PROJECT
gcloud secrets add-iam-policy-binding agent-api-secret \
  --member="serviceAccount:$SA" --role="roles/secretmanager.secretAccessor" --project $PROJECT

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/datastore.user"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/storage.objectAdmin"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"
```

#### Step 5 — Deploy the frontend (Vercel)

1. Push to GitHub
2. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your repo
3. Add all variables from `.env.local.example` in Vercel project settings
4. Set `AGENT_BACKEND_URL` to your Cloud Run URL
5. Deploy

</details>

---

## Cloud Run Wind-Down

To fully decommission the Cloud Run backend and eliminate ongoing costs, run these commands:

```bash
# 1. Delete the Cloud Run service (stops all billing for compute)
gcloud run services delete audit-agent \
  --region us-central1 \
  --project auditmysite-61bd1 \
  --quiet

# 2. Delete the container image from Container Registry (saves storage costs)
gcloud container images delete gcr.io/auditmysite-61bd1/audit-agent \
  --force-delete-tags \
  --project auditmysite-61bd1 \
  --quiet

# 3. (Optional) Delete secrets if no longer needed
gcloud secrets delete gemini-api-key --project auditmysite-61bd1 --quiet
gcloud secrets delete agent-api-secret --project auditmysite-61bd1 --quiet
```

After running these commands, your Cloud Run costs will drop to $0. The Vercel frontend (free tier) will continue to serve the sunset landing page at no cost.

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
