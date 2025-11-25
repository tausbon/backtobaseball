import pandas as pd
import re
import os
import requests
import statsapi # type: ignore
from pybaseball import statcast
from pybaseball import playerid_reverse_lookup
from pybaseball import team_ids
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Flowable
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak
import spacy
from spacy import displacy
from spacy.matcher import Matcher

TEAM_ABBR_TO_NAME = {
    "BOS": "Boston Red Sox",
    "NYY": "New York Yankees",
    "NYA": "New York Yankees",
    "CHC": "Chicago Cubs",
    "LAD": "Los Angeles Dodgers",
    "BRO": "Brooklyn Dodgers",
    "PHI": "Philadelphia Phillies",
    "WSH": "Washington Nationals",
    "WSA": "Washington Senators",
    "DET": "Detroit Tigers",
    "STL": "St. Louis Cardinals",
    "SLB": "St. Louis Browns",
    "HOU": "Houston Astros",
    "KCR": "Kansas City Royals",
    "OAK": "Oakland Athletics",
    "PHA": "Philadelphia Athletics",
    "ATL": "Atlanta Braves",
    "BSN": "Boston Braves",
    "MIL": "Milwaukee Brewers",
    "SEA": "Seattle Mariners",
    "SDP": "San Diego Padres",
    "MIA": "Miami Marlins",
    "FLA": "Florida Marlins",
    "COL": "Colorado Rockies",
    "MIN": "Minnesota Twins",
    "MON": "Montreal Expos",
    "TBR": "Tampa Bay Rays",
    "TBD": "Tampa Bay Devil Rays",
    "ARI": "Arizona Diamondbacks",
    "BAL": "Baltimore Orioles",
    "CLE": "Cleveland Guardians",
    "WSN": "Washington Nationals",
    "TOR": "Toronto Blue Jays",
    "CIN": "Cincinnati Reds",
    "PIT": "Pittsburgh Pirates",
    "NYM": "New York Mets",
    "CHW": "Chicago White Sox",
    "ANA": "Anaheim Angels",
    "LAA": "Los Angeles Angels",
    "CAL": "California Angels",
    "TEX": "Texas Rangers",
    "ATH": "Oakland Athletics",

}

def fetch_statcast_data(game_date, home_team, away_team):
    print(f"📥 Fetching play-by-play data for {game_date}...")
    df = statcast(start_dt=game_date, end_dt=game_date)

    if df.empty:
        print("⚠ No data found for this date. Please check the game date and try again.")
        return None
     # ✅ Filter data to only include rows for the specified teams
    df = df[(df['home_team'] == home_team) & (df['away_team'] == away_team)]

    if df.empty:
        print(f"⚠ No game found for {home_team} vs {away_team} on {game_date}. Check team abbreviations.")
        return None

    # ✅ Standardize inning formatting
    df = df.sort_values(by=['inning', 'at_bat_number'], ascending=[True, True]).reset_index(drop=True)
    
    return df

# Log unrecognized play descriptions for future review
UNKNOWN_PLAYS_LOG = "unknown_plays.txt"

def log_unknown_play(description):
    """ Logs unknown play descriptions for later review. """
    with open(UNKNOWN_PLAYS_LOG, "a") as file:
        file.write(description + "\n")
    print(f"LOGGED: Unrecognized Play -> {description}")

# Use spacy NLP to process the description
nlp = spacy.load('en_core_web_sm')

def compute_box_score_data(play_by_play_data):
    def extract_outcome_str(raw_outcome):
        return raw_outcome[0] if isinstance(raw_outcome, tuple) else raw_outcome

    def outcome_contains_error(outcome_str):
        return 'E' in outcome_str  # Broad match like '1B/E6' or 'GO6-3/E4'
    
    # ✅ Count hits by team from play_by_play_data directly
    hit_events = ['single', 'double', 'triple', 'home_run']
    team_hits = (
        play_by_play_data[play_by_play_data['events'].isin(hit_events)]
        .groupby('team')
        .size()
        .to_dict()
    )

    # Count errors against the fielding team
    team_errors = {}

    for _, row in play_by_play_data.iterrows():
        raw_outcome = row.get('Outcome', '')
        outcome_str = extract_outcome_str(raw_outcome)

        if outcome_contains_error(outcome_str):
            fielding_team = row['home_team'] if row['team'] == row['away_team'] else row['away_team']
            team_errors[fielding_team] = team_errors.get(fielding_team, 0) + 1

    runs_data = []

    # Initialize running totals per team and half
    last_score_by_team_half = {}

    for _, row in play_by_play_data.iterrows():
        team = row["team"]
        inning = row["inning"]
        half = row["inning_topbot"]

        key = (team, half)

        # Get which score column to use
        score_col = "post_away_score" if half == "Top" else "post_home_score"
        current_score = row[score_col]

        # Previous score tracker (default to 0)
        prev_score = last_score_by_team_half.get(key, 0)
        runs_scored = current_score - prev_score

        # Update running total
        last_score_by_team_half[key] = current_score

        # Only count *positive* changes (ignore no-score or weird backwards deltas)
        if runs_scored >= 0:
            runs_data.append({
                "team": team,
                "inning": inning,
                "runs": runs_scored
            })

    runs_df = pd.DataFrame(runs_data)

    # Group by team and inning, summing any split scoring within same inning
    runs_summary = runs_df.groupby(['team', 'inning'])['runs'].sum().unstack(fill_value=0)

    return runs_summary, team_hits, team_errors


def get_mlb_game_metadata(game_date, home_team_abbr):
    """
    Uses mlb-statsapi to pull venue and weather for a given date and home team.
    Returns a dict with 'venue', 'weather', and 'game_id'.
    """
    schedule = statsapi.schedule(date=game_date, sportId=1)

    for game in schedule:
        # Match on home team abbreviation
        if home_team_abbr.lower() in game['home_name'].lower():
            game_id = game['game_id']

            # Now fetch full game metadata using gamePk
            game_data = statsapi.get("game", {"gamePk": game_id})
            
            venue = (
                game_data.get("gameData", {})
                .get("venue", {})
                .get("name", "Unknown Ballpark")
            )

            weather = (
                game_data.get("gameInfo", {})
                or game_data.get("liveData", {}).get("weather", {}).get("condition")
                or "N/A"
            )

            attendance = (game_data.get("gameInfo", {}).get("attendance"))

            return {
                'venue': venue,
                'weather': weather,
                'game_id': game_id,
                'attendance': attendance
            }

    print("⚠ No matching game found in schedule.")
    return {
        'venue': 'Unknown Ballpark',
        'weather': 'N/A',
        'game_id': None,
        'attendance': 'N/A'
    }

# Standardize inning values
def refine_inning(inn):
    if isinstance(inn, str):
        match = re.search(r'\b(top|bottom)\s+of\s+the\s+(\d+)', inn, re.IGNORECASE)
        if match:
            side = 't' if 'top' in match.group(1).lower() else 'b'
            inning_number = match.group(2)
            return f"{side}{inning_number}"
        elif inn.lower().startswith(('t', 'b')) and inn[1:].isdigit():
            return inn
    return None

def extract_player_name(description, batter_id, id_to_name):

##    Extracts the first proper noun (likely the player's name) from the description.

    # ✅ Check if the batter ID exists in the lookup dictionary
    if batter_id in id_to_name:
        return id_to_name[batter_id]  # ✅ Use the official name from MLBAM ID lookup

# Parse play description outcomes
def parse_play_description(events, description, batter):

    if 'intentionally walks' in description:
        return 'IBB'

    if not isinstance(description, str) or description.strip() == "":
        return '-'
    
    if not isinstance(events,str) or events.strip() == "":
        return '-'

    events = events.lower()
    description = description.lower()

    description = re.sub(r'\(.*?\)', '', description)
    description = re.sub(r'\b(deep|short|short|weak|thru|hole|sharply)\b', '', description)
    description = description.strip()

    print(f"DEBUG: Cleaned description -> {description}")

    event_mappings = {
        'single': '1B',
        'double': '2B',
        'triple': '3B',
        'home_run': 'HR',
        'walk': 'BB',
        'hit_by_pitch': 'HBP',
        'catcher_interf': 'CI',
        'intentional_walk': 'IBB',
    }

    # **DEBUG PRINT: Check Description Processing**
    # print(f"DEBUG: Processing Description -> {description}")

    # **Explicitly Recognize Common Unique Plays**
    outcome = event_mappings.get(events, None)
    
    if 'hit by pitch' in description:
        return 'HBP'
    elif 'balk' in description:
        return 'BK'
    elif 'wild pitch' in description or 'wp' in description:
        return 'WP'
    elif 'passed ball' in description or 'pb' in description:
        return 'PB'
    elif 'fielder choice' in description or 'fc' in description:
        return 'FC'
    
    # **Rule-Based NLP Play Recognition**
    if 'strikeout' in events:
        if 'looking' in description or 'called' in description:
            return 'Ʞ'
        elif 'swinging' in description:
            return 'K'
        return 'K'

    # **Line Outs**
    if ('lines out' in description or 'lineout' in description) and ('cf' in description or 'center field' in description or 'center-field' in description):
        return 'L8'
    elif ('lines out' in description or 'lineout' in description) and ('rf' in description or 'right field' in description or 'right-field' in description):
        return 'L9'
    elif ('lines out' in description or 'lineout' in description) and ('lf' in description or 'left field' in description or 'left-field' in description):
        return 'L7'
    elif ('lines out' in description or 'lineout' in description) and ('ss' in description or 'shortstop' in description or 'short stop' in description):
        return 'L6'
    elif ('lines out' in description or 'lineout' in description) and ('2b' in description or 'second base' in description or 'second baseman' in description):
        return 'L4'
    elif ('lines out' in description or 'lineout' in description) and ('3b' in description or 'third base' in description or 'third baseman' in description):
        return 'L5'
    elif ('lines out' in description or 'lineout' in description) and ('1b' in description or 'first base' in description or 'first baseman' in description):
        return 'L3'
    elif ('lines out' in description or 'lineout' in description) and ('p' in description or 'pitcher' in description):
        return 'L1'
        
   # **Flyouts & Groundouts**
    if 'flies out' in description or 'flyball' in description:
        if 'left field' in description or 'lf' in description:
            return 'F7'
        elif 'center field' in description or 'cf' in description:
            return 'F8'
        elif 'right field' in description or 'rf' in description:
            return 'F9'
        
    #Sac Fly's
    if 'sac_fly'in events:
        if 'left field' in description or 'lf' in description or 'leftfield' in description:
            return 'SF7'
        if 'center field' in description or 'cf' in description or 'centerfield' in description:
            return 'SF8'
        if 'right field' in description or 'rf' in description or 'rightfield' in description:
            return 'SF9'
        
    #Grounded into Double Plays
    if 'grounded_into_double_play' in events:
        return parse_grounded_into_double_play(description)
    
    #Double-Plays
    if 'double_play' in events:
        return parse_double_play(description)
    
    #Force-Outs
    if 'force_out' in events:
        return parse_force_out(description)
    
    #Fielders Choices
    if 'fielders_choice' in events:
        return parse_fielders_choice(description)
    
    #Reached-On Errors
    if 'field_error' in events:
        return parse_reached_on_error(description)

    # **Groundouts**
    if ('grounds out' in description or 'groundout' in description):
        return parse_groundouts(description)

     # **Popouts**
    if ('pop flies' in description or 'pops out' in description or 'pop fly' in description) and ('catcher' in description or 'c ' in description):
        return 'P2'
    elif ('pop flies' in description or 'pops out' in description or 'pop fly' in description) and ('first baseman' in description or '1b' in description):
        return 'P3'
    elif ('pop flies' in description or 'pops out' in description or 'pop fly' in description) and ('second baseman' in description or '2b' in description):   
        return 'P4'
    elif ('pop flies' in description or 'pops out' in description or 'pop fly' in description) and ('third baseman' in description or '3b' in description):
        return 'P5'
    elif ('pop flies' in description or 'pops out' in description or 'pop fly' in description) and ('shortstop' in description or 'short stop' in description or 'ss' in description):
        return 'P6'
    elif ('pop flies' in description or 'pops out' in description or 'pop fly' in description) and ('pitcher' in description or 'p' in description):
        return 'P1'
    
     # ✅ Step 1: Identify the hit type (1B, 2B, 3B)
    if 'singles' in description or 'doubles' in description or 'triples' in description:
        hit_type = '1B' if 'singles' in description else ('2B' if 'doubles' in description else '3B')

        if isinstance(batter, int):
            batter = str(batter)

        # ✅ Step 2: Check if **the batter** was thrown out trying to advance
        # ✅ Use regex to match any variation of the batter's name (handles missing apostrophes)
        batter_pattern = re.sub(r"[^a-zA-Z ]", "", batter)  # ✅ Removes apostrophes & special chars from batter name
        if re.search(rf"{batter_pattern} out at", description, re.IGNORECASE):
            fielders = {
                'pitcher': '1', 'catcher': '2', 'first baseman': '3', 'second baseman': '4', 
                'third baseman': '5', 'shortstop': '6', 'left fielder': '7', 'center fielder': '8', 'right fielder': '9'
            }

            # ✅ Extract the putout sequence
            # ✅ Ensure only fielders after "out at" are captured
            match = re.search(r"out at(.*)", description)

            if match:
                fielders_after_out = match.group(1)  # ✅ Extract text **only after** "out at"
                fielders_found = re.findall(r"(pitcher|catcher|first baseman|second baseman|third baseman|shortstop|left fielder|center fielder|right fielder)", fielders_after_out)

                # ✅ Convert fielders to their correct position numbers
                involved_fielders = [fielders[f] for f in fielders_found if f in fielders]

            # ✅ Return the hit + putout notation
            if len(involved_fielders) >= 2:
                return f"{hit_type}/{'-'.join(involved_fielders)}", True  # ✅ Indicates baserunner action exists
        
        return hit_type, False  # ✅ No baserunner action
    
    return outcome if outcome else '-'

#Function to Parse GIDP's
def parse_grounded_into_double_play(description):
    """Identifies double plays and maintains the correct order of fielders."""
    fielders = {
        'pitcher': '1', 'catcher': '2', 'first baseman': '3', 'second baseman': '4', 'third baseman': '5',
        'shortstop': '6', 'left fielder': '7', 'center fielder': '8', 'right fielder': '9'
    }

     # ✅ Extract fielder names in order from the sentence
    fielders_found = re.findall(r"(pitcher|catcher|first baseman|second baseman|third baseman|shortstop|left fielder|center fielder|right fielder)", description)

    # ✅ Convert fielders to their corresponding numbers
    involved_fielders = [fielders[f] for f in fielders_found if f in fielders]

    # ✅ Remove only **consecutive** duplicates, but allow non-consecutive repeats
    cleaned_fielders = [involved_fielders[0]]
    for i in range(1, len(involved_fielders)):
        if involved_fielders[i] != involved_fielders[i - 1]:  # Only keep non-consecutive duplicates
            cleaned_fielders.append(involved_fielders[i])

    # ✅ Ensure proper force-out format
    if len(cleaned_fielders) >= 3:
        return f"GIDP{'-'.join(involved_fielders[:3])}"
    elif len(cleaned_fielders) >= 2:
        return f"GIDP{'-'.join(cleaned_fielders)}"
    elif len(cleaned_fielders) == 1:
        return f"GIDP{cleaned_fielders[0]}U"

    return "GIDP"

# ✅ Function to Parse Double Plays
def parse_double_play(description):
    """Identifies double plays and maintains the correct order of fielders."""
    fielders = {
        'pitcher': '1', 'catcher': '2', 'first baseman': '3', 'second baseman': '4', 'third baseman': '5',
        'shortstop': '6', 'left fielder': '7', 'center fielder': '8', 'right fielder': '9'
    }

     # ✅ Extract fielder names in order from the sentence
    fielders_found = re.findall(r"(pitcher|catcher|first baseman|second baseman|third baseman|shortstop|left fielder|center fielder|right fielder)", description)

    # ✅ Convert fielders to their corresponding numbers
    involved_fielders = [fielders[f] for f in fielders_found if f in fielders]

    # ✅ Remove only **consecutive** duplicates, but allow non-consecutive repeats
    cleaned_fielders = [involved_fielders[0]]
    for i in range(1, len(involved_fielders)):
        if involved_fielders[i] != involved_fielders[i - 1]:  # Only keep non-consecutive duplicates
            cleaned_fielders.append(involved_fielders[i])

    # ✅ Ensure proper force-out format
    if len(cleaned_fielders) >= 3:
        return f"DP{'-'.join(involved_fielders[:3])}"
    elif len(cleaned_fielders) >= 2:
        return f"DP{'-'.join(cleaned_fielders)}"
    elif len(cleaned_fielders) == 1:
        return f"DP{cleaned_fielders[0]}U"  # Unassisted Force Out

    return "DP"

# ✅ Function to Parse Force Outs
def parse_force_out(description):
    """Identifies force outs with fielder positions."""
    fielders = {
        'pitcher': '1', 'catcher': '2', 'first baseman': '3', 'second baseman': '4', 'third baseman': '5',
        'shortstop': '6', 'left fielder': '7', 'center fielder': '8', 'right fielder': '9'
    }

     # ✅ Extract fielder names in order from the sentence
    fielders_found = re.findall(r"(pitcher|catcher|first baseman|second baseman|third baseman|shortstop|left fielder|center fielder|right fielder)", description)

    # ✅ Convert fielders to their corresponding numbers
    involved_fielders = [fielders[f] for f in fielders_found if f in fielders]

    # ✅ Remove only **consecutive** duplicates, but allow non-consecutive repeats
    cleaned_fielders = [involved_fielders[0]]
    for i in range(1, len(involved_fielders)):
        if involved_fielders[i] != involved_fielders[i - 1]:  # Only keep non-consecutive duplicates
            cleaned_fielders.append(involved_fielders[i])

    # ✅ Ensure proper force-out format
    if len(cleaned_fielders) >= 2:
        return f"FO{'-'.join(cleaned_fielders)}"
    elif len(cleaned_fielders) == 1:
        return f"FO{cleaned_fielders[0]}U"  # Unassisted Force Out
    
    return "FO"

# ✅ Function to Parse Fielder’s Choice
def parse_fielders_choice(description):
    """Identifies fielder's choice plays with fielder positions."""
    fielders = {
        'pitcher': '1', 'catcher': '2', 'first baseman': '3', 'second baseman': '4', 'third baseman': '5',
        'shortstop': '6', 'left fielder': '7', 'center fielder': '8', 'right fielder': '9'
    }

     # ✅ Extract fielder names in order from the sentence
    fielders_found = re.findall(r"(pitcher|catcher|first baseman|second baseman|third baseman|shortstop|left fielder|center fielder|right fielder)", description)

    # ✅ Convert fielders to their corresponding numbers
    involved_fielders = [fielders[f] for f in fielders_found if f in fielders]

    # ✅ Remove only **consecutive** duplicates, but allow non-consecutive repeats
    cleaned_fielders = [involved_fielders[0]]
    for i in range(1, len(involved_fielders)):
        if involved_fielders[i] != involved_fielders[i - 1]:  # Only keep non-consecutive duplicates
            cleaned_fielders.append(involved_fielders[i])

    # ✅ Ensure proper force-out format
    if len(cleaned_fielders) >= 2:
        return f"FC{'-'.join(cleaned_fielders)}"
    elif len(cleaned_fielders) == 1:
        return f"FC{cleaned_fielders[0]}U"  # Unassisted Force Out

    return "FC"

def parse_groundouts(description):
    fielders = {
        'pitcher': '1', 'catcher': '2', 'first baseman': '3', 'second baseman': '4', 
        'third baseman': '5', 'shortstop': '6', 'left fielder': '7', 'center fielder': '8', 'right fielder': '9'
    }

     # ✅ Extract fielder names in order from the sentence
    fielders_found = re.findall(r"(pitcher|catcher|first baseman|second baseman|third baseman|shortstop|left fielder|center fielder|right fielder)", description)

    # ✅ Convert fielders to their corresponding numbers
    involved_fielders = [fielders[f] for f in fielders_found if f in fielders]

    # ✅ Ensure groundout abbreviation follows correct order
    if len(involved_fielders) >= 2:
        return f"GO{'-'.join(involved_fielders)}"
    elif len(involved_fielders) == 1:
        return f"GO{involved_fielders[0]}U"  # Unassisted Groundout
    
    return "GO"

def parse_reached_on_error(description):
    fielders = {
        'pitcher': '1', 'catcher': '2', 'first baseman': '3', 'second baseman': '4', 
        'third baseman': '5', 'shortstop': '6', 'left fielder': '7', 'center fielder': '8', 'right fielder': '9'
    }

    # ✅ Determine error type
    if 'throwing error' in description or 'errant throw' in description or 'bad throw' in description:
        error_type = '<sup>t</sup>' #Superscript 't' for throwing error
    elif 'fielding error' in description or 'misplay' in description or 'dropped ball' in description:
        error_type = '<sup>f</sup>' #Superscript 'f' for fielding error
    else:
        error_type = ''  # Default: No extra notation

    # ✅ Extract fielder names in order from the sentence
    fielders_found = re.findall(r"(pitcher|catcher|first baseman|second baseman|third baseman|shortstop|left fielder|center fielder|right fielder)", description)

    # ✅ Convert fielders to their corresponding numbers
    involved_fielders = [fielders[f] for f in fielders_found if f in fielders]

    # ✅ Ensure the abbreviation follows the correct order
    if len(involved_fielders) >= 2:
        return f"E{error_type}{'-'.join(involved_fielders)}"  # Multiple fielders involved
    elif len(involved_fielders) == 1:
        return f"E{error_type}{involved_fielders[0]}"  # Single fielder error

    return "E"  # Generic error if fielder is not identified

# Generate a miniature baseball diamond graphic
class BaseballDiamondGraphic(Flowable):
    def __init__(self, outcome, size=35, bases=None, balls=0, strikes=0):
        """
        outcome: The result of the play.
        size: The size of the diamond graphic.
        bases: List of bases occupied [1, 2, 3, 4].
        """
        Flowable.__init__(self)
        self.outcome = str(outcome) if outcome is not None else "-"
        self.size = size
        self.bases = bases if bases else []
        self.width = size
        self.height = size
        self.balls = balls
        self.strikes = strikes

    def draw(self):
        d = self.canv
        size = self.size
        center = size / 2

        # Center origin in the cell
        d.translate(center + 4, center - 2)

        # Diamond outline
        d.setStrokeColor(colors.black)
        d.setLineWidth(1)

        home = (0, -self.size * 0.4)
        first = (self.size * 0.4, 0)
        second = (0, self.size * 0.4)
        third = (-self.size * 0.4, 0)

        # Base diamond
        d.line(*home, *first)
        d.line(*first, *second)
        d.line(*second, *third)
        d.line(*third, *home)

        # Balls / strikes (small circles, notebook vibe)
        dot_radius = 2.2
        spacing = 4
        bx = -self.width / 2 - 2
        by = self.height / 2

        d.setLineWidth(0.3)

        # Strikes (2)
        for i in range(2):
            cy = by - i * spacing
            d.setFillColor(colors.white)
            d.setStrokeColor(colors.black)
            if i < self.strikes:
                d.setFillColor(colors.red)
            d.circle(bx, cy, dot_radius, stroke=1, fill=1)

        # Balls (3)
        for i in range(3):
            cy = by - i * spacing
            d.setFillColor(colors.white)
            d.setStrokeColor(colors.black)
            if i < self.balls:
                d.setFillColor(colors.green)
            d.circle(bx + dot_radius * 2 + 2, cy, dot_radius, stroke=1, fill=1)

        # Highlight basepaths in a slightly thicker line
        d.setLineWidth(1.5)
        d.setStrokeColor(colors.black)
        if 1 in self.bases:
            d.line(*home, *first)
        if 2 in self.bases:
            d.line(*first, *second)
        if 3 in self.bases:
            d.line(*second, *third)
        if 4 in self.bases:
            d.line(*third, *home)

        # Outcome text inside the diamond
        text = self.outcome
        if "/" in text:
            parts = text.split("/")
            # Show first part on top
            parts = [parts[0], parts[1]]
        else:
            parts = [text]

        # font size auto-fit
        longest = max(len(p) for p in parts)
        max_font_size = self.size / 3.3
        min_font_size = 4
        font_size = max(min_font_size, min(max_font_size, self.size * 1.6 / max(1, longest)))

        d.setFont(SCORECARD_CELL_FONT, font_size)
        d.setFillColor(BODY_TEXT_COLOR)

        if len(parts) > 1:
            y_offset = -font_size * 0.5
            for line in parts:
                d.drawCentredString(0, y_offset, line)
                y_offset += font_size
        else:
            d.drawCentredString(0, -font_size / 3, parts[0])

# Generate the PDF
def draw_page_background(canvas, doc):
    """Light notebook-gray background + thin border."""
    canvas.saveState()
    w, h = landscape(letter)
    canvas.setFillColor(PAGE_BG_COLOR)
    canvas.rect(0, 0, w, h, fill=1, stroke=0)

    # subtle border
    canvas.setStrokeColor(colors.HexColor("#b0b0b0"))
    canvas.setLineWidth(0.7)
    margin = 18
    canvas.rect(margin, margin, w - 2 * margin, h - 2 * margin, fill=0, stroke=1)
    canvas.restoreState()

def save_combined_scorecard(df, output_pdf, venue=None, weather=None, attendance=None, title=None, play_by_play_data=None, id_to_name=None):
    pdfmetrics.registerFont(TTFont('Unifont', "C:/Users/ncflo/.matplotlib/Unifont.ttf"))
    doc = SimpleDocTemplate(
        output_pdf,
        pagesize=landscape(letter),
        topMargin=20,
        bottomMargin=20,
        leftMargin=30,
        rightMargin=30
    )
    styles = getSampleStyleSheet()
    elements = []
    outcome_style = ParagraphStyle(
    'OutcomeStyle',
    parent=styles['Normal'],
    fontName='Unifont',  # ✅ Force Unifont in play descriptions
    fontSize=10
)

    # Title
    if title:
        title_style = ParagraphStyle('CustomTitle', parent=styles['Title'], fontSize=14, spaceAfter=10)
        elements.append(Paragraph(f"<b>{title}</b>", title_style))
        elements.append(Spacer(1, 4))

    valid_venue = venue and venue.lower() != "unknown ballpark"
    valid_weather = weather and weather != "N/A"
    valid_attendance = attendance and attendance != "N/A"

    if valid_venue and valid_weather:
        game_info = f"<b>Venue:</b> {venue} &nbsp;&nbsp;&nbsp; <b>Weather:</b> {weather}"
    elif valid_venue:
        game_info = f"<b>Venue:</b> {venue}"
    elif valid_weather:
        game_info = f"<b>Weather:</b> {weather}"
    elif valid_attendance:
        game_info = f"<b>Attendance:</b> {attendance:,}"
    else:
        game_info = None

    if game_info:
        elements.append(Paragraph(game_info, styles['Normal']))
        elements.append(Spacer(1, 4))

    # Create Box Score Table
    inning_run_summary, team_hits, team_errors = compute_box_score_data(play_by_play_data)

    # Determine how many innings to display (handle extras)
    max_inning = max(inning_run_summary.columns) if not inning_run_summary.empty else 9
    column_headers = ['Team'] + [str(i) for i in range(1, max_inning + 1)] + ['R', 'H', 'E']

    box_score_data = []

    for team_abbr, runs_per_inning in inning_run_summary.iterrows():
        row = [TEAM_ABBR_TO_NAME.get(team_abbr, team_abbr)]
        row += [runs_per_inning.get(i, 0) for i in range(1, max_inning + 1)]

        total_runs = sum(runs_per_inning)
        total_hits = team_hits.get(team_abbr, 0)
        total_errors = team_errors.get(team_abbr, 0)

        row += [total_runs, total_hits, total_errors]
        box_score_data.append(row)

    box_score_table = Table([column_headers] + box_score_data, hAlign='CENTER')
box_score_table.setStyle(TableStyle([
    ('GRID', (0, 0), (-1, -1), 0.5, GRID_COLOR),
    ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG_COLOR),
    ('TEXTCOLOR', (0, 0), (-1, 0), HEADER_TEXT_COLOR),
    ('FONTNAME', (0, 0), (-1, 0), SCORECARD_BOLD_FONT),
    ('FONTNAME', (0, 1), (-1, -1), SCORECARD_MAIN_FONT),
    ('FONTSIZE', (0, 0), (-1, -1), BOX_FONT_SIZE),
    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
]))


    elements.append(Spacer(1, 6))
    elements.append(Paragraph("<b>Box Score</b>", styles['Heading2']))
    elements.append(box_score_table)
    elements.append(Spacer(1, 10))


    for idx, (team_key, batter_stats) in enumerate(team_scorecards.items()):
        team = team_key[0] if isinstance(team_key, tuple) else team_key
        print(f"DEBUG: Generating scorecard for {team}")
        if batter_stats is None or batter_stats.empty:
            print(f"WARNING: No data for team {team}")
            continue
        # Add a team title
        full_team_name = TEAM_ABBR_TO_NAME.get(team, team)
        elements.append(Paragraph(f"<b>{full_team_name}</b>", styles['Heading2']))
        

        # Determine how many inning columns are present
        inning_columns = [
            col for team_df in team_scorecards.values() for col in team_df.columns if col.isdigit()
        ]
        max_inning = max(map(int, inning_columns)) if inning_columns else 9

        table_data = [['batter'] + [str(i) for i in range(1, max_inning + 1)] + ['PA', 'H', 'BB', 'SO']]

        # Add rows with diamond graphics
        for _, row in batter_stats.iterrows():
    display_batter = str(row['batter']).title()
    row_data = [display_batter]

            for i in range(1, max_inning + 1):
                outcome = row[str(i)]
                if isinstance(outcome, tuple):  # ✅ Ensure only the first value (string) is used
                    outcome = outcome[0]
                
                matching_play = play_by_play_data[
                (play_by_play_data["batter_name"] == row["batter"]) &
                (play_by_play_data["team"] == team) &
                (play_by_play_data["inning"] == i)
            ]
                bases = []

                # ✅ Handle ghost runners manually
                if i > 9 and matching_play.empty:
                    print(f"\n🔍 Evaluating possible ghost runner: Batter = {row['batter']} ({id_to_name.get(row['batter'], 'Unknown')}) — Inning: {i}")
                    # Get half-inning DataFrame
                    half_df = play_by_play_data[
                        (play_by_play_data["inning"] == i) &
                        (play_by_play_data["team"] == team)
                    ]

                    print(f"  ➤ Found {len(half_df)} plays in half-inning for team {team}")

                    if not half_df.empty:
                        first_play = half_df.iloc[0]
                        ghost_id = first_play.get("on_2b", None)
                        print(f"  ➤ First play 'on_2b' = {ghost_id} ({id_to_name.get(ghost_id, 'Unknown')})")

                        # ✅ Skip if this batter isn't the ghost runner
                        if (row["batter"]) != id_to_name.get(ghost_id, "").lower():
                            row_data.append(BaseballDiamondGraphic("-", bases=[]))
                            continue

                        if (row["batter"]) == id_to_name.get(ghost_id, "").lower():
                            print(f"  ➤ Comparing batter ID {row['batter']} to ghost ID {ghost_id}")
                            ghost_name = id_to_name.get(ghost_id, "").lower()
                            all_desc = " ".join(str(d).lower() for d in half_df["des"].dropna())
                            print(f"  ➤ Ghost runner name = {ghost_name}")
                            print(f"  ➤ Play-by-play mentions: {all_desc}")
                            if f"{ghost_name} scores" in all_desc:
                                ghost_bases = [3, 4]
                                print(f"  ✅ Ghost runner scored — highlighting 2B → home")
                            else:
                                ghost_bases = []
                                print(f"  ❕ Ghost runner did NOT score — only 2B highlighted")

                            row_data.append(BaseballDiamondGraphic("Ghost", bases=ghost_bases))
                            continue
                        else:
                            print(f"  🚫 This batter is not the ghost runner — skipping.")

                if not matching_play.empty:
                    initial = matching_play.iloc[0].get("initial_bases_reached", [])
                    advanced = matching_play.iloc[0].get("batter_bases_reached", [])
                    bases = sorted(set(initial + advanced))
                    balls = int(matching_play.iloc[0]['balls'])
                    strikes = int(matching_play.iloc[0]['strikes'])

                else:
                    balls = 0
                    strikes = 0
                   
                # Always draw a baseball diamond    
                # print(f"{row['batter']} in inning {i} — bases: {bases} — outcome: {outcome}")
                row_data.append(BaseballDiamondGraphic(str(outcome), bases=bases, balls=balls, strikes=strikes))
            row_data += [row['PA'], row['H'], row['BB'], row['SO']]
            table_data.append(row_data)

    # Define table
        # Total number of columns: batter + innings + stats
        num_stat_cols = 4
        total_columns = 1 + max_inning + num_stat_cols

        # Page width in points (landscape letter size)
        page_width = landscape(letter)[0]
        usable_width = page_width - 80  # leave ~40pt margin on each side

        # Assign more width to the batter name column
        batter_col_factor = 2  # batter name is 1.5x wider than others
        other_cols = total_columns - 1 + batter_col_factor  # sum of relative widths
        col_width = usable_width / other_cols

        col_widths = [col_width * batter_col_factor] + [col_width] * (total_columns - 1)
table = Table(table_data, colWidths=col_widths, rowHeights=ROW_HEIGHT, repeatRows=1)
table.setStyle(TableStyle([
    ('GRID', (0, 0), (-1, -1), 0.5, GRID_COLOR),
    ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG_COLOR),
    ('TEXTCOLOR', (0, 0), (-1, 0), HEADER_TEXT_COLOR),

    # Fonts: header vs batter names vs cells
    ('FONTNAME', (0, 0), (-1, 0), SCORECARD_BOLD_FONT),          # header row
    ('FONTNAME', (0, 1), (0, -1), SCORECARD_MAIN_FONT),          # batter names
    ('FONTNAME', (1, 1), (-1, -1), SCORECARD_CELL_FONT),         # inning boxes + stats
    ('FONTSIZE', (0, 0), (-1, -1), BOX_FONT_SIZE),

    ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ('ALIGN', (0, 1), (0, -1), 'LEFT'),
    ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
]))


        elements.append(table)

        # **Pitcher Stats Section**
        full_team_name = TEAM_ABBR_TO_NAME.get(team, team)
        elements.append(Paragraph(f"<b>Pitching Stats - {full_team_name}</b>", styles['Heading2']))

        pitcher_table_data = [['pitcher', 'IP', 'ER', 'H', 'HR', 'BB', 'K']]
        for pitcher, stats in pitcher_stats.items():
            pitcher_rows = play_by_play_data[play_by_play_data['pitcher'] == pitcher]
            if pitcher_rows.empty:
                continue

            # Check whether the pitcher pitched in top or bottom half
            inning_half = pitcher_rows['inning_topbot'].mode().values[0]
            inferred_team = pitcher_rows['home_team'].mode().values[0] if inning_half == 'Top' else pitcher_rows['away_team'].mode().values[0]

            if inferred_team != team:
                continue

          name = str(id_to_name.get(pitcher, str(pitcher))).title()

            pitcher_table_data.append([
                name,
                stats['IP'],
                stats['ER'],
                stats['H'],
                stats['HR'],
                stats['BB'],
                stats['K']
            ])

        # **Format Pitcher Table**
        pitcher_table = Table(pitcher_table_data, colWidths=[120, 50, 50, 50, 50, 50, 50])
        pitcher_table.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ]))

        elements.append(pitcher_table)
        elements.append(Spacer(1, 20))

        if idx < len(team_scorecards) - 1:
            elements.append(PageBreak()) # ✅ Separate each team's scorecard onto a new page
        
    #Generate the PDF
    doc.build(
    elements,
    onFirstPage=draw_page_background,
    onLaterPages=draw_page_background
)

    print(f"PDF scorecard saved at: {output_pdf}")

# Process play-by-play data
def process_play_by_play(df):
    
    # ✅ Extract relevant columns
    df_filtered = df[['inning', 'inning_topbot', 'des', 'batter', 'delta_home_win_exp','on_1b', 'on_2b', 'on_3b']].copy()

    # ✅ Keep only rows where 'events' is not null (final play results)
    # df_filtered = df.dropna(subset=['events']).copy()  # ✅ Keeps only plays with final outcomes

    df_filtered = df[
        df['des'].notna() & (
            df['events'].notna() | df['des'].str.contains("intentionally walks", case=False, na=False)
        )
    ].copy()

    #Identify Key Plays
    df_filtered['is_key_play'] = df_filtered['delta_home_win_exp'].abs() >= 0.25

    # ✅ Convert inning to T1, B1 format
    df_filtered['Refined Inn'] = df_filtered.apply(
        lambda row: f"T{row['inning']}" if row['inning_topbot'] == "Top" else f"B{row['inning']}", axis=1
    )

    df_filtered['team'] = df_filtered.apply(
    lambda row: row['away_team'] if row['inning_topbot'] == "Top" else row['home_team'], axis=1
    )

     # ✅ Parse play descriptions
    df_filtered['Outcome'] = df_filtered.apply(
    lambda row: parse_play_description(row['events'], row['des'], row['batter'])[0], axis=1  # ✅ Only get the first element (batter's outcome)
)
    # ✅ Ensure batter column is numeric
    df['batter'] = df['batter'].astype(int)

    # ✅ Get unique batter IDs
    batter_ids = df['batter'].dropna().unique()

    # ✅ Collect all unique pitcher IDs
    pitcher_ids = df['pitcher'].dropna().unique()

    # ✅ Fetch player names from MLBAM IDs
    player_info = playerid_reverse_lookup(batter_ids, key_type='mlbam')

    # ✅ Create a dictionary mapping ID -> Full Name
    id_to_name = {row['key_mlbam']: f"{row['name_first']} {row['name_last']}" for _, row in player_info.iterrows()}

    # ✅ Append to player lookup if not already included
    missing_pitchers = [pid for pid in pitcher_ids if pid not in id_to_name]
    if missing_pitchers:
        pitcher_info = playerid_reverse_lookup(missing_pitchers, key_type='mlbam')
        for _, row in pitcher_info.iterrows():
            id_to_name[row['key_mlbam']] = f"{row['name_first']} {row['name_last']}"

    # ✅ Apply name extraction using the player ID lookup
    df_filtered['batter_name'] = df_filtered.apply(
        lambda row: extract_player_name(row['des'], row['batter'], id_to_name), axis=1
    )

    def get_initial_bases_reached(row):
        event = str(row.get("events", "")).lower()
        if event == "single":
            return [1]
        elif event == "double":
            return [1, 2]
        elif event == "triple":
            return [1, 2, 3]
        elif event == "home_run":
            return [1, 2, 3, 4]
        elif event in ("walk", "hit_by_pitch", "catcher_interf"):
            return [1]
        else:
            return []
    df_filtered['initial_bases_reached'] = df_filtered.apply(get_initial_bases_reached, axis=1)

    def track_batter_base_advancement(batter_id, inning, half, inning_df, id_to_name, df_filtered):
            
            bases = set()
            batter_id = int(batter_id)

            # Track runs scored
            score_col = 'home_score' if half == 'Bot' else 'away_score'
            initial_score = inning_df.iloc[0][score_col]
            last_score = initial_score

            seen_on_base = False
            last_base_seen = 0

            for _, row in inning_df.iterrows():
                current_score = row[score_col]
                base_found = None

                # Check which base the batter is on
                if pd.notna(row['on_1b']) and int(row['on_1b']) == batter_id:
                    base_found = 1
                elif pd.notna(row['on_2b']) and int(row['on_2b']) == batter_id:
                    base_found = 2
                elif pd.notna(row['on_3b']) and int(row['on_3b']) == batter_id:
                    base_found = 3

                if base_found is not None:
                    seen_on_base = True
                    last_score = current_score
                    # Add all prior bases up to and including this one
                    for b in range(1, base_found + 1):
                        bases.add(b)
                    last_base_seen = base_found
                elif seen_on_base:
                    # Runner disappeared — check if they scored
                    if current_score > last_score:
                        for b in range(1, 5):
                            bases.add(b)
                    break

                # ✅ New: Check description text for “Name scores”
                desc = row.get('des', '').lower()
                name = id_to_name.get(batter_id, '').lower()
                if name and f"{name} scores" in desc:
                    for b in range(last_base_seen + 1, 5):
                        bases.add(b)
                    break

            return sorted(bases)
    
    batter_bases = []
    for i, row in df_filtered.iterrows():
        batter_id = int(row['batter'])
        inning = row['inning']
        half = row['inning_topbot']

        inning_df = df_filtered[(df_filtered['inning'] == inning) & (df_filtered['inning_topbot'] == half)]
        bases = track_batter_base_advancement(batter_id, inning, half, inning_df.loc[i:], id_to_name, df_filtered)
        batter_bases.append(bases)

    df_filtered["batter_bases_reached"] = batter_bases

    play_by_play_data = df_filtered.copy()
    # Group by team
    grouped_data = df_filtered.groupby(['team'])
    team_scorecards = {}
    pitcher_stats = {}
    pitcher_outs_counter = {}
    runner_responsibility = {}
    
    # Process each team's data separately
    for team, team_data in grouped_data:
        print(f"Processing scorecard for team: {team}")
        
        if team_data.empty:
                print(f"WARNING: No play data found for {team}!")
                continue
        
    # Initialize batter stats
    
        # ✅ Ensure 'des' column has valid batter names
        team_data['batter_name'] = team_data.apply(lambda row: extract_player_name(row['des'], row['batter'], id_to_name), axis=1)

        batting_innings = {batter: ['-'] * 9 for batter in team_data['batter_name'].dropna().unique()}
        for _, row in team_data.iterrows():
            batter = row['batter_name']
            pitcher = row['pitcher']
            inning = row['Refined Inn']

             # **Initialize Pitcher Stats If Not Tracked**
            if pitcher not in pitcher_stats:
                pitcher_stats[pitcher] = {
                    "IP": 0, "ER": 0, "K": 0, "BB": 0, "H": 0, "HR": 0,
                    "team": row['home_team'] if row['inning_topbot'] == 'Top' else row['away_team']
                }

            outcome = parse_play_description(row['events'], row['des'], row['batter'])
            if isinstance(outcome, tuple):  # handle complex returns
                outcome = outcome[0]

            if outcome in ('K', 'Ʞ'):
                pitcher_stats[pitcher]["K"] += 1

            elif outcome == 'BB':
                pitcher_stats[pitcher]["BB"] += 1

            elif outcome.split('/')[0] in ('1B', '2B', '3B', 'HR'):
                pitcher_stats[pitcher]["H"] += 1
                if outcome == 'HR':
                    pitcher_stats[pitcher]["HR"] += 1
            
            # Add this near the top of the loop
            current_half_inning = f"{row['inning_topbot']}_{row['inning']}"
            if 'last_half_inning' not in locals():
                last_half_inning = current_half_inning

            if current_half_inning != last_half_inning:
                print(f"🔄 New half-inning ({current_half_inning}) — resetting runner responsibility")
                runner_responsibility = {}
                last_half_inning = current_half_inning
            
            # ✅ Assign pitcher responsibility as soon as a runner reaches base
            batter_id = row["batter"]
            initial_bases = row.get("initial_bases_reached", [])
            batter_bases = row.get("batter_bases_reached", [])
            bases_reached = set(initial_bases + batter_bases)
            unearned_runner_ids = set()

            if any(base in bases_reached for base in [1, 2, 3, 4]) and batter_id not in runner_responsibility:
                runner_responsibility[batter_id] = pitcher
            # 🛑 Flag as unearned if play outcome is error/FC/etc.
            if isinstance(outcome, tuple):
                outcome = outcome[0]

            if outcome in ['E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7', 'E8', 'E9', 'FC', 'CI', 'WP', 'PB']:
                unearned_runner_ids.add(batter_id)

            # ✅ Assign responsibility for runners currently on base
            for base in ['on_1b', 'on_2b', 'on_3b']:
                runner_id = row[base]
                if pd.notna(runner_id) and runner_id not in runner_responsibility:
                    runner_responsibility[runner_id] = pitcher

            if 4 in bases_reached:
                responsible_pitcher = runner_responsibility.get(batter_id)
                print(f"✅ Batter {batter_id} scored — checking responsibility...")
                if responsible_pitcher:
                    print(f"🎯 Runner {batter_id} was put on base by pitcher {id_to_name.get(responsible_pitcher, responsible_pitcher)}")
                    if responsible_pitcher not in pitcher_stats:
                        print(f"❗Pitcher {responsible_pitcher} not yet in pitcher_stats — initializing...")
                        pitcher_stats[responsible_pitcher] = {
                            "IP": 0, "ER": 0, "K": 0, "BB": 0, "H": 0, "HR": 0,
                            "team": row['home_team'] if row['inning_topbot'] == 'Top' else row['away_team']
                        }
                    if batter_id not in unearned_runner_ids:
                        pitcher_stats[responsible_pitcher]["ER"] += 1
                        print(f"🏃 ER: Batter {batter_id} scored; charged to {id_to_name.get(responsible_pitcher, responsible_pitcher)}")
                    else:
                        print(f"🚫 Unearned Run: Batter {batter_id} reached on error/FC/etc.; no ER charged")
                    print(f"🏃 ER: Batter {batter_id} scored; charged to {id_to_name.get(responsible_pitcher, responsible_pitcher)}")
                    del runner_responsibility[batter_id]
                else:
                    print(f"⚠ No responsible pitcher found for runner {batter_id}")

            # Count IP by change in outs_when_up
            pitcher_rows = df[df['pitcher'] == pitcher].sort_values(by=['inning', 'at_bat_number'])
            outs_pitched = 0

            for i in range(1, len(pitcher_rows)):
                prev_row = pitcher_rows.iloc[i - 1]
                curr_row = pitcher_rows.iloc[i]

                prev_outs = prev_row['outs_when_up']
                curr_outs = curr_row['outs_when_up']

                if pd.notna(prev_outs) and pd.notna(curr_outs):
                    if curr_outs > prev_outs:
                        delta = int(curr_outs - prev_outs)
                        outs_pitched += delta
                        print(f"🟢 Counted {delta} outs for {id_to_name.get(pitcher, pitcher)} | {prev_outs} → {curr_outs}")
                    elif prev_outs == 2 and curr_outs == 0:
                        outs_pitched += 1
                        print(f"🔚 Inning reset (2 → 0) for {id_to_name.get(pitcher, pitcher)}")

                 # Possible double play: 1 → 0
                elif prev_outs == 1 and curr_outs == 0:
                    # Check if inning changes immediately after this play
                    prev_idx = prev_row.name
                    if prev_idx + 1 < len(df):
                        next_row = df.iloc[prev_idx + 1]
                        inning_ended = (
                            next_row['inning'] > prev_row['inning'] or
                            next_row['inning_topbot'] != prev_row['inning_topbot']
                        )
                        pitcher_returns = any(
                            (df['pitcher'] == pitcher) &
                            (df['inning'] == next_row['inning']) &
                            (df['inning_topbot'] == next_row['inning_topbot'])
                        )
                        if inning_ended and not pitcher_returns:
                            print(f"🔁 Inferred inning-ending double play for {id_to_name.get(pitcher, pitcher)}")
                            outs_pitched += 2
                # ✅ Extra check for first-inning double play (0 → 2) where pitcher has no inning-ending transition
                elif prev_outs == 0 and curr_outs == 2:
                            print(f"⚾️ Inferred 0→2 double play for {id_to_name.get(pitcher, pitcher)}")
                            outs_pitched += 2
            # Accounts for final out of pitcher's outing -- either if replaced after inning ends or replaced in same inning
            if not pitcher_rows.empty:
                last_row = pitcher_rows.iloc[-1]
                last_outs = last_row['outs_when_up']
                if pd.notna(last_outs) and last_outs in [0, 1, 2]:
                    last_idx = last_row.name
                    if last_idx + 1 < len(df):
                        next_row = df.iloc[last_idx + 1]

                        same_half = (
                            next_row['inning'] == last_row['inning'] and
                            next_row['inning_topbot'] == last_row['inning_topbot']
                        )
                        new_pitcher = next_row['pitcher'] != pitcher

                        inning_ended = (
                            next_row['inning'] > last_row['inning'] or
                            next_row['inning_topbot'] != last_row['inning_topbot']
                        )

                        if inning_ended or (same_half and new_pitcher and next_row['outs_when_up'] > last_row['outs_when_up']):
                            print(f"🧠 Inferred final out for {id_to_name.get(pitcher, pitcher)} at {last_outs} outs (exit)")
                            outs_pitched += 1
                    # ✅ NEW: Add this for final row of the game
                    elif last_idx + 1 == len(df):
                        if last_outs == 2:
                            print(f"🏁 Last out of the game credited to {id_to_name.get(pitcher, pitcher)}")
                            outs_pitched += 1

            full_innings = outs_pitched // 3
            partial_innings = outs_pitched % 3
            pitcher_stats[pitcher]["IP"] = float(f"{full_innings}.{partial_innings}")

                # **DEBUG PRINT: Confirm Pitcher Stats Are Being Updated**
            print(f"DEBUG: Pitcher Stats Updated -> {pitcher}: {pitcher_stats[pitcher]}")
            
            if inning and inning[1:].isdigit():
                inning_index = int(inning[1:]) - 1

                 # 🔁 Make sure list is long enough
                while len(batting_innings[batter]) <= inning_index:
                    batting_innings[batter].append('-')

                outcome = parse_play_description(row['events'],row['des'], row['batter'])

                if isinstance(outcome, tuple):
                    outcome = outcome[0]

                if row['is_key_play']:
                    outcome = f"{outcome} ⭐"
                    
                # **DEBUG PRINT: Ensure Play Outcome is Processed**
                print(f"DEBUG: Storing -> {batter}, Inning {inning_index+1}, Outcome: {outcome}")

                if batter not in batting_innings:
                    print(f"DEBUG: Batter '{batter}' not in batting_innings. All keys: {list(batting_innings.keys())}")
                    continue
                
                existing_outcome = batting_innings[batter][inning_index]

                if existing_outcome != '-':
                    if outcome not in existing_outcome.split("/"):
                        batting_innings[batter][inning_index] += f"/{outcome}"
                else:
                    batting_innings[batter][inning_index] = outcome
                
         # 🔁 Ensure all batters have the same number of inning columns (e.g. handle extras)
        max_inning_index = max(len(innings) for innings in batting_innings.values())
        for batter in batting_innings:
            while len(batting_innings[batter]) < max_inning_index:
                batting_innings[batter].append('-')  

    # Create batter stats DataFrame
        batter_stats = pd.DataFrame([{"batter": batter, **{str(i + 1): val for i, val in enumerate(innings)}}
            for batter, innings in batting_innings.items()
    ])
        
        # ✅ Identify all inning columns dynamically (e.g., 1–12 if extras)
        inning_columns = [col for col in batter_stats.columns if col.isdigit()]

    # Add statistics columns
        batter_stats["PA"] = batter_stats[inning_columns].apply(lambda row: sum(val != '-' for val in row), axis=1)
        def batter_cell_is_hit(val):
            return any(hit in str(val) for hit in ['1B', '2B', '3B', 'HR'])

        batter_stats["H"] = batter_stats[inning_columns].apply(
            lambda row: sum(batter_cell_is_hit(val) for val in row),
            axis=1
        )
        batter_stats["BB"] = batter_stats[inning_columns].apply(lambda row: sum(val == 'BB' for val in row), axis=1)
        batter_stats["SO"] = batter_stats[inning_columns].apply(lambda row: sum(val in ('K', 'Ʞ') for val in row), axis=1)

        # Store team stats separately
        print(f"DEBUG: Batter stats for {team}: \n{batter_stats}")
        print(batter_stats)

        if not batter_stats.empty:
            team_scorecards[team] = batter_stats
        else: print(f"WARNING: No valid batting stats for {team}, skipping storage.")

    
    print(f"DEBUG: Final teams stored: {list(team_scorecards.keys())}")  # ✅ Check if LAD appears
    print(df_filtered[['batter_name', 'inning', 'inning_topbot', 'batter_bases_reached']].tail(10))

    return team_scorecards, pitcher_stats, play_by_play_data, id_to_name

# Main Execution
if __name__ == "__main__":
    game_date = input("Enter the game date (YYYY-MM-DD): ").strip()
    home_team = input("Enter the home team abbreviation (e.g., NYY): ").strip().upper()
    away_team = input("Enter the away team abbreviation (e.g., LAD): ").strip().upper()
    output_pdf = input("Enter the path to save the PDF scorecard: ").strip()

    metadata = get_mlb_game_metadata(game_date, home_team)
    venue = metadata['venue']
    weather = metadata['weather']

    df = fetch_statcast_data(game_date, home_team, away_team)

    title_text = f"{away_team} @ {home_team} — {game_date}"

    if df is not None:
        team_scorecards, pitcher_stats, play_by_play_data, id_to_name = process_play_by_play(df)
        save_combined_scorecard(team_scorecards, output_pdf, venue=venue, weather=weather, title=title_text, play_by_play_data=play_by_play_data, id_to_name=id_to_name)  # ✅ Pass play_by_play_data
            
 # Keep the window open if running in Windows (for debugging purposes)
    input("Press Enter to exit...")

    
