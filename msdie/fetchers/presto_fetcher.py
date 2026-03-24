"""Minimal team-site fetcher for Johns Hopkins-style stats pages.

This module supports the Big Ten Tier-2 pilot by extracting team-season
statistics from a team page path pattern like:
  /sports/mens-lacrosse/stats/{season}
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

import requests


class PrestoFetchError(Exception):
    """Raised when a Presto/team-site stats page cannot be parsed."""


def _normalize_base_url(team_url: str) -> str:
    raw = (team_url or "").strip()
    if not raw:
        raise PrestoFetchError("team_url is empty")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.netloc:
        raise PrestoFetchError(f"invalid team_url: {team_url!r}")
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _parse_pair(value: str) -> tuple[str, str]:
    match = re.search(r"(\d+)\s*-\s*(\d+)", value or "")
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def _strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "", flags=re.IGNORECASE)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def _extract_dual_stat(html: str, label: str) -> tuple[str, str]:
    pat = (
        r"<tr[^>]*>\s*"
        r"<t[dh][^>]*>\s*"
        + re.escape(label)
        + r"\s*</t[dh]>\s*"
        r"<t[dh][^>]*>(.*?)</t[dh]>\s*"
        r"<t[dh][^>]*>(.*?)</t[dh]>\s*"
        r"</tr>"
    )
    m = re.search(pat, html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return "", ""
    return _strip_html(m.group(1)), _strip_html(m.group(2))


def _extract_row_cells(html: str, label: str) -> list[str]:
    row_pat = r"<tr[^>]*>(.*?)</tr>"
    for row_html in re.findall(row_pat, html, flags=re.IGNORECASE | re.DOTALL):
        if re.search(rf">\s*{re.escape(label)}\s*<", row_html, flags=re.IGNORECASE):
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.IGNORECASE | re.DOTALL)
            return [_strip_html(c) for c in cells]
    return []


def fetch_presto_team_season_stats(
    team_url: str,
    season: int,
    timeout_seconds: int = 25,
) -> dict[str, object]:
    """Fetch and convert Johns Hopkins season stats to Sidearm-like keys."""
    base = _normalize_base_url(team_url)
    candidates = [
        f"{base}/sports/mens-lacrosse/stats/{season}",
        f"{base}/sports/mens-lacrosse/stats/season/{season}",
        f"{base}/sports/mens-lacrosse/stats",
    ]
    request_url = ""
    content_type = ""
    resp = None
    last_error = ""
    for candidate in candidates:
        request_url = candidate
        try:
            trial = requests.get(
                candidate,
                timeout=timeout_seconds,
                headers={"User-Agent": "Mozilla/5.0 (compatible; MSDIE/1.0; +https://example.local)"},
            )
        except requests.RequestException as exc:
            last_error = f"request error: {exc}"
            continue
        content_type = trial.headers.get("Content-Type") or ""
        if trial.status_code == 200:
            resp = trial
            break
        last_error = f"HTTP {trial.status_code} (content-type: {content_type!r}) for {candidate!r}"
    if resp is None:
        raise PrestoFetchError(last_error or f"all candidate stats URLs failed for {base!r}")

    record_match = re.search(r"Team\s*\((\d+)-(\d+)", resp.text, flags=re.IGNORECASE)
    wins = record_match.group(1) if record_match else ""
    losses = record_match.group(2) if record_match else ""

    gf, ga = _extract_dual_stat(resp.text, "Goals")
    shots, _opp_shots = _extract_dual_stat(resp.text, "Shots")
    shot_pct, _opp_shot_pct = _extract_dual_stat(resp.text, "Shot Percentage")
    gb, _opp_gb = _extract_dual_stat(resp.text, "Ground Balls")
    turnovers, _opp_to = _extract_dual_stat(resp.text, "Turnovers")
    caused_to, _opp_ct = _extract_dual_stat(resp.text, "Caused Turnovers")
    faceoff_wl, _opp_faceoff_wl = _extract_dual_stat(resp.text, "Faceoffs: W-L")
    faceoff_pct, _opp_faceoff_pct = _extract_dual_stat(resp.text, "Faceoffs: Percentage")
    clears, _opp_clears = _extract_dual_stat(resp.text, "Clears")
    clear_pct, _opp_clear_pct = _extract_dual_stat(resp.text, "Clear Percentage")
    powerplays, _opp_powerplays = _extract_dual_stat(resp.text, "Power Plays: G-OPP")
    powerplays_pct, _opp_powerplays_pct = _extract_dual_stat(resp.text, "Power Plays: Percentage")

    if not gf and not shots:
        total_cells = _extract_row_cells(resp.text, "Total")
        opp_cells = _extract_row_cells(resp.text, "Opponents")
        if len(total_cells) >= 18 and len(opp_cells) >= 3:
            # Totals row pattern from Sidearm "Players" table:
            # Total, GP, G, A, PTS, SH, SH%, SOG, SOG%, GWG, UP, DWN, GB, TO, CT, FO, FO%, PN-PIM
            gf = total_cells[2]
            ga = opp_cells[2]
            shots = total_cells[5]
            shot_pct = total_cells[6]
            gb = total_cells[12]
            turnovers = total_cells[13]
            caused_to = total_cells[14]
            faceoff_wl = total_cells[15]
            faceoff_pct = total_cells[16]
            # no clear/EMO splits in this compact totals row
            clears = ""
            clear_pct = ""
            powerplays = ""
            powerplays_pct = ""
        else:
            raise PrestoFetchError(f"team totals rows not found in {request_url!r}")

    fow, fol = _parse_pair(faceoff_wl)
    cl_made, cl_att = _parse_pair(clears)
    emo_g, emo_att = _parse_pair(powerplays)

    row = {
        "tid": "",  # runner fills from org_id fallback
        "season": str(season),
        "w": wins,
        "l": losses,
        "gf": gf,
        "ga": ga,
        "sh": shots,
        "sh_pct": shot_pct,
        "fow": fow,
        "fol": fol,
        "fo_pct": faceoff_pct,
        "gb": gb,
        "to": turnovers,
        "ct": caused_to,
        "cl_att": cl_att,
        "cl_made": cl_made,
        "cl_pct": clear_pct,
        "emo_g": emo_g,
        "emo_att": emo_att,
        "emo_pct": powerplays_pct,
        "sv_pct": "",
    }
    return {
        "request_url": request_url,
        "content_type": content_type,
        "rows": [row],
    }

