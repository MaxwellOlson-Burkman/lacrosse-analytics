"""Quick check: parse a raw HTML file and show team count."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.data.parsers import parse_team_ranking_table

files = [
    ("2016/division_2/scoring_margin.html", "scoring_margin"),
    ("2016/division_2/scoring_offense.html", "scoring_offense"),
    ("2016/division_1/scoring_margin.html", "scoring_margin"),
    ("2020/division_2/scoring_margin.html", "scoring_margin"),
]

for rel, stat in files:
    p = Path("data/raw") / rel
    if not p.exists():
        print(f"{rel}: MISSING")
        continue
    html = p.read_text(encoding="utf-8")
    rows = parse_team_ranking_table(html, stat)
    print(f"{rel}: {len(rows)} teams parsed")
    if rows:
        r = rows[0]
        print(f"  First: {r.get('team_name')} org_id={r.get('org_id')} value={r.get('value')} record={r.get('record')}")
    if not rows:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if table:
            trs = table.find_all("tr")
            print(f"  Table has {len(trs)} rows (including header)")
            if len(trs) > 1:
                print(f"  First data row: {trs[1].get_text(strip=True)[:100]}")
