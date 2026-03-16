# AuditMySite — Real Feedback from Almost Real Users

AuditMySite is a web application built for the Gemini Live Agent Challenge (Track 3: UI Navigator). It provides automated UX auditing using multiple AI personas running headless browsers in parallel.

## What It Does

Users enter a URL, select built-in personas (First-Time Visitor, Mobile User, Accessibility User, etc.) or generate custom ones with AI, and the tool:
1. **Crawler Phase** — A dedicated Crawler agent (Playwright) explores the site and captures dual Desktop/Mobile screenshots of the homepage and top navigation subpages.
2. **Vision Filter** — A vision model (`gemini-2.5-flash`) automatically reviews every screenshot, rejecting "messy" artifacts like blank images, empty device frames, or loading placeholders.
3. **Persona Review** — Multiple persona agents (`gemini-3.1-pro-preview`) receive the "eligible" screenshots in parallel and provide detailed first-person feedback on every single image.
4. **Consolidation** — A final Consolidator agent synthesises all findings into a scored executive report.

## Scope: What Agents Evaluate

Most built-in personas are **content and presentation auditors**. Note: Functional QA testing was removed from the scope in March 2026.

UX personas focus on:
- Copy clarity and value proposition messaging
- Information architecture and content hierarchy
- Missing or incomplete information per persona
- Readability, visual density, and layout
- Emotional user journey through content
- Messaging consistency and outdated copy

`QA Agent` focuses on:
- Broken links or dead-end navigation
- Failed button / CTA interactions on the same site
- Form submission problems and visible validation failures
- Obvious 404s, blank states, and visible app error pages
- Confirming at least one working flow so the audit is not purely negative

Even `QA Agent` still deliberately ignores unreliable headless cases such as:
- App Store / Google Play links
- Embedded video players or interactive demos
- Third-party widgets / embeds
- Navigation that requires real device touch events

## Key Features

- **Parallel AI Personas** — Multiple agents review the site concurrently, each with a distinct UX perspective
- **Dual Viewport Capture** — The crawler automatically captures BOTH desktop and mobile screenshots for every page visited, ensuring personas evaluate the view that matches their device type.
- **Live Streaming** — Findings appear in the UI as agents run via Firebase Firestore `onSnapshot`
- **Screenshots as Evidence** — Viewport-grounded screenshots uploaded to Firebase Storage, linked to individual findings so the image matches the section being discussed more reliably
- **Screenshot QA Pass** — A separate multimodal reviewer checks screenshots for blank/missing frames, poor visual quality, and presentation fitness before they are reused as final evidence
- **Media-Artifact Hardening** — Video/iframe-heavy sections are treated cautiously so headless render gaps do not become fake "missing demo" or "blank player" findings
- **Custom AI Persona Builder** — Describe a persona in natural language, select Desktop/Mobile, and Gemini generates a structured persona
- **Persistent Custom Personas** — Saved per-user in Firestore; editable and deletable from the UI
- **Optional Auth Support** — Provide login credentials for password-protected sites; agents attempt to enter the authenticated experience directly and now stay grounded to the real signed-in page state. Best for standard email/password flows; MFA / verification-code checkpoints are not yet supported.
- **Executive Report** — Scored consolidated report with a brand-aware title ("UX Audit of [Company]"), Executive Summary, Key Recommendations, Critical Issues, and Positive Findings
- **Founder Presentation** — A slide-style post-audit walkthrough with per-slide narration, screenshot-first visuals, and a professional "UX Audit of [Company]" title format
- **Send to Agent** — One-click copy of the full consolidated report, formatted for pasting into Cursor, Claude Code, or any AI coding agent

## Project Structure

```
/
├── src/                          # Next.js 15 App Router frontend
│   ├── app/
│   │   ├── (protected)/
│   │   │   ├── audit/new/        # New audit page
│   │   │   └── audit/[auditId]/  # Live audit report page
│   │   └── api/
│   │       ├── audit/start/      # Triggers the Python backend
│   │       ├── personas/generate/ # AI persona generation
│   │       └── session/          # Firebase auth cookie
│   ├── components/audit/         # PersonaSelector, PersonaBuilder
│   └── lib/                      # Firebase client + admin SDK init
└── agent-backend/                # Python FastAPI + Google GenAI SDK
    ├── main.py                   # Entry point, /api/run_audit endpoint
    └── agents/
        ├── native_persona.py     # Custom BrowserDriver & execution loop (gemini-2.5-pro)
        └── native_consolidator.py # Final report synthesis (gemini-2.5-pro)
```

## Getting Started

### Prerequisites
- Node.js 18+
- Python 3.12+
- Google AI Studio API key (get one at [aistudio.google.com](https://aistudio.google.com))
- Firebase project with Firestore, Authentication, and Storage enabled
- Google Cloud ADC configured (`gcloud auth application-default login`)

### 1. Frontend Setup

Copy the example env file and fill in your values:

```bash
cp .env.local.example .env.local
npm install
npm run dev
```

See `.env.local.example` for all required variables with descriptions.

### 2. Agent Backend Setup

```bash
cd agent-backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

Copy the example env file:
```bash
cp .env.example .env
```

Notes:
- The core browsing audit runs on the Google AI Studio path (`GEMINI_API_KEY`) because the browser agents rely on the stable native Playwright tool loop.
- `AGENT_API_SECRET` must match between the frontend and backend. Generate one with `openssl rand -hex 32`.
- In production on Cloud Run, all env vars should be set via Cloud Run environment variables or Secret Manager — never baked into the Docker image.

Start the backend (**do NOT use `--reload`** — it kills running audits):
```bash
PYTHONUNBUFFERED=1 nohup uvicorn main:app --port 8080 > /tmp/audit_backend.log 2>&1 &
tail -f /tmp/audit_backend.log
```

For local development only:
```bash
cd agent-backend && source venv/bin/activate && fastapi dev main.py --port 8080
```
Note: `fastapi dev` uses WatchFiles which **will** reload and kill in-flight audits on any file save. Only use it when you're not running live audits.

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

## Creating the GitHub Repo

1. Go to [github.com/new](https://github.com/new)
2. Choose **Public**, give it a name
3. Select a license — **MIT** is standard for open-source projects. If you want to keep commercial rights, use **Apache 2.0** or skip the license for now.
4. Do NOT check "Add README" or ".gitignore" — you already have both.
5. Click **Create repository**, then run:

```bash
cd /path/to/AuditMySite
git init
git add .
git commit -m "initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git push -u origin main
```

Verify the push by checking GitHub — confirm that no `.env` files, no `*.json` service account files, and no `agent-backend/.adk/` directory appear in the file list.

## Deploying to Google Cloud Run

The agent backend is containerized and designed to run on Cloud Run. The Next.js frontend can be deployed to Vercel or also to Cloud Run.

### Agent Backend (Cloud Run)

The backend needs at least **2 GiB memory** because Playwright runs Chromium inside the container.

**Step 1:** Deploy Cloud Run first (you need its URL for Vercel, and it needs Vercel's URL for CORS).

```bash
cd agent-backend

# Build and push the image
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/audit-agent

# Deploy — use * for ALLOWED_ORIGINS temporarily until you have the Vercel URL
gcloud run deploy audit-agent \
  --image gcr.io/YOUR_PROJECT_ID/audit-agent \
  --platform managed \
  --region us-central1 \
  --memory 2Gi \
  --cpu 2 \
  --concurrency 5 \
  --min-instances 1 \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=YOUR_PROJECT_ID,GOOGLE_CLOUD_LOCATION=us-central1,FIREBASE_STORAGE_BUCKET=YOUR_PROJECT_ID.firebasestorage.app,ALLOWED_ORIGINS=*" \
  --set-secrets "GEMINI_API_KEY=gemini-api-key:latest,AGENT_API_SECRET=agent-api-secret:latest"
```

**Step 2:** Deploy frontend to Vercel (see below), then come back and lock down CORS:

```bash
# After you have your Vercel URL, update CORS on the existing Cloud Run service
gcloud run services update audit-agent \
  --region us-central1 \
  --update-env-vars "ALLOWED_ORIGINS=https://your-app.vercel.app"
```

Key Cloud Run settings explained:
- **Memory** — Playwright + Chromium requires at least 2 GiB. 4 GiB if you see OOM crashes.
- **`--min-instances 1`** — Critical. Audits run as background tasks after the HTTP response is sent. If Cloud Run scales to zero while an audit is in-flight, the container is killed mid-run. `min-instances 1` keeps one instance alive at all times.
- **Timeout** — The request timeout doesn't matter here. `/api/run_audit` returns immediately (202 accepted) and the real work runs as a FastAPI `BackgroundTask`. Default 300s is fine.
- **`--allow-unauthenticated`** — The Cloud Run URL is public but the app enforces its own auth via `X-Api-Secret`. Requests without the correct secret get a 401 at the app level.
- **Service account** — Cloud Run service account needs `roles/datastore.user`, `roles/storage.objectAdmin`, and `roles/aiplatform.user`. Firebase Admin SDK uses Application Default Credentials automatically — no service account JSON file needed in production.

### Secrets via Secret Manager (recommended)

Rather than setting secrets as plain env vars, store them in Secret Manager:

```bash
echo -n "your-gemini-key" | gcloud secrets create gemini-api-key --data-file=-
echo -n "your-api-secret" | gcloud secrets create agent-api-secret --data-file=-
```

Then grant the Cloud Run service account access:
```bash
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:YOUR_SERVICE_ACCOUNT@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Frontend (Vercel — easiest)

No CLI needed. Do this in your browser:

1. Push the repo to GitHub (see below)
2. Go to [vercel.com](https://vercel.com) → **Add New Project** → import your GitHub repo
3. Vercel auto-detects Next.js — no build config needed
4. In the project settings under **Environment Variables**, add everything from `.env.local.example`
5. Set `AGENT_BACKEND_URL` to the Cloud Run URL from the step above
6. Deploy

Your Vercel URL will be `https://your-repo-name.vercel.app`. Use that to update `ALLOWED_ORIGINS` on Cloud Run (Step 2 above).

### Frontend (Cloud Run — alternative)

```bash
# Build Next.js for standalone output (add to next.config.ts: output: 'standalone')
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/audit-frontend
gcloud run deploy audit-frontend \
  --image gcr.io/YOUR_PROJECT_ID/audit-frontend \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "NEXT_PUBLIC_FIREBASE_PROJECT_ID=YOUR_PROJECT_ID,..."
```

## Architecture Notes

For deep technical details, critical workarounds, Firestore schema, and known issues, see [docs/AGENT_HANDOFF.md](docs/AGENT_HANDOFF.md).
