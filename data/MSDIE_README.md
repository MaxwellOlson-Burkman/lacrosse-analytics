# Multi-Source Data Ingestion Engine (MSDIE)

Scope: men's lacrosse, D1 and D2, 2021-present

This README documents what exists in the repository now and what is planned next.
Canonical strategy and implementation phases live in:

- `docs/guides/msdie_ingestion_master.md`

## 1) What MSDIE is for

MSDIE is the ingestion layer for post-2020 lacrosse data where completeness and provenance matter.
Legacy `stats.ncaa.org` outputs for 2014-2020 are frozen and should not be re-scraped.

- Legacy (read-only): `data/processed/legacy/`
- MSDIE outputs (active): `data/processed/msdie/`

## 2) Source tiers (pragmatic policy)

MSDIE uses a non-blocking tier strategy:

1. Tier 1: conference hub when verified for that conference-season
2. Tier 2: team site (default reliable path when Tier 1 fails or is incomplete)
3. Tier 3: NCAA central only for IDs/schedule scaffolding

Important:
- Tier 1 is an optimization, not a hard requirement.
- Tier 2 per-team loops are first-class production paths.
- Big Ten is a known case where conference-hub-first can fail.

## 3) Current implementation (exists today)

### Fetchers
- `msdie/fetchers/sidearm_fetcher.py`
- `msdie/fetchers/presto_fetcher.py`

### Mapping
- `msdie/mapping.py` (team-season MSDIE columns + sidearm-like mapping)

### Runners
- `scripts/run_msdie_pilot.py`
  - Conference-hub Sidearm pilot for a selected conference/year
- `scripts/run_msdie_bigten_teams.py`
  - Team-site loop for Big Ten fallback workflows
- `scripts/run_msdie_team_seasons.py`
  - Generalized team-season ingestion by year/division/conference
- `scripts/run_msdie_player_seasons.py`
  - Player-season cumulative ingestion by year/division/conference
- `scripts/build_player_season_to_date.py`
  - Aggregates in-season player-game rows to season-to-date outputs

### Registry + fingerprint
- `data/vendors.csv` (team registry and vendor metadata)
- `data/conference_urls.yaml` / `data/team_url_hints.yaml` (URL sources for enrichment)
- `scripts/seed_vendors_csv.py` (program seeding)
- `scripts/enrich_vendors_urls.py` (fill `conference_url` / probe `team_url`, audit failures)
- `scripts/fingerprint_vendor.py` (vendor signal classification)

Rows with **empty** `team_url` are skipped by `run_msdie_team_seasons.py` / `run_msdie_player_seasons.py` (see `notes` on the row). Use `--continue-on-error` for division-wide runs.

### Current outputs
- Team pilot outputs:
  - `data/processed/msdie/d1_men_{year}.csv`
  - `data/processed/msdie/d1_men_{year}_bigten_teams.csv`
- General team outputs:
  - `data/processed/msdie/team/d{division}_men_{year}[_conference].csv`
- Player-season outputs:
  - `data/processed/msdie/players/player_seasons_d{division}_{year}[_conference].csv`
- Player season-to-date rollups:
  - `data/processed/msdie/players/player_season_to_date_d{division}_{year}.csv`

## 4) Team-season schema (implemented)

Current canonical team output columns are defined in `msdie/mapping.py`:

- `team_id`
- `season`
- `division`
- `conference`
- `wins`
- `losses`
- `goals_for`
- `goals_against`
- `shots`
- `shot_pct`
- `faceoff_wins`
- `faceoff_losses`
- `faceoff_pct`
- `ground_balls`
- `turnovers`
- `caused_turnovers`
- `clears_attempted`
- `clears_made`
- `clear_pct`
- `emo_goals`
- `emo_attempts`
- `emo_pct`
- `save_pct`
- `source_method`

## 5) Planned datasets (not yet complete)

### Player-season (2021-2025)
- Full cumulative player stat lines per team-season
- Separate player schema and mapping
- Per-row provenance fields (`source_method`, `source_url`, `scraped_at_utc`)

### Player-game (2026+)
- In-season game-by-game player lines from box scores
- Season-to-date rollups derived from player-game rows
- Scheduled refresh cadence during active season

See details in `docs/guides/msdie_ingestion_master.md`.
For in-season flow and cadence, see `docs/guides/msdie_2026_player_game_pipeline.md`.

## 6) Running currently implemented commands

Conference-hub pilot:

```bash
python scripts/run_msdie_pilot.py --conference "Big Ten" --year 2024
```

Team-site Big Ten pilot:

```bash
python scripts/run_msdie_bigten_teams.py --year 2024
```

General team-season ingestion:

```bash
python scripts/run_msdie_team_seasons.py --year 2024 --division D1 --conference "Big Ten" --continue-on-error
python scripts/run_msdie_team_seasons.py --year 2024 --division D2 --continue-on-error
```

Player-season ingestion:

```bash
python scripts/run_msdie_player_seasons.py --year 2024 --division D1 --continue-on-error
python scripts/run_msdie_player_seasons.py --year 2024 --division D2 --continue-on-error
```

Seed vendor registry:

```bash
python scripts/seed_vendors_csv.py --division D1
python scripts/seed_vendors_csv.py --division D2
```

Fingerprint vendor signals:

```bash
python scripts/fingerprint_vendor.py --conference "Big Ten" --probe
```

Build season-to-date from player-game rows:

```bash
python scripts/build_player_season_to_date.py --in data/processed/msdie/players/player_games_d1_2026.csv --out data/processed/msdie/players/player_season_to_date_d1_2026.csv --qa-out data/audit/player_game_goal_sums_d1_2026.csv
```

## 7) Planned commands (future or newly added tooling)

If this section references scripts not present in your checkout, treat those as roadmap only.
Do not assume an all-in-one `run_msdie.py` exists unless it is explicitly added to the repo.

## 8) Agent and contributor rules

1. Always consult `data/vendors.csv` first.
2. Prefer registry metadata updates over hard-coded URL overrides.
3. Attempt Tier 1 when verified, but do not block ingestion on Tier 1 failure.
4. Set `source_method` for every output row.
5. Never write MSDIE outputs into `data/processed/legacy/`.
6. Save unresolved or failed rows to audit outputs; do not silently drop.

# Multi-Source Data Ingestion Engine (MSDIE)
### High-Fidelity NCAA Lacrosse Data Pipeline — v2.0
#### Scope: D1 Men | D2 Men | 2021–Present

---

## 1. Purpose and Context

This engine replaces the legacy `run_pipeline.py` scraper that targeted `stats.ncaa.org` as its primary data source. The NCAA central hub consistently produced incomplete records — most critically, missing **Faceoff Win/Loss**, **Ground Ball**, and **Defensive** metrics that are high-leverage features in the predictive models.

MSDIE resolves this by going **directly to the source**: Conference-level hubs and Primary Team sites, which maintain the most complete and up-to-date statistical records for any given season. This approach achieves 99%+ data completeness across all tracked D1 and D2 Men's programs.

### Data Strategy Split

| Mode | Source | Years | Purpose |
|---|---|---|---|
| **Legacy (frozen)** | stats.ncaa.org | 2014–2020 | Historical reference only — do not re-scrape |
| **MSDIE (active)** | Conference + Team Sites | 2021–Present | Model training, live rankings, in-season updates |

> **Important for Cursor/AI Agents:** Do not modify or re-scrape the legacy dataset located at `data/processed/legacy/`. Treat it as strictly read-only. All new ingestion writes to `data/processed/msdie/`.

---

## 2. Source Tier Hierarchy

MSDIE follows a strict priority order to minimize server load and maximize data completeness. Always attempt a higher tier before falling back to the next.

```
Tier 1: Conference Hubs        (e.g., theacc.com, big10sports.com)
    ↓ fallback if incomplete
Tier 2: Primary Team Sites     (e.g., fightingirish.com, umterps.com)
    ↓ fallback if incomplete
Tier 3: NCAA Central           (stats.ncaa.org — cross-reference only)
```

### Tier Descriptions

**Tier 1 — Conference Hubs (Primary)**
- Sidearm/Presto-powered conference sites aggregate stats for 8–12 teams in a single request
- Reduces total API calls by ~85% compared to scraping individual team sites
- Most complete source for in-season and recently completed season data
- Target these first for every conference before moving to Tier 2

**Tier 2 — Primary Team Sites (Gap Fill)**
- Used when a conference hub is missing data for a specific team or game
- Best source for individual game logs, player bios, and deep box scores
- Each team site must be fingerprinted first (see Step 1 below)

**Tier 3 — NCAA Central (Cross-Reference Only)**
- Used strictly to resolve Team IDs and Season Schedule structures
- Do not use as a primary stats source — this is what MSDIE was built to replace
- Only invoke Tier 3 when Tier 1 and Tier 2 both fail for a given record

---

## 3. Step-by-Step Implementation

### Step 1: Vendor Fingerprinting (Discovery)

Before scraping, each conference and team site must be categorized by its technology vendor. Inspect HTML structure and network headers to identify the provider.

| Vendor | Market Share | Target Endpoint |
|---|---|---|
| **Sidearm Sports** | ~70% | `/services/responsive-stats.ashx` (JSON) |
| **PrestoSports** | ~20% | `/gameday` feeds (JSON/XML) |
| **Custom/Legacy** | ~10% | BeautifulSoup HTML parsing |

- Store all fingerprint results in `data/vendors.csv`
- Locked Step 1 columns:
  - `org_id` (required, stable team identifier from processed team stats)
  - `team_name` (required)
  - `conference` (required)
  - `division` (required, `D1`/`D2`; Step 1 execution currently D1-only)
  - `vendor` (required enum: `sidearm`, `presto`, `custom`, `unknown`)
  - `conference_url` (optional, duplicated on each team row for same conference)
  - `team_url` (optional, canonical team athletics URL for fallback)
  - `vendor_confidence` (optional enum: `high`, `medium`, `low`)
  - `last_verified_utc` (optional ISO8601 timestamp for last manual/automated validation)
  - `notes` (optional short free text)
- **Conference-hub strategy (locked):** keep `conference_url` on each team row in `vendors.csv` (no separate conference hubs file in Step 1).
- **Classification heuristics (locked):**
  - `sidearm`: HTML or scripts contain `sidearm`, `sidearmsports`, `learfieldsidearm`, or endpoint pattern `/services/responsive-stats.ashx`
  - `presto`: HTML or scripts contain `prestosports` or endpoint pattern `/gameday`
  - `custom`: neither Sidearm nor Presto signals are found, but site is reachable and serves custom stats pages
  - `unknown`: URL missing/unverified or fingerprint signals are insufficient
- **Rule for Cursor/AI Agents:** Always consult `data/vendors.csv` first. If a `conference_url` exists for a team, use it over the individual `team_url`.

### Step 2: Conference Hub Aggregation

For each conference in scope, attempt to pull the full statistical leaderboard in a single request before touching any individual team site.

- **Sidearm endpoint pattern:** `https://{conference_domain}/services/responsive-stats.ashx?type=team&division=1`
- **PrestoSports endpoint pattern:** `https://{conference_domain}/gameday/{sport}/stats`
- A successful Tier 1 pull covers all teams in that conference — mark them complete in `data/vendors.csv` before proceeding
- If a conference hub is blocked or does not expose usable stats endpoints, run a
  Tier 2 per-team loop for that conference instead of stalling ingestion.
  Big Ten pilot command:
  - `python scripts/run_msdie_bigten_teams.py --year 2024`
  - This runner fetches each school site and writes
    `data/processed/msdie/d1_men_{year}_bigten_teams.csv`

### Step 3: Protocol-Specific Ingestion

Use the correct fetcher based on the vendor fingerprint identified in Step 1.

**JSON Fetcher (Sidearm/Presto)**
- Ingests structured data directly: Player IDs, Shot Locations, FO Wins/Losses, Ground Balls
- Parse response into a normalized Pandas DataFrame
- Map vendor-specific field names to the MSDIE schema (see Section 4)

**XML Parser (Legacy/StatCrew)**
- Used for smaller D2 programs that publish StatCrew `.xml` files
- Target the `/stats/` or `/statcrew/` directory on the team site
- Parse with `lxml` and map to the MSDIE schema

**BeautifulSoup Fallback**
- Last resort for custom or legacy sites with no structured data endpoint
- Flag these records in the output with `source_method: "html_parse"` for manual review

### Step 4: Opponent Mirroring (Validation)

Every game record is cross-referenced between both participating teams. This is the primary mechanism for resolving NULL faceoff and ground ball values.

**The Mirroring Equation:**

$$Team_{A}[FO_{wins}] + Team_{B}[FO_{wins}] = Game[Total_{Faceoffs}]$$

- If Team A's record has a NULL faceoff value, retrieve Team B's site for that Game ID and derive the missing value
- Any record where the equation does not balance flags automatically for a Network Sniff or manual audit
- Log all mirrored records with `source_method: "opponent_mirror"` in the output CSV

### Step 5: Relational Normalization

All ingested data is normalized to match the existing project schema before writing to `data/processed/msdie/`.

- Run `scripts/normalize_msdie.py` after each ingestion batch
- This script maps vendor field names → MSDIE schema fields (see Section 4)
- Output: one CSV and one Parquet file per season per division
  - `data/processed/msdie/d1_men_{year}.csv`
  - `data/processed/msdie/d2_men_{year}.csv`

---

## 4. MSDIE Schema (Field Mapping Reference)

All ingested data must conform to this schema before being written to processed output. Use this as the normalization target in `scripts/normalize_msdie.py`.

| MSDIE Field | Description | Sidearm Key (example) |
|---|---|---|
| `team_id` | NCAA Team ID | `tid` |
| `season` | 4-digit year | `season` |
| `division` | `D1` or `D2` | derived |
| `conference` | Conference abbreviation | `conf` |
| `wins` | Season wins | `w` |
| `losses` | Season losses | `l` |
| `goals_for` | Total goals scored | `gf` |
| `goals_against` | Total goals allowed | `ga` |
| `shots` | Total shots | `sh` |
| `shot_pct` | Shooting percentage | `sh_pct` |
| `faceoff_wins` | Faceoff wins | `fow` |
| `faceoff_losses` | Faceoff losses | `fol` |
| `faceoff_pct` | Faceoff win percentage | `fo_pct` |
| `ground_balls` | Ground balls | `gb` |
| `turnovers` | Turnovers | `to` |
| `caused_turnovers` | Caused turnovers | `ct` |
| `clears_attempted` | Clear attempts | `cl_att` |
| `clears_made` | Successful clears | `cl_made` |
| `clear_pct` | Clearing percentage | `cl_pct` |
| `emo_goals` | Extra-man opportunity goals | `emo_g` |
| `emo_attempts` | Extra-man opportunity attempts | `emo_att` |
| `emo_pct` | Extra-man efficiency | `emo_pct` |
| `save_pct` | Goalkeeper save percentage | `sv_pct` |
| `source_method` | How the record was obtained | derived |

> Any field that cannot be resolved after Tier 1 → Tier 2 → Opponent Mirror must be written as `NULL` and flagged with `source_method: "unresolved"` for manual audit.

---

## 5. File Structure

```
msdie/
├── fetchers/
│   ├── sidearm_fetcher.py       # JSON fetcher for Sidearm endpoints
│   ├── presto_fetcher.py        # JSON/XML fetcher for PrestoSports
│   └── bs4_fetcher.py           # BeautifulSoup HTML fallback
├── parsers/
│   ├── json_parser.py           # Parses Sidearm/Presto JSON responses
│   └── xml_parser.py            # Parses StatCrew XML files
├── validators/
│   └── mirror_validator.py      # Opponent mirroring + faceoff equation check
├── run_msdie.py                 # Main entry point (replaces run_pipeline.py for 2021+)
└── README.md                    # This file

data/
├── vendors.csv                  # Fingerprint registry (team → vendor → URLs)
├── processed/
│   ├── legacy/                  # 2014–2020, read-only, do not modify
│   └── msdie/                   # 2021–Present MSDIE output
│       ├── d1_men_2021.csv
│       ├── d1_men_2022.csv
│       ├── ...
│       ├── d2_men_2021.csv
│       └── ...

scripts/
└── normalize_msdie.py           # Field mapping + schema normalization
```

---

## 6. Running the Pipeline

### Initial Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

### Run Full Ingestion (All conferences, all divisions, current season)

```bash
python msdie/run_msdie.py
```

### Run for a Specific Year

```bash
python msdie/run_msdie.py --year 2024
```

### Run for a Specific Division

```bash
python msdie/run_msdie.py --division d1
python msdie/run_msdie.py --division d2
```

### Run for a Specific Conference

```bash
python msdie/run_msdie.py --conference ACC
```

### Validate Faceoff Integrity After Ingestion

```bash
python msdie/validators/mirror_validator.py --year 2024
```

### Normalize Output to MSDIE Schema

```bash
python scripts/normalize_msdie.py --year 2024
```

---

## 7. Rules for Cursor / AI Agents

These rules govern how any AI agent or automated tool should interact with this codebase.

1. **Always consult `data/vendors.csv` first.** If a `conference_url` exists for a team, use it instead of the individual `team_url`.
2. **Attempt Tier 1 before Tier 2.** Never scrape an individual team site if a conference hub covers that team.
3. **Attempt the JSON/XML endpoint before HTML parsing.** Look for `/services/`, `/sidearmstats/`, or `/gameday/` paths before falling back to BeautifulSoup.
4. **Apply the Mirroring Equation to all NULL faceoff fields** before marking a record as unresolved.
5. **Never write to `data/processed/legacy/`.** That dataset is frozen and read-only.
6. **Always set `source_method`** on every record so the provenance of each data point is traceable.
7. **Log all unresolved NULLs** to `data/audit/unresolved_{year}.csv` for manual review — do not silently drop records.

---

## 8. Development Roadmap

- [ ] Build `data/vendors.csv` — fingerprint all D1 and D2 Men's programs (conferences + teams)
- [ ] Implement `sidearm_fetcher.py` — JSON ingestion for ~70% of programs
- [ ] Implement `presto_fetcher.py` — JSON/XML ingestion for ~20% of programs
- [ ] Implement `bs4_fetcher.py` — HTML fallback for remaining ~10%
- [ ] Implement `mirror_validator.py` — faceoff mirroring equation + NULL resolution
- [ ] Implement `normalize_msdie.py` — field mapping to MSDIE schema
- [ ] Backfill 2021–2023 seasons
- [ ] Wire MSDIE output into Django models and RAG pipeline
- [ ] Automate weekly in-season ingestion via cron or scheduled task