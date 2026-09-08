 
# ♟️ Chess Insights

> **3 things about your chess that actually matter — no engine, no noise.**

---

## The Idea

Chess.com already shows you what happened in each game.  
This project shows you **why you keep losing** — as a pattern across your games.

No Stockfish. No centipawn scores. No noisy dashboards.  
Just three questions answered with your real data:

---

## What It Does

### 📌 One Fix This Week
Every week, exactly **one** actionable thing to work on.

> *"You score 66% in your first 3 games per session, then drop to 44% after. Cap sessions at 3."*

Not 10 tips. Not a generic report. One thing. Fix it. Re-check next week.

---

### 🕐 Best Time to Play
When are you actually sharp?

> *"Your best window is 8 PM–11 PM with a 62% score over 48 games."*

You also see weak windows, day-of-week trends, fatigue after game 3, and tilt after 2 straight losses.

---

### 🧬 Your Losing Recipe
The specific combinations that predict your losses.

> *"When playing Black in long games (35+ moves), your loss rate jumps far above baseline."*

Includes **how losses happen** (checkmate / timeout / resignation) and **actual vs Elo-expected score**.

---

## Why No Engine?

All features use only **game metadata** from Chess.com's free API:
- timestamps
- results
- colors
- openings (ECO / ECOUrl)
- move counts from PGN movetext
- ratings

That means:
- Runs quickly
- No engine cost
- Automatically updates with new games
- 100% free

---

## Accuracy Improvements in v2

- ✅ Timezone-safe timestamps (`st.context.timezone` + manual fallback)
- ✅ Correct draw handling (symmetric: neither side has `win`)
- ✅ Better move counting from PGN movetext (plies → full moves)
- ✅ Filters out variants and auto-aborted 0-move games
- ✅ Score-based metrics everywhere (Win=1, Draw=0.5, Loss=0)
- ✅ Session logic based on inactivity gap (45 minutes), not calendar day
- ✅ Elo expected-score comparison instead of naive "stronger opponent" heuristics
- ✅ Retry/backoff and basic rate-limit handling for fetch reliability

---

## New UX / Analysis Features

- Time class includes `daily`
- Analysis depth selector (3 / 6 / 12 / 24 months / all time)
- Rated-only filter
- Rating progression chart with 20-game trend line
- Wilson lower-bound ranking for best/worst time windows
- Methodology explainer in-app

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Data | Chess.com public API |
| App | Streamlit |
| Language | Python |
| DataFrame / charts | pandas |
| Engine | ❌ None needed |

---

## Quick Start (Streamlit)

```bash
# Install dependencies
pip install -r requirements_streamlit.txt

# Run app
streamlit run streamlit_app.py
```

Open the local URL shown by Streamlit (typically `http://localhost:8501`).

---

## Notes

- On some local Windows environments, install timezone database support:

```bash
pip install tzdata
```

- Data is fetched from Chess.com public endpoints and cached for 1 hour.
- No personal data is stored by this app.

---

*Built by [Omar](https://www.chess.com/member/omarrrexe) — turning game history into practical, weekly improvement signals.*
