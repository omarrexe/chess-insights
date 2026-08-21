"""
Chess Insights — 3 purely metadata-based analyses.
No Stockfish. No engine. Just your game history.
"""

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pipeline.config import RAW_GAMES_DIR


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_games(username: str) -> list[dict]:
    path = os.path.join(RAW_GAMES_DIR, f"{username}_games.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _parse_game(game: dict, username: str) -> dict | None:
    """Extract the fields we care about from one raw Chess.com game dict."""
    white = game.get("white", {})
    black = game.get("black", {})
    wname = white.get("username", "").lower()
    bname = black.get("username", "").lower()
    u     = username.lower()

    if wname == u:
        color         = "white"
        result_str    = white.get("result", "")
        player_rating = white.get("rating", 0)
        opp_rating    = black.get("rating", 0)
        accuracy      = game.get("accuracies", {}).get("white")
    elif bname == u:
        color         = "black"
        result_str    = black.get("result", "")
        player_rating = black.get("rating", 0)
        opp_rating    = white.get("rating", 0)
        accuracy      = game.get("accuracies", {}).get("black")
    else:
        return None

    won   = result_str == "win"
    drew  = result_str in ("agreed", "stalemate", "repetition", "insufficient", "50move", "timevsinsufficient")
    lost  = not won and not drew

    end_ts   = game.get("end_time", 0)
    dt       = datetime.fromtimestamp(end_ts) if end_ts else None
    pgn      = game.get("pgn", "")
    tc       = game.get("time_class", "")

    # move count from PGN
    move_count = len(re.findall(r"\d+\.\s+\S+", pgn))

    # opening from PGN headers
    eco_url_m    = re.search(r'\[ECOUrl "([^"]+)"\]', pgn)
    opening_name = (eco_url_m.group(1).split("/")[-1].replace("-", " ").title()
                    if eco_url_m else "Unknown")
    eco_m = re.search(r'\[ECO "([^"]+)"\]', pgn)
    eco   = eco_m.group(1) if eco_m else ""

    return {
        "uuid":          game.get("uuid", ""),
        "end_time":      end_ts,
        "dt":            dt,
        "color":         color,
        "won":           won,
        "drew":          drew,
        "lost":          lost,
        "result":        result_str,
        "player_rating": player_rating,
        "opp_rating":    opp_rating,
        "time_class":    tc,
        "time_control":  game.get("time_control", ""),
        "move_count":    move_count,
        "opening":       opening_name,
        "eco":           eco,
        "accuracy":      accuracy,
        "rated":         game.get("rated", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. BEST TIME TO PLAY
# ─────────────────────────────────────────────────────────────────────────────

def best_time_to_play(username: str, time_class: str = "rapid") -> dict:
    """
    Returns win-rate broken down by:
      - hour of day (0-23)
      - day of week (Monday=0 … Sunday=6)
      - game number within the day session (1st game, 2nd, …)

    Also returns the top recommendation as a plain sentence.
    """
    raw   = _load_games(username)
    games = [p for g in raw if (p := _parse_game(g, username)) and p["time_class"] == time_class]
    games.sort(key=lambda g: g["end_time"])

    if not games:
        return {}

    hour_data  = defaultdict(lambda: {"games": 0, "wins": 0})
    day_data   = defaultdict(lambda: {"games": 0, "wins": 0})
    seq_data   = defaultdict(lambda: {"games": 0, "wins": 0})  # game # in day

    # group by calendar day to find session sequence number
    day_groups: dict[str, list] = defaultdict(list)
    for g in games:
        if g["dt"]:
            day_key = g["dt"].strftime("%Y-%m-%d")
            day_groups[day_key].append(g)

    # assign session sequence
    for day_games in day_groups.values():
        day_games.sort(key=lambda g: g["end_time"])
        for idx, g in enumerate(day_games):
            g["session_seq"] = idx + 1  # 1-based

    for g in games:
        if not g["dt"]:
            continue
        h   = g["dt"].hour
        dow = g["dt"].weekday()    # 0=Mon, 6=Sun
        seq = g.get("session_seq", 1)
        seq_key = min(seq, 6)      # cap at "6+" bucket

        hour_data[h]["games"] += 1
        day_data[dow]["games"] += 1
        seq_data[seq_key]["games"] += 1
        if g["won"]:
            hour_data[h]["wins"] += 1
            day_data[dow]["wins"] += 1
            seq_data[seq_key]["wins"] += 1

    def to_rate(d: dict) -> list[dict]:
        rows = []
        for k, v in sorted(d.items()):
            g = v["games"]
            rows.append({
                "key":      k,
                "games":    g,
                "wins":     v["wins"],
                "win_rate": round(v["wins"] / g * 100, 1) if g else 0,
            })
        return rows

    hour_rows = to_rate(hour_data)
    day_rows  = to_rate(day_data)
    seq_rows  = to_rate(seq_data)

    DAY_NAMES  = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # ── Best / worst insights ──────────────────────────────────────────────
    insights = []

    # Best hour block (group into 3-hour windows for readability)
    hour_block: dict[int, dict] = defaultdict(lambda: {"games": 0, "wins": 0})
    for row in hour_rows:
        block = (row["key"] // 3) * 3
        hour_block[block]["games"] += row["games"]
        hour_block[block]["wins"]  += row["wins"]

    best_block = max(hour_block.items(),
                     key=lambda x: x[1]["wins"] / x[1]["games"] if x[1]["games"] >= 10 else 0,
                     default=None)
    worst_block = min(hour_block.items(),
                      key=lambda x: x[1]["wins"] / x[1]["games"] if x[1]["games"] >= 10 else 1,
                      default=None)

    if best_block and best_block[1]["games"] >= 10:
        bh = best_block[0]
        br = round(best_block[1]["wins"] / best_block[1]["games"] * 100, 1)
        def fmt(h):
            if h == 0: return "12 AM"
            if h < 12: return f"{h} AM"
            if h == 12: return "12 PM"
            return f"{h - 12} PM"
        insights.append({
            "type":  "best_hour",
            "text":  f"You win {br}% of games played between {fmt(bh)}–{fmt(bh+3)} — your sharpest window.",
            "value": br,
        })

    if worst_block and worst_block[1]["games"] >= 10:
        wh = worst_block[0]
        wr = round(worst_block[1]["wins"] / worst_block[1]["games"] * 100, 1)
        def fmt(h):
            if h == 0: return "12 AM"
            if h < 12: return f"{h} AM"
            if h == 12: return "12 PM"
            return f"{h - 12} PM"
        insights.append({
            "type":  "worst_hour",
            "text":  f"Avoid playing at {fmt(wh)}–{fmt(wh+3)} — only {wr}% win rate.",
            "value": wr,
        })

    # Session fatigue
    if len(seq_rows) >= 4:
        g1 = next((r for r in seq_rows if r["key"] == 1), None)
        g4 = next((r for r in seq_rows if r["key"] >= 4), None)
        if g1 and g4 and g1["games"] >= 10 and g4["games"] >= 10:
            drop = g1["win_rate"] - g4["win_rate"]
            if drop > 10:
                insights.append({
                    "type":  "fatigue",
                    "text":  f"Your win rate drops {drop:.0f}% after game 3 in a session. Stop at 3 games.",
                    "value": drop,
                })

    # Best day
    best_day = max(day_rows, key=lambda r: r["win_rate"] if r["games"] >= 10 else 0, default=None)
    if best_day and best_day["games"] >= 10:
        insights.append({
            "type":  "best_day",
            "text":  f"{DAY_NAMES[best_day['key']]} is your best day — {best_day['win_rate']}% win rate.",
            "value": best_day["win_rate"],
        })

    return {
        "by_hour":   hour_rows,
        "by_day":    [{"key": r["key"], "label": DAY_NAMES[r["key"]], **{k: v for k, v in r.items() if k != "key"}}
                      for r in day_rows],
        "by_session": seq_rows,
        "insights":  insights,
        "total_games": len(games),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. YOUR PERSONAL LOSING RECIPE
# ─────────────────────────────────────────────────────────────────────────────

def losing_recipe(username: str, time_class: str = "rapid") -> dict:
    """
    Finds the specific combination of factors most predictive of YOUR losses.
    Returns plain-language sentences instead of raw stats.
    """
    raw   = _load_games(username)
    games = [p for g in raw if (p := _parse_game(g, username)) and p["time_class"] == time_class]

    if not games:
        return {}

    total    = len(games)
    losses   = [g for g in games if g["lost"]]
    wins     = [g for g in games if g["won"]]
    loss_rate_overall = len(losses) / total

    patterns = []

    # ── helper: test a factor combination ─────────────────────────────────
    def pattern_stat(subset: list[dict], label: str, min_games: int = 20) -> dict | None:
        n   = len(subset)
        if n < min_games:
            return None
        n_loss = sum(1 for g in subset if g["lost"])
        rate   = n_loss / n
        lift   = rate / loss_rate_overall if loss_rate_overall else 1
        return {"label": label, "games": n, "loss_rate": round(rate * 100, 1),
                "overall_loss_rate": round(loss_rate_overall * 100, 1), "lift": round(lift, 2)}

    # ── Color ─────────────────────────────────────────────────────────────
    for color in ["white", "black"]:
        sub = [g for g in games if g["color"] == color]
        s   = pattern_stat(sub, f"Playing as {color.title()}")
        if s and s["lift"] > 1.2:
            patterns.append(s)

    # ── Long games ────────────────────────────────────────────────────────
    long_threshold = 35
    long_games = [g for g in games if g["move_count"] >= long_threshold]
    s = pattern_stat(long_games, f"Games lasting {long_threshold}+ moves")
    if s and s["lift"] > 1.2:
        patterns.append(s)

    # ── Short games (getting crushed fast) ────────────────────────────────
    short_threshold = 20
    short_games = [g for g in games if g["move_count"] <= short_threshold]
    s = pattern_stat(short_games, f"Games ending in {short_threshold} moves or fewer")
    if s and s["lift"] > 1.2:
        patterns.append(s)

    # ── Rating difference ─────────────────────────────────────────────────
    stronger_opp = [g for g in games if g["opp_rating"] - g["player_rating"] > 100]
    s = pattern_stat(stronger_opp, "Opponent rated 100+ above you", min_games=15)
    if s and s["lift"] > 1.1:
        patterns.append(s)

    equal_opp = [g for g in games if abs(g["opp_rating"] - g["player_rating"]) <= 50]
    s = pattern_stat(equal_opp, "Opponent at similar rating (±50)", min_games=15)
    if s and s["lift"] > 1.2:
        patterns.append(s)

    # ── Time of day (night = after 22:00) ─────────────────────────────────
    night_games = [g for g in games if g["dt"] and g["dt"].hour >= 22]
    s = pattern_stat(night_games, "Playing after 22:00 (late night)", min_games=10)
    if s and s["lift"] > 1.2:
        patterns.append(s)

    # ── Opening color combos ───────────────────────────────────────────────
    opening_counts: dict[str, dict] = defaultdict(lambda: {"games": 0, "losses": 0})
    for g in games:
        key = f"{g['color']}|{g['opening']}"
        opening_counts[key]["games"]  += 1
        opening_counts[key]["losses"] += int(g["lost"])

    for key, d in opening_counts.items():
        if d["games"] < 10:
            continue
        color_str, opening_str = key.split("|", 1)
        rate = d["losses"] / d["games"]
        lift = rate / loss_rate_overall if loss_rate_overall else 1
        if lift > 1.5:
            patterns.append({
                "label":     f"Playing {opening_str} as {color_str.title()}",
                "games":     d["games"],
                "loss_rate": round(rate * 100, 1),
                "overall_loss_rate": round(loss_rate_overall * 100, 1),
                "lift":      round(lift, 2),
            })

    # Sort by lift (worst first)
    patterns.sort(key=lambda p: p["lift"], reverse=True)

    # ── Convert to human sentences ─────────────────────────────────────────
    sentences = []
    for p in patterns[:5]:
        sentences.append(
            f"When {p['label'].lower()}, you lose {p['loss_rate']}% of the time "
            f"(vs your average of {p['overall_loss_rate']}%)."
        )

    # ── Won-but-should-have-lost: long games you win vs long you lose ──────
    long_wins  = [g for g in wins if g["move_count"] >= long_threshold]
    long_losses= [g for g in losses if g["move_count"] >= long_threshold]
    endgame_note = None
    if long_losses and long_wins:
        endgame_win_rate = len(long_wins) / (len(long_wins) + len(long_losses))
        if endgame_win_rate < 0.4:
            endgame_note = (
                f"You win only {endgame_win_rate*100:.0f}% of long games ({long_threshold}+ moves). "
                "Your endgame conversion is your biggest leak."
            )

    return {
        "patterns":        patterns[:8],
        "sentences":       sentences,
        "endgame_note":    endgame_note,
        "total_games":     total,
        "total_losses":    len(losses),
        "overall_loss_rate": round(loss_rate_overall * 100, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. ONE FIX THIS WEEK
# ─────────────────────────────────────────────────────────────────────────────

def one_fix_this_week(username: str, time_class: str = "rapid") -> dict:
    """
    Looks at the last 7 days of games and returns exactly ONE actionable fix.
    No charts. No tables. Just: here's what to fix, here's why, here's how.
    """
    import time as _time
    raw   = _load_games(username)
    games = [p for g in raw if (p := _parse_game(g, username)) and p["time_class"] == time_class]
    games.sort(key=lambda g: g["end_time"])

    if not games:
        return {}

    now_ts      = _time.time()
    week_ago_ts = now_ts - 7 * 86400
    recent      = [g for g in games if g["end_time"] >= week_ago_ts]

    # fallback: use last 20 games if fewer than 5 in past week
    if len(recent) < 5:
        recent = games[-20:]
        window_label = "last 20 games"
    else:
        window_label = "last 7 days"

    if not recent:
        return {}

    total    = len(recent)
    wins     = sum(1 for g in recent if g["won"])
    losses   = sum(1 for g in recent if g["lost"])
    draws    = total - wins - losses
    win_rate = wins / total

    # ── Candidate fixes ────────────────────────────────────────────────────
    candidates = []

    # FIX A: Session overplay — win rate drops after 3 games
    day_groups: dict[str, list] = defaultdict(list)
    for g in recent:
        if g["dt"]:
            day_groups[g["dt"].strftime("%Y-%m-%d")].append(g)
    early_wr = late_wr = None
    early_n = late_n = 0
    for day_games in day_groups.values():
        day_games.sort(key=lambda g: g["end_time"])
        for idx, g in enumerate(day_games):
            if idx < 3:
                early_n += 1
                early_wr = (early_wr or 0) + int(g["won"])
            else:
                late_n += 1
                late_wr = (late_wr or 0) + int(g["won"])

    if early_n >= 5 and late_n >= 3 and early_wr is not None and late_wr is not None:
        ewr = early_wr / early_n
        lwr = late_wr  / late_n
        if ewr - lwr > 0.15:
            candidates.append({
                "priority": (ewr - lwr) * 100,
                "fix":      "Stop after 3 games per session",
                "reason":   f"Your win rate is {ewr*100:.0f}% for your first 3 games, then drops to {lwr*100:.0f}% after. Fatigue is costing you real points.",
                "action":   "Set a hard limit: maximum 3 rapid games per sitting. Walk away after. Come back fresh tomorrow.",
                "icon":     "🛑",
            })

    # FIX B: Color weakness
    white_games  = [g for g in recent if g["color"] == "white"]
    black_games  = [g for g in recent if g["color"] == "black"]
    if len(white_games) >= 5 and len(black_games) >= 5:
        wwr = sum(1 for g in white_games if g["won"]) / len(white_games)
        bwr = sum(1 for g in black_games if g["won"]) / len(black_games)
        gap = abs(wwr - bwr)
        if gap > 0.2:
            weak_color  = "Black" if bwr < wwr else "White"
            weak_rate   = min(wwr, bwr) * 100
            strong_rate = max(wwr, bwr) * 100
            candidates.append({
                "priority": gap * 80,
                "fix":      f"Spend 15 min studying your {weak_color} openings",
                "reason":   f"You win {strong_rate:.0f}% as {'White' if weak_color == 'Black' else 'Black'} but only {weak_rate:.0f}% as {weak_color}. That's a {gap*100:.0f}% gap.",
                "action":   f"Pick ONE {weak_color} opening and learn it properly. Just one. Consistency beats variety at your level.",
                "icon":     "♟️",
            })

    # FIX C: Losing won positions (long games with bad result)
    long_games    = [g for g in recent if g["move_count"] >= 35]
    if len(long_games) >= 4:
        long_losses = sum(1 for g in long_games if g["lost"])
        long_loss_rate = long_losses / len(long_games)
        short_games = [g for g in recent if g["move_count"] < 25]
        short_loss_rate = sum(1 for g in short_games if g["lost"]) / len(short_games) if short_games else 0
        if long_loss_rate > 0.55 and long_loss_rate > short_loss_rate + 0.2:
            candidates.append({
                "priority": (long_loss_rate - short_loss_rate) * 70,
                "fix":      "Practice basic endgames — you're losing winning positions",
                "reason":   f"You lose {long_loss_rate*100:.0f}% of your long games. You're reaching good positions but can't convert them.",
                "action":   "Solve 5 basic king+pawn vs king endgame puzzles today. It takes 10 minutes and fixes the most common conversion failure.",
                "icon":     "📚",
            })

    # FIX D: Accuracy gap
    acc_games = [g for g in recent if g["accuracy"] is not None]
    if len(acc_games) >= 5:
        win_acc  = [g["accuracy"] for g in acc_games if g["won"]]
        loss_acc = [g["accuracy"] for g in acc_games if g["lost"]]
        if win_acc and loss_acc:
            avg_win  = sum(win_acc)  / len(win_acc)
            avg_loss = sum(loss_acc) / len(loss_acc)
            gap_acc  = avg_win - avg_loss
            if gap_acc > 10 and avg_loss < 75:
                candidates.append({
                    "priority": gap_acc * 0.6,
                    "fix":      "Slow down — take 5 more seconds before each move",
                    "reason":   f"Your accuracy is {avg_win:.0f}% when you win but {avg_loss:.0f}% when you lose. The gap is all about rushing.",
                    "action":   "Before every move, ask: 'Is any of my pieces hanging? Can they take anything for free?' That one habit alone is worth 50 rating points.",
                    "icon":     "⏸️",
                })

    # FIX E: general — just losing a lot
    if not candidates and win_rate < 0.40:
        candidates.append({
            "priority": 50,
            "fix":      "Check every move for hanging pieces before you click",
            "reason":   f"You won only {wins} of {total} games this week ({win_rate*100:.0f}%). At your level, most losses come from leaving pieces unprotected.",
            "action":   "Before EVERY move, look at all your pieces. Are any of them undefended? Can your opponent capture any of them for free? Do this without exception.",
            "icon":     "👁️",
        })

    if not candidates:
        candidates.append({
            "priority": 10,
            "fix":      "Keep playing — you're doing well",
            "reason":   f"You won {wins} of {total} games ({win_rate*100:.0f}%) this week. No major leak detected.",
            "action":   "Focus on enjoying the game. Review one loss per session just by replaying the moves — no engine needed.",
            "icon":     "✅",
        })

    # Pick the highest-priority fix
    best = max(candidates, key=lambda c: c["priority"])

    return {
        "window":    window_label,
        "games":     total,
        "wins":      wins,
        "losses":    losses,
        "draws":     draws,
        "win_rate":  round(win_rate * 100, 1),
        "fix":       best["fix"],
        "reason":    best["reason"],
        "action":    best["action"],
        "icon":      best["icon"],
        "all_candidates": [{"fix": c["fix"], "priority": round(c["priority"], 1)} for c in
                           sorted(candidates, key=lambda c: c["priority"], reverse=True)],
    }
