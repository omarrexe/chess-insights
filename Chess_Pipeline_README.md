# ♟️ Chess Insights

> **3 things about your chess that actually matter — no engine, no noise.**

---

## The Idea

Chess.com already shows you what happened in each game.  
This project shows you **why you keep losing** — as a pattern across all your games.

No Stockfish. No centipawn scores. No charts for the sake of charts.  
Just three questions answered with your real data:

---

## What It Does

### 📌 One Fix This Week
Every week, exactly **one** actionable thing to work on.

> *"You win 68% of your first 3 games per session, then drop to 41% after. Stop at 3 games."*

Not 10 tips. Not a report. One thing. Change it. Come back next week.

---

### 🕐 Best Time to Play
When are you actually sharp?

> *"You win 61% of games between 20:00–23:00. You win 31% on weekend afternoons."*

Knowing when NOT to play is as valuable as knowing how to play.

---

### 🧬 Your Losing Recipe
The specific combination of factors that predict your losses.

> *"When you play the Sicilian as Black and the game goes past move 35, you lose 78% of the time."*

This is personal to **you** — not generic advice.

---

## Why No Engine?

All three features use only **game metadata** from Chess.com's free API:
- Timestamps, results, colors, openings, move counts, ratings

That means:
- Runs in **seconds** (not hours)
- No CPU cost
- Updates automatically with every new game
- 100% free

---

## Tech Stack

| Layer | Tech |
|-------|------|
| Data | Chess.com public API |
| Backend | Python + FastAPI |
| Frontend | Next.js 15 |
| Engine | ❌ None needed |

---

## Quick Start

```bash
# Install Python deps
pip install -r requirements.txt

# Install dashboard deps
cd dashboard && npm install && cd ..

# Start the API
python -m pipeline.api

# Start the dashboard (new terminal)
cd dashboard && npm run dev
```

Open **http://localhost:3000**

---

## Player: omarrrexe

| Time Control | Games | Win Rate |
|---|---|---|
| Rapid | ~916 | ~50% |
| Blitz | ~510 | ~49% |

---

*Built by [Omar](https://www.chess.com/member/omarrrexe) — turning 1,448 games into 3 useful insights.*
