import json
import firebase_admin
from firebase_admin import firestore
from google import genai
from google.genai import types
from agents.consolidator import write_final_report_to_db
from agents.audit_recap import generate_audio_presentation


def _build_consolidator_payload(persona_reports: dict) -> str:
    sections: list[str] = []

    for persona_name, report in persona_reports.items():
        findings = report.get("findings", []) or []
        summary = (report.get("summary") or "").strip()

        lines = [f"=== {persona_name} ==="]
        if findings:
            lines.append("Evidence-backed findings:")
            for finding in findings:
                page_url = finding.get("pageUrl") or "unknown page"
                text = (finding.get("text") or "").strip()
                lines.append(f"- [{page_url}] {text}")
        else:
            lines.append("Evidence-backed findings: none logged.")

        if summary:
            lines.append("")
            lines.append("Optional persona summary (secondary context only; do not use it to invent unsupported issues):")
            lines.append(summary)

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


async def run_native_consolidator(audit_id: str, persona_reports: dict):
    """
    Native implementation of the Consolidator using GenAI SDK directly.
    """
    # Build the prompt payload
    consolidator_payload = _build_consolidator_payload(persona_reports)
    
    instruction = """
    You are the Lead UX Researcher.
    Several UX Persona Agents have just finished auditing a website and returning evidence-backed findings.

    Your task:
    1. Read the provided evidence-backed findings from all persona agents.
    2. Synthesize these findings into a single, cohesive executive report.
    3. Generate an overall UX Score out of 100 based on the severity of issues found.
    4. You MUST return a valid JSON string matching this schema exactly (no markdown formatting blocks):
       {
           "summary": "2-3 paragraph executive summary",
           "score": 85,
           "criticalIssues": [
               "Title (Persona A, Persona B): Specific description.",
               "Title (Persona C): Specific description."
           ],
           "recommendations": [
               "Title (Persona A, Persona B): Specific actionable recommendation.",
               "Title (Persona C): Specific actionable recommendation."
           ],
           "positives": [
               "Title (Persona A, Persona B): Specific description of what worked well.",
               "Title (Persona C): Specific description of what worked well."
           ]
       }

    STRICT RULES for criticalIssues, recommendations, and positives:
    - Format MUST be: "Short Title (Persona 1, Persona 2): Description"
    - If multiple personas flagged the same issue, combine into ONE entry listing all personas.
    - Descriptions MUST be SPECIFIC and concrete — reference the EXACT element, page section,
      copy, or interaction the agents observed.
    - ONLY use evidence-backed findings as source-of-truth. Persona summaries are secondary context only.
    - NEVER introduce an issue unless it is supported by at least one evidence-backed finding above.
    - IGNORE simulator artifacts and unsupported claims such as broken demos, dead links, rendering failures,
      empty device frames, or other interaction failures unless they appear in the evidence-backed findings.
    - criticalIssues: the top 3-5 most impactful problems, ordered by severity.
    - recommendations: one concrete, actionable fix per critical issue.
    - positives: 2-4 things that genuinely worked well for users.
    """

    prompt = (
        "Here are the evidence-backed findings from the agents, plus optional summary context.\n\n"
        f"{consolidator_payload}\n\n"
        "Generate the final JSON report."
    )

    print(f"[Consolidator] Generating report for audit {audit_id}...")
    client = genai.Client()
    
    # Add retry logic for the main report generation (hitting transient 500s in preview)
    response = None
    last_err = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-2.5-pro',
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=instruction,
                    temperature=0.2
                )
            )
            break
        except Exception as e:
            last_err = e
            if "500" in str(e) or "429" in str(e) or "INTERNAL" in str(e):
                import asyncio
                await asyncio.sleep(2 ** attempt)
                continue
            raise e
    
    if not response:
        raise last_err or RuntimeError("Report generation failed across all attempts.")
    
    # Clean up markdown JSON blocks if present
    raw_text = response.text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.startswith("```"):
        raw_text = raw_text[3:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]

    write_result = write_final_report_to_db(audit_id, raw_text.strip())
    report_data = write_result.get("reportData", {})

    audit_url = ""
    if firebase_admin._apps:
        db = firestore.client()
        audit_snapshot = db.collection("audits").document(audit_id).get()
        if audit_snapshot.exists:
            audit_url = (audit_snapshot.to_dict() or {}).get("url", "")

    try:
        await generate_audio_presentation(audit_id, audit_url, report_data, persona_reports)
    except Exception as presentation_error:
        print(f"[Consolidator] Presentation generation failed for audit {audit_id}: {presentation_error}")

    print(f"[Consolidator] Finished report for audit {audit_id}")
