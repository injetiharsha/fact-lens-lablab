"""Tools used by the collaborative fact-checking agents."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import time
import mimetypes
import os
import re
import urllib.parse
import urllib.request
import subprocess
from pathlib import Path
from typing import Iterable, List

from .schemas import EvidenceItem
from .llm import generate_gemini_json


TRUSTED_DOMAINS = (
    ".gov",
    ".edu",
    "who.int",
    "nasa.gov",
    "worldbank.org",
    "imf.org",
    "un.org",
    "europa.eu",
    "ec.europa.eu",
    "oecd.org",
    "nih.gov",
    "cdc.gov",
    "data.gov",
    "ourworldindata.org",
    "nature.com",
    "science.org",
    "reuters.com",
    "apnews.com",
)
BAD_DOMAIN_HINTS = (
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "wa.me",
    "t.me",
    "reddit.com",
    "quora.com",
    "pinterest.com",
)

# Credibility tiers (CW) tuned for deterministic source scoring.
DOMAIN_TIERS = (
    # T1: Government / official scientific bodies
    (
        1.00,
        (
            ".gov",
            ".edu",
            "gov.in",
            "rbi.org.in",
            "nasa.gov",
            "who.int",
            "nih.gov",
            "cdc.gov",
            "ecb.europa.eu",
            "data.gov",
        ),
    ),
    # T2: International acclaimed orgs / high-quality science
    (
        0.95,
        (
            "un.org",
            "unicef.org",
            "worldbank.org",
            "imf.org",
            "oecd.org",
            "europa.eu",
            "ec.europa.eu",
            "ourworldindata.org",
            "nature.com",
            "science.org",
            "thelancet.com",
            "nejm.org",
        ),
    ),
    # T3: National wire services / major international outlets
    (
        0.85,
        (
            "reuters.com",
            "apnews.com",
            "bloomberg.com",
            "ft.com",
            "wsj.com",
            "bbc.com",
            "bbc.co.uk",
            "afp.com",
            "pti.in",
            "ani.in",
        ),
    ),
    # T4: Reputable national news
    (
        0.75,
        (
            "thehindu.com",
            "indianexpress.com",
            "ndtv.com",
            "economictimes.com",
            "livemint.com",
            "hindustantimes.com",
            "timesofindia.indiatimes.com",
        ),
    ),
    # T5: Regional/fact-checkers
    (
        0.65,
        (
            "factly.in",
            "altnews.in",
            "boomlive.in",
            "thequint.com",
            "snopes.com",
            "politifact.com",
            "factcheck.org",
        ),
    ),
    # T7/T8: Low-trust and social/restricted
    (0.15, ("satire", "tabloid", "clickbait", "gossip")),
    (0.05, ("twitter.com", "x.com", "facebook.com", "instagram.com", "t.me", "wa.me", "tiktok.com")),
)

TEMPORAL_WEIGHTS = (
    (0, 1.00),
    (1, 0.90),
    (2, 0.80),
    (5, 0.65),
    (10, 0.50),
    (999, 0.40),
)

_TRANSLATION_CACHE: dict[str, str] = {}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def extract_pdf_text(path: str, max_chars: int = 12000, page_spec: str = "") -> str:
    try:
        import fitz  # type: ignore
    except Exception:
        return ""

    chunks: List[str] = []
    with fitz.open(path) as doc:
        indices = _parse_pdf_page_spec(page_spec, len(doc))
        for idx in indices:
            page = doc[idx]
            chunks.append(page.get_text("text"))
            if sum(len(chunk) for chunk in chunks) >= max_chars:
                break
    return normalize_text(" ".join(chunks))[:max_chars]


def extract_pdf_sections(path: str, max_chars: int = 12000, page_spec: str = "", max_sections: int = 16) -> List[dict]:
    """Extract section-like chunks from PDF with page metadata."""
    try:
        import fitz  # type: ignore
    except Exception:
        return []

    sections: List[dict] = []
    used = 0
    with fitz.open(path) as doc:
        indices = _parse_pdf_page_spec(page_spec, len(doc))
        for idx in indices:
            page = doc[idx]
            blocks = page.get_text("blocks") or []
            # sort by top-to-bottom then left-to-right
            blocks = sorted(blocks, key=lambda b: (float(b[1]), float(b[0])))
            for block in blocks:
                text = normalize_text(block[4] if len(block) > 4 else "")
                if not text or len(text.split()) < 5:
                    continue
                if re.match(r"^(page|figure|table)\b", text.lower()):
                    continue
                heading_guess = bool(re.match(r"^[A-Z][A-Z0-9\\s:,_-]{6,}$", text[:90])) or len(text.split()) <= 10
                chunk = text[:800]
                sections.append(
                    {
                        "page": idx + 1,
                        "heading_guess": heading_guess,
                        "text": chunk,
                    }
                )
                used += len(chunk)
                if len(sections) >= max_sections or used >= max_chars:
                    break
            if len(sections) >= max_sections or used >= max_chars:
                break
    return sections


def _detect_script_hint(text: str) -> str:
    if re.search(r"[\u0900-\u097F]", text):
        return "hin"
    if re.search(r"[\u0C80-\u0CFF]", text):
        return "kan"
    if re.search(r"[\u0D00-\u0D7F]", text):
        return "mal"
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "tam"
    if re.search(r"[\u0C00-\u0C7F]", text):
        return "tel"
    return "eng"


def extract_image_text(path: str, preferred_lang: str = "") -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        return ""

    image = Image.open(path)
    lang_candidates: List[str] = []
    pref = normalize_text(preferred_lang).lower()
    if pref:
        lang_candidates.extend([pref, f"eng+{pref}"])
    lang_candidates.extend(["eng", "eng+hin", "eng+kan", "eng+mal", "eng+tam", "eng+tel"])

    seen = set()
    ordered = []
    for lang in lang_candidates:
        if lang and lang not in seen:
            seen.add(lang)
            ordered.append(lang)

    best = ""
    for lang in ordered:
        try:
            text = normalize_text(pytesseract.image_to_string(image, lang=lang))
            if len(text) > len(best):
                best = text
        except Exception:
            continue
    return best


def extract_file_text(path: str, pdf_pages: str = "") -> str:
    mime, _ = mimetypes.guess_type(path)
    ext = Path(path).suffix.lower()
    if ext == ".pdf" or mime == "application/pdf":
        return extract_pdf_text(path, page_spec=pdf_pages)
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"} or str(mime or "").startswith("image/"):
        return extract_image_text(path)
    try:
        return normalize_text(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return ""


def extract_file_payload(path: str, pdf_pages: str = "") -> dict:
    """Return extracted text plus diagnostics for OCR/PDF inputs."""
    mime, _ = mimetypes.guess_type(path)
    ext = Path(path).suffix.lower()
    meta = {"kind": "text", "chars": 0}

    if ext == ".pdf" or mime == "application/pdf":
        text = extract_pdf_text(path, page_spec=pdf_pages)
        sections = extract_pdf_sections(path, page_spec=pdf_pages)
        meta.update({"kind": "pdf", "page_spec": normalize_text(pdf_pages) or "default_first_pages"})
        try:
            import fitz  # type: ignore

            with fitz.open(path) as doc:
                indices = _parse_pdf_page_spec(pdf_pages, len(doc))
                meta["pages_total"] = len(doc)
                meta["pages_used"] = len(indices)
        except Exception:
            pass
        meta["sections_count"] = len(sections)
        if sections:
            top_pages = sorted({int(s.get("page") or 0) for s in sections if s.get("page")})[:5]
            meta["section_pages"] = top_pages
        meta["chars"] = len(text)
        return {"text": text, "meta": meta, "sections": sections}

    if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"} or str(mime or "").startswith("image/"):
        raw = extract_image_text(path)
        lang = _detect_script_hint(raw)
        best = extract_image_text(path, preferred_lang=lang)
        meta.update({"kind": "image", "ocr_lang": lang, "chars": len(best)})
        return {"text": best, "meta": meta}

    text = extract_file_text(path, pdf_pages=pdf_pages)
    meta["chars"] = len(text)
    return {"text": text, "meta": meta}


def _source_type(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if any(token in host for token in TRUSTED_DOMAINS):
        return "trusted"
    if "wikipedia.org" in host:
        return "reference"
    return "web"


def _parse_pdf_page_spec(spec: str, total_pages: int) -> List[int]:
    # Printer-style: "1", "2", "1-4", "1,3,5-7"
    if total_pages <= 0:
        return []
    raw = normalize_text(spec)
    if not raw:
        return list(range(min(5, total_pages)))
    out: List[int] = []
    for token in [x.strip() for x in raw.split(",") if x.strip()]:
        if "-" in token:
            a, b = token.split("-", 1)
            if not a.isdigit() or not b.isdigit():
                continue
            s, e = int(a), int(b)
            if s > e:
                s, e = e, s
            for n in range(s, e + 1):
                if 1 <= n <= total_pages:
                    out.append(n - 1)
        else:
            if token.isdigit():
                n = int(token)
                if 1 <= n <= total_pages:
                    out.append(n - 1)
    # de-dupe keep order
    seen = set()
    uniq = []
    for i in out:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    return uniq or list(range(min(5, total_pages)))


def _credibility(url: str) -> int:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "wikipedia.org" in host:
        return 72
    for weight, patterns in DOMAIN_TIERS:
        if any(pattern in host for pattern in patterns):
            return int(round(weight * 100))
    if any(token in host for token in TRUSTED_DOMAINS):
        return 90
    return 40


def root_domain(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p and p != "www"]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def temporal_weight(url: str, claim: str = "") -> float:
    low = normalize_text(claim).lower()
    # Historical/science facts should not decay aggressively by age.
    if any(token in low for token in ("revolves", "revolve", "planet", "astronomy", "history", "historic")):
        return 1.00
    year_match = re.search(r"(19|20)\d{2}", url)
    if not year_match:
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(token in host for token in TRUSTED_DOMAINS) or "wikipedia.org" in host:
            return 0.80
        return 0.40
    year = int(year_match.group(0))
    now_year = dt.datetime.utcnow().year
    age_years = max(0, now_year - year)
    for max_age, weight in TEMPORAL_WEIGHTS:
        if age_years <= max_age:
            return weight
    return 0.40


def search_web(query: str, max_results: int = 5) -> List[EvidenceItem]:
    rows = _search_vertex_grounding(query, max_results=max_results)
    if rows:
        return rows

    rows = _search_gemini_grounding(query, max_results=max_results)
    if rows:
        return rows

    rows = _search_duckduckgo(query, max_results=max_results)
    if rows:
        return rows

    google_key = os.getenv("GOOGLE_SEARCH_API_KEY", "").strip()
    google_cx = os.getenv("GOOGLE_SEARCH_CX", "").strip()
    wiki_first = os.getenv("GOOGLE_WIKIPEDIA_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}
    if google_key and google_cx:
        if wiki_first:
            rows = _search_google_cse(f"{query} site:wikipedia.org", google_key, google_cx, max_results=max_results)
            if rows:
                return rows
        rows = _search_google_cse(query, google_key, google_cx, max_results=max_results)
        if rows:
            return rows
        rows = _search_google_cse(f"{query} site:wikipedia.org", google_key, google_cx, max_results=max_results)
        if rows:
            return rows

    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        return _search_tavily(query, tavily_key, max_results=max_results)

    rows = _search_wikipedia(query, max_results=max_results)
    if rows:
        return rows

    if os.getenv("FACTLENS_ALLOW_OFFLINE_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return _offline_search_stub(query, max_results=max_results)

    return []


def _search_google_cse(query: str, api_key: str, cx: str, max_results: int = 5) -> List[EvidenceItem]:
    try:
        encoded_q = urllib.parse.quote(query)
        url = (
            "https://www.googleapis.com/customsearch/v1"
            f"?key={api_key}&cx={cx}&q={encoded_q}&num={max(1, min(max_results, 10))}"
        )
        with urllib.request.urlopen(url, timeout=12) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return []

    rows: List[EvidenceItem] = []
    for item in data.get("items", [])[:max_results]:
        link = str(item.get("link") or "")
        title = str(item.get("title") or link or "Google result")
        snippet = normalize_text(item.get("snippet") or "")
        rows.append(
            EvidenceItem(
                title=title,
                url=link,
                snippet=snippet,
                source_type=_source_type(link),
                channel="google_search_api",
                credibility=_credibility(link),
            )
        )
    return rows[:max_results]


def classify_claim_domain(claim: str) -> str:
    # LLM-first semantic routing to avoid brittle keyword-only classification.
    prompt = (
        "Classify this claim into exactly one domain for retrieval routing.\n"
        "Allowed values: economy, population, health, politics, science, general.\n"
        "Return JSON only with key domain.\n\n"
        f"Claim: {claim}"
    )
    llm = generate_gemini_json(prompt, "GEMINI_INTAKE_MODEL", "gemini-2.5-flash")
    domain = normalize_text(str(llm.get("domain") or "")).lower()
    if domain in {"economy", "population", "health", "politics", "science", "general"}:
        return domain

    # Fallback heuristic remains as safety if LLM is unavailable/invalid.
    low = normalize_text(claim).lower()
    if any(token in low for token in ("gdp", "economy", "inflation", "unemployment", "fiscal", "imf", "world bank")):
        return "economy"
    if any(token in low for token in ("population", "census", "demography", "birth rate", "mortality")):
        return "population"
    if any(token in low for token in ("covid", "disease", "vaccine", "health", "who", "hospital")):
        return "health"
    if any(token in low for token in ("election", "minister", "government", "parliament", "policy", "law")):
        return "politics"
    if any(token in low for token in ("earth", "planet", "orbit", "sun", "climate", "space", "nasa")):
        return "science"
    return "general"


def routed_queries(claim: str, domain: str) -> List[str]:
    llm_plan = _llm_query_plan(claim, domain)
    if llm_plan:
        return llm_plan[:5]

    facts = extract_claim_facts(claim)
    intent = facts.get("intent_query") or ""
    year = facts.get("year")
    rank = facts.get("rank")
    country = facts.get("country") or ""
    rank_hint = f"{rank} largest" if rank else ""
    year_hint = str(year) if year else "latest"
    common_compare = f"{country} {rank_hint} {year_hint}".strip()
    base: List[str]
    if domain == "economy":
        base = [
            f"{claim} {intent} {common_compare} site:imf.org OR site:worldbank.org OR site:oecd.org OR site:fred.stlouisfed.org",
            f"{claim} {common_compare} nominal GDP ranking official statistics",
            f"{claim} {common_compare} Reuters AP Bloomberg",
        ]
    elif domain == "population":
        base = [
            f"{claim} {intent} {common_compare} site:worldbank.org OR site:un.org OR site:census.gov OR site:data.gov.in",
            f"{claim} {common_compare} official census demographic estimate",
            f"{claim} {common_compare} peer reviewed demographic data",
        ]
    elif domain == "health":
        base = [
            f"{claim} {intent} {common_compare} site:who.int OR site:cdc.gov OR site:nih.gov OR site:thelancet.com",
            f"{claim} {common_compare} public health dataset",
            f"{claim} {common_compare} systematic review",
        ]
    elif domain == "politics":
        base = [
            f"{claim} {intent} {common_compare} site:eci.gov.in OR site:parliament.uk OR site:gov.in OR site:gov",
            f"{claim} {common_compare} official statement transcript",
            f"{claim} {common_compare} Reuters AP fact check",
        ]
    elif domain == "science":
        base = [
            f"{claim} {intent} {common_compare} site:nasa.gov OR site:noaa.gov OR site:nature.com OR site:sciencedirect.com",
            f"{claim} {common_compare} educational institution source",
            f"{claim} {common_compare} scientific consensus",
        ]
    else:
        base = [
            f"{claim} {intent}",
            f"{claim} {common_compare} official source",
            f"{claim} {common_compare} reputable news",
        ]

    # Multilingual claim support: prioritize translated English retrieval
    # queries, while preserving original-language backup queries.
    if any(ord(ch) > 127 for ch in claim):
        translated = _translate_claim_to_english(claim)
        if translated and translated.lower() != claim.lower():
            pref = [
                f"{translated} official source fact check",
                f"{translated} Reuters AP IMF World Bank",
                f"{translated} site:gov OR site:edu OR site:who.int OR site:worldbank.org OR site:reuters.com",
            ]
            base = pref + base
        base.append(f"{claim} fact check official source")
    return dedupe_str(base)


def _llm_query_plan(claim: str, domain: str) -> List[str]:
    translated = _translate_claim_to_english(claim)
    translated = translated if translated else claim
    prompt = (
        "You are Query Planner Agent for fact-check retrieval.\n"
        "Generate EXACTLY 5 queries using these fixed keys:\n"
        "1) original_query\n"
        "2) primary_source_query\n"
        "3) refutation_query\n"
        "4) entity_keyword_query\n"
        "5) translated_query\n\n"
        "Primary source definition (strict): origin authority that publishes the original fact/value.\n"
        "Priority order:\n"
        "- official institutions/regulators/stat agencies (.gov, central banks, IMF/World Bank/OECD/UN)\n"
        "- original dataset/report/statistical release pages\n"
        "- first-party official documents/transcripts\n"
        "- only if missing/conflicting, use top-tier wire corroboration\n\n"
        "Do not assume extra country/year/metric unless present in claim.\n"
        "Return JSON only with these exact keys and string values.\n\n"
        f"Claim: {claim}\nDomain hint: {domain}"
    )
    out = generate_gemini_json(prompt, "GEMINI_INTAKE_MODEL", "gemini-2.5-flash")
    keys = [
        "original_query",
        "primary_source_query",
        "refutation_query",
        "entity_keyword_query",
        "translated_query",
    ]
    rows: List[str] = []
    for key in keys:
        value = normalize_text(str(out.get(key) or ""))
        if value:
            rows.append(value)
    # Guarantee fixed-size output even if model omits some keys.
    if len(rows) < 5:
        fallback = [
            f"{claim} official source",
            f"{claim} site:imf.org OR site:worldbank.org OR site:oecd.org OR site:un.org",
            f"{claim} actual rank not claim verification",
            f"{claim} entities metric year comparison",
            translated,
        ]
        rows = dedupe_str(rows + fallback)
    return rows[:5]


def dedupe_str(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in items:
        key = normalize_text(item).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", normalize_text(text).lower()) if len(t) > 2}


def _mmr_select(items: List[EvidenceItem], claim: str, top_k: int = 12, lambda_mult: float = 0.70) -> List[EvidenceItem]:
    if not items:
        return []
    claim_tok = _token_set(claim)
    pool = list(items)
    selected: List[EvidenceItem] = []
    selected_tok: List[set[str]] = []

    def relevance(it: EvidenceItem) -> float:
        base = max(0.0, min(1.0, float(it.relevance or it.extract_score or 0.0)))
        cred = max(0.0, min(1.0, float(it.credibility or 0) / 100.0))
        text_tok = _token_set(f"{it.title} {it.snippet}")
        overlap = (len(claim_tok & text_tok) / max(1, len(claim_tok))) if claim_tok else 0.0
        return 0.45 * base + 0.35 * cred + 0.20 * overlap

    def redundancy(it: EvidenceItem) -> float:
        if not selected:
            return 0.0
        t = _token_set(f"{it.title} {it.snippet}")
        if not t:
            return 0.0
        sims = [len(t & s) / max(1, len(t | s)) for s in selected_tok]
        return max(sims) if sims else 0.0

    while pool and len(selected) < top_k:
        best = None
        best_score = -1e9
        for it in pool:
            score = lambda_mult * relevance(it) - (1.0 - lambda_mult) * redundancy(it)
            if score > best_score:
                best_score = score
                best = it
        if best is None:
            break
        selected.append(best)
        selected_tok.append(_token_set(f"{best.title} {best.snippet}"))
        pool.remove(best)
    return selected


def _translate_claim_to_english(claim: str) -> str:
    claim = normalize_text(claim)
    if not claim:
        return ""
    if claim in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[claim]

    # Skip call for already-ascii input.
    if all(ord(ch) < 128 for ch in claim):
        _TRANSLATION_CACHE[claim] = claim
        return claim

    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip()
    model = os.getenv("GEMINI_TRANSLATE_MODEL", os.getenv("GEMINI_SEARCH_MODEL", "gemini-2.5-flash")).strip() or "gemini-2.5-flash"
    if not project or not location:
        _TRANSLATION_CACHE[claim] = ""
        return ""

    token = _get_gcloud_access_token()
    if not token:
        _TRANSLATION_CACHE[claim] = ""
        return ""

    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent"
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    prompts = [
        (
            "Translate the following claim into ONE complete English sentence for fact-check retrieval.\n"
            "Preserve entities, numbers, places, dates, and key action.\n"
            "Do NOT summarize to keywords.\n"
            "Output only translated sentence, no markdown, no JSON.\n\n"
            f"Claim: {claim}"
        ),
        (
            "Rewrite this non-English claim in clear English with 12-24 words.\n"
            "Must include who/what/where/when details if present.\n"
            "Output plain English sentence only.\n\n"
            f"Claim: {claim}"
        ),
    ]
    best = ""
    for prompt in prompts:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 256},
        }
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=max(3, int(os.getenv("GEMINI_CALL_TIMEOUT_SECONDS", "20")))) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            text = normalize_text(_extract_vertex_text(data))
            text = normalize_text(text.splitlines()[0] if text else "").strip("\"' ")
            if len(text.split()) > len(best.split()):
                best = text
            if len(best.split()) >= 8:
                break
        except Exception:
            continue
    if len(best.split()) < 4:
        best = claim
    _TRANSLATION_CACHE[claim] = best
    return best


def search_web_routed(claim: str, domain: str, max_results: int = 8) -> List[EvidenceItem]:
    pool: List[EvidenceItem] = []
    for query in routed_queries(claim, domain)[:3]:
        pool.extend(_safe_search_web(query, max_results=max(3, max_results // 2)))
        if len(dedupe_sources(pool)) >= max_results:
            break
    deduped = filter_domains(dedupe_sources(pool))
    if deduped:
        return deduped[:max_results]

    # Fallback 1: direct claim search (helps multilingual/OCR claims).
    fallback_pool = _safe_search_web(claim, max_results=max_results)
    deduped = filter_domains(dedupe_sources(fallback_pool))
    if deduped:
        return deduped[:max_results]

    # Fallback 2: broad English instruction query.
    broad = _safe_search_web(f"fact check claim official sources: {claim}", max_results=max_results)
    deduped = filter_domains(dedupe_sources(broad))
    return deduped[:max_results]


def filter_domains(items: Iterable[EvidenceItem]) -> List[EvidenceItem]:
    out: List[EvidenceItem] = []
    for item in items:
        host = urllib.parse.urlparse(item.url).netloc.lower()
        if any(hint in host for hint in BAD_DOMAIN_HINTS):
            continue
        out.append(item)
    return out


def extract_claim_facts(claim: str) -> dict:
    low = normalize_text(claim).lower()
    year_match = re.search(r"\b(19|20)\d{2}\b", low)
    rank_match = re.search(r"\b(\d+)(st|nd|rd|th)\s+largest\b", low)
    country = ""
    for token in ("india", "china", "united states", "usa", "germany", "japan", "france", "uk", "brazil"):
        if token in low:
            country = token
            break
    metric = "gdp" if "gdp" in low or "economy" in low else ("population" if "population" in low else "general")
    intent_tokens = []
    if country:
        intent_tokens.append(country)
    if metric != "general":
        intent_tokens.append(metric)
    if year_match:
        intent_tokens.append(year_match.group(0))
    if rank_match:
        intent_tokens.append(f"{rank_match.group(1)} largest")
    comparative = bool(rank_match) or any(t in low for t in ("largest", "smallest", "higher than", "lower than", "rank"))
    return {
        "country": country,
        "year": int(year_match.group(0)) if year_match else None,
        "rank": int(rank_match.group(1)) if rank_match else None,
        "metric": metric,
        "comparative": comparative,
        "intent_query": " ".join(intent_tokens).strip(),
    }


def compare_claim_to_evidence(claim: str, evidence_text: str) -> float:
    claim_tokens = {t for t in re.findall(r"[a-z0-9]+", claim.lower()) if len(t) > 2}
    evidence_tokens = set(re.findall(r"[a-z0-9]+", evidence_text.lower()))
    if not claim_tokens:
        return 0.0
    overlap = len(claim_tokens.intersection(evidence_tokens)) / float(len(claim_tokens))
    return max(0.0, min(1.0, overlap))


def gather_api_evidence(claim: str, domain: str) -> List[EvidenceItem]:
    facts = extract_claim_facts(claim)
    if domain not in {"economy", "population"}:
        return []
    country = facts.get("country") or "india"
    metric = "NY.GDP.MKTP.CD" if facts.get("metric") == "gdp" or domain == "economy" else "SP.POP.TOTL"
    country_code = "IN" if country in {"india"} else "US"
    url = f"https://api.worldbank.org/v2/country/{country_code}/indicator/{metric}?format=json"
    target_year = facts.get("year")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read(32000).decode("utf-8", errors="ignore")
    except Exception:
        return []
    snippet = normalize_text(raw)[:600]
    # Extract year-aware compact snippet when possible.
    try:
        payload = json.loads(raw)
        rows = payload[1] if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], list) else []
        selected = None
        if target_year:
            by_year = [r for r in rows if str(r.get("date", "")).isdigit()]
            if by_year:
                by_year.sort(key=lambda r: abs(int(r.get("date", 0)) - int(target_year)))
                selected = by_year[0]
        if not selected and rows:
            vals = [r for r in rows if r.get("value") is not None and str(r.get("date", "")).isdigit()]
            if vals:
                vals.sort(key=lambda r: int(r.get("date", 0)), reverse=True)
                selected = vals[0]
        if selected:
            snippet = normalize_text(
                f"indicator={metric}; country={country}; year={selected.get('date')}; value={selected.get('value')}; source=World Bank API"
            )[:600]
    except Exception:
        pass
    score = compare_claim_to_evidence(claim, snippet)
    return [
        EvidenceItem(
            title=f"World Bank indicator {metric}",
            url=url,
            snippet=snippet,
            source_type="trusted",
            channel="api_data",
            domain=domain,
            credibility=95,
            relevance=score,
            extract_score=score,
        )
    ]


def gather_web_scrape_evidence(claim: str, domain: str, max_results: int = 4) -> List[EvidenceItem]:
    seeds = search_web_routed(claim, domain=domain, max_results=max_results)
    if not seeds:
        seeds = _safe_search_web(claim, max_results=max_results)
    out: List[EvidenceItem] = []
    for seed in seeds[:max_results]:
        text = scrape_url(seed.url, max_chars=1800)
        snippet = text[:480] if text else seed.snippet
        score = compare_claim_to_evidence(claim, snippet)
        out.append(
            EvidenceItem(
                title=seed.title,
                url=seed.url,
                snippet=snippet,
                source_type=seed.source_type,
                channel="web_scrape",
                domain=domain,
                credibility=seed.credibility,
                relevance=score,
                extract_score=score,
            )
        )
    return filter_domains(out)


def gather_evidence(claim: str, domain: str, max_results: int = 10) -> List[EvidenceItem]:
    start = time.time()
    api_rows = gather_api_evidence(claim, domain)
    web_rows = search_web_routed(claim, domain=domain, max_results=min(8, max_results))
    for row in web_rows:
        row.channel = "web_search"
        row.domain = domain
        row.extract_score = compare_claim_to_evidence(claim, f"{row.title} {row.snippet}")
        row.relevance = row.extract_score
    scrape_rows: List[EvidenceItem] = []
    if (time.time() - start) < 15:
        scrape_rows = gather_web_scrape_evidence(claim, domain=domain, max_results=2)
    all_rows = dedupe_sources(api_rows + web_rows + scrape_rows)
    all_rows = filter_domains(all_rows)
    all_rows.sort(key=lambda x: (x.credibility, x.extract_score), reverse=True)
    return all_rows[:max_results]


def has_live_evidence_sources() -> bool:
    if (
        os.getenv("GEMINI_API_KEY", "").strip()
        or os.getenv("TAVILY_API_KEY", "").strip()
        or (os.getenv("GOOGLE_CLOUD_PROJECT", "").strip() and os.getenv("GOOGLE_CLOUD_LOCATION", "").strip())
    ):
        return True
    try:
        from ddgs import DDGS  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def is_offline_source(source: EvidenceItem) -> bool:
    return source.url.startswith("https://example.org/offline-demo-evidence")


def live_sources(items: Iterable[EvidenceItem]) -> List[EvidenceItem]:
    return [item for item in items if not is_offline_source(item)]


def offline_fallback_message() -> str:
    return (
        "No live evidence source returned results. Install dependencies and ensure network access, "
        "or configure GEMINI_API_KEY/TAVILY_API_KEY. For local UI demos only, set "
        "FACTLENS_ALLOW_OFFLINE_FALLBACK=1."
    )


def search_primary_sources(query: str, max_results: int = 5) -> List[EvidenceItem]:
    facts = extract_claim_facts(query)
    intent = facts.get("intent_query") or ""
    trusted_query = (
        f"{query} {intent} "
        "site:gov OR site:edu OR site:who.int OR site:nasa.gov OR site:worldbank.org OR site:imf.org OR site:oecd.org"
    )
    rows = _safe_search_web(trusted_query, max_results=max_results)
    trusted = [row for row in rows if row.source_type in {"trusted", "reference"}]
    return trusted or rows[:max_results]


def _safe_search_web(query: str, max_results: int = 5) -> List[EvidenceItem]:
    timeout_s = int(os.getenv("SEARCH_CALL_TIMEOUT_SECONDS", "10"))
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(search_web, query, max_results)
            return future.result(timeout=max(3, timeout_s))
    except Exception:
        return []


def scrape_url(url: str, max_chars: int = 4000) -> str:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FactLensCrew/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(max_chars * 4).decode("utf-8", errors="ignore")
    except Exception:
        return ""
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_text(text)[:max_chars]


def dedupe_sources(items: Iterable[EvidenceItem]) -> List[EvidenceItem]:
    seen = set()
    out: List[EvidenceItem] = []
    for item in items:
        key = item.url.rstrip("/").lower() or item.title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _search_duckduckgo(query: str, max_results: int = 5) -> List[EvidenceItem]:
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:
        return []

    rows: List[EvidenceItem] = []
    try:
        with DDGS() as ddgs:
            results = ddgs.text(query, max_results=max_results)
            for item in results:
                url = str(item.get("href") or item.get("url") or "")
                rows.append(
                    EvidenceItem(
                        title=str(item.get("title") or url or "DuckDuckGo result"),
                        url=url,
                        snippet=normalize_text(item.get("body") or item.get("snippet") or ""),
                        source_type=_source_type(url),
                        channel="web_search",
                        credibility=_credibility(url),
                    )
                )
    except Exception:
        return []
    return rows[:max_results]


def _search_gemini_grounding(query: str, max_results: int = 5) -> List[EvidenceItem]:
    if os.getenv("GEMINI_SEARCH_ENABLE", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return []

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return []

    try:
        import google.generativeai as genai  # type: ignore
    except Exception:
        return []

    prompt = (
        "Find concise fact-checking evidence for this claim. "
        "Return plain lines in the format TITLE | URL | SNIPPET. "
        "Prefer official, academic, government, and reputable news sources.\n\n"
        f"Claim: {query}"
    )
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(os.getenv("GEMINI_SEARCH_MODEL", "gemini-1.5-flash"))
        timeout_s = int(os.getenv("GEMINI_CALL_TIMEOUT_SECONDS", "20"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(model.generate_content, prompt)
            response = future.result(timeout=max(3, timeout_s))
        text = normalize_text(getattr(response, "text", "") or "")
    except Exception:
        return []

    rows: List[EvidenceItem] = []
    for line in re.split(r"\n+|(?<=\.)\s+(?=[A-Z])", text):
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        title, url, snippet = parts[0], parts[1], parts[2]
        if not url.startswith("http"):
            continue
        rows.append(
            EvidenceItem(
                title=title or "Gemini suggested source",
                url=url,
                snippet=snippet,
                source_type=_source_type(url),
                channel="gemini_search",
                credibility=_credibility(url),
            )
        )
        if len(rows) >= max_results:
            break
    return rows


def _search_vertex_grounding(query: str, max_results: int = 5) -> List[EvidenceItem]:
    if os.getenv("VERTEX_SEARCH_ENABLE", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return []
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "").strip()
    model = os.getenv("GEMINI_SEARCH_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
    if not project or not location:
        return []

    token = _get_gcloud_access_token()
    if not token:
        return []

    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    user_prompt = (
        "Find concise fact-checking evidence for this claim from live web search.\n"
        "Use only sources you can cite with valid http(s) URLs.\n"
        "Return ONLY JSON array. No markdown, no code fences, no prose.\n"
        "Array item schema: {\"title\":\"string\",\"url\":\"https://...\",\"snippet\":\"string\"}\n"
        "If no usable sources found, return [].\n"
        "Prefer official/government/academic/reputable sources. "
        f"Return up to {max_results} items (prefer exactly {max_results}). "
        "Each item MUST include a valid http(s) URL.\n\n"
        f"Claim: {query}"
    )

    # Use the supported googleSearch tool schema. Some models/regions reject
    # alternate tool names (e.g. googleSearchRetrieval) with HTTP 400.
    payload_variants = [
        {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "tools": [{"googleSearch": {}}],
            "generationConfig": {"temperature": float(os.getenv("FACTLENS_LLM_TEMPERATURE", "0.1")), "maxOutputTokens": 2048},
        }
    ]

    timeout_s = int(os.getenv("GEMINI_CALL_TIMEOUT_SECONDS", "20"))
    text = ""
    for payload in payload_variants:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=max(3, timeout_s)) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            text = _extract_vertex_text(data)
            if text:
                break
        except Exception:
            continue
    if not text:
        return []

    rows = _parse_json_evidence_rows(text, max_results=max_results)
    if not rows:
        # Fallback parser for plain text lines.
        for line in re.split(r"\n+|(?<=\.)\s+(?=[A-Z])", normalize_text(text)):
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3:
                continue
            title, link, snippet = parts[0], parts[1], parts[2]
            if not link.startswith("http"):
                continue
            link = _resolve_grounding_redirect(link)
            rows.append(
                EvidenceItem(
                    title=title or "Vertex grounded source",
                    url=link,
                    snippet=snippet,
                    source_type=_source_type(link),
                    channel="vertex_google_search",
                    credibility=_credibility(link),
                )
            )
            if len(rows) >= max_results:
                break
    return rows[:max_results]


def _extract_vertex_text(data: dict) -> str:
    try:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        chunks = [normalize_text(str(p.get("text") or "")) for p in parts if isinstance(p, dict)]
        return normalize_text(" ".join(c for c in chunks if c))
    except Exception:
        return ""


def _parse_json_evidence_rows(text: str, max_results: int = 5) -> List[EvidenceItem]:
    rows: List[EvidenceItem] = []
    if not text:
        return rows

    # Remove common Markdown code fences and surrounding noise.
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()

    # Try to locate a JSON array using greedy bracket search or regex.
    start = clean.find("[")
    end = clean.rfind("]")
    candidate = None
    if start != -1 and end != -1 and end > start:
        candidate = clean[start : end + 1]
    else:
        m = re.search(r"(\[\s*\{[\s\S]*?\}\s*\])", clean)
        if m:
            candidate = m.group(1)

    if not candidate:
        return rows

    # Attempt to parse, applying simple cleanups on common model-output issues.
    def try_load(s: str):
        try:
            return json.loads(s)
        except Exception:
            # Remove trailing commas before closing brackets/braces
            s2 = re.sub(r",\s*(\]|\})", r"\1", s)
            try:
                return json.loads(s2)
            except Exception:
                return None

    payload = try_load(candidate)
    if not isinstance(payload, list):
        return rows

    for item in payload:
        if not isinstance(item, dict):
            continue
        link = normalize_text(item.get("url") or item.get("uri") or "")
        if not link.startswith("http"):
            continue
        link = _resolve_grounding_redirect(link)
        title = normalize_text(item.get("title") or "Vertex grounded source")
        snippet = normalize_text(item.get("snippet") or "")
        rows.append(
            EvidenceItem(
                title=title,
                url=link,
                snippet=snippet,
                source_type=_source_type(link),
                channel="vertex_google_search",
                credibility=_credibility(link),
            )
        )
        if len(rows) >= max_results:
            break
    return rows


def _get_gcloud_access_token() -> str:
    token = os.getenv("GOOGLE_ACCESS_TOKEN", "").strip()
    if token:
        return token
    try:
        import google.auth  # type: ignore
        from google.auth.transport.requests import Request  # type: ignore

        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        if getattr(creds, "token", None):
            return normalize_text(str(creds.token))
    except Exception:
        pass
    keyfile = os.getenv("GOOGLE_CLOUD_KEYFILE", "").strip()
    if keyfile:
        try:
            from google.oauth2 import service_account  # type: ignore
            from google.auth.transport.requests import Request  # type: ignore

            creds = service_account.Credentials.from_service_account_file(
                keyfile,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            creds.refresh(Request())
            if creds.token:
                return normalize_text(creds.token)
        except Exception:
            pass
    try:
        proc = subprocess.run(
            ["gcloud", "auth", "print-access-token"],
            capture_output=True,
            text=True,
            timeout=8,
            check=True,
        )
        return normalize_text(proc.stdout)
    except Exception:
        return ""


def _resolve_grounding_redirect(url: str) -> str:
    if "vertexaisearch.cloud.google.com/grounding-api-redirect/" not in url:
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FactLensCrew/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            final_url = getattr(resp, "geturl", lambda: url)()
        if isinstance(final_url, str) and final_url.startswith("http"):
            return final_url
    except Exception:
        pass
    return url


def _search_tavily(query: str, api_key: str, max_results: int = 5) -> List[EvidenceItem]:
    try:
        import json

        payload = json.dumps({"query": query, "max_results": max_results}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    rows = []
    for item in data.get("results", [])[:max_results]:
        url = str(item.get("url") or "")
        rows.append(
            EvidenceItem(
                title=str(item.get("title") or url or "Tavily result"),
                url=url,
                snippet=normalize_text(item.get("content") or item.get("snippet") or ""),
                source_type=_source_type(url),
                channel="api_search",
                credibility=_credibility(url),
            )
        )
    return rows[:max_results]


def _offline_search_stub(query: str, max_results: int = 5) -> List[EvidenceItem]:
    clean = normalize_text(query)
    return [
        EvidenceItem(
            title="Offline demo evidence pack",
            url="https://example.org/offline-demo-evidence",
            snippet=f"Offline fallback only. This is not live evidence for: {clean}",
            source_type="offline",
            channel="offline",
            stance_hint="neutral",
            credibility=10,
        )
    ][:max_results]


def _search_wikipedia(query: str, max_results: int = 5) -> List[EvidenceItem]:
    try:
        q = urllib.parse.quote(query)
        search_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&format=json&srlimit={max_results}"
        with urllib.request.urlopen(search_url, timeout=10) as resp:
            import json

            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception:
        return []

    rows: List[EvidenceItem] = []
    for item in data.get("query", {}).get("search", [])[:max_results]:
        title = str(item.get("title") or "Wikipedia article")
        snippet_raw = str(item.get("snippet") or "")
        snippet = normalize_text(re.sub(r"<[^>]+>", " ", snippet_raw))
        url_title = urllib.parse.quote(title.replace(" ", "_"))
        url = f"https://en.wikipedia.org/wiki/{url_title}"
        rows.append(
            EvidenceItem(
                title=title,
                url=url,
                snippet=snippet,
                source_type="reference",
                channel="wikipedia_api",
                credibility=72,
            )
        )
    return rows
