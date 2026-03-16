import asyncio
import json
import os
import time
import traceback
import mimetypes
import urllib.request
from urllib.parse import urlparse, unquote
from typing import Any

import firebase_admin
from firebase_admin import firestore

from google import genai
from google.genai import types

ALLOWED_FINDING_CATEGORIES = [
    "copy_clarity",
    "missing_information",
    "information_architecture",
    "visual_hierarchy",
    "readability",
    "emotional_journey",
    "content_completeness",
    "messaging_consistency",
]

ALLOWED_FINDING_SENTIMENTS = ["positive", "negative"]

PERSONAS = {
    'p_first_time': "First-Time Visitor: You have zero context, low patience, and want to know what the site does immediately.",
    'p_mobile': "Mobile User: You are on a small screen, using one hand, and hate tiny touch targets or horizontal scrolling.",
    'p_accessibility': "Accessibility User: You rely on clear contrast, readable fonts, and obvious visual hierarchy.",
    'p_non_technical': "Non-Technical User: You are easily confused by industry jargon and complex UI patterns.",
    'p_power_user': "Power User: You want to find advanced features quickly and skip basic onboarding.",
}

def _get_persona_display_name(persona_id: str, custom_persona_data: dict = None) -> str:
    if custom_persona_data and custom_persona_data.get('name'):
        return custom_persona_data.get('name')
    name_map = {
        'p_first_time': 'First-Time Visitor',
        'p_mobile': 'Mobile User',
        'p_accessibility': 'Accessibility User',
        'p_non_technical': 'Non-Technical User',
        'p_power_user': 'Power User',
    }
    return name_map.get(persona_id, persona_id.replace('p_', '').replace('_', ' ').title())

def normalize_persona_quote(text: str) -> str:
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    return text


def _looks_like_image_url(url: str | None) -> bool:
    if not isinstance(url, str):
        return False
    cleaned = url.strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if lowered.startswith("data:image/"):
        return True
    try:
        parsed = urlparse(cleaned)
    except Exception:
        return False
    path = unquote(parsed.path or "").lower()
    return any(ext in path for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"))

def report_finding(
    audit_id: str,
    persona_id: str,
    finding: str,
    action: str,
    custom_persona_data: dict,
    page_url: str,
    explicit_screenshot_url: str = None,
    x: int = -1,
    y: int = -1,
    evidence_backed: bool = True,
    category: str = None,
    sentiment: str = None,
    page_label: str = None,
) -> dict:
    if not firebase_admin._apps:
        return {"status": "success"}

    db = firestore.client()
    doc_ref = db.collection('audits').document(audit_id).collection('agentReports').document(persona_id)
    
    finding_obj = {
        "text": finding,
        "action": action,
        "pageUrl": page_url,
        "evidenceBacked": evidence_backed,
    }
    if explicit_screenshot_url:
        finding_obj["screenshotUrl"] = explicit_screenshot_url
    if x >= 0 and y >= 0:
        finding_obj["x"] = x
        finding_obj["y"] = y
    if category:
        finding_obj["category"] = category
    if sentiment:
        finding_obj["sentiment"] = sentiment
    if page_label:
        finding_obj["pageLabel"] = page_label
        
    doc_ref.set({
        "findings": firestore.firestore.ArrayUnion([finding_obj])
    }, merge=True)
    
    return {"status": "success"}

def get_browser_tools():
    log_issue_description = (
        "Log a content or presentation finding. The finding must be a short first-person quote that sounds like this persona speaking out loud, not analyst prose. "
        "Only call this for observations about copy, messaging, layout, readability, missing information, or content quality. "
        "You must provide the page_url and the explicit screenshot_url that best supports this finding."
    )
    return [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="log_issue",
                description=log_issue_description,
                parameters_json_schema={
                    "type": "object", 
                    "properties": {
                        "finding": {"type": "string", "description": "Short first-person quote this persona would realistically say out loud"},
                        "page_url": {"type": "string", "description": "Exact URL of the page this finding refers to"},
                        "screenshot_url": {"type": "string", "description": "The specific screenshot URL from the payload that supports this finding"},
                        "sentiment": {"type": "string", "enum": ["positive", "negative"]},
                        "category": {
                            "type": "string",
                            "enum": ALLOWED_FINDING_CATEGORIES
                        },
                        "page_label": {"type": "string", "description": "The label or name of the page this finding refers to, such as 'Homepage' or 'Pricing'"}
                    }, 
                    "required": ["finding", "page_url", "screenshot_url", "page_label"]
                }
            ),
            types.FunctionDeclaration(
                name="finish",
                description="Call this ONLY after you have logged all individual findings via `log_issue`. The summary should be 2-3 sentences max.",
                parameters_json_schema={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}
            )
        ])
    ]

async def run_persona_agent(persona_id: str, audit_id: str, target_url: str, crawled_pages: list, custom_persona_data: dict = None):
    """
    Phase 2 map/reduce: Pure Reviewer Agent.
    Takes a batch of screenshots and evaluates them in a single pass without Playwright.
    """
    if custom_persona_data:
        persona_desc = f"{custom_persona_data.get('name')}: {custom_persona_data.get('description')}. Goals: {', '.join(custom_persona_data.get('goals', []))}"
        device_type = custom_persona_data.get('deviceType', 'desktop')
    else:
        persona_desc = PERSONAS.get(persona_id, "Generic User")
        device_type = "mobile" if "mobile" in persona_id else "desktop"

    doc_ref = None
    db = firestore.client() if firebase_admin._apps else None
    if db:
        doc_ref = db.collection('audits').document(audit_id).collection('agentReports').document(persona_id)
        doc_ref.set({
            'personaName': _get_persona_display_name(persona_id, custom_persona_data),
            'status': 'running',
            'currentAction': 'Reviewing screenshots...',
            'findingsCount': 0,
        }, merge=True)

    try:
        # Determine which screenshot set to use based on persona device type
        screenshot_key = "mobile_screenshots" if device_type == "mobile" else "desktop_screenshots"
        
        # Download all screenshots in parallel to attach them to the prompt
        all_screenshot_urls = []
        for page in crawled_pages:
            # Fallback to 'screenshots' if device-specific ones aren't found
            urls = page.get(screenshot_key, page.get("screenshots", []))
            all_screenshot_urls.extend(urls)
        valid_screenshot_urls = {url for url in all_screenshot_urls if _looks_like_image_url(url)}
        
        async def _download_screenshot_part(url: str) -> tuple[str, types.Part]:
            def _download():
                try:
                    request = urllib.request.Request(
                        url,
                        headers={"User-Agent": "AuditMySite Persona Reviewer"},
                    )
                    with urllib.request.urlopen(request, timeout=15) as response:
                        data = response.read()
                        mime_type = response.headers.get_content_type() or mimetypes.guess_type(url)[0] or "image/png"
                        return types.Part.from_bytes(data=data, mime_type=mime_type)
                except Exception as e:
                    print(f"[{persona_id}] Failed to download screenshot {url}: {e}")
                    return None
            part = await asyncio.to_thread(_download)
            return (url, part)

        print(f"[{persona_id}] Downloading {len(all_screenshot_urls)} screenshots...")
        download_results = await asyncio.gather(*[_download_screenshot_part(u) for u in all_screenshot_urls])
        parts_by_url = {u: p for u, p in download_results if p is not None}

        system_instruction = f"""
    You are an AI Content & UX Researcher acting as this persona: {persona_desc}

    You are reviewing a set of screenshots captured from {target_url}. 
    Imagine you are experiencing this website for the first time as this specific persona.

    ══════════════════════════════════════════════════════
    YOUR ONLY JOB IS TO EVALUATE CONTENT AND PRESENTATION.
    ══════════════════════════════════════════════════════
    Every finding you log must sound like a real person speaking in character, in first person.
    Write short quotes such as "I still don't understand what this product does" or "I feel reassured by the simple pricing copy."
    Do NOT write analyst prose like "The homepage lacks clarity" or "Visual hierarchy is weak."
    Avoid generic quotes. Your quotes must reflect this persona's unique needs, frustrations, and decision criteria.
    Reference SPECIFIC elements you see: exact headlines, button labels, section names, layout choices, image content.

    WHAT YOU ARE HERE TO EVALUATE:
    ✓ Copy clarity — Is the headline and value proposition immediately clear for your persona?
    ✓ Missing information — What specific questions does your persona have that the page fails to answer?
    ✓ Information architecture — Is the content organized in a way that makes sense for this persona's goals?
    ✓ Visual hierarchy — Is the most important content visually prominent and easy to scan?
    ✓ Readability — Is the text readable? Is the layout cluttered or overwhelming?
    ✓ Emotional journey — Does the content make your persona feel confident, curious, confused, or skeptical?
    ✓ Content completeness — What is missing that this persona would need to make a decision?
    ✓ Messaging consistency — Do different sections of the page contradict each other or feel disjointed?

    WORKFLOW:
    1. Study each screenshot carefully. Read the actual copy. Notice what is prominent, what is buried, what is missing.
    2. For EVERY screenshot provided, you MUST call `log_issue` a MINIMUM OF 2 TIMES with distinct, specific findings.
       - Each finding must address a different element or concern visible in that screenshot.
       - Do NOT repeat the same observation twice in different words.
       - Be specific: quote actual text you see, name actual buttons or sections, describe actual layout problems.
    3. You must provide the `page_url` and the exact `screenshot_url` for every finding.
    4. Call `finish` ONLY after you have logged at least 2 findings per screenshot for ALL screenshots.

    THOROUGHNESS IS REQUIRED. A lazy review with only one generic finding per screenshot is a failure.
    Real users form multiple impressions from a single page. So should you.
    """

        client = genai.Client()
        chat = client.aio.chats.create(
            model="gemini-3.1-pro-preview",
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                tools=get_browser_tools(),
                temperature=0.5,
            )
        )

        prompt_parts = ["Please review the following pages and their screenshots. For EVERY screenshot, log at least 2 distinct findings via `log_issue` before moving on.\n\n"]
        for page in crawled_pages:
            prompt_parts.append(f"Page URL: {page['url']}\nLabel: {page.get('label', 'Unknown')}\n")
            # Use device-specific screenshots if available
            urls = page.get(screenshot_key, page.get("screenshots", []))
            for i, url in enumerate(urls, start=1):
                part = parts_by_url.get(url)
                if part:
                    prompt_parts.append(f"Screenshot {i} of {len(urls)} for this page — URL: {url}\nLog at least 2 findings for this screenshot before continuing.\n")
                    prompt_parts.append(part)
                else:
                    prompt_parts.append(f"(Image failed to load): {url}\n")
            prompt_parts.append("\n---\n")
            
        print(f"[{persona_id}] Sending payload to model for review (including {len(parts_by_url)} images)...")
        response = await asyncio.wait_for(chat.send_message(prompt_parts), timeout=240.0)

        findings_logged_count = 0
        summary = ""
        
        if response.function_calls:
            for call in response.function_calls:
                if call.name == "log_issue":
                    finding = call.args.get("finding", "")
                    action = call.args.get("action", "Reviewing")
                    page_url = call.args.get("page_url", target_url)
                    screenshot_url = call.args.get("screenshot_url") # Not natively strictly passed by Gemini unless prompted, but we can fallback
                    sentiment = str(call.args.get("sentiment", "")).strip().lower()
                    category = str(call.args.get("category", "")).strip().lower()
                    page_label = call.args.get("page_label", "")

                    normalized_finding = normalize_persona_quote(finding)
                    if category not in ALLOWED_FINDING_CATEGORIES:
                        category = None
                    if sentiment not in ALLOWED_FINDING_SENTIMENTS:
                        sentiment = None

                    # Only trust screenshot URLs that match the actual crawled screenshot payload.
                    resolved_screenshot_url = screenshot_url if screenshot_url in valid_screenshot_urls else None
                    if not resolved_screenshot_url:
                        for p in crawled_pages:
                            # Use device-specific screenshots if available
                            urls = p.get(screenshot_key, p.get("screenshots", []))
                            if p.get('url') == page_url and urls:
                                resolved_screenshot_url = urls[0]
                                break
                    if not resolved_screenshot_url and crawled_pages:
                         # Use device-specific screenshots if available
                         first_page_urls = crawled_pages[0].get(screenshot_key, crawled_pages[0].get("screenshots", []))
                         if first_page_urls:
                             resolved_screenshot_url = first_page_urls[0]
                        
                    report_finding(
                        audit_id,
                        persona_id,
                        normalized_finding,
                        action,
                        custom_persona_data,
                        page_url,
                        explicit_screenshot_url=resolved_screenshot_url,
                        evidence_backed=True,
                        category=category,
                        sentiment=sentiment,
                        page_label=page_label,
                    )
                    findings_logged_count += 1
                elif call.name == "finish":
                    summary = call.args.get("summary", "")

        if doc_ref:
            doc_ref.set({
                'status': 'completed',
                'currentAction': 'Done',
                'summary': summary,
                'findingsCount': findings_logged_count,
            }, merge=True)
            
    except Exception as e:
        print(f"[{persona_id}] Fatal Error: {e}")
        traceback.print_exc()
        if doc_ref:
            doc_ref.set({
                'status': 'error',
                'currentAction': f'Error: {str(e)}',
            }, merge=True)
            
    return {"status": "completed"}
