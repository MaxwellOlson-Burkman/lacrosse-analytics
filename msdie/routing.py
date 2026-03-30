"""Shared source-type and route-method taxonomy for team URL ingestion."""

from __future__ import annotations

from urllib.parse import urlparse

REQUIRED_TEAM_FIELDS = ("gf", "ga", "sh", "gb", "to", "ct")

ROUTE_BY_SOURCE_TYPE: dict[str, str] = {
    "ashx": "sidearm_json",
    "xml": "xml_parse",
    "pdf": "pdf_parse",
    "aspx": "html_parse",
    "html": "html_parse",
    "wmt_api": "html_parse",
}


def classify_source_type(url: str, content_type: str = "") -> str:
    u = (url or "").strip().lower()
    ct = (content_type or "").strip().lower()
    netloc = urlparse(u).netloc
    if "api.wmt.games" in netloc:
        return "wmt_api"
    if ".ashx" in u:
        return "ashx"
    if ".xml" in u or "output=xml" in u or "format=xml" in u or "xml=1" in u:
        return "xml"
    if ".pdf" in u or "application/pdf" in ct:
        return "pdf"
    if ".aspx" in u:
        return "aspx"
    return "html"


def route_method_for_source(source_type: str) -> str:
    return ROUTE_BY_SOURCE_TYPE.get((source_type or "").strip().lower(), "")


def confidence_for_probe(source_type: str, note: str) -> str:
    st = (source_type or "").strip().lower()
    n = (note or "").strip().lower()
    if n == "ok" and st in {"ashx", "xml", "pdf", "aspx", "wmt_api"}:
        return "high"
    if n == "ok":
        return "medium"
    return "low"
