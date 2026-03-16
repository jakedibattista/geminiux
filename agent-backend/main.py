import os
import asyncio
import traceback
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# Initialize Firebase Admin
if not firebase_admin._apps:
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
        import google.auth
        credentials, default_project = google.auth.default()
        print(f"Auth Default Project: {default_project}, Env Project: {project_id}")
        
        if project_id:
            from firebase_admin import credentials as firebase_credentials
            cred = firebase_credentials.ApplicationDefault()
            storage_bucket = os.environ.get("FIREBASE_STORAGE_BUCKET", f"{project_id}.firebasestorage.app")
            firebase_admin.initialize_app(cred, {
                'projectId': project_id,
                'storageBucket': storage_bucket,
            })
        else:
            firebase_admin.initialize_app()
        print(f"Firebase initialized successfully")
    except Exception as e:
        print(f"Warning: Firebase initialization failed: {e}")

db = firestore.client() if firebase_admin._apps else None

app = FastAPI()

# In production set ALLOWED_ORIGINS to your frontend URL, e.g.:
# ALLOWED_ORIGINS=https://your-app.vercel.app,https://your-app.run.app
_raw_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Api-Secret"],
)

@app.on_event("startup")
async def cleanup_orphaned_audits():
    if not db: return
    try:
        orphans = db.collection('audits').where('status', '==', 'running').stream()
        count = 0
        for doc in orphans:
            doc.reference.set({'status': 'error', 'errorMsg': 'Server restarted mid-audit.'}, merge=True)
            for report in doc.reference.collection('agentReports').stream():
                if report.to_dict().get('status') == 'running':
                    report.reference.set({'status': 'error'}, merge=True)
            count += 1
        if count:
            print(f"Startup cleanup: marked {count} orphaned audit(s) as error.")
    except Exception as e:
        print(f"Startup cleanup failed: {e}")

async def run_audit_background(audit_id: str, url: str, persona_ids: list, user_id: str, custom_personas: list = None, auth: dict = None):
    if custom_personas is None:
        custom_personas = []
        
    try:
        if db:
            db.collection('audits').document(audit_id).set({'status': 'running'}, merge=True)

        # 1. Run the Crawler Agent first to capture screenshots
        from agents.crawler import run_crawler_agent
        crawler_result = await run_crawler_agent(audit_id, url, auth=auth)
        
        if crawler_result.get("status") == "error":
            raise Exception(f"Crawler failed: {crawler_result.get('reason')}")
            
        crawled_pages = crawler_result.get("crawledPages", [])
        if not crawled_pages:
            raise Exception("Crawler finished but returned no screenshots.")

        # 2. Pre-filter screenshots using the Vision model to ensure only "eligible" ones are used
        from agents.screenshot_reviewer import review_urls
        all_urls = []
        for p in crawled_pages:
            all_urls.extend(p.get("desktop_screenshots", []))
            all_urls.extend(p.get("mobile_screenshots", []))
        
        print(f"[Audit {audit_id}] Reviewing {len(all_urls)} screenshots for evidence quality...")
        reviews_by_url = await review_urls(audit_id, all_urls)
        
        # Mark and filter the screenshots in crawled_pages
        filtered_crawled_pages = []
        for page in crawled_pages:
            approved_desktop = [u for u in page.get("desktop_screenshots", []) if reviews_by_url.get(u, {}).get("approved")]
            approved_mobile = [u for u in page.get("mobile_screenshots", []) if reviews_by_url.get(u, {}).get("approved")]
            
            # If a page has NO approved screenshots, we keep it but it will have empty screenshot lists
            # This ensures we still know about the page, but agents won't see "messy" evidence.
            filtered_page = dict(page)
            filtered_page["desktop_screenshots"] = approved_desktop
            filtered_page["mobile_screenshots"] = approved_mobile
            filtered_page["screenshots"] = approved_desktop # For backwards compatibility
            filtered_crawled_pages.append(filtered_page)
            
        print(f"[Audit {audit_id}] Filtered out {len(all_urls) - sum(len(p['desktop_screenshots']) + len(p['mobile_screenshots']) for p in filtered_crawled_pages)} messy screenshots.")

        # 3. Run persona agents in parallel, passing ONLY the eligible crawled pages
        from agents.native_persona import run_persona_agent
        
        tasks = []
        for pid in persona_ids:
            custom_data = next((p for p in custom_personas if p.get('id') == pid), None)
            tasks.append(run_persona_agent(pid, audit_id, url, filtered_crawled_pages, custom_data))
            
        print(f"[Audit {audit_id}] Starting {len(tasks)} persona reviewers concurrently...")
        await asyncio.gather(*tasks, return_exceptions=True)
        print(f"[Audit {audit_id}] All persona agents finished.")

        # Run consolidator
        from agents.native_consolidator import run_native_consolidator
        
        # Build the consolidator payload from evidence-backed findings first.
        # Persona summaries are included only as secondary context.
        persona_reports = {}
        if db:
            reports_ref = db.collection('audits').document(audit_id).collection('agentReports')
            for doc in reports_ref.stream():
                data = doc.to_dict()
                name = data.get('personaName', doc.id)
                summary = data.get('summary', 'No summary provided by agent.')
                raw_findings = data.get('findings', []) or []
                findings = []
                for finding in raw_findings:
                    if not isinstance(finding, dict):
                        continue
                    if not finding.get('evidenceBacked', True):
                        continue
                    findings.append(dict(finding))
                persona_reports[name] = {
                    'personaId': doc.id,
                    'summary': summary,
                    'findings': findings,
                }

        # No longer need to run screenshot_reviewer here as we did it upfront
        await run_native_consolidator(audit_id, persona_reports)
        
    except Exception as e:
        print(f"Error running audit {audit_id}: {e}")
        traceback.print_exc()
        if db:
            db.collection('audits').document(audit_id).set({'status': 'error', 'errorMsg': str(e)}, merge=True)

@app.post("/api/run_audit")
async def trigger_audit(request: Request, background_tasks: BackgroundTasks):
    api_secret = request.headers.get("X-Api-Secret")
    expected_secret = os.environ.get("AGENT_API_SECRET")
    
    if expected_secret and api_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Unauthorized API Secret")
        
    data = await request.json()
    audit_id = data.get("auditId")
    url = data.get("url")
    persona_ids = data.get("personaIds", [])
    custom_personas = data.get("customPersonas", [])
    user_id = data.get("userId", "anonymous")

    raw_auth = {
        "loginUrl": data.get("loginUrl", "").strip(),
        "loginEmail": data.get("loginEmail", "").strip(),
        "loginPassword": data.get("loginPassword", ""),
    }
    auth = raw_auth if all(raw_auth.values()) else None

    if not audit_id or not url:
        raise HTTPException(status_code=400, detail="Missing auditId or url")

    background_tasks.add_task(run_audit_background, audit_id, url, persona_ids, user_id, custom_personas, auth)
    
    return {"status": "accepted", "audit_id": audit_id}
