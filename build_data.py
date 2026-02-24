#!/usr/bin/env python3
"""
Build script for HoopsMatic Salary Season Finder.
Downloads data from Google Sheets, merges with local agent/salary cap data,
computes derived fields, and outputs data/data.json.

Usage:
    python3 build_data.py --local      # Read CSVs from data_sources/ (for GH Actions)
    python3 build_data.py --download   # Download CSVs from Google Sheets, save to data_sources/, then process
    python3 build_data.py              # Same as --download with fallback to --local
"""

import csv
import json
import os
import re
import sys
import io
import unicodedata
from collections import defaultdict
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_DIR = os.path.join(BASE_DIR, "data_sources")
OUT_DIR = os.path.join(BASE_DIR, "data")

SHEET_ID = "1ZrDfzqiC31Hu3YCtxT4aZbZF4QVCVyGe6wBytR2LF30"

# Map from local filename -> Google Sheets gid
CSV_SOURCES = {
    "stats.csv":                "0",
    "salaries_historical.csv":  "1151460858",
    "salaries_future.csv":      "1555460703",
    "awards.csv":               "1456513900",
    "bio.csv":                  "1488063724",
}

# ── Team abbreviation mapping ──────────────────────────────────────────
TEAM_ABBREV = {
    # Full names
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA", "Charlotte Bobcats": "CHA", "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU",
    "Indiana Pacers": "IND", "Los Angeles Clippers": "LAC", "LA Clippers": "LAC",
    "Los Angeles Lakers": "LAL", "LA Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Vancouver Grizzlies": "VAN", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New Orleans Hornets": "NOH", "New Orleans/Oklahoma City Hornets": "NOK",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS", "Seattle SuperSonics": "SEA",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
    "Washington Bullets": "WAS", "New Jersey Nets": "NJN",
    # Standard 3-letter codes
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL", "MEM": "MEM",
    "VAN": "VAN", "MIA": "MIA", "MIL": "MIL", "MIN": "MIN", "NOP": "NOP",
    "NOH": "NOH", "NOK": "NOK", "NYK": "NYK", "OKC": "OKC", "ORL": "ORL",
    "PHI": "PHI", "PHX": "PHX", "POR": "POR", "SAC": "SAC", "SAS": "SAS",
    "SEA": "SEA", "TOR": "TOR", "UTA": "UTA", "WAS": "WAS", "NJN": "NJN",
    # Alternate short forms
    "GS": "GSW", "NO": "NOP", "NY": "NYK", "PHO": "PHX", "SA": "SAS",
    "WSH": "WAS", "NJ": "NJN", "UTAH": "UTA",
    "CHH": "CHA", "CHO": "CHA",  # Charlotte historical
    "TOT": "TOT",  # Total (multi-team)
    # Common wrong abbreviations from data sources
    "GOL": "GSW", "NEW": "NOP", "SAN": "SAS", "BRO": "BKN", "OKL": "OKC",
    # City-only names (from future salaries sheet)
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "Charlotte": "CHA",
    "Chicago": "CHI", "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN",
    "Detroit": "DET", "Golden State": "GSW", "Houston": "HOU", "Indiana": "IND",
    "LA Clippers": "LAC", "LA Lakers": "LAL", "Los Angeles": "LAL",
    "Memphis": "MEM", "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN",
    "New Orleans": "NOP", "New York": "NYK", "Oklahoma City": "OKC",
    "Orlando": "ORL", "Philadelphia": "PHI", "Phoenix": "PHX",
    "Portland": "POR", "Sacramento": "SAC", "San Antonio": "SAS",
    "Seattle": "SEA", "Toronto": "TOR", "Utah": "UTA", "Washington": "WAS",
    "New Jersey": "NJN", "Vancouver": "VAN",
}

# Known name aliases (old_name -> canonical_name used in salary data)
NAME_ALIASES = {
    "metta world peace": "ron artest",
    "metta sandiford-artest": "ron artest",
}


# ── Parsing helpers ────────────────────────────────────────────────────
def normalize_team(team_str):
    if not team_str:
        return ""
    t = team_str.strip()
    if t in TEAM_ABBREV:
        return TEAM_ABBREV[t]
    if t.upper() in TEAM_ABBREV:
        return TEAM_ABBREV[t.upper()]
    return t[:3].upper()


def strip_accents(s):
    """Remove accent marks: Jokić -> Jokic."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def normalize_name(name):
    """Normalize player name for fuzzy matching across data sources."""
    if not name:
        return ""
    name = name.strip()
    n = strip_accents(name).lower()
    # Remove suffixes
    for suf in [" jr.", " jr", " sr.", " sr", " iii", " ii", " iv", " v"]:
        n = n.replace(suf, "")
    n = n.replace(".", "").replace("'", "").replace("-", " ")
    n = " ".join(n.split())  # collapse whitespace
    # Apply known aliases
    if n in NAME_ALIASES:
        n = NAME_ALIASES[n]
    return n


def year_to_season(year_val):
    """2025 -> '2024-25',  1999 -> '1998-99',  2000 -> '1999-00'."""
    try:
        y = int(year_val)
    except (ValueError, TypeError):
        return None
    if y < 1000:
        return None
    return f"{y-1}-{str(y)[-2:]}"


def season_to_year(season):
    """'2024-25' -> 2025,  '1999-00' -> 2000."""
    if not season:
        return None
    if season == "1998--1":
        return 1999
    parts = season.split("-")
    if len(parts) != 2:
        return None
    try:
        start = int(parts[0])
        end_short = int(parts[1])
        century = start // 100 * 100
        end = century + end_short
        if end <= start:
            end += 100
        return end
    except (ValueError, TypeError):
        return None


def normalize_season(season_str):
    """Normalize various season formats to 'YYYY-YY'."""
    if not season_str:
        return None
    s = str(season_str).strip()
    if re.match(r"^\d{4}-\d{2}$", s):
        return s
    if s == "1998--1":
        return "1998-99"
    if re.match(r"^\d{4}$", s):
        return year_to_season(int(s))
    m = re.match(r"^(\d{4})-(\d{4})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)[-2:]}"
    return None


def parse_salary(val):
    if not val:
        return None
    v = str(val).strip()
    if v.lower() in ("n/a", "", "-", "nan", "none"):
        return None
    v = v.replace("$", "").replace(",", "").replace(" ", "")
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def parse_float(val):
    if not val:
        return None
    v = str(val).strip()
    if v.lower() in ("n/a", "", "-", "nan", "none"):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def parse_int(val):
    if not val:
        return None
    v = str(val).strip()
    if v.lower() in ("n/a", "", "-", "nan", "none"):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def parse_csv_string(csv_string):
    if not csv_string:
        return []
    return list(csv.DictReader(io.StringIO(csv_string)))


# ── Data loading ───────────────────────────────────────────────────────
def load_csv_file(filename):
    """Load a CSV from data_sources/ directory."""
    path = os.path.join(SOURCES_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:
            data = f.read()
        print(f"  Loaded {filename} ({len(data):,} bytes)")
        return data
    print(f"  WARNING: {path} not found")
    return None


def download_csvs():
    """Download all CSVs from Google Sheets into data_sources/."""
    import urllib.request
    os.makedirs(SOURCES_DIR, exist_ok=True)
    for filename, gid in CSV_SOURCES.items():
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"
        path = os.path.join(SOURCES_DIR, filename)
        print(f"  Downloading {filename}...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=60)
            data = resp.read()
            with open(path, "wb") as f:
                f.write(data)
            print(f"    Saved {len(data):,} bytes to {path}")
        except Exception as e:
            print(f"    FAILED: {e}")


def load_salary_cap():
    """Load salary cap data from CSV."""
    cap_path = os.path.join(BASE_DIR, "salary_cap_info.csv")
    print(f"  Loading salary cap from {cap_path}")
    cap_data = {}
    with open(cap_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            season_raw = row.get("Season", "").strip()
            season = normalize_season(season_raw)
            if not season:
                continue
            cap_data[season] = {
                "cap": parse_salary(row.get("Salary Cap")),
                "tax": parse_salary(row.get("Luxury Tax")),
                "apron1": parse_salary(row.get("1st Apron")),
                "apron2": parse_salary(row.get("2nd Apron")),
            }
    print(f"    Loaded {len(cap_data)} seasons")
    return cap_data


def load_agent_data():
    """Load agent tracker data."""
    path = os.path.join(BASE_DIR, "data_raw.json")
    print(f"  Loading agent data from {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ps = data.get("playerSalaries", {})
    ce = data.get("playerCareerEarnings", {})
    ad = data.get("agentData", [])
    print(f"    {len(ps)} players, {len(ad)} agent records, {len(ce)} career earnings")
    return ps, ce, ad


def build_agent_lookup(agent_records):
    lookup = defaultdict(list)
    for rec in agent_records:
        player = rec.get("player", "").strip()
        if not player:
            continue
        lookup[normalize_name(player)].append({
            "agent": rec.get("agent", ""),
            "start": rec.get("start", ""),
            "end": rec.get("end", ""),
            "current": rec.get("current", False),
            "team": rec.get("team", ""),
        })
    return lookup


def find_agent_for_season(agent_lookup, player_name, season):
    records = agent_lookup.get(normalize_name(player_name), [])
    if not records:
        return None
    end_year = season_to_year(season)
    if not end_year:
        return records[0]["agent"]
    season_end = date(end_year, 6, 30)
    season_start = date(end_year - 1, 10, 1)
    best = None
    for rec in records:
        try:
            a_start = datetime.strptime(rec["start"], "%Y-%m-%d").date() if rec["start"] else date(1900, 1, 1)
        except ValueError:
            a_start = date(1900, 1, 1)
        try:
            a_end = datetime.strptime(rec["end"], "%Y-%m-%d").date() if rec["end"] else date(2099, 12, 31)
        except ValueError:
            a_end = date(2099, 12, 31)
        if a_start <= season_end and a_end >= season_start:
            best = rec["agent"]
    if best:
        return best
    for rec in records:
        if rec.get("current"):
            return rec["agent"]
    return records[0]["agent"]


# ── CSV processors ─────────────────────────────────────────────────────
def process_stats(csv_data):
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}
    lookup = {}
    for row in rows:
        player = row.get("PLAYER", "").strip()
        year_raw = row.get("YEAR", "").strip()
        team = row.get("TEAM", "").strip()
        if not player or not year_raw:
            continue
        season = normalize_season(year_raw)
        if not season:
            continue
        key = (normalize_name(player), season)
        stats = {
            "player_original": player,
            "team": normalize_team(team),
            "gp": parse_int(row.get("GP")),
            "min": parse_int(row.get("MIN")),
            "pts": parse_int(row.get("PTS")),
            "age": parse_int(row.get("AGE (Feb 1)")),
            "ppg": parse_float(row.get("PTS / G")),
            "rpg": parse_float(row.get("REB / G")),
            "apg": parse_float(row.get("AST / G")),
            "spg": parse_float(row.get("STL / G")),
            "bpg": parse_float(row.get("BLK / G")),
            "fg_pct": parse_float(row.get("FG%")),
            "tp_pct": parse_float(row.get("3P%")),
            "ft_pct": parse_float(row.get("FT%")),
        }
        if key not in lookup:
            lookup[key] = []
        lookup[key].append(stats)
    print(f"    Parsed {len(lookup)} unique player-seasons from stats")
    return lookup


def process_salaries_csv(csv_data):
    """Parse historical salaries. Uses column positions (0=TEAM, 1=YEAR,
    2=PLAYER, 3=SALARY) because the header row has duplicate column names
    from extra spreadsheet sections embedded in the same sheet."""
    if not csv_data:
        return {}
    reader = csv.reader(io.StringIO(csv_data))
    header = next(reader, None)
    if not header:
        return {}
    lookup = {}
    for cols in reader:
        if len(cols) < 4:
            continue
        team = cols[0].strip()
        year_raw = cols[1].strip()
        player = cols[2].strip()
        salary = parse_salary(cols[3])
        if not player or not year_raw or salary is None:
            continue
        season = normalize_season(year_raw)
        if not season:
            continue
        key = (normalize_name(player), season)
        if key not in lookup:
            lookup[key] = []
        lookup[key].append({
            "player_original": player,
            "team": normalize_team(team),
            "salary": salary,
        })
    print(f"    Parsed {len(lookup)} player-seasons from historical salaries")
    return lookup


def process_future_salaries(csv_data):
    """Process future salaries sheet (one row per player, year columns)."""
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}
    lookup = {}
    year_cols = ["2026", "2027", "2028", "2029", "2030", "2031"]
    for row in rows:
        player = row.get("PLAYER", "").strip()
        team = row.get("TEAM", "").strip()
        if not player:
            continue
        for ycol in year_cols:
            salary = parse_salary(row.get(ycol))
            if salary is None or salary == 0:
                continue
            season = year_to_season(int(ycol))
            if not season:
                continue
            key = (normalize_name(player), season)
            if key not in lookup:
                lookup[key] = []
            lookup[key].append({
                "player_original": player,
                "team": normalize_team(team),
                "salary": salary,
            })
    print(f"    Parsed {len(lookup)} player-seasons from future salaries")
    return lookup


def process_awards(csv_data):
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}, set()
    lookup = {}
    all_awards = set()
    for row in rows:
        player = row.get("PLAYER / COACH", "").strip()
        year_raw = row.get("YEAR", "").strip()
        awards_str = row.get("SEASON AWARDS", "").strip()
        if not player or not year_raw:
            continue
        season = normalize_season(year_raw)
        if not season:
            continue
        key = (normalize_name(player), season)
        awards = [a.strip() for a in awards_str.split(",") if a.strip()] if awards_str else []
        all_awards.update(awards)
        if key not in lookup:
            lookup[key] = awards
        else:
            existing = set(lookup[key])
            existing.update(awards)
            lookup[key] = list(existing)
    print(f"    Parsed {len(lookup)} player-seasons with awards, {len(all_awards)} award types")
    return lookup, all_awards


def process_bio(csv_data):
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}
    lookup = {}
    for row in rows:
        player = row.get("PLAYER", "").strip()
        if not player:
            continue
        lookup[normalize_name(player)] = {
            "pos": row.get("POS", "").strip(),
            "height": row.get("HEIGHT", "").strip(),
            "weight": parse_int(row.get("WEIGHT")),
            "nationality": row.get("NATIONALITY", "").strip(),
            "college": row.get("COLLEGE / TEAM", "").strip(),
            "draft_year": parse_int(row.get("DRAFT")),
            "draft_pick": parse_int(row.get("PICK")),
            "birthday": row.get("BIRTHDAY", "").strip(),
        }
    print(f"    Parsed {len(lookup)} player bios")
    return lookup


# ── Main build ─────────────────────────────────────────────────────────
def build_data():
    print("=" * 60)
    print("HoopsMatic Salary Season Finder — Data Builder")
    print("=" * 60)

    mode = "auto"
    if "--local" in sys.argv:
        mode = "local"
    elif "--download" in sys.argv:
        mode = "download"

    # Step 1: Download if requested
    if mode in ("download", "auto"):
        print("\n[1/7] Downloading CSVs from Google Sheets...")
        try:
            download_csvs()
        except Exception as e:
            print(f"  Download failed: {e}")
            if mode == "download":
                print("  FATAL: --download mode requires network access")
                sys.exit(1)
            print("  Falling back to local files...")
    else:
        print("\n[1/7] Skipping download (--local mode)")

    # Step 2: Load local data
    print("\n[2/7] Loading salary cap data...")
    salary_cap = load_salary_cap()

    print("\n[3/7] Loading agent tracker data...")
    agent_salaries, career_earnings_map, agent_records = load_agent_data()
    agent_lookup = build_agent_lookup(agent_records)

    # Step 3: Load CSVs from data_sources/
    print("\n[4/7] Loading CSV data from data_sources/...")
    stats_csv = load_csv_file("stats.csv")
    hist_sal_csv = load_csv_file("salaries_historical.csv")
    future_sal_csv = load_csv_file("salaries_future.csv")
    awards_csv = load_csv_file("awards.csv")
    bio_csv = load_csv_file("bio.csv")

    # Step 4: Parse
    print("\n[5/7] Parsing CSV data...")
    stats_lookup = process_stats(stats_csv) if stats_csv else {}
    hist_sal_lookup = process_salaries_csv(hist_sal_csv) if hist_sal_csv else {}
    future_sal_lookup = process_future_salaries(future_sal_csv) if future_sal_csv else {}
    awards_lookup, all_awards_set = process_awards(awards_csv) if awards_csv else ({}, set())
    bio_lookup = process_bio(bio_csv) if bio_csv else {}

    # Merge historical + future salary lookups
    salary_csv_lookup = {}
    for k, v in hist_sal_lookup.items():
        salary_csv_lookup[k] = v
    for k, v in future_sal_lookup.items():
        if k in salary_csv_lookup:
            salary_csv_lookup[k].extend(v)
        else:
            salary_csv_lookup[k] = v

    # Step 5: Build unified player-season list
    print("\n[6/7] Merging data and computing derived fields...")

    # Start from agent tracker salary data (most comprehensive salary source)
    # Key: (normalized_name, season) -> record dict
    ps_map = {}
    for player_name, seasons in agent_salaries.items():
        for season_raw, salary in seasons.items():
            season = normalize_season(season_raw)
            if not season:
                continue
            end_year = season_to_year(season)
            if not end_year or end_year < 1991:
                continue
            key = (normalize_name(player_name), season)
            if key not in ps_map or salary > ps_map[key]["salary"]:
                ps_map[key] = {
                    "player": player_name,
                    "season": season,
                    "salary": salary,
                    "end_year": end_year,
                    "team": "",
                }

    # Merge CSV salaries: add missing, update team info
    for (nk, season), recs in salary_csv_lookup.items():
        for rec in recs:
            end_year = season_to_year(season)
            if not end_year or end_year < 1991:
                continue
            key = (nk, season)
            if key in ps_map:
                # Update team from CSV if we don't have one
                if rec.get("team") and not ps_map[key]["team"]:
                    ps_map[key]["team"] = rec["team"]
            else:
                ps_map[key] = {
                    "player": rec["player_original"],
                    "season": season,
                    "salary": rec["salary"],
                    "end_year": end_year,
                    "team": rec.get("team", ""),
                }

    player_season_list = list(ps_map.values())
    print(f"    Total player-season records: {len(player_season_list)}")

    # Compute league-wide salary ranks per season
    by_season = defaultdict(list)
    for ps in player_season_list:
        by_season[ps["season"]].append(ps)
    for season, recs in by_season.items():
        recs.sort(key=lambda x: x["salary"], reverse=True)
        for rank, rec in enumerate(recs, 1):
            rec["salary_rank_league"] = rank

    # Compute years of experience and career earnings
    player_years = defaultdict(set)
    player_yearly_salary = defaultdict(lambda: defaultdict(int))
    for ps in player_season_list:
        nk = normalize_name(ps["player"])
        player_years[nk].add(ps["end_year"])
        player_yearly_salary[nk][ps["end_year"]] += ps["salary"]

    # Build final records
    print("    Building final records...")
    all_seasons_set = set()
    all_teams_set = set()
    all_agents_set = set()
    all_players_set = set()
    final_records = []

    for ps in player_season_list:
        player = ps["player"]
        season = ps["season"]
        salary = ps["salary"]
        end_year = ps["end_year"]
        nk = normalize_name(player)
        sk = (nk, season)

        # Stats
        stats_list = stats_lookup.get(sk, [])
        stats = None
        if stats_list:
            tot = [s for s in stats_list if s.get("team") == "TOT"]
            nontot = [s for s in stats_list if s.get("team") != "TOT"]
            stats = tot[0] if tot else (nontot[0] if nontot else stats_list[0])

        # Team: prefer CSV salary team, then stats team
        team = ps.get("team", "")
        if not team and stats and stats.get("team") and stats["team"] != "TOT":
            team = stats["team"]
        if not team and stats_list:
            for s in stats_list:
                if s.get("team") and s["team"] != "TOT":
                    team = s["team"]
                    break
        if not team:
            csv_recs = salary_csv_lookup.get(sk, [])
            if csv_recs:
                team = csv_recs[0].get("team", "")

        # Awards
        awards = awards_lookup.get(sk, [])

        # Bio
        bio = bio_lookup.get(nk, {})

        # Agent
        agent = find_agent_for_season(agent_lookup, player, season)

        # Cap %
        cap_info = salary_cap.get(season, {})
        cap = cap_info.get("cap")
        tax = cap_info.get("tax")
        cap_pct = round(salary / cap * 100, 2) if cap and salary else None
        tax_pct = round(salary / tax * 100, 2) if tax and salary else None

        # Years of experience (completed seasons only; current season doesn't count)
        years_exp = sum(1 for y in player_years.get(nk, set()) if y < end_year)

        # Career earnings to date
        career_earnings = sum(
            v for y, v in player_yearly_salary.get(nk, {}).items() if y <= end_year
        )

        # Cost metrics
        gp = stats.get("gp") if stats else None
        pts = stats.get("pts") if stats else None
        ppg = stats.get("ppg") if stats else None
        rpg = stats.get("rpg") if stats else None
        apg = stats.get("apg") if stats else None
        cost_per_point = round(salary / pts) if pts and pts > 0 and salary else None
        cost_per_game = round(salary / gp) if gp and gp > 0 and salary else None

        # Age
        age = stats.get("age") if stats else None
        if not age and bio.get("birthday"):
            try:
                bday = datetime.strptime(bio["birthday"], "%m/%d/%Y").date()
                feb1 = date(end_year, 2, 1)
                age = feb1.year - bday.year - ((feb1.month, feb1.day) < (bday.month, bday.day))
            except (ValueError, TypeError):
                pass

        record = {
            "player": player,
            "season": season,
            "team": team,
            "age": age,
            "salary": salary,
            "salary_cap_pct": cap_pct,
            "luxury_tax_pct": tax_pct,
            "salary_rank_league": ps.get("salary_rank_league"),
            "salary_rank_team": None,  # computed below
            "years_exp": years_exp,
            "agent": agent,
            "gp": gp,
            "ppg": round(ppg, 1) if ppg is not None else None,
            "rpg": round(rpg, 1) if rpg is not None else None,
            "apg": round(apg, 1) if apg is not None else None,
            "spg": round(stats.get("spg"), 1) if stats and stats.get("spg") is not None else None,
            "bpg": round(stats.get("bpg"), 1) if stats and stats.get("bpg") is not None else None,
            "fg_pct": stats.get("fg_pct") if stats else None,
            "tp_pct": stats.get("tp_pct") if stats else None,
            "ft_pct": stats.get("ft_pct") if stats else None,
            "cost_per_point": cost_per_point,
            "cost_per_game": cost_per_game,
            "career_earnings": career_earnings,
            "awards": awards,
            "pos": bio.get("pos", ""),
            "nationality": bio.get("nationality", ""),
            "college": bio.get("college", ""),
            "draft_year": bio.get("draft_year"),
            "draft_pick": bio.get("draft_pick"),
            "height": bio.get("height", ""),
            "weight": bio.get("weight"),
        }
        final_records.append(record)
        all_seasons_set.add(season)
        if team:
            all_teams_set.add(team)
        if agent:
            all_agents_set.add(agent)
        all_players_set.add(player)

    # Compute team salary ranks
    team_season_groups = defaultdict(list)
    for i, rec in enumerate(final_records):
        if rec["team"]:
            team_season_groups[(rec["season"], rec["team"])].append(i)
    for indices in team_season_groups.values():
        indices.sort(key=lambda i: final_records[i]["salary"], reverse=True)
        for rank, idx in enumerate(indices, 1):
            final_records[idx]["salary_rank_team"] = rank

    # Sort reference lists
    seasons_sorted = sorted(all_seasons_set, key=lambda s: season_to_year(s) or 0, reverse=True)
    teams_sorted = sorted(all_teams_set)
    agents_sorted = sorted(all_agents_set)
    awards_sorted = sorted(all_awards_set)

    # Build salary cap output
    cap_output = {}
    for season in seasons_sorted:
        ci = salary_cap.get(season, {})
        if ci.get("cap"):
            cap_output[season] = {
                "cap": ci["cap"],
                "tax": ci.get("tax"),
                "apron1": ci.get("apron1"),
                "apron2": ci.get("apron2"),
            }

    output = {
        "seasons": final_records,
        "salary_cap": cap_output,
        "agents": agents_sorted,
        "teams": teams_sorted,
        "seasons_list": seasons_sorted,
        "awards_list": awards_sorted,
        "players": sorted(all_players_set),
        "meta": {
            "built": datetime.now().isoformat(),
            "total_records": len(final_records),
            "total_players": len(all_players_set),
            "season_range": f"{seasons_sorted[-1] if seasons_sorted else 'N/A'} to {seasons_sorted[0] if seasons_sorted else 'N/A'}",
            "has_stats": bool(stats_lookup),
            "has_awards": bool(awards_lookup),
            "has_bio": bool(bio_lookup),
        },
    }

    # Step 6: Write output
    print(f"\n[7/7] Writing output...")
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    size_mb = os.path.getsize(out_path) / 1024 / 1024
    print(f"    Written to {out_path}")
    print(f"    File size: {size_mb:.2f} MB")
    print(f"    Records: {len(final_records)}")
    print(f"    Players: {len(all_players_set)}")
    print(f"    Teams: {len(all_teams_set)}")
    print(f"    Agents: {len(all_agents_set)}")
    print(f"    Awards: {len(all_awards_set)}")
    print(f"    Seasons: {seasons_sorted[-1] if seasons_sorted else 'N/A'} to {seasons_sorted[0] if seasons_sorted else 'N/A'}")
    print(f"    Has stats: {bool(stats_lookup)}")
    print(f"    Has awards: {bool(awards_lookup)}")
    print(f"    Has bio: {bool(bio_lookup)}")
    print("\n" + "=" * 60)
    print("Build complete!")
    print("=" * 60)


if __name__ == "__main__":
    build_data()
