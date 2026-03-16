import firebase_admin
from firebase_admin import firestore
import os
os.environ["GOOGLE_CLOUD_PROJECT"] = "auditmysite-61bd1"
firebase_admin.initialize_app()
db = firestore.client()
audits = db.collection("audits").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(1).stream()
for a in audits:
    print(a.id)
    reports = a.reference.collection("agentReports").stream()
    for r in reports:
        findings = r.to_dict().get("findings", [])
        print(f"  {r.id}: {len(findings)} findings")
        for f in findings:
            print(f"    {f.get('screenshotUrl')}")
