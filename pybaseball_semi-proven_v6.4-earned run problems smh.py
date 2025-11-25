import pandas as pd
import re
import os
import requests
import statsapi  # type: ignore
from pybaseball import statcast
from pybaseball import playerid_reverse_lookup
from pybaseball import team_ids
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Flowable,
    PageBreak,
)
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib import colors
from reportlab.lib.units import inch
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

# Where to log unknown play strings
UNKNOWN_PLAYS_LOG = "unknown_plays.txt"

# NLP model
nlp = spacy.load("en_core_web_sm")


def fetch_statcast_data(game_date, home_team, away_team):
    print(f"📥 Fetching play-by-play data for {game_date}...")
    df = statcast(start_dt=game_date, end_dt=game_date)

    if df.empty:
        print("⚠ No data found for this date. Please check the game date and try again.")
        return None

    # Filter to specific game
    df = df[(df["home_team"] == home_team) & (df["away_team"] == away_team)]

    if df.empty:
        print(f"⚠ No game found for {home_team} vs {away_team} on {game_date}. Check team abbreviations.")
        return None

    df = df.sort_values(by=["inning", "at_bat_number"], ascending=[True, True]).reset_index(drop=True)
    return df


def log_unknown_play(description: str) -> None:
    with open(UNKNOWN_PLAYS_LOG, "a") as file:
        file.write(description + "\n")
    print(f"LOGGED: Unrecognized Play -> {description}")


def compute_box_score_data(play_by_play_data):
    def extract_outcome_str(raw_outcome):
        return raw_outcome[0] if isinstance(raw_outcome, tuple) else raw_outcome

    def outcome_contains_error(outcome_str):
        return "E" in outcome_str

    # hits
    hit_events = ["single", "double", "triple", "home_run"]
    team_hits = (
        play_by_play_data[play_by_play_data["events"].isin(hit_events)]
        .groupby("team")
        .size()
        .to_dict()
    )

    # errors
    team_errors = {}
    for _, row in play_by_play_data.iterrows():
        raw_outcome = row.get("Outcome", "")
        outcome_str = extract_outcome_str(raw_outcome)
