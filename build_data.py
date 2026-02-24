#!/usr/bin/env python3
"""
Build script for HoopsMatic Salary Season Finder.
Downloads data from Google Sheets, merges with local agent/salary cap data,
computes derived fields, and outputs data/data.json.

Usage:
    python3 build_data.py            # Download from Google Sheets + local files
    python3 build_data.py --local    # Use only local cached CSV files
"""

import csv
import json
import os
import re
import sys
import io
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "data_raw")
OUT_DIR = os.path.join(BASE_DIR, "data")

SHEET_ID = "1ZrDfzqiC31Hu3YCtxT4aZbZF4QVCVyGe6wBytR2LF30"
SHEETS = {
    "stats": {"gid": "0", "file": "stats.csv"},
    "salaries": {"gid": "1151460858", "file": "salaries.csv"},
    "future_salaries": {"gid": "1555460703", "file": "future_salaries.csv"},
    "awards": {"gid": "1456513900", "file": "awards.csv"},
    "bio": {"gid": "1488063724", "file": "bio.csv"},
}

# Team abbreviation mapping: full name -> standard 3-letter code
TEAM_ABBREV = {
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
    # Short forms
    "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA", "CHI": "CHI",
    "CLE": "CLE", "DAL": "DAL", "DEN": "DEN", "DET": "DET", "GSW": "GSW",
    "GS": "GSW", "HOU": "HOU", "IND": "IND", "LAC": "LAC", "LAL": "LAL",
    "MEM": "MEM", "VAN": "VAN", "MIA": "MIA", "MIL": "MIL", "MIN": "MIN",
    "NOP": "NOP", "NO": "NOP", "NOH": "NOH", "NOK": "NOK", "NYK": "NYK",
    "NY": "NYK", "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX",
    "PHO": "PHX", "POR": "POR", "SAC": "SAC", "SAS": "SAS", "SA": "SAS",
    "SEA": "SEA", "TOR": "TOR", "UTA": "UTA", "UTAH": "UTA",
    "WAS": "WAS", "WSH": "WAS", "NJN": "NJN", "NJ": "NJN",
    "CHH": "CHA", "CHO": "CHA",  # Charlotte historical
    "TOT": "TOT",  # Total (multi-team)
}


def normalize_team(team_str):
    """Normalize team name/abbreviation to standard 3-letter code."""
    if not team_str:
        return ""
    team_str = team_str.strip()
    if team_str in TEAM_ABBREV:
        return TEAM_ABBREV[team_str]
    # Try uppercase
    if team_str.upper() in TEAM_ABBREV:
        return TEAM_ABBREV[team_str.upper()]
    return team_str[:3].upper()


def normalize_name(name):
    """Normalize player name for matching: strip suffixes, lowercase, etc."""
    if not name:
        return ""
    name = name.strip()
    # Remove common suffixes for matching purposes
    name_lower = name.lower()
    for suffix in [" jr.", " jr", " sr.", " sr", " iii", " ii", " iv"]:
        name_lower = name_lower.replace(suffix, "")
    # Remove periods and extra spaces
    name_lower = name_lower.replace(".", "").replace("  ", " ").strip()
    return name_lower


def year_to_season(year_val):
    """Convert a year number to season string. e.g., 2025 -> '2024-25', 1999 -> '1998-99'."""
    if not year_val:
        return None
    try:
        y = int(year_val)
    except (ValueError, TypeError):
        return None
    if y < 100:
        return None
    start = y - 1
    end_short = str(y)[-2:]
    # Handle century boundary: 2000 -> '1999-00'
    return f"{start}-{end_short}"


def season_to_year(season):
    """Convert season string to ending year. e.g., '2024-25' -> 2025."""
    if not season:
        return None
    # Handle the lockout season format
    if season == "1998--1":
        return 1999
    parts = season.split("-")
    if len(parts) != 2:
        return None
    try:
        start = int(parts[0])
        end_short = parts[1]
        if len(end_short) == 2:
            century = start // 100 * 100
            end = century + int(end_short)
            # Handle century wrap: 1999-00 -> 2000
            if end <= start:
                end += 100
            return end
        elif len(end_short) == 1:
            # Handle "1998--1" -> already handled above
            return start + 1
        else:
            return int(end_short)
    except (ValueError, TypeError):
        return None


def normalize_season(season_str):
    """Normalize various season formats to 'YYYY-YY'."""
    if not season_str:
        return None
    season_str = str(season_str).strip()

    # Already in correct format
    if re.match(r'^\d{4}-\d{2}$', season_str):
        return season_str

    # Handle "1998--1" lockout format
    if season_str == "1998--1":
        return "1998-99"

    # Handle plain year (ending year)
    if re.match(r'^\d{4}$', season_str):
        return year_to_season(int(season_str))

    # Handle "YYYY-YYYY" format
    m = re.match(r'^(\d{4})-(\d{4})$', season_str)
    if m:
        start = int(m.group(1))
        end_short = m.group(2)[-2:]
        return f"{start}-{end_short}"

    return None


def parse_salary(val):
    """Parse salary string like '$48,728,845' or '48728845' to int."""
    if not val:
        return None
    val = str(val).strip()
    if val.lower() in ("n/a", "", "-", "nan"):
        return None
    val = val.replace("$", "").replace(",", "").replace(" ", "")
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def parse_float(val):
    """Parse a float value, return None if invalid."""
    if not val:
        return None
    val = str(val).strip()
    if val.lower() in ("n/a", "", "-", "nan"):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def parse_int(val):
    """Parse an int value, return None if invalid."""
    if not val:
        return None
    val = str(val).strip()
    if val.lower() in ("n/a", "", "-", "nan"):
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def download_csv(sheet_name, gid, local_file):
    """Download CSV from Google Sheets, falling back to local file."""
    local_path = os.path.join(RAW_DIR, local_file)

    if "--local" not in sys.argv:
        url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={gid}"
        print(f"  Downloading {sheet_name} from Google Sheets...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = resp.read().decode("utf-8-sig")
            os.makedirs(RAW_DIR, exist_ok=True)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(data)
            print(f"    Saved to {local_path} ({len(data)} bytes)")
            return data
        except Exception as e:
            print(f"    Download failed: {e}")

    if os.path.exists(local_path):
        print(f"  Using local file: {local_path}")
        with open(local_path, "r", encoding="utf-8-sig") as f:
            return f.read()

    print(f"  WARNING: No data available for {sheet_name}")
    return None


def parse_csv_string(csv_string):
    """Parse a CSV string into a list of dicts."""
    if not csv_string:
        return []
    reader = csv.DictReader(io.StringIO(csv_string))
    return list(reader)


def load_salary_cap():
    """Load salary cap data from CSV."""
    cap_path = os.path.join(BASE_DIR, "salary_cap_info.csv")
    print(f"  Loading salary cap from {cap_path}")
    cap_data = {}
    with open(cap_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            season_raw = row.get("Season", "").strip()
            # Convert "2024-2025" to "2024-25"
            if re.match(r'^\d{4}-\d{4}$', season_raw):
                start = season_raw[:4]
                end_short = season_raw[-2:]
                season = f"{start}-{end_short}"
            else:
                season = season_raw

            cap_data[season] = {
                "cap": parse_salary(row.get("Salary Cap")),
                "tax": parse_salary(row.get("Luxury Tax")),
                "apron1": parse_salary(row.get("1st Apron")),
                "apron2": parse_salary(row.get("2nd Apron")),
            }
    print(f"    Loaded {len(cap_data)} seasons of cap data")
    return cap_data


def load_agent_data():
    """Load agent tracker data from data.json."""
    agent_path = os.path.join(BASE_DIR, "data_raw.json")
    print(f"  Loading agent data from {agent_path}")
    with open(agent_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    player_salaries = data.get("playerSalaries", {})
    career_earnings = data.get("playerCareerEarnings", {})
    agent_records = data.get("agentData", [])

    print(f"    {len(player_salaries)} players with salary data")
    print(f"    {len(agent_records)} agent-player relationships")
    print(f"    {len(career_earnings)} career earnings records")

    return player_salaries, career_earnings, agent_records


def build_agent_lookup(agent_records):
    """Build lookup: player_name_normalized -> list of {agent, start, end, current}."""
    lookup = defaultdict(list)
    for rec in agent_records:
        player = rec.get("player", "").strip()
        if not player:
            continue
        key = normalize_name(player)
        start = rec.get("start", "")
        end = rec.get("end", "")
        lookup[key].append({
            "agent": rec.get("agent", ""),
            "player_original": player,
            "team": rec.get("team", ""),
            "start": start,
            "end": end,
            "current": rec.get("current", False),
        })
    return lookup


def find_agent_for_season(agent_lookup, player_name, season):
    """Find which agent represented a player during a given season."""
    key = normalize_name(player_name)
    records = agent_lookup.get(key, [])
    if not records:
        return None

    end_year = season_to_year(season)
    if not end_year:
        return records[0]["agent"] if records else None

    # Season runs roughly from October of start_year to June of end_year
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

        # Check if agent period overlaps with the season
        if a_start <= season_end and a_end >= season_start:
            # Prefer the one active at the end of season
            if a_start <= season_end:
                best = rec["agent"]
    if best:
        return best

    # Fallback: current agent
    for rec in records:
        if rec.get("current"):
            return rec["agent"]

    return records[0]["agent"] if records else None


def process_stats(csv_data):
    """Process player stats CSV into lookup dict keyed by (normalized_name, season)."""
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}

    stats_lookup = {}
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

        # Skip TOT rows if we already have team-specific data, or prefer TOT
        # We'll keep all entries and let the merge logic handle multi-team players

        stats = {
            "player_original": player,
            "team": normalize_team(team),
            "gp": parse_int(row.get("GP")),
            "min": parse_int(row.get("MIN")),
            "pts": parse_int(row.get("PTS")),
            "fgm": parse_int(row.get("FGM")),
            "fga": parse_int(row.get("FGA")),
            "tp": parse_int(row.get("3P")),
            "tpa": parse_int(row.get("3PA")),
            "ftm": parse_int(row.get("FTM")),
            "fta": parse_int(row.get("FTA")),
            "orb": parse_int(row.get("ORB")),
            "drb": parse_int(row.get("DRB")),
            "reb": parse_int(row.get("REB")),
            "ast": parse_int(row.get("AST")),
            "stl": parse_int(row.get("STL")),
            "blk": parse_int(row.get("BLK")),
            "tov": parse_int(row.get("TOV")),
            "pf": parse_int(row.get("PF")),
            "age": parse_int(row.get("AGE (Feb 1)")),
            "ppg": parse_float(row.get("PTS/G")),
            "rpg": parse_float(row.get("REB/G")),
            "apg": parse_float(row.get("AST/G")),
            "spg": parse_float(row.get("STL/G")),
            "bpg": parse_float(row.get("BLK/G")),
            "tov_g": parse_float(row.get("TOV/G")),
            "fg_pct": parse_float(row.get("FG%")),
            "tp_pct": parse_float(row.get("3P%")),
            "ft_pct": parse_float(row.get("FT%")),
        }

        # For multi-team: keep a list; we'll pick the best one later
        if key not in stats_lookup:
            stats_lookup[key] = []
        stats_lookup[key].append(stats)

    print(f"    Parsed {len(stats_lookup)} unique player-seasons from stats")
    return stats_lookup


def process_awards(csv_data):
    """Process awards CSV into lookup dict keyed by (normalized_name, season)."""
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}, set()

    awards_lookup = {}
    all_awards = set()
    for row in rows:
        player = row.get("PLAYER/COACH", "").strip()
        year_raw = row.get("YEAR", "").strip()
        season_awards_str = row.get("SEASON AWARDS", "").strip()

        if not player or not year_raw:
            continue

        season = normalize_season(year_raw)
        if not season:
            continue

        key = (normalize_name(player), season)

        awards = []
        if season_awards_str:
            awards = [a.strip() for a in season_awards_str.split(",") if a.strip()]
            for a in awards:
                all_awards.add(a)

        if key not in awards_lookup:
            awards_lookup[key] = {"player_original": player, "awards": awards}
        else:
            # Merge awards
            existing = set(awards_lookup[key]["awards"])
            existing.update(awards)
            awards_lookup[key]["awards"] = list(existing)

    print(f"    Parsed {len(awards_lookup)} player-seasons with awards")
    print(f"    Found {len(all_awards)} unique award types")
    return awards_lookup, all_awards


def process_bio(csv_data):
    """Process bio CSV into lookup dict keyed by normalized_name."""
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}

    bio_lookup = {}
    for row in rows:
        player = row.get("PLAYER", "").strip()
        if not player:
            continue

        key = normalize_name(player)
        bio_lookup[key] = {
            "player_original": player,
            "pos": row.get("POS", "").strip(),
            "height": row.get("HEIGHT", "").strip(),
            "weight": parse_int(row.get("WEIGHT")),
            "nationality": row.get("NATIONALITY", "").strip(),
            "college": row.get("COLLEGE/TEAM", "").strip(),
            "draft_year": parse_int(row.get("DRAFT (year)")),
            "draft_pick": parse_int(row.get("PICK (overall)")),
            "birthday": row.get("BIRTHDAY", "").strip(),
            "birth_country": row.get("BIRTH STATE/COUNTRY", "").strip(),
        }

    print(f"    Parsed {len(bio_lookup)} player bios")
    return bio_lookup


def process_salaries_csv(csv_data):
    """Process salaries CSV (through 2024-25) into salary records."""
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}

    salary_lookup = {}
    for row in rows:
        player = row.get("PLAYER", "").strip()
        year_raw = row.get("YEAR", "").strip()
        team = row.get("TEAM", "").strip()
        salary = parse_salary(row.get("SALARY"))

        if not player or not year_raw or salary is None:
            continue

        season = normalize_season(year_raw)
        if not season:
            continue

        key = (normalize_name(player), season)
        if key not in salary_lookup:
            salary_lookup[key] = []
        salary_lookup[key].append({
            "player_original": player,
            "team": normalize_team(team),
            "salary": salary,
        })

    print(f"    Parsed {len(salary_lookup)} player-seasons from salaries CSV")
    return salary_lookup


def build_data():
    """Main build function."""
    print("=" * 60)
    print("HoopsMatic Salary Season Finder - Data Builder")
    print("=" * 60)

    # 1. Load local data
    print("\n[1/6] Loading salary cap data...")
    salary_cap = load_salary_cap()

    print("\n[2/6] Loading agent tracker data...")
    player_salaries, career_earnings_map, agent_records = load_agent_data()
    agent_lookup = build_agent_lookup(agent_records)

    # 3. Download/load Google Sheets CSVs
    print("\n[3/6] Loading Google Sheets data...")
    stats_csv = download_csv("stats", SHEETS["stats"]["gid"], SHEETS["stats"]["file"])
    salaries_csv = download_csv("salaries", SHEETS["salaries"]["gid"], SHEETS["salaries"]["file"])
    awards_csv = download_csv("awards", SHEETS["awards"]["gid"], SHEETS["awards"]["file"])
    bio_csv = download_csv("bio", SHEETS["bio"]["gid"], SHEETS["bio"]["file"])

    # 4. Parse CSVs
    print("\n[4/6] Parsing CSV data...")
    stats_lookup = process_stats(stats_csv) if stats_csv else {}
    salaries_csv_lookup = process_salaries_csv(salaries_csv) if salaries_csv else {}
    awards_lookup, all_awards_set = process_awards(awards_csv) if awards_csv else ({}, set())
    bio_lookup = process_bio(bio_csv) if bio_csv else {}

    # 5. Merge all data
    print("\n[5/6] Merging data and computing derived fields...")

    # Build the primary dataset from agent tracker salary data
    # This is our most comprehensive salary source
    all_records = []
    player_seasons_seen = set()
    player_season_list = []

    # Collect all player-season salary records
    for player_name, seasons in player_salaries.items():
        for season, salary in seasons.items():
            season_norm = normalize_season(season)
            if not season_norm:
                continue

            # Filter to 1990-91 onwards
            end_year = season_to_year(season_norm)
            if not end_year or end_year < 1991:
                continue

            player_season_list.append({
                "player": player_name,
                "season": season_norm,
                "salary": salary,
                "end_year": end_year,
            })

    # Also add any from the CSV salaries that aren't in agent tracker
    for (name_key, season), recs in salaries_csv_lookup.items():
        for rec in recs:
            end_year = season_to_year(season)
            if not end_year or end_year < 1991:
                continue
            # Check if already in agent tracker
            found = False
            for ps in player_season_list:
                if normalize_name(ps["player"]) == name_key and ps["season"] == season:
                    # Update team from CSV if available
                    if rec.get("team") and not ps.get("team_from_csv"):
                        ps["team_from_csv"] = rec["team"]
                    found = True
                    break
            if not found:
                player_season_list.append({
                    "player": rec["player_original"],
                    "season": season,
                    "salary": rec["salary"],
                    "end_year": end_year,
                    "team_from_csv": rec.get("team", ""),
                })

    print(f"    Total player-season records before dedup: {len(player_season_list)}")

    # Compute salary ranks
    # Group by season for league rank, by (season, team) for team rank
    season_salaries = defaultdict(list)
    for ps in player_season_list:
        season_salaries[ps["season"]].append(ps)

    # Compute league-wide salary ranks
    for season, records in season_salaries.items():
        sorted_recs = sorted(records, key=lambda x: x["salary"], reverse=True)
        for rank, rec in enumerate(sorted_recs, 1):
            rec["salary_rank_league"] = rank

    # Compute years of experience (cumulative seasons with salary)
    player_all_seasons = defaultdict(set)
    for ps in player_season_list:
        player_all_seasons[normalize_name(ps["player"])].add(ps["end_year"])

    # Compute career earnings to date
    player_salary_by_year = defaultdict(lambda: defaultdict(int))
    for ps in player_season_list:
        player_salary_by_year[normalize_name(ps["player"])][ps["end_year"]] += ps["salary"]

    # Build team salary lookup from CSV salaries + stats for team rank
    # For team rank, we need to know which team each player was on
    # Use salaries CSV, stats, or agent tracker team data

    # Now build final records
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
        name_key = normalize_name(player)

        # Get stats
        stats_key = (name_key, season)
        stats_list = stats_lookup.get(stats_key, [])

        # Pick best stats entry (prefer non-TOT, or TOT if aggregated)
        stats = None
        if stats_list:
            # If there's a TOT entry, use it for aggregate stats
            tot_entry = None
            team_entries = []
            for s in stats_list:
                if s.get("team") == "TOT":
                    tot_entry = s
                else:
                    team_entries.append(s)
            stats = tot_entry if tot_entry else (team_entries[0] if team_entries else stats_list[0])

        # Determine team
        team = ""
        if ps.get("team_from_csv"):
            team = ps["team_from_csv"]
        elif stats and stats.get("team") and stats["team"] != "TOT":
            team = stats["team"]
        elif stats_list:
            # Use first non-TOT team from stats
            for s in stats_list:
                if s.get("team") and s["team"] != "TOT":
                    team = s["team"]
                    break
        # Fallback: try to find team from salaries CSV
        if not team:
            csv_recs = salaries_csv_lookup.get(stats_key, [])
            if csv_recs:
                team = csv_recs[0].get("team", "")

        # Get awards
        awards_data = awards_lookup.get(stats_key, {})
        awards = awards_data.get("awards", [])

        # Get bio
        bio = bio_lookup.get(name_key, {})

        # Get agent
        agent = find_agent_for_season(agent_lookup, player, season)

        # Compute cap %
        cap_info = salary_cap.get(season, {})
        cap = cap_info.get("cap")
        tax = cap_info.get("tax")

        salary_cap_pct = round(salary / cap * 100, 2) if cap and salary else None
        luxury_tax_pct = round(salary / tax * 100, 2) if tax and salary else None

        # Compute years of experience
        player_years = sorted(player_all_seasons.get(name_key, set()))
        years_exp = 0
        for y in player_years:
            if y <= end_year:
                years_exp += 1

        # Compute career earnings to date
        player_yearly = player_salary_by_year.get(name_key, {})
        career_earnings = sum(v for y, v in player_yearly.items() if y <= end_year)

        # Compute cost metrics
        gp = stats.get("gp") if stats else None
        pts_total = stats.get("pts") if stats else None
        ppg = stats.get("ppg") if stats else None
        rpg = stats.get("rpg") if stats else None
        apg = stats.get("apg") if stats else None

        cost_per_point = None
        if pts_total and pts_total > 0 and salary:
            cost_per_point = round(salary / pts_total)

        cost_per_game = None
        if gp and gp > 0 and salary:
            cost_per_game = round(salary / gp)

        # Age: from stats or compute from bio birthday
        age = stats.get("age") if stats else None
        if not age and bio.get("birthday"):
            try:
                bday = datetime.strptime(bio["birthday"], "%m/%d/%Y").date()
                # Age as of Feb 1 of the season's end year
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
            "salary_cap_pct": salary_cap_pct,
            "luxury_tax_pct": luxury_tax_pct,
            "salary_rank_league": ps.get("salary_rank_league"),
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
    # Group by (season, team) and rank within each group
    team_season_groups = defaultdict(list)
    for i, rec in enumerate(final_records):
        if rec["team"]:
            team_season_groups[(rec["season"], rec["team"])].append(i)

    for (season, team), indices in team_season_groups.items():
        sorted_indices = sorted(indices, key=lambda i: final_records[i]["salary"], reverse=True)
        for rank, idx in enumerate(sorted_indices, 1):
            final_records[idx]["salary_rank_team"] = rank

    # Set None for records without team rank
    for rec in final_records:
        if "salary_rank_team" not in rec:
            rec["salary_rank_team"] = None

    # Sort seasons descending
    seasons_sorted = sorted(all_seasons_set, key=lambda s: season_to_year(s) or 0, reverse=True)
    teams_sorted = sorted(all_teams_set)
    agents_sorted = sorted(all_agents_set)
    awards_sorted = sorted(all_awards_set)

    # Build salary cap output
    cap_output = {}
    for season in seasons_sorted:
        cap_info = salary_cap.get(season, {})
        if cap_info.get("cap"):
            cap_output[season] = {
                "cap": cap_info["cap"],
                "tax": cap_info.get("tax"),
                "apron1": cap_info.get("apron1"),
                "apron2": cap_info.get("apron2"),
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

    # 6. Write output
    print(f"\n[6/6] Writing output...")
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "data.json")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    file_size = os.path.getsize(out_path)
    print(f"    Written to {out_path}")
    print(f"    File size: {file_size / 1024 / 1024:.2f} MB")
    print(f"    Total records: {len(final_records)}")
    print(f"    Total players: {len(all_players_set)}")
    print(f"    Seasons: {seasons_sorted[-1] if seasons_sorted else 'N/A'} to {seasons_sorted[0] if seasons_sorted else 'N/A'}")
    print(f"    Has stats: {bool(stats_lookup)}")
    print(f"    Has awards: {bool(awards_lookup)}")
    print(f"    Has bio: {bool(bio_lookup)}")

    print("\n" + "=" * 60)
    print("Build complete!")
    print("=" * 60)


if __name__ == "__main__":
    build_data()
