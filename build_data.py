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
CYRO_SHEET_ID = "14TQPdQ9mDhHMMMQa5vcs0coL98ZloHORtYElDikKoWY"

# Map from local filename -> (sheet_id, gid)
CSV_SOURCES = {
    "stats.csv":                (SHEET_ID, "0"),
    "salaries_historical.csv":  (SHEET_ID, "1151460858"),
    "salaries_future.csv":      (SHEET_ID, "1555460703"),
    "salaries_2526_current.csv": (CYRO_SHEET_ID, "1402642613"),
    "salaries_2526_dead.csv":   (CYRO_SHEET_ID, "1668033956"),
    "awards.csv":               (SHEET_ID, "1456513900"),
    "bio.csv":                  (SHEET_ID, "1488063724"),
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


# ── Player identity ────────────────────────────────────────────────────
# The salary sheets carry no player id, so a person has to be recognised by
# name. A name alone is not enough: this league has had a Gary Payton and a
# Gary Payton II, a Tim Hardaway and a Tim Hardaway Jr, two Chris Smiths and
# three Charles Joneses. bio.csv is the one source with a row per person,
# carrying a birth date, a draft year and the suffix or birth-year marker that
# tells two men of the same name apart, so it is the register this build joins
# against. Every other sheet's spelling is resolved to one of its people, or,
# when the register has never heard the name, to a person of its own.
#
# What the register settles, per person: the bio block (position, college,
# nationality, draft, height, weight, birth date), the agent, the display name
# printed in data.json, and career_earnings, which is a running total of one
# man's salaries and nothing else.

NAME_SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")

# Longest career on record is 22 seasons, so a salary paid 25 years after a
# draft belongs to a younger man of the same name. The father's own seasons are
# what this rules out: John Lucas III's rows read 'John Lucas', and his father,
# drafted in 1976, cannot be the man paid in 2005-06.
MAX_CAREER_SPAN = 25

SUFFIX_DISPLAY = {
    "jr": "Jr", "sr": "Sr", "ii": "II", "iii": "III", "iv": "IV", "v": "V",
}


def split_player_name(name):
    """Pull a name apart into the three things that identify a person.

    ``('Larry Nance, Jr.')`` -> ``('larry nance', 'jr', '')`` and
    ``('Charles Smith (1965)')`` -> ``('charles smith', '', '1965')``. The base
    is what normalize_name already produced, so a lookup that wants the old
    loose behaviour still gets it; the suffix and the marker are what the loose
    key threw away.
    """
    if not name:
        return "", "", ""
    text = strip_accents(str(name).strip()).lower()
    marker = ""
    found = re.search(r"\(([^)]*)\)", text)
    if found:
        marker = " ".join(found.group(1).split())
    text = re.sub(r"\([^)]*\)", " ", text)
    text = text.replace(".", "").replace("'", "").replace("-", " ")
    text = text.replace(",", " ")
    tokens = text.split()
    suffix = []
    while len(tokens) > 1 and tokens[-1] in NAME_SUFFIXES:
        suffix.insert(0, tokens.pop())
    base = " ".join(tokens)
    base = NAME_ALIASES.get(base, base)
    return base, " ".join(suffix), marker


class PersonIndex:
    """Who each name in the sheets is.

    One person per bio.csv row, plus one for every name the salary sheets carry
    that bio.csv has never heard of. ``resolve`` turns a spelling and a season
    into the person it belongs to; everything keyed on a person rather than on a
    name follows from there.
    """

    def __init__(self):
        self.people = {}                       # pid -> person
        self.by_base = defaultdict(list)       # base name -> [pid] in file order
        self.suffix_hints = defaultdict(set)   # base name -> suffixes a stable id spells
        self.spellings = defaultdict(lambda: defaultdict(int))
        self.adopted = {}                      # pid -> a suffix bio.csv left off
        self.unknown = set()

    # ── building ──
    def add_person(self, row):
        name = (row.get("PLAYER") or "").strip()
        if not name:
            return
        base, suffix, marker = split_player_name(name)
        if not base:
            return
        pid = ("bio", base, suffix, marker)
        if pid in self.people:
            return
        self.people[pid] = {
            "display": name,
            "base": base,
            "suffix": suffix,
            "marker": marker,
            "draft_year": parse_int(row.get("DRAFT")),
            "bio": {
                "pos": (row.get("POS") or "").strip(),
                "height": (row.get("HEIGHT") or "").strip(),
                "weight": parse_int(row.get("WEIGHT")),
                "nationality": (row.get("NATIONALITY") or "").strip(),
                "college": (row.get("COLLEGE / TEAM") or "").strip(),
                "draft_year": parse_int(row.get("DRAFT")),
                "draft_pick": parse_int(row.get("PICK")),
                "birthday": (row.get("BIRTHDAY") or "").strip(),
            },
        }
        self.by_base[base].append(pid)

    def add_suffix_hint(self, spelling, stable_id):
        """A stable id that spells a suffix the loose spelling leaves off.

        stats.csv carries two id columns next to the name it prints. Where the
        printed name is 'Terrence Shannon' and the id says 'Terrence Shannon
        Jr.', the suffix is that same man's, not a second man's, and a salary
        sheet that writes the suffix is writing about him.
        """
        base, suffix, marker = split_player_name(spelling)
        if not base or suffix or marker:
            return
        id_base, id_suffix, id_marker = split_player_name(stable_id)
        if id_base != base or not id_suffix or id_marker:
            return
        self.suffix_hints[base].add(id_suffix)

    # ── resolving ──
    def resolve(self, name, season=None, note=True, span=True):
        """The person a sheet's spelling and season belong to.

        ``note`` is False for a sheet that does not become a record, so that the
        name data.json prints for a man the register has never heard of is the
        one the salary sheets spell.

        ``span`` is False for a sheet whose rows are not salaries. A salary is
        never paid 30 years after a draft, which is what says the John Lucas
        paid in 2005-06 is John Lucas III; an honour can be handed to a man
        twenty years retired, and Gary Payton's NBA Top-75 is dated 2020-21.
        """
        base, suffix, marker = split_player_name(name)
        if not base:
            return None
        pid = self._resolve(base, suffix, marker, season, span=span)
        if note:
            self.spellings[pid][str(name).strip()] += 1
        return pid

    def _resolve(self, base, suffix, marker, season, span=True):
        group = self.by_base.get(base) or []
        if not group:
            # A name the register has never heard of, so there is no father and
            # son here to keep apart and a suffix is only a fuller spelling.
            return self._unknown(base, "", "")

        start = None
        if season:
            try:
                start = int(str(season).split("-")[0])
            except (ValueError, TypeError):
                start = None

        def possible(pid):
            """Whether this man could have been paid for that season."""
            if start is None:
                return True
            drafted = self.people[pid]["draft_year"]
            if drafted is None:
                return True
            if not span:
                return drafted <= start
            return drafted <= start <= drafted + MAX_CAREER_SPAN

        exact = [
            pid for pid in group
            if self.people[pid]["suffix"] == suffix
            and self.people[pid]["marker"] == marker
        ]
        if len(exact) == 1 and possible(exact[0]):
            return exact[0]

        if start is None:
            # Nothing to place him by, so answer only where one man is possible.
            if suffix or marker:
                return self._marked(base, suffix, marker, group)
            if len(group) == 1:
                return group[0]
            return self._unknown(base, suffix, marker)

        # Either the spelling names nobody in the register exactly, or it names
        # a man who had not been drafted yet when that salary was paid. Both
        # mean the season decides.
        candidates = [pid for pid in group if possible(pid)]

        if suffix or marker:
            marked = [pid for pid in candidates if pid in exact]
            if marked:
                return self._latest(marked)
            if exact:
                # The register does know a man of this suffix, and he is not the
                # one who was paid, so the sheet has put the son's suffix on the
                # father's row: Jaren Jackson's seasons are all filed under
                # Jaren Jackson Jr. The season says which man it was.
                if candidates:
                    return self._latest(candidates)
                return self._unknown(base, suffix, marker)
            return self._marked(base, suffix, marker, candidates)

        if candidates:
            return self._latest(candidates)
        return self._unknown(base, suffix, marker)

    def _marked(self, base, suffix, marker, candidates):
        """A spelling carrying a suffix the register does not give this name.

        Either the register left the suffix off one man, which a stable id in
        stats.csv proves, or the suffix belongs to a man the register has never
        heard of. Jameer Nelson Jr is not Jameer Nelson.
        """
        if suffix and not marker and suffix in self.suffix_hints.get(base, ()):
            plain = [
                pid for pid in candidates
                if not self.people[pid]["suffix"] and not self.people[pid]["marker"]
            ]
            if len(plain) == 1:
                self.adopted[plain[0]] = suffix
                return plain[0]
        return self._unknown(base, suffix, marker)

    def _latest(self, pids):
        """The last of these men to be drafted, who is the one still playing."""
        best = pids[0]
        for pid in pids[1:]:
            mine = self.people[pid]["draft_year"] or 0
            if mine > (self.people[best]["draft_year"] or 0):
                best = pid
        return best

    def _unknown(self, base, suffix, marker):
        pid = ("new", base, suffix, marker)
        self.unknown.add(pid)
        return pid

    # ── reading ──
    def display(self, pid):
        """The name data.json prints for this person."""
        person = self.people.get(pid)
        if person:
            name = person["display"]
            adopted = self.adopted.get(pid)
            if adopted:
                name = "{} {}".format(
                    name, " ".join(SUFFIX_DISPLAY[s] for s in adopted.split()))
            return name
        seen = self.spellings.get(pid) or {}
        if seen:
            return sorted(
                seen.items(), key=lambda kv: (-kv[1], len(kv[0]), kv[0]))[0][0]
        return ""

    def bio(self, pid):
        person = self.people.get(pid)
        return dict(person["bio"]) if person else {}


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
    for filename, (sheet_id, gid) in CSV_SOURCES.items():
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
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


def build_agent_lookup(agent_records, persons):
    lookup = defaultdict(list)
    for rec in agent_records:
        player = rec.get("player", "").strip()
        if not player:
            continue
        # No season: an agent signs a player before his first salary, so the
        # dates on these rows cannot place him. Without one the resolver only
        # answers where the answer is unambiguous.
        lookup[persons.resolve(player, note=False)].append({
            "agent": rec.get("agent", ""),
            "start": rec.get("start", ""),
            "end": rec.get("end", ""),
            "current": rec.get("current", False),
            "team": rec.get("team", ""),
        })
    return lookup


def find_agent_for_season(agent_lookup, pid, season):
    records = agent_lookup.get(pid, [])
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
#: How many rostered players a season needs before it counts as under way. The
#: same rule js/app.js and scripts/factoids.py use to find the current season,
#: so all three agree on which seasons are finished.
ROSTERED_PER_TEAM = 15


def current_season_of(records):
    """The newest season with a full league's worth of salaries on it."""
    counts = defaultdict(int)
    teams = set()
    for rec in records:
        if rec.get("salary"):
            counts[rec["season"]] += 1
        for team in (rec.get("team") or "").split(","):
            if team.strip():
                teams.add(team.strip())
    floor = max(len(teams), 1) * ROSTERED_PER_TEAM
    seasons = sorted({r["season"] for r in records},
                     key=lambda s: season_to_year(s) or 0, reverse=True)
    for season in seasons:
        if counts.get(season, 0) >= floor:
            return season
    return seasons[0] if seasons else ""


def stats_teams_for(stats_lookup, player, season):
    """The teams a man's stats lines put him on that season, in order.

    TOT is a league total row, not a team, so it never counts as one.
    """
    out = []
    for row in stats_lookup.get((normalize_name(player), season)) or []:
        team = row.get("team")
        if team and team != "TOT" and team not in out:
            out.append(team)
    return out


def correct_past_season_teams(records, stats_lookup):
    """A completed season belongs to the team he played it for.

    The current-salaries sheet carries one TEAM column per player and applies it
    to every season in his row, so a move in the summer rewrites the team on a
    season he had already played: after Giannis Antetokounmpo moved to Miami his
    2025-26 read MIA, a season he played in Milwaukee.

    Two disagreements are rewritten, both of them the sheet carrying one team
    for a season that had more than one truth in it.

    INHERITED. A past season carries one team, that team is one of the teams he
    is on now, and the stats name someone else: the row's TEAM column reaching a
    season it should not have. One OF the teams rather than the whole TEAM
    string, because a man traded inside the current season holds two of them:
    Kentavious Caldwell-Pope reads "MEM, PHI" in 2026-27, and his 2025-26 came
    through as PHI, a season he played in Memphis.

    TRADED. A past season carries one team, the stats name two or more, and the
    sheet's team is one of them. The sheet holds one row for the season and
    books the whole salary to a single team, and it is not consistently the one
    whose books carried it: over this dataset it names the first team he played
    for 705 times and the last 663, which is a sheet recording the money once
    rather than apportioning it. Believing it credits a franchise money another
    one paid, so James Harden's whole 2021-22 sat on Philadelphia for a season
    Brooklyn paid him most of. These take the same treatment the inherited
    multi-team case already took: both teams named, no allocation invented, and
    the season marked split so franchise records skip it.

    Everywhere else the two sources are saying different true things: the sheet
    names whose books paid the salary and the stats name who he played for, and
    for a man waived by one team while playing for another the sheet is the one
    a salary tool wants. A sheet naming a team nowhere in the stats is that man,
    and a sheet naming two teams has the apportionment this one lacks. Those are
    counted in the report and left alone.
    """
    current = current_season_of(records)
    current_key = season_to_year(current) or 0
    now_team = {
        rec["player"]: (rec.get("team") or "").strip()
        for rec in records
        if rec["season"] == current and (rec.get("team") or "").strip()
    }
    # The teams he is on now, one by one, so a current season he was traded
    # inside still matches the single team a past season inherited from it.
    now_teams = {
        player: {t.strip() for t in teams.split(",") if t.strip()}
        for player, teams in now_team.items()
    }

    applied, seen, skipped = [], 0, 0
    for rec in records:
        if (season_to_year(rec["season"]) or 0) >= current_key:
            continue
        played = stats_teams_for(stats_lookup, rec["player"], rec["season"])
        if not played:
            continue
        sheet = [t.strip() for t in (rec.get("team") or "").split(",") if t.strip()]
        if set(sheet) == set(played):
            continue
        seen += 1
        one = sheet[0] if len(sheet) == 1 else None
        inherited = one is not None and one in now_teams.get(rec["player"], set())
        traded = one is not None and len(played) > 1 and one in played
        if not (inherited or traded):
            skipped += 1
            continue

        row = {
            "player": rec["player"],
            "season": rec["season"],
            "sheet_team": ", ".join(sheet),
            "corrected_team": ", ".join(played),
        }
        rec["team"] = ", ".join(played)
        if len(played) == 1:
            # One team played it, so the whole salary is that team's and there
            # is no split to carry.
            rec.pop("team_salaries", None)
        else:
            # He was traded inside the season. The sheet put the whole salary on
            # one of the teams, and this build has no record of how they
            # actually divided it.
            # Allocating it by games played would put a figure on the page that
            # nobody paid, so the season is flagged as split and carries no
            # per-team amounts: the teams are named with the full salary, and
            # franchise records skip the season rather than credit either team.
            rec.pop("team_salaries", None)
            rec["teams_split"] = True
            row["split_basis"] = "no allocation; season marked split"
        applied.append(row)

    applied.sort(key=lambda r: (r["player"], r["season"]))
    print(f"    Team corrections: {len(applied)} applied, {skipped} left to the "
          f"salary sheet, of {seen} disagreements before {current}")
    return {"current_season": current, "applied": applied,
            "disagreements": seen, "left_alone": skipped}


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
        # CSV headers use the 2025-26+ column order (TOV/G, STL/G, BLK/G).
        # Pre-2026 rows still have data in the old order (STL/G, BLK/G, TOV/G),
        # so the header-labeled columns don't match the actual values.
        # Raw total columns (STL, BLK, TOV) are rotated for 2026 data only.
        if year_raw >= "2026":
            spg = parse_float(row.get("STL / G"))
            bpg = parse_float(row.get("BLK / G"))
            stl_raw = parse_int(row.get("BLK"))
            blk_raw = parse_int(row.get("TOV"))
        else:
            spg = parse_float(row.get("TOV / G"))
            bpg = parse_float(row.get("STL / G"))
            stl_raw = parse_int(row.get("STL"))
            blk_raw = parse_int(row.get("BLK"))
        stats = {
            "player_original": player,
            "team": normalize_team(team),
            "gp": parse_int(row.get("GP")),
            "min": parse_int(row.get("MIN")),
            "pts": parse_int(row.get("PTS")),
            "reb": parse_int(row.get("REB")),
            "ast": parse_int(row.get("AST")),
            "stl": stl_raw,
            "blk": blk_raw,
            "age": parse_int(row.get("AGE (Feb 1)")),
            "ppg": parse_float(row.get("PTS / G")),
            "rpg": parse_float(row.get("REB / G")),
            "apg": parse_float(row.get("AST / G")),
            "spg": spg,
            "bpg": bpg,
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
    """Process future salaries sheet (one row per player-team, year columns).
    A player can appear on multiple rows (different teams), e.g. Lillard has
    one row for POR and one for MIL. We combine them per player-season with
    per-team salary breakdowns, same as 2025-26 logic.
    """
    rows = parse_csv_string(csv_data)
    if not rows:
        return {}
    # Accumulate: (nk, season) -> { "team_salaries": {team: int}, "display_name": str }
    accum = {}
    year_cols = ["2027", "2028", "2029", "2030", "2031"]
    for row in rows:
        player = row.get("PLAYER", "").strip()
        team = row.get("TEAM", "").strip()
        if not player:
            continue
        team_abbr = normalize_team(team) if team else ""
        nk = normalize_name(player)
        for ycol in year_cols:
            salary = parse_salary(row.get(ycol))
            if salary is None or salary == 0:
                continue
            season = year_to_season(int(ycol))
            if not season:
                continue
            key = (nk, season)
            if key not in accum:
                accum[key] = {"team_salaries": defaultdict(int), "display_name": player}
            if team_abbr:
                accum[key]["team_salaries"][team_abbr] += salary

    # Build lookup with combined records
    lookup = {}
    multi_team = 0
    for (nk, season), data in accum.items():
        ts = dict(data["team_salaries"])
        total_salary = sum(ts.values())
        if total_salary <= 0:
            continue
        teams_sorted = sorted(ts.keys())
        team_str = ", ".join(teams_sorted) if teams_sorted else ""
        rec = {
            "player_original": data["display_name"],
            "team": team_str,
            "salary": total_salary,
        }
        if len(teams_sorted) > 1:
            rec["team_salaries"] = ts
            multi_team += 1
        lookup[(nk, season)] = [rec]

    print(f"    Parsed {len(lookup)} player-seasons from future salaries ({multi_team} multi-team)")
    return lookup


def process_cyro_salaries(current_csv, dead_csv):
    """Process Cyro's salary sheets for ALL seasons (current + dead money).
    
    Current salaries: year columns 2026, 2027, 2028, ... (2026 = season 2025-26).
    Dead money: columns SALARY 25-26, SALARY 26-27, SALARY 27-28, ...
    
    Both sheets can have the same player on different teams (multi-team).
    We combine per player-season with per-team salary breakdowns.
    """
    # Accumulate: (nk, season) -> { "team_salaries": {team: int}, "display_name": str }
    accum = {}
    
    def add_entry(nk, display_name, team_abbr, season, salary):
        key = (nk, season)
        if key not in accum:
            accum[key] = {"team_salaries": defaultdict(int), "display_name": display_name}
        if team_abbr:
            accum[key]["team_salaries"][team_abbr] += salary
    
    # Parse current salaries — find all year columns (2026, 2027, ...)
    if current_csv:
        reader = csv.reader(io.StringIO(current_csv))
        header = next(reader, None)
        if header:
            h = [c.strip().upper() for c in header]
            player_col = h.index("PLAYER") if "PLAYER" in h else 0
            team_col = h.index("TEAM") if "TEAM" in h else 2
            # Find all year columns
            year_cols = {}  # { col_index: season_str }
            for i, col_name in enumerate(h):
                if col_name.isdigit() and len(col_name) == 4:
                    yr = int(col_name)
                    if 2026 <= yr <= 2035:
                        season = year_to_season(yr)
                        if season:
                            year_cols[i] = season
            print(f"    Current salaries: PLAYER={player_col}, TEAM={team_col}, year cols={list(year_cols.values())}")
            count = 0
            for cols in reader:
                player = cols[player_col].strip() if len(cols) > player_col else ""
                team = cols[team_col].strip() if len(cols) > team_col else ""
                if not player:
                    continue
                nk = normalize_name(player)
                team_abbr = normalize_team(team) if team else ""
                for col_idx, season in year_cols.items():
                    if len(cols) <= col_idx:
                        continue
                    salary = parse_salary(cols[col_idx])
                    if salary is None or salary == 0:
                        continue
                    add_entry(nk, player, team_abbr, season, salary)
                    count += 1
            print(f"    Parsed {count} salary entries from current salaries")
    
    # Parse dead money — find all "SALARY XX-YY" columns
    if dead_csv:
        reader = csv.reader(io.StringIO(dead_csv))
        header = next(reader, None)
        if header:
            h = [c.strip().upper() for c in header]
            player_col = h.index("PLAYER") if "PLAYER" in h else 0
            team_col = h.index("TEAM") if "TEAM" in h else 3
            # Find all SALARY XX-YY columns
            salary_cols = {}  # { col_index: season_str }
            for i, col_name in enumerate(h):
                m = re.search(r'SALARY\s+(\d{2})-(\d{2})', col_name)
                if m:
                    start_yr = int(m.group(1))
                    end_yr = int(m.group(2))
                    # Convert "25-26" to "2025-26"
                    season = f"20{start_yr}-{end_yr:02d}"
                    salary_cols[i] = season
            print(f"    Dead money: PLAYER={player_col}, TEAM={team_col}, salary cols={list(salary_cols.values())}")
            count = 0
            for cols in reader:
                player = cols[player_col].strip() if len(cols) > player_col else ""
                team = cols[team_col].strip() if len(cols) > team_col else ""
                if not player:
                    continue
                nk = normalize_name(player)
                team_abbr = normalize_team(team) if team else ""
                for col_idx, season in salary_cols.items():
                    if len(cols) <= col_idx:
                        continue
                    salary = parse_salary(cols[col_idx])
                    if salary is None or salary == 0:
                        continue
                    add_entry(nk, player, team_abbr, season, salary)
                    count += 1
            print(f"    Parsed {count} salary entries from dead money")
    
    # Build lookup per season, filtering out $0 players
    lookup = {}
    skipped = 0
    multi_team = 0
    seasons_found = set()
    for (nk, season), data in accum.items():
        ts = dict(data["team_salaries"])
        total_salary = sum(ts.values())
        if total_salary <= 0:
            skipped += 1
            continue
        teams_sorted = sorted(ts.keys())
        team_str = ", ".join(teams_sorted) if teams_sorted else ""
        rec = {
            "player_original": data["display_name"],
            "team": team_str,
            "salary": total_salary,
        }
        if len(teams_sorted) > 1:
            rec["team_salaries"] = ts
            multi_team += 1
        lookup[(nk, season)] = [rec]
        seasons_found.add(season)
    
    seasons_list = sorted(seasons_found)
    print(f"    Combined into {len(lookup)} player-season records across {seasons_list}")
    print(f"    Skipped {skipped} with $0, {multi_team} multi-team")
    return lookup, seasons_found


def load_award_overrides():
    """Award rows the awards sheet files under the wrong spelling.

    The join runs on the person, which is right almost everywhere and wrong
    where the sheet puts the father's name on the son's award: both of Jaren
    Jackson Jr's Blocks Leader seasons read "Jaren Jackson". One entry per row
    that has to move, checked by hand.
    """
    path = os.path.join(BASE_DIR, "data", "award_overrides.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return (json.load(fh) or {}).get("overrides") or []


def process_awards(csv_data, persons):
    """Awards per person-season, not per loose name.

    Every award on the sheet counts for the man the row names, so Gary Payton's
    NBA Top-75 stops landing on Gary Payton II's 2020-21 season. The award list
    keeps the order the sheet gives it, deduplicated: a set here made the order
    a function of the hash seed, which put the same data.json through a diff
    differently on every run.
    """
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
        key = (persons.resolve(player, season, note=False, span=False), season)
        awards = [a.strip() for a in awards_str.split(",") if a.strip()] if awards_str else []
        all_awards.update(awards)
        held = lookup.setdefault(key, [])
        for award in awards:
            if award not in held:
                held.append(award)

    moved = 0
    for entry in load_award_overrides():
        season = normalize_season(entry.get("season"))
        awards = entry.get("awards") or []
        if not season or not awards:
            continue
        source = (persons.resolve(
            entry.get("filed_as") or "", season, note=False, span=False), season)
        target = (persons.resolve(
            entry.get("belongs_to") or "", season, note=False, span=False), season)
        if source[0] is None or target[0] is None or source == target:
            continue
        held = lookup.get(source) or []
        taken = [a for a in awards if a in held]
        if not taken:
            continue
        lookup[source] = [a for a in held if a not in awards]
        if not lookup[source]:
            del lookup[source]
        destination = lookup.setdefault(target, [])
        for award in taken:
            if award not in destination:
                destination.append(award)
        moved += len(taken)
    print(f"    Parsed {len(lookup)} player-seasons with awards, {len(all_awards)} award types")
    print(f"    {moved} award(s) moved by data/award_overrides.json")
    return lookup, all_awards


def build_person_index(bio_csv, stats_csv):
    """The register of people every other sheet is joined against."""
    persons = PersonIndex()
    for row in parse_csv_string(bio_csv):
        persons.add_person(row)
    print(f"    Registered {len(persons.people)} people from bio.csv")
    # stats.csv prints a name and, beside it, the stable ids two other
    # databases use for the same man. Where an id carries a suffix the printed
    # name leaves off, that suffix belongs to him.
    hinted = 0
    if stats_csv:
        reader = csv.reader(io.StringIO(stats_csv))
        header = next(reader, None) or []
        head = [c.strip().upper() for c in header]
        name_col = head.index("PLAYER") if "PLAYER" in head else None
        id_cols = [i for i, c in enumerate(head) if c in ("RG CODE", "NB CODE")]
        if name_col is not None:
            for cols in reader:
                if len(cols) <= name_col:
                    continue
                printed = cols[name_col].strip()
                if not printed:
                    continue
                for i in id_cols:
                    if len(cols) > i and cols[i].strip():
                        before = len(persons.suffix_hints)
                        persons.add_suffix_hint(printed, cols[i].strip())
                        hinted += len(persons.suffix_hints) - before

    print(f"    {hinted} names carry a suffix only their stable id spells")
    return persons


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

    # Step 3: Load CSVs from data_sources/
    print("\n[4/7] Loading CSV data from data_sources/...")
    stats_csv = load_csv_file("stats.csv")
    hist_sal_csv = load_csv_file("salaries_historical.csv")
    future_sal_csv = load_csv_file("salaries_future.csv")
    sal_2526_current_csv = load_csv_file("salaries_2526_current.csv")
    sal_2526_dead_csv = load_csv_file("salaries_2526_dead.csv")
    awards_csv = load_csv_file("awards.csv")
    bio_csv = load_csv_file("bio.csv")

    # Step 4: Parse
    print("\n[5/7] Parsing CSV data...")
    stats_lookup = process_stats(stats_csv) if stats_csv else {}
    hist_sal_lookup = process_salaries_csv(hist_sal_csv) if hist_sal_csv else {}
    future_sal_lookup = process_future_salaries(future_sal_csv) if future_sal_csv else {}
    cyro_lookup, cyro_seasons = process_cyro_salaries(sal_2526_current_csv, sal_2526_dead_csv)
    persons = build_person_index(bio_csv, stats_csv)
    awards_lookup, all_awards_set = (
        process_awards(awards_csv, persons) if awards_csv else ({}, set())
    )
    agent_lookup = build_agent_lookup(agent_records, persons)

    # Merge historical + future salary lookups
    # For seasons covered by Cyro's sheets: use Cyro's data exclusively
    # For other future seasons: use old future salaries sheet
    salary_csv_lookup = {}
    for k, v in hist_sal_lookup.items():
        salary_csv_lookup[k] = v
    for k, v in future_sal_lookup.items():
        # Skip any season Cyro covers — his data is authoritative
        if k[1] in cyro_seasons:
            continue
        if k in salary_csv_lookup:
            salary_csv_lookup[k].extend(v)
        else:
            salary_csv_lookup[k] = v
    # Add Cyro's data for all seasons he covers
    for k, v in cyro_lookup.items():
        if k in salary_csv_lookup:
            salary_csv_lookup[k].extend(v)
        else:
            salary_csv_lookup[k] = v

    # Re-key every salary row on the person it belongs to. Two spellings of one
    # man (Wendell Carter and Wendell Carter Jr) land together; a father and a
    # son who share a loose name (Gary Payton and Gary Payton II) come apart.
    salary_by_person = {}
    for (nk, season), recs in salary_csv_lookup.items():
        for rec in recs:
            pid = persons.resolve(rec["player_original"], season)
            salary_by_person.setdefault((pid, season), []).append(rec)
    salary_csv_lookup = salary_by_person

    # Combine multi-team records in salary_csv_lookup
    # e.g. Griffin 2020-21: [{team:DET, salary:32M}, {team:BKN, salary:1.2M}]
    # becomes: [{team:"BKN, DET", salary:33.9M, team_salaries:{BKN:1.2M, DET:32M}}]
    for key, recs in salary_csv_lookup.items():
        if len(recs) <= 1:
            continue
        # Already a combined record from Cyro? Skip
        if recs[0].get("team_salaries"):
            continue
        # Collect unique teams
        team_sals = defaultdict(int)
        display_name = recs[0]["player_original"]
        for rec in recs:
            tm = rec.get("team", "")
            sal = rec.get("salary", 0)
            if tm and sal:
                team_sals[tm] += sal
            elif sal:
                team_sals[""] += sal
        if len(team_sals) > 1:
            # Multi-team: combine into one record
            teams_sorted = sorted(t for t in team_sals.keys() if t)
            total = sum(team_sals.values())
            combined = {
                "player_original": display_name,
                "team": ", ".join(teams_sorted),
                "salary": total,
                "team_salaries": {t: s for t, s in team_sals.items() if t},
            }
            salary_csv_lookup[key] = [combined]

    # Step 5: Build unified player-season list
    print("\n[6/7] Merging data and computing derived fields...")

    # Start from agent tracker salary data (most comprehensive salary source)
    # Key: (normalized_name, season) -> record dict
    # NOTE: Skip seasons covered by Cyro's sheets — they have authoritative
    # real salary data (not cap holds)
    ps_map = {}
    for player_name, seasons in agent_salaries.items():
        for season_raw, salary in seasons.items():
            season = normalize_season(season_raw)
            if not season:
                continue
            if season in cyro_seasons:
                continue  # Cyro's data handles this season
            end_year = season_to_year(season)
            if not end_year or end_year < 1991:
                continue
            pid = persons.resolve(player_name, season)
            key = (pid, season)
            if key not in ps_map or salary > ps_map[key]["salary"]:
                ps_map[key] = {
                    "pid": pid,
                    "season": season,
                    "salary": salary,
                    "end_year": end_year,
                    "team": "",
                }

    # Merge CSV salaries: add missing, update team info
    for (pid, season), recs in salary_csv_lookup.items():
        for rec in recs:
            end_year = season_to_year(season)
            if not end_year or end_year < 1991:
                continue
            key = (pid, season)
            if key in ps_map:
                # Update team from CSV if we don't have one
                if rec.get("team") and not ps_map[key]["team"]:
                    ps_map[key]["team"] = rec["team"]
                # Multi-team records: override salary, team, and carry breakdown
                if rec.get("team_salaries"):
                    ps_map[key]["team_salaries"] = rec["team_salaries"]
                    ps_map[key]["salary"] = rec["salary"]
                    ps_map[key]["team"] = rec["team"]
            else:
                ps_map[key] = {
                    "pid": pid,
                    "season": season,
                    "salary": rec["salary"],
                    "end_year": end_year,
                    "team": rec.get("team", ""),
                }
                if rec.get("team_salaries"):
                    ps_map[key]["team_salaries"] = rec["team_salaries"]

    player_season_list = list(ps_map.values())
    print(f"    Total player-season records: {len(player_season_list)}")
    print(f"    {len(persons.unknown)} names bio.csv has no row for")
    if persons.adopted:
        for pid, suffix in sorted(persons.adopted.items()):
            print(f"      suffix from a stable id: {persons.display(pid)}")

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
        pid = ps["pid"]
        player_years[pid].add(ps["end_year"])
        player_yearly_salary[pid][ps["end_year"]] += ps["salary"]

    # Build final records
    print("    Building final records...")
    all_seasons_set = set()
    all_teams_set = set()
    all_agents_set = set()
    all_players_set = set()
    final_records = []

    for ps in player_season_list:
        pid = ps["pid"]
        player = persons.display(pid)
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
            csv_recs = salary_csv_lookup.get((pid, season), [])
            if csv_recs:
                team = csv_recs[0].get("team", "")

        # Awards
        awards = awards_lookup.get((pid, season), [])

        # Bio
        bio = persons.bio(pid)

        # Agent
        agent = find_agent_for_season(agent_lookup, pid, season)

        # Cap %
        cap_info = salary_cap.get(season, {})
        cap = cap_info.get("cap")
        tax = cap_info.get("tax")
        cap_pct = round(salary / cap * 100, 2) if cap and salary else None
        tax_pct = round(salary / tax * 100, 2) if tax and salary else None

        # Years of experience (completed seasons only; current season doesn't count)
        years_exp = sum(1 for y in player_years.get(pid, set()) if y < end_year)

        # Career earnings to date
        career_earnings = sum(
            v for y, v in player_yearly_salary.get(pid, {}).items() if y <= end_year
        )

        # Cost metrics
        gp = stats.get("gp") if stats else None
        pts = stats.get("pts") if stats else None
        reb = stats.get("reb") if stats else None
        ast = stats.get("ast") if stats else None
        stl = stats.get("stl") if stats else None
        blk = stats.get("blk") if stats else None
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
            "pts": pts,
            "reb": reb,
            "ast": ast,
            "stl": stl,
            "blk": blk,
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
        # Add per-team salary breakdown for multi-team players
        if ps.get("team_salaries"):
            record["team_salaries"] = ps["team_salaries"]
        final_records.append(record)
        all_seasons_set.add(season)
        if team:
            # For multi-team records like "MIL, POR", add each individual team
            for t in team.split(", "):
                t = t.strip()
                if t:
                    all_teams_set.add(t)
        if agent:
            all_agents_set.add(agent)
        all_players_set.add(player)

    # A completed season belongs to the team he played it for
    corrections = correct_past_season_teams(final_records, stats_lookup)
    if corrections["applied"]:
        all_teams_set = set()
        for rec in final_records:
            for t in (rec.get("team") or "").split(","):
                t = t.strip()
                if t:
                    all_teams_set.add(t)

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
            "has_bio": bool(persons.people),
        },
    }

    # Step 6: Write output
    print(f"\n[7/7] Writing output...")
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, separators=(",", ":"))

    # Every team the build moved, committed so a reader of the data can see
    # which seasons were taken off the salary sheet's word and why.
    report_path = os.path.join(OUT_DIR, "team_corrections_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "note": (
                "A completed season takes its team from the player's stats line "
                "where the salary sheet gave it the team he is on now, which is "
                "what the one-TEAM-per-row current sheet does to a man who moved "
                "in the summer. Disagreements of any other shape are the sheet "
                "naming whose books paid a salary and the stats naming who he "
                "played for, which are different facts, and are left alone. "
                "A season he was traded inside keeps both teams and its whole "
                "salary: nothing here records how the two divided it, so "
                "neither is given a figure, and the season is marked split so "
                "no franchise record counts it."
            ),
            "current_season": corrections["current_season"],
            "disagreements_found": corrections["disagreements"],
            "left_to_the_salary_sheet": corrections["left_alone"],
            "corrections": corrections["applied"],
        }, f, indent=1, sort_keys=True)
        f.write("\n")

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
    print(f"    Has bio: {bool(persons.people)}")
    print("\n" + "=" * 60)
    print("Build complete!")
    print("=" * 60)


if __name__ == "__main__":
    build_data()
