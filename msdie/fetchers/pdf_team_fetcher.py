"""PDF team-season stats fetcher for MSDIE-required fields."""

from __future__ import annotations

import io
import re
from typing import Any
from urllib.parse import urljoin

import requests
from pypdf import PdfReader

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class PdfFetchError(Exception):
    """Raised when a PDF stats document cannot be parsed into required fields."""


def _clean_text(s: str) -> str:
    txt = (s or "").replace("\r", " ").replace("\n", " ")
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def _m(text: str, patterns: list[str]) -> str:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip()
    return ""


def _pair(text: str, patterns: list[str]) -> tuple[str, str]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return (m.group(1) or "").strip(), (m.group(2) or "").strip()
    return "", ""


def _quad(text: str, patterns: list[str]) -> tuple[str, str, str, str]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            return (
                (m.group(1) or "").strip(),
                (m.group(2) or "").strip(),
                (m.group(3) or "").strip(),
                (m.group(4) or "").strip(),
            )
    return "", "", "", ""


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:  # pragma: no cover - parser internals
        raise PdfFetchError(f"unable to read PDF: {exc}") from exc
    parts: list[str] = []
    for page in reader.pages[:6]:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    txt = _clean_text(" ".join(parts))
    if not txt:
        raise PdfFetchError("PDF text extraction returned empty text")
    return txt


def _extract_cume_pdf_link(html_text: str, base_url: str) -> str:
    text = html_text or ""
    patterns = [
        r'https?://[^"\']+/stats/mlax/\d{4}/pdf/cume\.pdf',
        r'href=["\']([^"\']+/stats/mlax/\d{4}/pdf/cume\.pdf)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if not m:
            continue
        candidate = (m.group(1) if (m.lastindex or 0) >= 1 else m.group(0) or "").strip()
        if candidate:
            return urljoin(base_url, candidate)
    return ""


def fetch_pdf_team_season_stats(
    pdf_url: str,
    *,
    team_name: str,
    season: int,
    timeout_seconds: float = 25.0,
) -> dict[str, Any]:
    """Parse a season-summary PDF and return a Sidearm-style row dict.

    Required output fields for MSDIE validation:
    gf, ga, sh, gb, to, ct
    """
    try:
        resp = requests.get(
            pdf_url,
            timeout=max(6.0, float(timeout_seconds)),
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    except requests.RequestException as exc:
        raise PdfFetchError(f"request failed for {pdf_url!r}: {exc}") from exc
    if resp.status_code != 200:
        raise PdfFetchError(f"request failed for {pdf_url!r}: HTTP {resp.status_code}")

    content_type = (resp.headers.get("Content-Type") or "").lower()
    raw = resp.content or b""
    looks_like_html = "text/html" in content_type or raw.startswith(b"<") or raw.startswith(b"\r\n\r\n<")
    if looks_like_html:
        cume_url = _extract_cume_pdf_link(resp.text or "", resp.url)
        if cume_url:
            try:
                resp2 = requests.get(
                    cume_url,
                    timeout=max(6.0, float(timeout_seconds)),
                    allow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                )
                if resp2.status_code == 200:
                    resp = resp2
                    raw = resp2.content or b""
            except requests.RequestException:
                pass

    text = _extract_pdf_text(raw)

    w, l = _pair(
        text,
        [
            r"all\s+games\s+(\d+)\s*-\s*(\d+)\b",
            r"overall(?:\s+record)?\s*[:\-]?\s*(\d+)\s*-\s*(\d+)",
            r"record\s*[:\-]?\s*(\d+)\s*-\s*(\d+)",
        ],
    )
    gf = _m(
        text,
        [
            r"\bgoals\s*[:\-]?\s*(\d{1,4})\b",
            r"\btotal\s+goals\s+(\d{1,4})\s+\d{1,4}\b",
            r"\bgoals-shot\s+att\.\s*(\d{1,4})-\d{1,5}\s+\d{1,4}-\d{1,5}\b",
        ],
    )
    ga = _m(
        text,
        [
            r"\bgoals\s+against\s*[:\-]?\s*(\d{1,4})\b",
            r"\bopp(?:onent)?\s+goals\s*[:\-]?\s*(\d{1,4})\b",
            r"\btotal\s+goals\s+\d{1,4}\s+(\d{1,4})\b",
            r"\bgoals-shot\s+att\.\s*\d{1,4}-\d{1,5}\s+(\d{1,4})-\d{1,5}\b",
        ],
    )
    shots = _m(
        text,
        [
            r"\bshots\s*[:\-]?\s*(\d{1,5})\b",
            r"\bgoals-shot\s+att\.\s*\d{1,4}-(\d{1,5})\s+\d{1,4}-\d{1,5}\b",
        ],
    )
    gb = _m(text, [r"\bground\s*balls?\s*[:\-]?\s*(\d{1,5})\b"])
    to = _m(text, [r"\bturnovers?\s*[:\-]?\s*(\d{1,5})\b"])
    ct = _m(
        text,
        [
            r"\bcaused\s*turnovers?\s*[:\-]?\s*(\d{1,5})\b",
            r"\bcto\b\s*[:\-]?\s*(\d{1,5})\b",
        ],
    )

    # Optional fields.
    sh_pct = _m(text, [r"\bshot\s*%+\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)\b"])
    fow, fol = _pair(text, [r"\bfaceoffs?\s*(?:won[-/ ]lost)?\s*[:\-]?\s*(\d+)\s*-\s*(\d+)\b"])
    cl_made, cl_att = _pair(text, [r"\bclears?\s*[:\-]?\s*(\d+)\s*-\s*(\d+)\b"])
    emo_g, emo_att = _pair(text, [r"\b(?:man[- ]?up|extra[- ]?man)\s*[:\-]?\s*(\d+)\s*-\s*(\d+)\b"])
    sv_pct = _m(text, [r"\bsave\s*%+\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)\b"])

    # Common Sidearm cume PDF format has paired own/opponent values in one row.
    cume_gf, cume_sh, cume_ga, cume_opp_sh = _quad(
        text,
        [r"\bgoals-shot\s+att\.\s*(\d{1,4})-(\d{1,5})\s+(\d{1,4})-(\d{1,5})\b"],
    )
    if not gf:
        gf = cume_gf
    if not ga:
        ga = cume_ga
    if not shots:
        shots = cume_sh

    missing_required = [k for k, v in {"gf": gf, "ga": ga, "sh": shots, "gb": gb, "to": to, "ct": ct}.items() if not v]
    if missing_required:
        raise PdfFetchError(
            f"PDF parse missing required fields for {team_name} {season}: {','.join(missing_required)}"
        )

    return {
        "tid": "",
        "season": str(season),
        "w": w,
        "l": l,
        "gf": gf,
        "ga": ga,
        "sh": shots,
        "sh_pct": sh_pct,
        "fow": fow,
        "fol": fol,
        "fo_pct": "",
        "gb": gb,
        "to": to,
        "ct": ct,
        "cl_att": cl_att,
        "cl_made": cl_made,
        "cl_pct": "",
        "emo_g": emo_g,
        "emo_att": emo_att,
        "emo_pct": "",
        "sv_pct": sv_pct,
        "parser_method": "pdf",
        "request_url": resp.url,
    }

