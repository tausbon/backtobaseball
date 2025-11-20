# Baseball Scorecard Generator

**Author**: Nick Floratos  
**License**: [Personal, Non-Commercial Use Only](LICENSE.txt)

---

## 🧠 Overview

This is a Python-based program that automatically generates accurately formatted **baseball scorecards** from real MLB games using **Statcast data**. The scorecards visually summarize the entire game, including:
- Batter-by-batter outcomes
- Pitching stats
- Runners on base (highlighted basepaths)
- Balls and strikes per plate appearance
- Key plays (highlighted)

This tool is intended to help baseball fans **commemorate the games they attend** or revisit historical games in a visual, engaging way.

---

## ⚙️ How It Works

1. **User Input**:
   - A specific game date (`YYYY-MM-DD`)
   - Home and away team abbreviations (e.g., `LAD`, `NYY`, `ATL`)
   - A destination file path for the final scorecard PDF (eg. C:\Users\ncflo\OneDrive\Desktop\test_1.pdf)

2. **Data Collection**:
   - Uses `pybaseball` to fetch Statcast data for the specified date
   - Filters the game by home/away teams
   - Uses `statsapi` to retrieve venue, weather, and attendance

3. **Play-by-Play Processing**:
   - Parses every play to determine its outcome
   - Tracks runners on base, advancement, and who is responsible for each earned run
   - Tags “key plays” with significant win probability swings

4. **PDF Generation**:
   - Creates a printable or shareable scorecard
   - Includes box score, detailed inning-by-inning outcomes, and pitcher stats
   - Uses baseball diamond graphics to show basepaths, pitch counts, and outcomes

---

## 📥 Input Requirements

When prompted, enter the following:
- Game date: `YYYY-MM-DD` (e.g., `2025-03-28`)
- Home team abbreviation (e.g., `LAD` for Dodgers)
- Away team abbreviation (e.g., `DET` for Tigers)
- File path to save the output PDF (e.g., `output.pdf`)

Example run:

```
Enter the game date (YYYY-MM-DD): 2025-03-28
Enter the home team abbreviation (e.g., NYY): LAD
Enter the away team abbreviation (e.g., DET): DET
Enter the path to save the PDF scorecard: (eg. C:\Users\ncflo\OneDrive\Desktop\test_1.pdf)
```

---

## 📤 Output

- A **print-ready PDF scorecard**, containing:
  - Box score and inning-by-inning run tally
  - Detailed play results for every batter
  - Runner movements, basepaths, and pitch counts
  - Pitching statistics per team
  - Highlighted “key plays” based on win expectancy change

Example output: See `/samples/test_1.pdf`

---

## 💡 Features

- Accurate representation of complex plays (e.g. GIDP, FC, E5)
- Ghost runner logic for extra innings
- Pitcher responsibility for earned runs
- Dynamic basepath highlighting and pitch count dots
- Play outcome parsing powered by regex and NLP
- PDF layout optimized for printing or digital sharing

---

## Problems Being Encountered

- Though play description is logical and reliable, there are obscure plays that may give the concatenating logic problems (triple plays, long relays, etc.)
- The 'earned run' stat calculation is being fine-tuned but is unreliable due to the concept of runner attribution to pitchers who leave games mid-inning.
- Basepath highlighting is mostly reliable -- most commonly encounters issues in extra innings with the 'ghost runner' rule.
- Pinch-hitters and pinch-runners are automatically generated at the bottom of the 'batter' column instead of in or below the spot in the lineup they took over for. This is the next step of code generation.
- If you encounter any more problems or inconsistencies, let me know!

---

## 🚫 Legal Notice
  
This project is **not affiliated with or endorsed by Major League Baseball (MLB)** or MLB Advanced Media.

All MLB trademarks, logos, and proprietary data belong to their respective owners.

See [`LICENSE.txt`](LICENSE.txt) for full terms.

---

## 📬 Contact

For questions, ideas, or anything else:  
**ncfloratos@gmail.com**

---

## 🙏 Acknowledgments

- [pybaseball](https://github.com/jldbc/pybaseball) — for Statcast data access  
- [statsapi](https://github.com/toddrob99/MLB-StatsAPI) — for game metadata  
- MLB Statcast — for publicly available game data  
- ReportLab — for professional PDF rendering
