import json
import firebase_admin
from firebase_admin import firestore
from google.adk.agents import LlmAgent

def write_final_report_to_db(audit_id: str, report_json: str):
    """
    Saves the final synthesized report to Firestore and marks the persona agents 
    as officially completed so the frontend knows to switch tabs.
    """
    if not firebase_admin._apps:
        print(f"Mock Final Report Write: {report_json}")
        try:
            mock_report_data = json.loads(report_json)
        except json.JSONDecodeError:
            mock_report_data = {"summary": report_json}
        return {"status": "success", "reportData": mock_report_data}
        
    db = firestore.client()
    
    try:
        report_data = json.loads(report_json)
    except json.JSONDecodeError:
        # Fallback if the LLM output raw text instead of JSON
        report_data = {
            "summary": report_json,
            "score": 50,
            "criticalIssues": ["Failed to parse structured JSON from consolidator"],
            "recommendations": [],
            "positives": []
        }
    
    # 1. Update the main audit doc
    db.collection('audits').document(audit_id).set({
        'consolidatedReport': report_data,
        'status': 'completed'
    }, merge=True)
    
    # 2. Ensure all sub-agents are marked completed so the frontend progress bar fills
    agent_reports_ref = db.collection('audits').document(audit_id).collection('agentReports')
    docs = agent_reports_ref.stream()
    for doc in docs:
        if doc.to_dict().get('status') != 'completed':
            doc.reference.update({'status': 'completed', 'currentAction': 'Done'})
            
    return {"status": "success", "reportData": report_data}


def make_consolidator_agent(audit_id: str):
    """
    Creates an LlmAgent that runs after the ParallelAgent finishes.
    It reads the session state (where all persona outputs were saved via their output_keys)
    and generates a final JSON report.
    """
    
    def save_report(report_json: str) -> dict:
        """
        Saves the final consolidated report to the database.
        
        Args:
            report_json (str): A raw JSON string containing: summary (str), score (int 0-100), criticalIssues (list of str), recommendations (list of str).
        """
        return write_final_report_to_db(audit_id, report_json)

    instruction = """
    You are the Lead UX Researcher.
    Several UX Persona Agents have just finished auditing a website and returning their individual findings.
    The persona agent names map as follows — use the FULL human-readable name in your output:
      - report_p_first_time    → "First-Time Visitor"
      - report_p_mobile        → "Mobile User"
      - report_p_accessibility → "Accessibility User"
      - report_p_non_technical → "Non-Technical User"
      - report_p_power_user    → "Power User"
    For any custom persona key, derive the name from the report key itself.

    Your task:
    1. Read the session state context which contains the outputs from all persona agents.
    2. Synthesize these findings into a single, cohesive executive report.
    3. Generate an overall UX Score out of 100 based on the severity of issues found.
    4. You MUST use the `save_report` tool to save your final output.
    5. The `report_json` argument MUST be a valid JSON string matching this schema exactly:
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
      copy, or interaction the agents observed. Generic statements like "improve readability" or
      "enhance navigation" are NOT acceptable. Instead write things like:
        BAD:  "Poor Readability (Mobile User): Text is hard to read."
        GOOD: "Header Nav Link Contrast (Mobile User, Accessibility User): The white navigation
               links on the light-grey header background fail WCAG AA contrast (estimated ratio
               ~2.1:1 vs the required 4.5:1), making them nearly invisible on mobile screens."
    - criticalIssues: the top 3-5 most impactful problems, ordered by severity.
    - recommendations: one concrete, actionable fix per critical issue (matched 1-to-1 where possible).
    - positives: 2-4 things that genuinely worked well for users — do not skip this section.
    """
    
    return LlmAgent(
        name="consolidator_agent",
        model="gemini-2.5-pro",
        instruction=instruction,
        tools=[save_report]
    )