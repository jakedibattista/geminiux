import asyncio
import json
import mimetypes
import urllib.request

import firebase_admin
from firebase_admin import firestore
from google import genai
from google.genai import types


SCREENSHOT_REVIEW_MODEL = "gemini-2.5-flash"
MAX_REVIEW_CONCURRENCY = 3


def _audit_doc_ref(audit_id: str):
    return firestore.client().collection("audits").document(audit_id)


def _set_screenshot_review_state(audit_id: str, **fields):
    if not firebase_admin._apps:
        print(f"[ScreenshotReviewer] Mock state update for {audit_id}: {fields}")
        return

    _audit_doc_ref(audit_id).set(
        {
            "mediaArtifacts": {
                "screenshotReview": fields,
            }
        },
        merge=True,
    )


def _clean_url(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _review_schema() -> dict:
    return {
        "type": "object",
        "required": [
            "approved",
            "qualityScore",
            "visualAppeal",
            "missingImagesOrFrames",
            "issues",
            "summary",
        ],
        "properties": {
            "approved": {"type": "boolean"},
            "qualityScore": {"type": "integer"},
            "visualAppeal": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "missingImagesOrFrames": {"type": "boolean"},
            "issues": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
            },
            "summary": {"type": "string"},
        },
    }


def _download_image_bytes(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AuditMySite Screenshot Reviewer",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        image_bytes = response.read()
        mime_type = response.headers.get_content_type() or mimetypes.guess_type(url)[0] or "image/png"
    return image_bytes, mime_type


def _review_screenshot_sync(url: str, image_bytes: bytes, mime_type: str) -> dict:
    instruction = """
You are a screenshot QA reviewer for an automated UX audit.

Your job is to decide whether this screenshot is safe to use as evidence in a polished founder presentation.

Approve the screenshot only if it is:
- visually clear, legible, and presentation-ready
- representative of a real page state
- reasonably polished and presentation-ready
- free of obvious rendering failures or broken visual containers

Reject the screenshot if it shows any of the following:
- blank product images or missing content
- empty device frames (phone, tablet, browser, etc.)
- loading placeholders or dominant loading skeletons
- major clipping, corruption, blur, or awkward composition that makes it poor presentation material

Important:
- Ignore normal whitespace, minimalist layouts, and intentionally simple design.
- Be conservative about approving screenshots for presentation use.
- Return JSON only.
""".strip()

    client = genai.Client()
    response = client.models.generate_content(
        model=SCREENSHOT_REVIEW_MODEL,
        contents=[
            instruction,
            f"Review this screenshot URL for evidence quality: {url}",
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=_review_schema(),
            temperature=0.0,
        ),
    )

    parsed = response.parsed or json.loads((response.text or "").strip())
    review = {
        "screenshotUrl": url,
        "approved": bool(parsed.get("approved")),
        "qualityScore": max(0, min(int(parsed.get("qualityScore", 0)), 100)),
        "visualAppeal": str(parsed.get("visualAppeal", "low")).strip().lower(),
        "missingImagesOrFrames": bool(parsed.get("missingImagesOrFrames")),
        "issues": [str(item).strip() for item in (parsed.get("issues") or []) if str(item).strip()],
        "summary": str(parsed.get("summary", "")).strip(),
    }
    if review["visualAppeal"] not in {"high", "medium", "low"}:
        review["visualAppeal"] = "low"
    if not review["summary"]:
        review["summary"] = "Screenshot review completed."
    return review


def _failed_review(url: str, reason: str) -> dict:
    return {
        "screenshotUrl": url,
        "approved": False,
        "qualityScore": 0,
        "visualAppeal": "low",
        "missingImagesOrFrames": True,
        "issues": [reason[:240]],
        "summary": "Screenshot could not be validated and was excluded from reviewed evidence.",
    }


def _sample_rejection_issues(reviews: list[dict]) -> list[str]:
    samples: list[str] = []
    seen: set[str] = set()
    for review in reviews:
        if review.get("approved"):
            continue
        for issue in review.get("issues", []) or []:
            cleaned = str(issue).strip()
            signature = cleaned.lower()
            if not cleaned or signature in seen:
                continue
            seen.add(signature)
            samples.append(cleaned)
            if len(samples) >= 5:
                return samples
    return samples


async def _review_single_screenshot(url: str, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        try:
            image_bytes, mime_type = await asyncio.to_thread(_download_image_bytes, url)
            return await asyncio.to_thread(_review_screenshot_sync, url, image_bytes, mime_type)
        except Exception as exc:
            return _failed_review(url, f"review_error: {exc}")


async def review_urls(audit_id: str, urls: list[str]) -> dict[str, dict]:
    """
    Review a list of screenshot URLs and return a mapping of URL to review results.
    """
    if not urls:
        return {}

    _set_screenshot_review_state(
        audit_id,
        status="reviewing",
        totalScreenshots=len(urls),
        reviewedCount=0,
        approvedCount=0,
        rejectedCount=0,
        sampleIssues=[],
        error=None,
    )

    try:
        semaphore = asyncio.Semaphore(MAX_REVIEW_CONCURRENCY)
        reviews = await asyncio.gather(
            *[_review_single_screenshot(url, semaphore) for url in sorted(set(urls))]
        )
        reviews_by_url = {
            _clean_url(review.get("screenshotUrl")): review
            for review in reviews
            if _clean_url(review.get("screenshotUrl"))
        }

        _set_screenshot_review_state(
            audit_id,
            status="ready",
            totalScreenshots=len(urls),
            reviewedCount=len(reviews),
            approvedCount=sum(1 for review in reviews if review.get("approved")),
            rejectedCount=sum(1 for review in reviews if not review.get("approved")),
            sampleIssues=_sample_rejection_issues(reviews),
            reviews=reviews_by_url,
            error=None,
            reviewedAt=firestore.SERVER_TIMESTAMP if firebase_admin._apps else None,
        )
        return reviews_by_url
    except Exception as exc:
        _set_screenshot_review_state(
            audit_id,
            status="error",
            totalScreenshots=len(urls),
            reviewedCount=0,
            approvedCount=0,
            rejectedCount=0,
            sampleIssues=[],
            error=str(exc),
        )
        raise


async def run_screenshot_reviewer(audit_id: str, persona_reports: dict) -> dict:
    screenshot_urls: set[str] = set()
    for report in persona_reports.values():
        for finding in report.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            screenshot_url = _clean_url(finding.get("screenshotUrl"))
            if screenshot_url:
                screenshot_urls.add(screenshot_url)

    if not screenshot_urls:
        _set_screenshot_review_state(
            audit_id,
            status="ready",
            totalScreenshots=0,
            reviewedCount=0,
            approvedCount=0,
            rejectedCount=0,
            sampleIssues=[],
            error=None,
            reviewedAt=firestore.SERVER_TIMESTAMP if firebase_admin._apps else None,
        )
        return persona_reports

    try:
        reviews_by_url = await review_urls(audit_id, list(screenshot_urls))

        reviewed_reports: dict = {}
        approved_count = 0
        rejected_count = 0

        for persona_name, report in persona_reports.items():
            updated_report = dict(report)
            updated_findings = []
            persona_approved_urls: set[str] = set()
            persona_review_entries: list[dict] = []

            for finding in report.get("findings", []) or []:
                if not isinstance(finding, dict):
                    updated_findings.append(finding)
                    continue

                updated_finding = dict(finding)
                screenshot_url = _clean_url(updated_finding.get("screenshotUrl"))
                review = dict(reviews_by_url.get(screenshot_url) or {}) if screenshot_url else {}

                if review:
                    persona_review_entries.append(review)
                    updated_finding["screenshotReview"] = {
                        "approved": review.get("approved"),
                        "qualityScore": review.get("qualityScore"),
                        "visualAppeal": review.get("visualAppeal"),
                        "missingImagesOrFrames": review.get("missingImagesOrFrames"),
                        "issues": review.get("issues"),
                        "summary": review.get("summary"),
                    }

                if screenshot_url and review and not review.get("approved"):
                    updated_finding["screenshotUrl"] = None
                    rejected_count += 1
                elif screenshot_url and review.get("approved"):
                    persona_approved_urls.add(screenshot_url)
                    approved_count += 1
                elif screenshot_url and review.get("approved") is None:
                    # In case of no explicit approval status, keep the screenshot but don't count it as approved/rejected
                    pass

                updated_findings.append(updated_finding)

            updated_report["findings"] = updated_findings
            reviewed_reports[persona_name] = updated_report

            if firebase_admin._apps and report.get("personaId"):
                doc_ref = (
                    _audit_doc_ref(audit_id)
                    .collection("agentReports")
                    .document(report["personaId"])
                )
                snapshot = doc_ref.get()
                existing = snapshot.to_dict() if snapshot.exists else {}
                page_screenshots = existing.get("pageScreenshots", {}) or {}
                filtered_page_screenshots = {
                    page_key: url
                    for page_key, url in page_screenshots.items()
                    if _clean_url(url) in persona_approved_urls
                }
                latest_screenshot = _clean_url(existing.get("latestScreenshot"))
                latest_page = existing.get("latestScreenshotPage") if latest_screenshot in persona_approved_urls else None

                doc_ref.set(
                    {
                        "findings": updated_findings,
                        "pageScreenshots": filtered_page_screenshots,
                        "latestScreenshot": latest_screenshot if latest_screenshot in persona_approved_urls else None,
                        "latestScreenshotPage": latest_page,
                        "screenshotReview": {
                            "status": "ready",
                            "reviewedCount": len(persona_review_entries),
                            "approvedCount": sum(1 for item in persona_review_entries if item.get("approved")),
                            "rejectedCount": sum(1 for item in persona_review_entries if not item.get("approved")),
                            "reviews": persona_review_entries,
                        },
                    },
                    merge=True,
                )

        return reviewed_reports
    except Exception as exc:
        print(f"Error in run_screenshot_reviewer: {exc}")
        raise
