"""
Chess Insights — Streamlit App
3 purely metadata-based analyses. No engine. No noise.
"""

import streamlit as st
import requests
import json
import re
from collections import defaultdict
from datetime import datetime

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Chess Insights",
    page_icon="♟️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ─── Minimal custom CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 2rem; max-width: 800px; }
    .metric-row { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
    .insight-box {
        background: #1a1a2a;
        border-left: 4px solid #7c6eff;
        border-radius: 8px;
        padding: 0.85rem 1rem;
        margin-bottom: 0.6rem;
        font-size: 0.95rem;
    }
    .insight-box.bad  { border-left-color: #ff5e5e; background: #1f1212; }
    .insight-box.good { border-left-color: #4fffb0; background: #111f18; }
    .fix-box {
        background: linear-gradient(135deg, #141422, #1a1a2e);
        border: 1px solid #7c6eff;
        border-radius: 12px;
        padding: 1.5rem;
        margin-bottom: 1rem;
    }
    .action-box {
        background: #1c1c28;
        border-left: 4px solid #4fffb0;
        border-radius: 6px;
        padding: 0.75rem 1rem;
        margin-top: 0.75rem;
    }
</style>
""", unsafe_allow_html=True)

HEADERS = {"User-Agent": "Chess Insights App (github.com/omarrrexe)"}

# ─── Chess.com fetcher ────────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_all_games(username: str) -> list[dict]:
    """Fetch all games for a Chess.com username. Cached for 1 hour."""
    url = f"https://api.chess.com/pub/player/{username}/games/archives"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    if resp.status_code != 200:
        return []
    archives = resp.json().get("archives", [])
    all_games = []
    progress = st.progress(0, text="Fetching games from Chess.com…")
    for i, archive_url in enumerate(archives):
        try:
            r = requests.get(archive_url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                all_games.extend(r.json().get("games", []))
        except Exception:
            pass
        progress.progress((i + 1) / len(archives), text=f"Fetching… {i+1}/{len(archives)} months")
    progress.empty()
    return all_games


# ─── Parse a single game ─────────────────────────────────────────────────────
def parse_game(game: dict, username: str) -> dict | None:
    white = game.get("white", {})
    black = game.get("black", {})
    wname = white.get("username", "").lower()
    bname = black.get("username", "").lower()
    u = username.lower()

    if wname == u:
        color = "white"
        result_str = white.get("result", "")
        player_rating = white.get("rating", 0)
        opp_rating = black.get("rating", 0)
        accuracy = game.get("accuracies", {}).get("white")
    elif bname == u:
        color = "black"
        result_str = black.get("result", "")
        player_rating = black.get("rating", 0)
        opp_rating = white.get("rating", 0)
        accuracy = game.get("accuracies", {}).get("black")
    else:
        return None

    won  = result_str == "win"
    drew = result_str in ("agreed", "stalemate", "repetition", "insufficient", "50move", "timevsinsufficient")
    lost = not won and not drew

    end_ts = game.get("end_time", 0)
    dt = datetime.fromtimestamp(end_ts) if end_ts else None
    pgn = game.get("pgn", "")
    move_count = len(re.findall(r"\d+\.\s+\S+", pgn))

    eco_url_m = re.search(r'\[ECOUrl "([^"]+)"\]', pgn)
    opening = (eco_url_m.group(1).split("/")[-1].replace("-", " ").title()
               if eco_url_m else "Unknown")

    return {
        "end_time": end_ts, "dt": dt, "color": color,
        "won": won, "drew": drew, "lost": lost, "result": result_str,
        "player_rating": player_rating, "opp_rating": opp_rating,
        "time_class": game.get("time_class", ""),
        "move_count": move_count, "opening": opening, "accuracy": accuracy,
    }


def get_games(raw: list, username: str, time_class: str) -> list[dict]:
    games = [p for g in raw if (p := parse_game(g, username)) and p["time_class"] == time_class]
    games.sort(key=lambda g: g["end_time"])
    return games


# ─── Analysis 1: One Fix This Week ───────────────────────────────────────────
def one_fix(games: list[dict]) -> dict:
    import time as _time
    now_ts = _time.time()
    week_ago = now_ts - 7 * 86400
    recent = [g for g in games if g["end_time"] >= week_ago]
    if len(recent) < 5:
        recent = games[-20:]
        window_label = "last 20 games"
    else:
        window_label = "last 7 days"
    if not recent:
        return {}

    total = len(recent)
    wins  = sum(1 for g in recent if g["won"])
    losses = sum(1 for g in recent if g["lost"])
    draws = total - wins - losses
    win_rate = wins / total if total else 0

    candidates = []
    day_groups = defaultdict(list)
    for g in recent:
        if g["dt"]:
            day_groups[g["dt"].strftime("%Y-%m-%d")].append(g)

    early_wins = early_n = late_wins = late_n = 0
    for day_games in day_groups.values():
        day_games.sort(key=lambda g: g["end_time"])
        for idx, g in enumerate(day_games):
            if idx < 3:
                early_n += 1; early_wins += int(g["won"])
            else:
                late_n += 1; late_wins += int(g["won"])

    if early_n >= 5 and late_n >= 3:
        ewr = early_wins / early_n; lwr = late_wins / late_n
        if ewr - lwr > 0.15:
            candidates.append({
                "priority": (ewr - lwr) * 100,
                "fix": "Stop after 3 games per session",
                "reason": f"Your win rate is {ewr*100:.0f}% for your first 3 games, then drops to {lwr*100:.0f}% after. Fatigue is costing you real points.",
                "action": "Set a hard limit: maximum 3 games per sitting. Walk away after. Come back fresh tomorrow.",
                "icon": "🛑",
            })

    white_g = [g for g in recent if g["color"] == "white"]
    black_g = [g for g in recent if g["color"] == "black"]
    if len(white_g) >= 5 and len(black_g) >= 5:
        wwr = sum(1 for g in white_g if g["won"]) / len(white_g)
        bwr = sum(1 for g in black_g if g["won"]) / len(black_g)
        gap = abs(wwr - bwr)
        if gap > 0.2:
            weak = "Black" if bwr < wwr else "White"
            strong_rate = max(wwr, bwr) * 100; weak_rate = min(wwr, bwr) * 100
            candidates.append({
                "priority": gap * 80,
                "fix": f"Study your {weak} openings",
                "reason": f"You win {strong_rate:.0f}% as {'White' if weak == 'Black' else 'Black'} but only {weak_rate:.0f}% as {weak}. A {gap*100:.0f}% gap.",
                "action": f"Pick ONE {weak} opening and learn it properly. Just one. Consistency beats variety at your level.",
                "icon": "♟️",
            })

    long_g = [g for g in recent if g["move_count"] >= 35]
    if len(long_g) >= 4:
        ll_rate = sum(1 for g in long_g if g["lost"]) / len(long_g)
        short_g = [g for g in recent if g["move_count"] < 25]
        sl_rate = sum(1 for g in short_g if g["lost"]) / len(short_g) if short_g else 0
        if ll_rate > 0.55 and ll_rate > sl_rate + 0.2:
            candidates.append({
                "priority": (ll_rate - sl_rate) * 70,
                "fix": "Practice basic endgames",
                "reason": f"You lose {ll_rate*100:.0f}% of long games. You're reaching good positions but can't convert them.",
                "action": "Solve 5 basic king+pawn endgame puzzles today. 10 minutes. Fixes the most common conversion failure.",
                "icon": "📚",
            })

    acc_g = [g for g in recent if g["accuracy"] is not None]
    if len(acc_g) >= 5:
        win_acc  = [g["accuracy"] for g in acc_g if g["won"]]
        loss_acc = [g["accuracy"] for g in acc_g if g["lost"]]
        if win_acc and loss_acc:
            avg_w = sum(win_acc) / len(win_acc)
            avg_l = sum(loss_acc) / len(loss_acc)
            if avg_w - avg_l > 10 and avg_l < 75:
                candidates.append({
                    "priority": (avg_w - avg_l) * 0.6,
                    "fix": "Slow down — take 5 more seconds per move",
                    "reason": f"Your accuracy is {avg_w:.0f}% when you win but {avg_l:.0f}% when you lose. The gap is all about rushing.",
                    "action": "Before every move: 'Is any piece of mine hanging? Can they take anything for free?' One habit = 50 rating points.",
                    "icon": "⏸️",
                })

    if not candidates:
        candidates.append({
            "priority": 10,
            "fix": "Keep playing — no major leak detected",
            "reason": f"You won {wins}/{total} games ({win_rate*100:.0f}%) this week. Solid performance.",
            "action": "Review one loss per session by replaying moves — no engine needed. Just ask 'where did it go wrong?'",
            "icon": "✅",
        })

    best = max(candidates, key=lambda c: c["priority"])
    return {
        "window": window_label, "total": total, "wins": wins,
        "losses": losses, "draws": draws, "win_rate": round(win_rate * 100, 1),
        **best, "others": [c["fix"] for c in sorted(candidates, key=lambda c: -c["priority"]) if c["fix"] != best["fix"]],
    }


# ─── Analysis 2: Best Time to Play ───────────────────────────────────────────
def best_time(games: list[dict]) -> dict:
    hour_data = defaultdict(lambda: {"games": 0, "wins": 0})
    day_data  = defaultdict(lambda: {"games": 0, "wins": 0})
    seq_data  = defaultdict(lambda: {"games": 0, "wins": 0})

    day_groups = defaultdict(list)
    for g in games:
        if g["dt"]:
            day_groups[g["dt"].strftime("%Y-%m-%d")].append(g)
    for day_games in day_groups.values():
        day_games.sort(key=lambda g: g["end_time"])
        for idx, g in enumerate(day_games):
            g["_seq"] = min(idx + 1, 6)

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for g in games:
        if not g["dt"]: continue
        h   = g["dt"].hour
        dow = g["dt"].weekday()
        seq = g.get("_seq", 1)
        hour_data[h]["games"] += 1
        day_data[dow]["games"] += 1
        seq_data[seq]["games"] += 1
        if g["won"]:
            hour_data[h]["wins"] += 1
            day_data[dow]["wins"] += 1
            seq_data[seq]["wins"] += 1

    def to_rate(d):
        return [(k, v["games"], round(v["wins"]/v["games"]*100, 1) if v["games"] else 0)
                for k, v in sorted(d.items())]

    def fmt_h(h):
        if h == 0: return "12 AM"
        if h < 12: return f"{h} AM"
        if h == 12: return "12 PM"
        return f"{h-12} PM"

    hour_rows = [(fmt_h(k), g, wr) for k, g, wr in to_rate(hour_data) if g >= 3]
    day_rows  = [(DAY_NAMES[k], g, wr) for k, g, wr in to_rate(day_data)]
    seq_rows  = [(f"#{k}" if k < 6 else "6+", g, wr) for k, g, wr in to_rate(seq_data)]

    # Insights
    insights = []
    hour_block = defaultdict(lambda: {"games": 0, "wins": 0})
    for h, v in hour_data.items():
        b = (h // 3) * 3
        hour_block[b]["games"] += v["games"]; hour_block[b]["wins"] += v["wins"]

    best_b  = max(hour_block.items(), key=lambda x: x[1]["wins"]/x[1]["games"] if x[1]["games"]>=10 else 0, default=None)
    worst_b = min(hour_block.items(), key=lambda x: x[1]["wins"]/x[1]["games"] if x[1]["games"]>=10 else 1, default=None)
    if best_b and best_b[1]["games"] >= 10:
        bh = best_b[0]; br = round(best_b[1]["wins"]/best_b[1]["games"]*100,1)
        insights.append(("good", f"You win **{br}%** of games between {fmt_h(bh)}–{fmt_h(bh+3)} — your sharpest window."))
    if worst_b and worst_b[1]["games"] >= 10:
        wh = worst_b[0]; wr = round(worst_b[1]["wins"]/worst_b[1]["games"]*100,1)
        insights.append(("bad", f"Avoid playing at {fmt_h(wh)}–{fmt_h(wh+3)} — only **{wr}%** win rate."))

    seq_list = [(k, v["games"], round(v["wins"]/v["games"]*100,1) if v["games"] else 0) for k, v in sorted(seq_data.items())]
    if len(seq_list) >= 4:
        g1 = next((r for r in seq_list if r[0] == 1), None)
        g4 = next((r for r in seq_list if r[0] >= 4), None)
        if g1 and g4 and g1[1] >= 5 and g4[1] >= 3 and g1[2] - g4[2] > 10:
            insights.append(("bad", f"Win rate drops **{g1[2]-g4[2]:.0f}%** after game 3 in a session. Stop at 3 games."))

    best_day = max(day_data.items(), key=lambda x: x[1]["wins"]/x[1]["games"] if x[1]["games"]>=10 else 0, default=None)
    if best_day and best_day[1]["games"] >= 10:
        wr = round(best_day[1]["wins"]/best_day[1]["games"]*100,1)
        insights.append(("good", f"**{DAY_NAMES[best_day[0]]}** is your best day — **{wr}%** win rate."))

    return {"hour_rows": hour_rows, "day_rows": day_rows, "seq_rows": seq_rows, "insights": insights}


# ─── Analysis 3: Losing Recipe ────────────────────────────────────────────────
def losing_recipe(games: list[dict]) -> dict:
    if not games: return {}
    total = len(games)
    losses = [g for g in games if g["lost"]]
    lr_overall = len(losses) / total

    patterns = []

    def check(subset, label, min_g=20):
        n = len(subset)
        if n < min_g: return
        nl = sum(1 for g in subset if g["lost"])
        rate = nl / n
        lift = rate / lr_overall if lr_overall else 1
        if lift > 1.2:
            patterns.append({"label": label, "games": n, "loss_rate": round(rate*100,1),
                              "overall": round(lr_overall*100,1), "lift": round(lift,2)})

    check([g for g in games if g["color"]=="black"], "Playing as Black")
    check([g for g in games if g["color"]=="white"], "Playing as White")
    check([g for g in games if g["move_count"] >= 35], "Long games (35+ moves)")
    check([g for g in games if g["move_count"] <= 20], "Short games (≤20 moves)")
    check([g for g in games if g["opp_rating"] - g["player_rating"] > 100], "Opponent 100+ above you", 15)
    check([g for g in games if g["dt"] and g["dt"].hour >= 22], "Playing after 10 PM", 10)

    oc = defaultdict(lambda: {"games": 0, "losses": 0})
    for g in games:
        key = f"{g['color']}|{g['opening']}"
        oc[key]["games"] += 1; oc[key]["losses"] += int(g["lost"])
    for key, d in oc.items():
        if d["games"] < 10: continue
        color_str, opening_str = key.split("|", 1)
        rate = d["losses"] / d["games"]; lift = rate / lr_overall if lr_overall else 1
        if lift > 1.5:
            patterns.append({"label": f"{opening_str} as {color_str.title()}", "games": d["games"],
                              "loss_rate": round(rate*100,1), "overall": round(lr_overall*100,1), "lift": round(lift,2)})

    patterns.sort(key=lambda p: p["lift"], reverse=True)
    sentences = [
        f"When **{p['label'].lower()}**, you lose **{p['loss_rate']}%** (vs your average of {p['overall']}%)."
        for p in patterns[:5]
    ]

    long_wins  = [g for g in games if g["won"] and g["move_count"] >= 35]
    long_losses= [g for g in games if g["lost"] and g["move_count"] >= 35]
    endgame_note = None
    if long_wins or long_losses:
        total_long = len(long_wins) + len(long_losses)
        if total_long > 0:
            ewr = len(long_wins) / total_long
            if ewr < 0.4:
                endgame_note = f"You win only **{ewr*100:.0f}%** of long games. Your endgame conversion is your biggest leak."

    return {
        "patterns": patterns[:8], "sentences": sentences,
        "endgame_note": endgame_note,
        "total": total, "total_losses": len(losses),
        "overall_loss_rate": round(lr_overall*100, 1),
    }


# ─── Bar chart helper ─────────────────────────────────────────────────────────
def render_bars(rows: list[tuple], key_label: str):
    """rows = [(label, games, win_rate)]"""
    if not rows:
        st.caption("Not enough data yet.")
        return
    max_wr = max(r[2] for r in rows) or 1
    for label, games, wr in rows:
        col1, col2, col3 = st.columns([2, 5, 1])
        with col1:
            st.caption(label)
        with col2:
            color = "#4fffb0" if wr >= 55 else "#ff5e5e" if wr <= 38 else "#7c6eff"
            fill = int((wr / max_wr) * 100)
            st.markdown(f"""
            <div style="background:#1c1c22;border-radius:4px;height:18px;overflow:hidden;margin-top:4px">
              <div style="background:{color};width:{fill}%;height:100%;border-radius:4px"></div>
            </div>""", unsafe_allow_html=True)
        with col3:
            st.caption(f"**{wr}%**" if games >= 10 else "—")


# ─── Main App ─────────────────────────────────────────────────────────────────
st.markdown("# ♟️ Chess Insights")
st.markdown("*3 things about your chess that actually matter — no engine, no noise.*")
st.divider()

# Username input
col1, col2 = st.columns([4, 1])
with col1:
    username = st.text_input("", placeholder="Chess.com username…", label_visibility="collapsed")
with col2:
    search = st.button("Analyze →", type="primary", use_container_width=True)

if not username:
    st.markdown("""
    <div style="text-align:center;padding:3rem 1rem;color:#6b6b80">
        <div style="font-size:3rem;margin-bottom:1rem">♟️</div>
        <div style="font-size:1.1rem;color:#e8e8f0;font-weight:600;margin-bottom:0.5rem">Search any Chess.com player</div>
        <div style="font-size:0.875rem;line-height:1.7;max-width:360px;margin:0 auto">
            Enter a username above and click <strong>Analyze →</strong><br>
            Games are fetched live from Chess.com — no setup needed.
        </div>
    </div>""", unsafe_allow_html=True)
    st.stop()

username = username.strip().lower()

# Time class selector
time_class = st.radio(
    "Time class", ["rapid", "blitz", "bullet"],
    horizontal=True, label_visibility="collapsed",
    index=0,
)

# Fetch games
with st.spinner(f"Loading {username}'s games…"):
    raw_games = fetch_all_games(username)

if not raw_games:
    st.error(f"❌ Could not find **{username}** on Chess.com. Check the username and try again.")
    st.stop()

games = get_games(raw_games, username, time_class)

# Player overview
wins   = sum(1 for g in games if g["won"])
losses = sum(1 for g in games if g["lost"])
draws  = sum(1 for g in games if g["drew"])
total  = len(games)
current_rating = games[-1]["player_rating"] if games else 0

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total Games",    f"{total:,}")
c2.metric("Win Rate",       f"{wins/total*100:.0f}%" if total else "—")
c3.metric("Wins",           f"{wins:,}")
c4.metric("Current Rating", current_rating)

st.divider()

if not games:
    st.warning(f"No {time_class} games found for **{username}**. Try a different time class.")
    st.stop()

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["📌 One Fix This Week", "🕐 Best Time to Play", "🧬 Losing Recipe"])

# ── Tab 1: One Fix ────────────────────────────────────────────────────────────
with tab1:
    fix = one_fix(games)
    if not fix:
        st.info("Not enough recent games to analyze.")
    else:
        st.markdown(f"""
        <div class="fix-box">
            <div style="font-size:2rem;margin-bottom:0.5rem">{fix['icon']}</div>
            <div style="font-size:1.2rem;font-weight:700;color:#7c6eff;margin-bottom:0.5rem">{fix['fix']}</div>
            <div style="color:#9090a0;font-size:0.9rem;line-height:1.6;margin-bottom:0.75rem">{fix['reason']}</div>
            <div class="action-box">
                <div style="font-size:0.7rem;text-transform:uppercase;letter-spacing:.06em;color:#4fffb0;margin-bottom:0.25rem;font-weight:600">What to do right now</div>
                {fix['action']}
            </div>
        </div>
        <div style="font-size:0.78rem;color:#6b6b80;margin-top:0.5rem">
            Based on your {fix['window']} · {fix['wins']}W / {fix['losses']}L / {fix['draws']}D ({fix['win_rate']}% win rate)
        </div>
        """, unsafe_allow_html=True)

        if fix.get("others"):
            with st.expander("Other things to work on"):
                for other in fix["others"]:
                    st.markdown(f"- {other}")

# ── Tab 2: Best Time ──────────────────────────────────────────────────────────
with tab2:
    bt = best_time(games)

    if bt.get("insights"):
        for cls, text in bt["insights"]:
            box_cls = "good" if cls == "good" else "bad"
            st.markdown(f'<div class="insight-box {box_cls}">{text}</div>', unsafe_allow_html=True)
        st.markdown("")

    st.markdown("**Win Rate by Hour (your local time)**")
    render_bars(bt.get("hour_rows", []), "Hour")

    st.markdown("")
    st.markdown("**Win Rate by Day of Week**")
    render_bars(bt.get("day_rows", []), "Day")

    st.markdown("")
    st.markdown("**Win Rate by Game # in Session**")
    render_bars(bt.get("seq_rows", []), "Game #")

# ── Tab 3: Losing Recipe ──────────────────────────────────────────────────────
with tab3:
    lr = losing_recipe(games)
    if not lr:
        st.info("Not enough data.")
    else:
        st.markdown(f"You lose **{lr['overall_loss_rate']}%** of your {lr['total']} {time_class} games. Here's when it's much worse.")
        st.markdown("")

        if lr.get("sentences"):
            st.markdown("**Your Losing Patterns**")
            for s in lr["sentences"]:
                st.markdown(f'<div class="insight-box bad">{s}</div>', unsafe_allow_html=True)
            st.markdown("")

        if lr.get("endgame_note"):
            st.warning(lr["endgame_note"])

        if lr.get("patterns"):
            st.markdown("**Loss Rate Breakdown**")
            for p in lr["patterns"]:
                c1, c2, c3 = st.columns([3, 4, 2])
                with c1: st.caption(p["label"])
                with c2:
                    fill = int(min(p["loss_rate"], 100))
                    st.markdown(f"""
                    <div style="background:#1c1c22;border-radius:4px;height:18px;overflow:hidden;margin-top:4px">
                      <div style="background:#ff5e5e;width:{fill}%;height:100%;border-radius:4px"></div>
                    </div>""", unsafe_allow_html=True)
                with c3:
                    st.caption(f"**{p['loss_rate']}%** · {p['games']} games")

st.divider()
st.caption("Data fetched from Chess.com public API · No engine · No personal data stored")
