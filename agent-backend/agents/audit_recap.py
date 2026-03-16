import asyncio
import io
import mimetypes
import os
import re
import time
import urllib.parse
import uuid
import wave
import json
from urllib.parse import urlparse, unquote

import firebase_admin
from firebase_admin import firestore, storage
from google import genai
from google.genai import types


DEFAULT_AUDIO_MODEL = "gemini-2.5-flash-preview-tts"
DEFAULT_AUDIO_VOICE = "charon"
MAX_SUPPORTING_FINDINGS = 4
MAX_PRESENTATION_EVIDENCE_SLIDES = 2
MAX_PRESENTATION_BULLETS = 3
MAX_BULLET_WORDS = 15
VERTEX_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
PRESENTATION_TEXT_MODEL = "gemini-3.1-pro-preview"
PRESENTATION_TEXT_MODEL_FALLBACK = "gemini-3-pro-preview"
PRESENTATION_IMAGE_MODEL = "gemini-3-pro-image-preview"
PRESENTATION_IMAGE_MODEL_FALLBACK = "gemini-2.5-flash-image"


def _artifact_doc_ref(audit_id: str):
    db = firestore.client()
    return db.collection("audits").document(audit_id)


def _get_existing_media_artifact(audit_id: str, artifact_key: str) -> dict:
    if not firebase_admin._apps:
        return {}

    snapshot = _artifact_doc_ref(audit_id).get()
    if not snapshot.exists:
        return {}

    data = snapshot.to_dict() or {}
    return ((data.get("mediaArtifacts") or {}).get(artifact_key) or {})


def _set_media_artifact_state(audit_id: str, artifact_key: str, **fields):
    if not firebase_admin._apps:
        print(f"[AuditRecap] Mock state update for {audit_id} ({artifact_key}): {fields}")
        return

    doc_ref = _artifact_doc_ref(audit_id)
    payload = {
        "mediaArtifacts": {
            artifact_key: fields,
        }
    }
    doc_ref.set(payload, merge=True)


def _get_existing_presentation(audit_id: str) -> dict:
    return _get_existing_media_artifact(audit_id, "presentation")


def _set_presentation_state(audit_id: str, **fields):
    _set_media_artifact_state(audit_id, "presentation", **fields)


def _make_vertex_client() -> genai.Client:
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise ValueError("GOOGLE_CLOUD_PROJECT is required for Vertex AI presentation generation.")

    return genai.Client(
        vertexai=True,
        project=project,
        location=VERTEX_LOCATION,
        http_options=types.HttpOptions(api_version="v1"),
    )


def _clean_line(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.strip().split())


def _is_image_like_url(url: str | None) -> bool:
    cleaned = _clean_line(url)
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


def _friendly_site_name(audit_url: str) -> str:
    hostname = urlparse(audit_url).hostname or audit_url
    hostname = hostname.removeprefix("www.")
    root = hostname.split(".")[0]
    words = [part for part in re.split(r"[-_]+", root) if part]
    label = " ".join(word.capitalize() for word in words)
    return label or hostname


def _normalize_page_key(page_url: str | None) -> str:
    cleaned = _clean_line(page_url)
    if not cleaned:
        return ""
    try:
        parsed = urlparse(cleaned)
        path = re.sub(r"/+$", "", parsed.path or "") or "/"
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    except Exception:
        return cleaned


def _build_presentation_screenshot_maps(crawled_pages: list[dict] | None) -> tuple[dict[str, str], dict[str, str]]:
    by_page: dict[str, str] = {}
    by_source: dict[str, str] = {}

    for page in crawled_pages or []:
        page_url = _clean_line(page.get("url"))
        if not page_url:
            continue

        page_key = _normalize_page_key(page_url)
        desktop_screenshots = [
            _clean_line(url) for url in page.get("desktop_screenshots", []) or []
            if _is_image_like_url(url)
        ]
        mobile_screenshots = [
            _clean_line(url) for url in page.get("mobile_screenshots", []) or []
            if _is_image_like_url(url)
        ]
        desktop_preview = _clean_line(page.get("desktop_presentation_screenshot"))
        mobile_preview = _clean_line(page.get("mobile_presentation_screenshot"))

        if not _is_image_like_url(desktop_preview):
            desktop_preview = desktop_screenshots[0] if desktop_screenshots else ""
        if not _is_image_like_url(mobile_preview):
            mobile_preview = mobile_screenshots[0] if mobile_screenshots else ""

        default_preview = desktop_preview or mobile_preview
        if default_preview:
            by_page[page_key] = default_preview

        for url in desktop_screenshots:
            by_source[url] = desktop_preview or default_preview or url
        for url in mobile_screenshots:
            by_source[url] = mobile_preview or default_preview or url

        if desktop_preview:
            by_source[desktop_preview] = desktop_preview
        if mobile_preview:
            by_source[mobile_preview] = mobile_preview

    return by_page, by_source


def _preferred_presentation_screenshot(
    page_url: str | None,
    source_url: str | None,
    presentation_screenshot_by_page: dict[str, str] | None,
    presentation_screenshot_by_source: dict[str, str] | None,
) -> str:
    cleaned_source = _clean_line(source_url)
    if cleaned_source and presentation_screenshot_by_source:
        preferred_by_source = presentation_screenshot_by_source.get(cleaned_source)
        if _is_image_like_url(preferred_by_source):
            return preferred_by_source

    page_key = _normalize_page_key(page_url)
    if page_key and presentation_screenshot_by_page:
        preferred_by_page = presentation_screenshot_by_page.get(page_key)
        if _is_image_like_url(preferred_by_page):
            return preferred_by_page

    return cleaned_source


def _finding_has_approved_screenshot(finding: dict) -> bool:
    screenshot_url = _clean_line((finding or {}).get("screenshotUrl"))
    if not screenshot_url or not _is_image_like_url(screenshot_url):
        return False

    review = (finding or {}).get("screenshotReview")
    if isinstance(review, dict) and review.get("approved") is False:
        return False
    return True


def _pick_supporting_findings(
    persona_reports: dict,
    limit: int = MAX_SUPPORTING_FINDINGS,
    presentation_screenshot_by_page: dict[str, str] | None = None,
    presentation_screenshot_by_source: dict[str, str] | None = None,
) -> list[dict]:
    supporting: list[dict] = []
    seen_urls: set[str] = set()
    seen_texts: set[str] = set()

    for persona_name, report in persona_reports.items():
        for finding in report.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            screenshot_url = (finding.get("screenshotUrl") or "").strip()
            text = _clean_line(finding.get("text"))
            if not text or not _finding_has_approved_screenshot(finding):
                continue

            screenshot_url = _preferred_presentation_screenshot(
                finding.get("pageUrl"),
                screenshot_url,
                presentation_screenshot_by_page,
                presentation_screenshot_by_source,
            )
            if not _is_image_like_url(screenshot_url):
                continue

            signature = text.lower()
            if screenshot_url in seen_urls or signature in seen_texts:
                continue

            supporting.append({
                "personaName": persona_name,
                "text": text,
                "pageUrl": finding.get("pageUrl"),
                "pageLabel": finding.get("pageLabel"),
                "screenshotUrl": screenshot_url,
            })
            seen_urls.add(screenshot_url)
            seen_texts.add(signature)

            if len(supporting) >= limit:
                return supporting

    return supporting


def _top_lines(items: list[str], limit: int) -> list[str]:
    cleaned = [_clean_line(item) for item in items or []]
    return [item for item in cleaned if item][:limit]


def _clamp_words(text: str, max_words: int = MAX_BULLET_WORDS) -> str:
    words = _clean_line(text).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(",.;:") + "..."


def _split_sentences_or_clauses(text: str) -> list[str]:
    cleaned = _clean_line(text)
    if not cleaned:
        return []

    parts = re.split(r"(?<=[.!?])\s+|;\s+|:\s+|,\s+(?=[A-Z])", cleaned)
    return [_clean_line(part) for part in parts if _clean_line(part)]


def _attributed_parts(text: str) -> tuple[str, str]:
    cleaned = _clean_line(text)
    match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*:\s*(.+)$", cleaned)
    if match:
        return _clean_line(match.group(1)), _clean_line(match.group(3))
    return "", cleaned


def _dedupe_bullets(lines: list[str], limit: int = MAX_PRESENTATION_BULLETS) -> list[str]:
    seen: set[str] = set()
    bullets: list[str] = []
    for line in lines:
        cleaned = _clean_line(line).strip("- ").strip()
        if not cleaned:
            continue
        signature = cleaned.lower()
        if signature in seen:
            continue
        seen.add(signature)
        bullets.append(_clamp_words(cleaned))
        if len(bullets) >= limit:
            break
    return bullets


def _build_summary_bullets(summary: str, score) -> list[str]:
    bullets = [f"UX score: {score}/100"]
    bullets.extend(_split_sentences_or_clauses(summary))
    return _dedupe_bullets(bullets)


def _build_issue_bullets(items: list[str]) -> list[str]:
    bullets: list[str] = []
    for item in items[:MAX_PRESENTATION_BULLETS]:
        title, description = _attributed_parts(item)
        if title:
            bullets.append(title)
        if description:
            bullets.append(_split_sentences_or_clauses(description)[0])
    return _dedupe_bullets(bullets)


def _build_recommendation_bullets(items: list[str]) -> list[str]:
    bullets: list[str] = []
    for item in items[:MAX_PRESENTATION_BULLETS]:
        _, description = _attributed_parts(item)
        if description:
            bullets.append(_split_sentences_or_clauses(description)[0])
        else:
            bullets.append(item)
    return _dedupe_bullets(bullets)


def _build_positive_bullets(items: list[str]) -> list[str]:
    bullets: list[str] = []
    for item in items[:MAX_PRESENTATION_BULLETS]:
        title, description = _attributed_parts(item)
        if title:
            bullets.append(title)
        if description:
            bullets.append(_split_sentences_or_clauses(description)[0])
    return _dedupe_bullets(bullets)


def _build_evidence_bullets(finding_text: str) -> list[str]:
    parts = [
        part for part in _split_sentences_or_clauses(finding_text)
        if len(part.split()) >= 6
    ]
    return _dedupe_bullets(parts, limit=2)


def _make_slide(
    slide_id: str,
    title: str,
    body_lines: list[str],
    narration: str,
    eyebrow: str | None = None,
    screenshot_url: str | None = None,
    page_url: str | None = None,
    page_label: str | None = None,
    persona_name: str | None = None,
    visual_prompt: str | None = None,
) -> dict:
    slide = {
        "id": slide_id,
        "title": _clean_line(title),
        "bodyLines": [_clean_line(line) for line in body_lines if _clean_line(line)],
        "narration": _clean_line(narration),
    }
    if eyebrow:
        slide["eyebrow"] = _clean_line(eyebrow)
    if screenshot_url:
        slide["screenshotUrl"] = screenshot_url
    if page_url:
        slide["pageUrl"] = page_url
    if page_label:
        slide["pageLabel"] = page_label
    if persona_name:
        slide["personaName"] = persona_name
    if visual_prompt:
        slide["visualPrompt"] = visual_prompt
    return slide


def _keyword_set(*parts: str | None) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        cleaned = _clean_line(part).lower()
        for token in re.findall(r"[a-z0-9]+", cleaned):
            if len(token) < 3:
                continue
            if token in {
                "the", "and", "for", "with", "from", "that", "this", "your",
                "slide", "audit", "site", "user", "users", "page", "pages",
            }:
                continue
            tokens.add(token)
    return tokens


def _slide_visual_needs_grounding(slide: dict) -> bool:
    slide_id = _clean_line(slide.get("id")).lower()
    eyebrow = _clean_line(slide.get("eyebrow")).lower()
    title = _clean_line(slide.get("title")).lower()
    combined = " ".join(
        [slide_id, eyebrow, title, " ".join(slide.get("bodyLines", []) or [])]
    )

    grounding_terms = {
        "summary", "issue", "issues", "evidence", "recommendation", "recommendations",
        "strength", "strengths", "homepage", "contact", "team", "pricing", "demo",
        "flow", "flows", "navigation", "messaging", "product", "experience",
        "trust", "conversion", "improvement", "improve",
    }
    return any(term in combined for term in grounding_terms)


def _is_evidence_slide(slide: dict) -> bool:
    return _clean_line(slide.get("id")).lower().startswith("evidence-")


def _is_cross_persona_slide(slide: dict) -> bool:
    slide_id = _clean_line(slide.get("id")).lower()
    return slide_id in {"overview", "top-issues", "next-steps", "strengths"}


def _score_supporting_finding_for_slide(slide: dict, finding: dict) -> int:
    score = 0
    slide_keywords = _keyword_set(
        slide.get("id"),
        slide.get("eyebrow"),
        slide.get("title"),
        slide.get("narration"),
        " ".join(slide.get("bodyLines", []) or []),
    )
    finding_keywords = _keyword_set(
        finding.get("personaName"),
        finding.get("pageUrl"),
        finding.get("text"),
    )

    overlap = slide_keywords & finding_keywords
    score += len(overlap) * 4

    if slide.get("personaName") and finding.get("personaName"):
        if _clean_line(slide.get("personaName")).lower() == _clean_line(finding.get("personaName")).lower():
            score += 8

    if slide.get("pageUrl") and finding.get("pageUrl"):
        if _clean_line(slide.get("pageUrl")).lower() == _clean_line(finding.get("pageUrl")).lower():
            score += 10

    slide_id = _clean_line(slide.get("id")).lower()
    if slide_id.startswith("evidence-"):
        score += 12

    if _slide_visual_needs_grounding(slide):
        score += 3

    return score


def _attach_supporting_screenshots(
    slides: list[dict],
    persona_reports: dict,
    presentation_screenshot_by_page: dict[str, str] | None = None,
    presentation_screenshot_by_source: dict[str, str] | None = None,
) -> list[dict]:
    supporting = _pick_supporting_findings(
        persona_reports,
        limit=12,
        presentation_screenshot_by_page=presentation_screenshot_by_page,
        presentation_screenshot_by_source=presentation_screenshot_by_source,
    )
    
    # Also collect ALL raw screenshots as a final fallback
    all_raw_screenshots: list[str] = []
    seen_raw: set[str] = set()
    for url in (presentation_screenshot_by_page or {}).values():
        u = _clean_line(url)
        if _is_image_like_url(u) and u not in seen_raw:
            all_raw_screenshots.append(u)
            seen_raw.add(u)
    for report in persona_reports.values():
        # Get screenshots from the page map
        for url in (report.get("pageScreenshots", {}) or {}).values():
            u = _preferred_presentation_screenshot(
                None,
                url,
                presentation_screenshot_by_page,
                presentation_screenshot_by_source,
            )
            if _is_image_like_url(u) and u not in seen_raw:
                all_raw_screenshots.append(u)
                seen_raw.add(u)
        # Get latest screenshot
        u_latest = _preferred_presentation_screenshot(
            report.get("latestScreenshotPage"),
            report.get("latestScreenshot"),
            presentation_screenshot_by_page,
            presentation_screenshot_by_source,
        )
        if _is_image_like_url(u_latest) and u_latest not in seen_raw:
            all_raw_screenshots.append(u_latest)
            seen_raw.add(u_latest)

    # If persona_reports were built without pageScreenshots/latestScreenshot (the common
    # case when built from main.py), fall back to collecting URLs from findings directly.
    # This ensures the cyclic reuse path below always has a pool to draw from.
    if not all_raw_screenshots:
        for report in persona_reports.values():
            for finding in report.get("findings", []) or []:
                if not isinstance(finding, dict):
                    continue
                u = _preferred_presentation_screenshot(
                    finding.get("pageUrl"),
                    finding.get("screenshotUrl"),
                    presentation_screenshot_by_page,
                    presentation_screenshot_by_source,
                )
                if _is_image_like_url(u) and u not in seen_raw:
                    all_raw_screenshots.append(u)
                    seen_raw.add(u)

    used_urls: set[str] = set()
    evidence_index = 0
    raw_index = 0
    enriched: list[dict] = []

    for slide in slides:
        existing_url = _clean_line(slide.get("screenshotUrl"))
        if existing_url and not _is_image_like_url(existing_url):
            existing_url = ""
        if existing_url and existing_url not in used_urls:
            used_urls.add(existing_url)
            enriched.append(slide)
            continue

        if existing_url:
            slide = {
                **slide,
                "screenshotUrl": None,
            }

        candidate_url = None
        candidate_persona = None
        candidate_page_url = None
        candidate_page_label = None

        # 1. Try to find a matching evidence-backed finding
        if _clean_line(slide.get("id")).lower().startswith("evidence-"):
            while evidence_index < len(supporting):
                c = supporting[evidence_index]
                evidence_index += 1
                u = _clean_line(c.get("screenshotUrl"))
                if _is_image_like_url(u) and u not in used_urls:
                    candidate_url = u
                    candidate_persona = c.get("personaName")
                    candidate_page_url = c.get("pageUrl")
                    candidate_page_label = c.get("pageLabel")
                    break

        # 2. Try to find a highly relevant finding based on keywords
        if not candidate_url and _slide_visual_needs_grounding(slide):
            ranked = sorted(
                [
                    finding for finding in supporting
                    if _is_image_like_url(_clean_line(finding.get("screenshotUrl")))
                    and _clean_line(finding.get("screenshotUrl")) not in used_urls
                ],
                key=lambda finding: (
                    _score_supporting_finding_for_slide(slide, finding),
                ),
                reverse=True,
            )
            if ranked and _score_supporting_finding_for_slide(slide, ranked[0]) > 5:
                c = ranked[0]
                candidate_url = _clean_line(c.get("screenshotUrl"))
                candidate_persona = c.get("personaName")
                candidate_page_url = c.get("pageUrl")
                candidate_page_label = c.get("pageLabel")

        # 3. Fallback to any unused evidence-backed finding
        if not candidate_url and supporting:
            unused_supporting = [
                f for f in supporting
                if _is_image_like_url(_clean_line(f.get("screenshotUrl")))
                and _clean_line(f.get("screenshotUrl")) not in used_urls
            ]
            if unused_supporting:
                c = unused_supporting[0]
                candidate_url = _clean_line(c.get("screenshotUrl"))
                candidate_persona = c.get("personaName")
                candidate_page_url = c.get("pageUrl")
                candidate_page_label = c.get("pageLabel")

        # 4. FINAL FALLBACK: Any raw screenshot from the audit
        if not candidate_url and all_raw_screenshots:
            unused_raw = [
                u for u in all_raw_screenshots
                if u not in used_urls
            ]
            if unused_raw:
                candidate_url = unused_raw[0]
            elif all_raw_screenshots:
                # If we've used everything, start reusing raw screenshots
                candidate_url = all_raw_screenshots[raw_index % len(all_raw_screenshots)]
                raw_index += 1

        if candidate_url:
            slide = {
                **slide,
                "screenshotUrl": candidate_url,
                "pageUrl": slide.get("pageUrl") or candidate_page_url,
                "pageLabel": slide.get("pageLabel") or candidate_page_label,
                "visualPrompt": None,
            }
            if _is_evidence_slide(slide):
                slide["personaName"] = slide.get("personaName") or candidate_persona
            elif _is_cross_persona_slide(slide):
                slide["personaName"] = None
            used_urls.add(candidate_url)

        enriched.append(slide)

    return enriched


def build_founder_presentation(
    audit_url: str,
    report_data: dict,
    persona_reports: dict,
    presentation_screenshot_by_page: dict[str, str] | None = None,
    presentation_screenshot_by_source: dict[str, str] | None = None,
) -> dict:
    summary = _clean_line(report_data.get("summary"))
    score = report_data.get("score", "N/A")
    critical_issues = _top_lines(report_data.get("criticalIssues", []), 3)
    recommendations = _top_lines(report_data.get("recommendations", []), 3)
    positives = _top_lines(report_data.get("positives", []), 2)
    supporting = _pick_supporting_findings(
        persona_reports,
        presentation_screenshot_by_page=presentation_screenshot_by_page,
        presentation_screenshot_by_source=presentation_screenshot_by_source,
    )
    summary_bullets = _build_summary_bullets(summary, score)
    issue_bullets = _build_issue_bullets(critical_issues)
    recommendation_bullets = _build_recommendation_bullets(recommendations)
    positive_bullets = _build_positive_bullets(positives)

    slides: list[dict] = [
        _make_slide(
            slide_id="overview",
            eyebrow="Founder Presentation",
            title=f"{_friendly_site_name(audit_url)} Overview",
            body_lines=summary_bullets,
            narration=(
                f"This is your Audit My Site presentation for {audit_url}. "
                f"Your current user experience score is {score} out of 100. "
                f"{summary}"
            ),
            visual_prompt=f"A modern, abstract representation of a website overview dashboard for {audit_url}.",
        )
    ]

    if critical_issues:
        slides.append(
            _make_slide(
                slide_id="top-issues",
                eyebrow="Biggest Opportunities",
                title="What needs attention first",
                body_lines=issue_bullets,
                narration=(
                    "These are the most important issues to address first. "
                    + " ".join(critical_issues)
                ),
                visual_prompt="A conceptual representation of analyzing and resolving critical website issues, clean modern style.",
            )
        )

    for index, finding in enumerate(supporting[:MAX_PRESENTATION_EVIDENCE_SLIDES], start=1):
        persona_name = finding.get("personaName", "A persona")
        finding_text = _clean_line(finding.get("text"))
        page_url = finding.get("pageUrl")
        page_label = finding.get("pageLabel")
        slides.append(
            _make_slide(
                slide_id=f"evidence-{index}",
                eyebrow=f"Evidence Spotlight {index}",
                title=f"{persona_name} perspective",
                body_lines=_build_evidence_bullets(finding_text),
                narration=(
                    f"Here is a concrete example from the audit. "
                    f"{persona_name} noticed the following on the page: {finding_text}"
                ),
                screenshot_url=finding.get("screenshotUrl"),
                page_url=page_url,
                page_label=page_label,
                persona_name=persona_name,
                visual_prompt=f"A user perspective visual showing someone examining a digital interface, representing a {persona_name}.",
            )
        )

    if recommendations:
        slides.append(
            _make_slide(
                slide_id="next-steps",
                eyebrow="Recommended Actions",
                title="What to do next",
                body_lines=recommendation_bullets,
                narration=(
                    "These are the highest leverage next steps to improve the experience. "
                    + " ".join(recommendations)
                ),
                visual_prompt="A clean, abstract upward-trending roadmap or action plan diagram, modern minimalist tech aesthetic.",
            )
        )

    if positives:
        slides.append(
            _make_slide(
                slide_id="strengths",
                eyebrow="Keep These Wins",
                title="What is already working",
                body_lines=positive_bullets,
                narration=(
                    "The audit also found strengths worth protecting. "
                    + " ".join(positives)
                ),
                visual_prompt="A celebratory but professional abstract representation of a successful website, perhaps a subtle trophy or checkmark motif in clean product design style.",
            )
        )

    return {
        "title": f"UX Audit of {_friendly_site_name(audit_url)}",
        "subtitle": "A guided walkthrough of the most important audit takeaways.",
        "score": score,
        "slides": slides,
        "voice": DEFAULT_AUDIO_VOICE,
        "model": DEFAULT_AUDIO_MODEL,
    }


def _presentation_authoring_schema() -> dict:
    return {
        "type": "object",
        "required": ["title", "subtitle", "score", "slides"],
        "properties": {
            "title": {"type": "string"},
            "subtitle": {"type": "string"},
            "score": {"type": "integer"},
            "slides": {
                "type": "array",
                "minItems": 4,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "required": ["id", "eyebrow", "title", "bodyLines", "narration"],
                    "properties": {
                        "id": {"type": "string"},
                        "eyebrow": {"type": "string"},
                        "title": {"type": "string"},
                        "bodyLines": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 3,
                            "items": {"type": "string"},
                        },
                        "narration": {"type": "string"},
                        "pageUrl": {"type": "string"},
                        "pageLabel": {"type": "string"},
                        "personaName": {"type": "string"},
                        "visualPrompt": {"type": "string"},
                    },
                },
            },
        },
    }


def _build_presentation_authoring_prompt(
    audit_url: str,
    report_data: dict,
    persona_reports: dict,
    presentation_screenshot_by_page: dict[str, str] | None = None,
    presentation_screenshot_by_source: dict[str, str] | None = None,
) -> str:
    supporting = _pick_supporting_findings(
        persona_reports,
        limit=6,
        presentation_screenshot_by_page=presentation_screenshot_by_page,
        presentation_screenshot_by_source=presentation_screenshot_by_source,
    )
    prompt_payload = {
        "auditUrl": audit_url,
        "score": report_data.get("score"),
        "summary": report_data.get("summary"),
        "criticalIssues": report_data.get("criticalIssues", []),
        "recommendations": report_data.get("recommendations", []),
        "positives": report_data.get("positives", []),
        "supportingFindings": supporting,
    }
    return json.dumps(prompt_payload, indent=2)


def _sanitize_presentation_deck(
    deck: dict,
    report_data: dict,
    audit_url: str,
    persona_reports: dict,
    presentation_screenshot_by_page: dict[str, str] | None = None,
    presentation_screenshot_by_source: dict[str, str] | None = None,
) -> dict:
    fallback = build_founder_presentation(
        audit_url,
        report_data,
        persona_reports,
        presentation_screenshot_by_page=presentation_screenshot_by_page,
        presentation_screenshot_by_source=presentation_screenshot_by_source,
    )
    cleaned_slides: list[dict] = []

    for raw_slide in deck.get("slides", []) or []:
        if not isinstance(raw_slide, dict):
            continue

        body_lines = [
            _clean_line(line)
            for line in raw_slide.get("bodyLines", []) or []
            if _clean_line(line)
        ][:MAX_PRESENTATION_BULLETS]

        normalized_lines: list[str] = []
        for line in body_lines:
            # Force complete, boardroom-ready bullets rather than ellipsis fragments.
            normalized_line = line.replace("...", ".").strip()
            normalized_line = re.sub(r"\s+", " ", normalized_line)
            normalized_lines.append(normalized_line)

        if not normalized_lines:
            continue

        title = _clean_line(raw_slide.get("title")) or "Audit Insight"
        if title.startswith("http://") or title.startswith("https://"):
            title = f"{_friendly_site_name(audit_url)} Overview"

        cleaned_slides.append({
            "id": _clean_line(raw_slide.get("id")) or f"slide-{len(cleaned_slides) + 1}",
            "eyebrow": _clean_line(raw_slide.get("eyebrow")) or "Founder Presentation",
            "title": title,
            "bodyLines": normalized_lines,
            "narration": _clean_line(raw_slide.get("narration")) or "This slide highlights one of the most important audit takeaways.",
            "pageUrl": _clean_line(raw_slide.get("pageUrl")) or None,
            "pageLabel": _clean_line(raw_slide.get("pageLabel")) or None,
            "personaName": (
                _clean_line(raw_slide.get("personaName")) or None
                if _clean_line(raw_slide.get("id")).lower().startswith("evidence-")
                else None
            ),
            "visualPrompt": _clean_line(raw_slide.get("visualPrompt")) or None,
        })

    cleaned_slides = _attach_supporting_screenshots(
        cleaned_slides[:6],
        persona_reports,
        presentation_screenshot_by_page=presentation_screenshot_by_page,
        presentation_screenshot_by_source=presentation_screenshot_by_source,
    )

    if not cleaned_slides:
        return fallback

    deck_title = _clean_line(deck.get("title")) or f"UX Audit of {_friendly_site_name(audit_url)}"
    if deck_title.startswith("http://") or deck_title.startswith("https://") or "presentation" in deck_title.lower():
        deck_title = f"UX Audit of {_friendly_site_name(audit_url)}"

    return {
        "title": deck_title,
        "subtitle": _clean_line(deck.get("subtitle")) or "A guided walkthrough of the most important audit takeaways.",
        "score": report_data.get("score", deck.get("score")),
        "slides": cleaned_slides[:6],
        "voice": DEFAULT_AUDIO_VOICE,
        "model": PRESENTATION_TEXT_MODEL,
    }


def _author_founder_presentation(
    audit_url: str,
    report_data: dict,
    persona_reports: dict,
    presentation_screenshot_by_page: dict[str, str] | None = None,
    presentation_screenshot_by_source: dict[str, str] | None = None,
) -> dict:
    prompt = _build_presentation_authoring_prompt(
        audit_url,
        report_data,
        persona_reports,
        presentation_screenshot_by_page=presentation_screenshot_by_page,
        presentation_screenshot_by_source=presentation_screenshot_by_source,
    )
    instruction = f"""
You are a senior UX researcher preparing a polished board-of-directors presentation.

Create a concise founder presentation from the provided audit payload.

CRITICAL RULE: 
- EVERY slide must be grounded in a REAL audit screenshot. 
- You MUST prefer real audit screenshots for EVERY slide (overview, issues, recommendations, strengths, evidence). 
- If a slide can be grounded by any finding or page in the audit, you MUST include `pageUrl`, `pageLabel`, and `personaName` so the runtime can attach a real screenshot.
- Only rely on a `visualPrompt` as a last resort if NO screenshots are available, which should not happen.

General Rules:
- The overall presentation `title` MUST follow the format: "UX Audit of <company or product name>".
- Return valid JSON only.
- Do NOT use raw URLs as slide titles.
- Use concise, human slide titles like "Homepage Positioning" or "Partner Experience".
- Each slide must contain 1 to 3 bullets only.
- Every bullet must be a complete thought with no ellipses.
- Each bullet must be 15 words or fewer.
- Bullets should be presentation-ready, not copied verbatim from the report.
- Keep the tone polished, executive, and trustworthy.
- Only evidence spotlight slides should be framed as a single persona perspective.
- Overview, key issues, action plan, and closing slides must synthesize the strongest common themes across personas, not one persona's opinion.
- Do not assign a single `personaName` to cross-persona summary or recommendation slides.
- Preserve factual grounding from the audit payload. Do not invent unsupported claims.
- Include short narration for each slide that sounds like a UX researcher presenting to executives.
"""
    configs = [
        (
            _make_vertex_client,
            PRESENTATION_TEXT_MODEL,
            types.GenerateContentConfig(
                system_instruction=instruction,
                response_mime_type="application/json",
                response_json_schema=_presentation_authoring_schema(),
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
                temperature=0.4,
            ),
        ),
        (
            _make_vertex_client,
            PRESENTATION_TEXT_MODEL_FALLBACK,
            types.GenerateContentConfig(
                system_instruction=instruction,
                response_mime_type="application/json",
                response_json_schema=_presentation_authoring_schema(),
                temperature=0.4,
            ),
        ),
        (
            genai.Client,
            "gemini-2.5-pro",
            types.GenerateContentConfig(
                system_instruction=instruction,
                response_mime_type="application/json",
                response_json_schema=_presentation_authoring_schema(),
                temperature=0.3,
            ),
        ),
    ]

    last_error = None
    for client_factory, model_name, config in configs:
        try:
            client = client_factory()
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=config,
            )
            deck = response.parsed or json.loads(response.text)
            sanitized = _sanitize_presentation_deck(
                deck,
                report_data,
                audit_url,
                persona_reports,
                presentation_screenshot_by_page=presentation_screenshot_by_page,
                presentation_screenshot_by_source=presentation_screenshot_by_source,
            )
            sanitized["model"] = model_name
            return sanitized
        except Exception as exc:
            last_error = exc
            # If it's an internal error or rate limit, wait a bit before trying the next model
            if "500" in str(exc) or "429" in str(exc) or "INTERNAL" in str(exc):
                time.sleep(1)
            continue

    raise last_error or RuntimeError("No presentation authoring model was available.")


def _extract_audio_blob(response) -> tuple[bytes, str]:
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                mime_type = getattr(inline_data, "mime_type", None) or "audio/wav"
                return inline_data.data, mime_type

    parts = getattr(response, "parts", None) or []
    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data and getattr(inline_data, "data", None):
            mime_type = getattr(inline_data, "mime_type", None) or "audio/wav"
            return inline_data.data, mime_type

    raise ValueError("Gemini TTS response did not include audio bytes.")


def _extension_for_mime_type(mime_type: str) -> str:
    guessed = mimetypes.guess_extension(mime_type or "")
    if guessed:
        return guessed
    return ".wav"


def _pcm_l16_to_wav(audio_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    rate_match = re.search(r"rate=(\d+)", mime_type or "", re.I)
    sample_rate = int(rate_match.group(1)) if rate_match else 24000

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        # The SDK returns raw PCM frames; writing them directly produces
        # intelligible WAV output for browser playback.
        wav_file.writeframes(audio_bytes)

    return wav_buffer.getvalue(), "audio/wav"


def _normalize_audio_for_storage(audio_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    normalized_mime = (mime_type or "").lower()
    if normalized_mime.startswith("audio/l16"):
        return _pcm_l16_to_wav(audio_bytes, mime_type)
    return audio_bytes, mime_type or "audio/wav"


async def _upload_audio_artifact(
    audit_id: str,
    audio_bytes: bytes,
    mime_type: str,
    artifact_prefix: str = "audio_recap",
) -> tuple[str, str]:
    bucket = storage.bucket()
    token = str(uuid.uuid4())
    ts_ms = int(time.time() * 1000)
    extension = _extension_for_mime_type(mime_type)
    blob_name = f"media/{audit_id}/{artifact_prefix}_{ts_ms}{extension}"
    blob = bucket.blob(blob_name)
    blob.metadata = {"firebaseStorageDownloadTokens": token}

    await asyncio.to_thread(blob.upload_from_string, audio_bytes, content_type=mime_type)

    encoded = urllib.parse.quote(blob_name, safe="")
    download_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{encoded}?alt=media&token={token}"
    return blob_name, download_url


async def _upload_image_artifact(
    audit_id: str,
    image_bytes: bytes,
    mime_type: str,
    artifact_prefix: str,
) -> tuple[str, str]:
    bucket = storage.bucket()
    token = str(uuid.uuid4())
    ts_ms = int(time.time() * 1000)
    extension = _extension_for_mime_type(mime_type) if mime_type.startswith("image/") else ".png"
    blob_name = f"media/{audit_id}/{artifact_prefix}_{ts_ms}{extension}"
    blob = bucket.blob(blob_name)
    blob.metadata = {"firebaseStorageDownloadTokens": token}

    await asyncio.to_thread(blob.upload_from_string, image_bytes, content_type=mime_type)

    encoded = urllib.parse.quote(blob_name, safe="")
    download_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}/o/{encoded}?alt=media&token={token}"
    return blob_name, download_url


def _extract_image_blob(response) -> tuple[bytes, str]:
    parts = getattr(response, "parts", None) or []
    for part in parts:
        inline_data = getattr(part, "inline_data", None)
        if inline_data and getattr(inline_data, "data", None):
            mime_type = getattr(inline_data, "mime_type", None) or "image/png"
            return inline_data.data, mime_type

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                mime_type = getattr(inline_data, "mime_type", None) or "image/png"
                return inline_data.data, mime_type

    raise ValueError("Image model response did not include image bytes.")


def _generate_tts_response(script: str, voice_name: str = DEFAULT_AUDIO_VOICE):
    client = genai.Client()
    return client.models.generate_content(
        model=DEFAULT_AUDIO_MODEL,
        contents=script,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            temperature=0.6,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_name
                    )
                )
            ),
        ),
    )


def _generate_presentation_image_response(prompt: str):
    client = _make_vertex_client()
    try:
        return client.models.generate_content(
            model=PRESENTATION_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio="16:9",
                    image_size="1K",
                ),
            ),
        )
    except Exception:
        return client.models.generate_content(
            model=PRESENTATION_IMAGE_MODEL_FALLBACK,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio="16:9",
                ),
            ),
        )


async def _generate_tts_audio_asset(script: str, voice_name: str = DEFAULT_AUDIO_VOICE) -> tuple[bytes, str]:
    # Add retry logic for TTS as it's prone to transient 500 errors in preview
    last_err = None
    for attempt in range(3):
        try:
            response = await asyncio.to_thread(_generate_tts_response, script, voice_name)
            audio_bytes, mime_type = _extract_audio_blob(response)
            return _normalize_audio_for_storage(audio_bytes, mime_type)
        except Exception as e:
            last_err = e
            error_str = str(e)
            # Retry on transient API errors and on missing-audio responses (can be transient in preview)
            if (
                "500" in error_str
                or "429" in error_str
                or "INTERNAL" in error_str
                or "did not include audio bytes" in error_str
            ):
                await asyncio.sleep(2 ** attempt)
                continue
            raise e
    raise last_err

async def _generate_presentation_visual_asset(
    audit_id: str,
    slide: dict,
    index: int,
) -> tuple[str, str] | tuple[None, None]:
    visual_prompt = _clean_line(slide.get("visualPrompt"))
    if not visual_prompt:
        return None, None

    image_prompt = (
        "Create a premium 16:9 presentation visual for a board-level UX audit. "
        "Minimal, polished, monochrome-first product design aesthetic with subtle blue accents. "
        "No paragraphs. Prefer abstract product storytelling, UI motifs, dashboards, diagrams, or elegant brand scenes. "
        f"Slide title: {slide.get('title')}. Prompt: {visual_prompt}"
    )
    
    # Add retry logic for image generation
    last_err = None
    for attempt in range(2):
        try:
            response = await asyncio.to_thread(_generate_presentation_image_response, image_prompt)
            image_bytes, mime_type = _extract_image_blob(response)
            return await _upload_image_artifact(
                audit_id,
                image_bytes,
                mime_type,
                artifact_prefix=f"presentation_visual_{index}",
            )
        except Exception as e:
            last_err = e
            if "500" in str(e) or "429" in str(e) or "INTERNAL" in str(e):
                await asyncio.sleep(1)
                continue
            break # Non-retriable error

    # If image generation fails, just log it and return None — the presentation can survive without it
    print(f"[AuditRecap] Image generation failed for slide {index}: {last_err}")
    return None, None


async def generate_audio_presentation(audit_id: str, audit_url: str, report_data: dict, persona_reports: dict) -> dict:
    if not firebase_admin._apps:
        try:
            presentation = _author_founder_presentation(audit_url, report_data, persona_reports)
        except Exception as author_error:
            print(f"[AuditRecap] Presentation authoring fallback for {audit_id}: {author_error}")
            presentation = build_founder_presentation(audit_url, report_data, persona_reports)
        print(f"[AuditRecap] Mock generate presentation for {audit_id}")
        return {"status": "mocked", **presentation}

    existing = _get_existing_presentation(audit_id)
    if existing.get("status") == "ready" and existing.get("slides"):
        return existing
    if existing.get("status") == "generating":
        return existing

    pending_state = {
        "status": "generating",
        "title": f"UX Audit of {_friendly_site_name(audit_url)}" if audit_url else "UX Audit",
        "subtitle": "Building your guided presentation with voiceover and grounded screenshots.",
        "voice": DEFAULT_AUDIO_VOICE,
        "error": None,
    }
    _set_presentation_state(audit_id, **pending_state)

    audit_snapshot = _artifact_doc_ref(audit_id).get()
    audit_data = audit_snapshot.to_dict() if audit_snapshot.exists else {}
    crawled_pages = audit_data.get("crawledPages", []) or []
    presentation_screenshot_by_page, presentation_screenshot_by_source = _build_presentation_screenshot_maps(crawled_pages)

    try:
        presentation = _author_founder_presentation(
            audit_url,
            report_data,
            persona_reports,
            presentation_screenshot_by_page=presentation_screenshot_by_page,
            presentation_screenshot_by_source=presentation_screenshot_by_source,
        )
    except Exception as author_error:
        print(f"[AuditRecap] Presentation authoring fallback for {audit_id}: {author_error}")
        presentation = build_founder_presentation(
            audit_url,
            report_data,
            persona_reports,
            presentation_screenshot_by_page=presentation_screenshot_by_page,
            presentation_screenshot_by_source=presentation_screenshot_by_source,
        )

    base_state = {
        "status": "generating",
        "title": presentation["title"],
        "subtitle": presentation["subtitle"],
        "score": presentation["score"],
        "voice": presentation["voice"],
        "model": presentation["model"],
        "slides": presentation["slides"],
        "error": None,
    }
    _set_presentation_state(audit_id, **base_state)

    try:
        ready_slides: list[dict] = []
        for index, slide in enumerate(presentation["slides"], start=1):
            audio_bytes, mime_type = await _generate_tts_audio_asset(slide["narration"], DEFAULT_AUDIO_VOICE)
            storage_path, download_url = await _upload_audio_artifact(
                audit_id,
                audio_bytes,
                mime_type,
                artifact_prefix=f"presentation_slide_{index}",
            )
            visual_storage_path, visual_download_url = None, None
            if not slide.get("screenshotUrl"):
                try:
                    visual_storage_path, visual_download_url = await _generate_presentation_visual_asset(audit_id, slide, index)
                except Exception as visual_error:
                    print(f"[AuditRecap] Presentation visual fallback on slide {index} for {audit_id}: {visual_error}")

            ready_slides.append({
                **slide,
                "screenshotUrl": slide.get("screenshotUrl") or visual_download_url,
                "visualStoragePath": visual_storage_path,
                "visualSource": "evidence" if slide.get("screenshotUrl") else ("generated" if visual_download_url else "none"),
                "audioUrl": download_url,
                "audioStoragePath": storage_path,
                "audioMimeType": mime_type,
            })

        result = {
            "status": "ready",
            "title": presentation["title"],
            "subtitle": presentation["subtitle"],
            "score": presentation["score"],
            "voice": presentation["voice"],
            "model": presentation["model"],
            "slides": ready_slides,
            "generatedAt": firestore.SERVER_TIMESTAMP,
            "error": None,
        }
        _set_presentation_state(audit_id, **result)
        return result
    except Exception as exc:
        error_state = {**base_state, "status": "error", "error": str(exc)}
        _set_presentation_state(audit_id, **error_state)
        raise
