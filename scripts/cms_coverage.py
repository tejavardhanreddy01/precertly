"""Fetch governing coverage documents (NCD, LCD, article) from the CMS Coverage API.

Used by verify_policy_quotes.py. Fetched text is never written into the repository.
LCD and article endpoints need a token from the API's license-agreement endpoint; using
this module means accepting those terms (AMA CPT, ADA CDT, AHA NUBC).
"""

from __future__ import annotations

import html
import json
import re
import urllib.request
from functools import lru_cache

API = "https://api.coverage.cms.gov/v1"

# API field -> section heading as shown in the Medicare Coverage Database.
SECTIONS = {
    "lcd": {
        "cms_cov_policy": "CMS National Coverage Policy",
        "indication": "Coverage Indications, Limitations, and/or Medical Necessity",
        "doc_reqs": "Documentation Requirements",
        "util_guide": "Utilization Guidelines",
        "coding_guidelines": "Coding Guidelines",
        "appendices": "Appendices",
        "associated_info": "Associated Information",
    },
    "article": {"description": "Article Text"},
    "ncd": {
        "item_service_description": "Item/Service Description",
        "indications_limitations": "Indications and Limitations of Coverage",
        "other_text": "Other",
    },
}


def _get(path: str, token: str | None = None) -> dict:
    request = urllib.request.Request(f"{API}{path}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


@lru_cache
def _token() -> str:
    return _get("/metadata/license-agreement/")["data"][0]["Token"]


@lru_cache
def _ncd_ids() -> dict[str, tuple[int, int]]:
    rows = _get("/reports/national-coverage-ncd/")["data"]
    return {r["document_display_id"]: (r["document_id"], r["document_version"]) for r in rows}


def to_text(markup: str) -> str:
    """Plain text of an API field (HTML that is itself entity-escaped)."""
    # Unescape once to get the HTML, strip its tags, then unescape the text itself, so a
    # literal "<" in the prose (as in "<80 degrees") is not mistaken for a tag.
    text = html.unescape(markup or "")
    text = re.sub(r"<(br|/p|/li|/h\d|/tr|/div)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def fetch(document: str, version: str) -> dict[str, str]:
    """Sections of a governing document as plain text, keyed by section heading.

    document is "NCD 100.1", "LCD L38773" or "Article A58364"; version is the document
    version recorded in the policy file.
    """
    kind, _, display_id = document.partition(" ")
    kind = kind.lower()
    if kind == "ncd":
        ncd_id, current = _ncd_ids()[display_id]
        path = f"/data/ncd/?ncdid={ncd_id}&ncdver={version or current}"
        record = _get(path)["data"][0]
    elif kind == "lcd":
        record = _get(f"/data/lcd/?lcdid={display_id.lstrip('L')}&ver={version}", _token())["data"][
            0
        ]
    elif kind == "article":
        path = f"/data/article/?articleid={display_id.lstrip('A')}&ver={version}"
        record = _get(path, _token())["data"][0]
    else:
        raise ValueError(f"unknown document kind in {document!r}")
    sections = {
        heading: to_text(record.get(field) or "") for field, heading in SECTIONS[kind].items()
    }
    return {heading: text for heading, text in sections.items() if text}


def squash(text: str) -> str:
    """Text with all whitespace removed, for comparing quotes regardless of line breaks."""
    return re.sub(r"\s+", "", text.replace("\xa0", " "))
