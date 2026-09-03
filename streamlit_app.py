"""
Chess Insights — Streamlit App
3 purely metadata-based analyses. No engine. No noise.

v2 — accuracy + usefulness pass
"""

import html as _html
import math
import re
import statistics
import time as time_mod
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

try:
    import plotly.graph_objects as go
    HAS_PLOTLY = True
except Exception:
    HAS_PLOTLY = False

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Chess Insights",
    page_icon="♟️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
    .block-container { padding-top: 2rem; max-width: 800px; }
    .insight-box {
        background: #1a1a2a; border-left: 4px solid #7c6eff;
        border-radius: 8px; padding: 0.85rem 1rem; margin-bottom: 0.6rem; font-size: 0.95rem;
    }
    .insight-box.bad  { border-left-color: #ff5e5e; background: #1f1212; }
    .insight-box.good { border-left-color: #4fffb0; background: #111f18; }
    .fix-box {
        background: linear-gradient(135deg, #141422, #1a1a2e);
        border: 1px solid #7c6eff; border-radius: 12px; padding: 1.5rem; margin-bottom: 1rem;
    }
    .action-box {
        background: #1c1c28; border-left: 4px solid #4fffb0;
        border-radius: 6px; padding: 0.75rem 1rem; margin-top: 0.75rem;
    }
</style>
""", unsafe_allow_html=True)

# ─── Constants ───────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Chess Insights App (github.com/omarrrexe)"}

SESSION_GAP_SECONDS = 45 * 60          # gap that separates two sessions
MIN_PLIES = 1                          # skip 0-move (auto-aborted) games

LOSS_RESULTS = {"checkmated", "resigned", "timeout", "abandoned", "lose", "ruleviolation"}
LOSS_TYPE = {
    "checkmated": "Checkmated",
    "resigned":   "Resigned",
    "timeout":    "Out of time",
    "abandoned":  "Abandoned / quit",
}

# Robust SAN matcher (piece moves, pawn moves incl. promotion, castling)
SAN_RE = re.compile(
    r'(?:[NBRQK][a-h1-8]{0,2}x?[a-h][1-8]'
    r'|[a-h](?:[a-h])?x?[a-h]?[1-8](?:=[NBRQ])?'
    r'|O-O(?:-O)?)[+#]?'
)

DEPTH_OPTIONS = {"Past 3 months": 3, "Past 6 months": 6, "Past 12 months": 12,
                 "Past 24 months": 24, "All time": 0}

TIMEZONES = [
    "UTC", "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
    "America/Mexico_City", "America/Bogota", "America/Lima",
    "America/Sao_Paulo", "America/Argentina/Buenos_Aires",
    "Europe/London", "Europe/Madrid", "Europe/Paris", "Europe/Berlin",
    "Europe/Warsaw", "Europe/Istanbul", "Europe/Moscow",
    "Africa/Cairo", "Africa/Johannesburg",
    "Asia/Dubai", "Asia/Karachi", "Asia/Kolkata", "Asia/Dhaka",
    "Asia/Jakarta", "Asia/Singapore", "Asia/Manila", "Asia/Shanghai",
    "Asia/Tokyo", "Australia/Sydney", "Pacific/Auckland",
]

# ─── Small helpers ───────────────────────────────────────────────────────────
def get_tz(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc

def fmt_h(h: int) -> str:
    h %= 24
    if h == 0:  return "12 AM"
    if h < 12:  return f"{h} AM"
    if h == 12: return "12 PM"
    return f"{h-12} PM"

def block_label(h: int) -> str:
    return f"{fmt_h(h)} – {fmt_h((h+3) % 24)}"

def pts(g: dict) -> float:
    """Score: win = 1, draw = ½, loss = 0."""
    return 1.0 if g["won"] else (0.5 if g["drew"] else 0.0)

def prate(gs: list) -> float:
    return sum(pts(g) for g in gs) / len(gs) if gs else 0.0

def wilson_lo(p: float, n: int, z: float = 1.96) -> float:
    """Wilson lower bound — ranks rates without trusting tiny samples."""
    if n == 0: return 0.0
    denom  = 1 + z*z/n
    centre = p + z*z/(2*n)
    adj    = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n)
    return max(0.0, (centre - adj) / denom)

def expected_score(p_rating: int, o_rating: int) -> float:
    """Elo expected score against a given opponent."""
    if not p_rating or not o_rating: return 0.5
    return 1 / (1 + 10 ** ((o_rating - p_rating) / 400))

def count_plies(pgn: str) -> int:
    """Accurately count half-moves from PGN movetext."""
    if not pgn: return 0
    body = pgn.split("\n\n", 1)
    mt = body[1] if len(body) == 2 else body[0]
    mt = re.sub(r"\{[^}]*\}", " ", mt)   # {comments}
    mt = re.sub(r";[^\n]*",    " ", mt)  # line comments
    mt = re.sub(r"\$\d+",      " ", mt)  # NAGs
    mt = re.sub(r"\b\d+\.*",   " ", mt)  # move numbers (incl. 12...)
    return len(SAN_RE.findall(mt))

def month_key(url: str):
    m = re.search(r"/(\d{4})/(\d{1,2})$", url)
    return (int(m.group(1)), int(m.group(2))) if m else None

def http_json(url: str, retries: int = 3):
    """GET with retry/backoff. Returns (data, status)."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json(), 200
            if r.status_code == 429:               # rate limited
                time_mod.sleep(1.5 * (attempt + 1))
                continue
            return None, r.status_code
        except (requests.RequestException, ValueError):
            time_mod.sleep(0.5 * (attempt + 1))
    return None, 0

def clean_username(raw: str) -> str:
    s = raw.strip()
    if "chess.com/" in s.lower():                 # accept pasted profile URLs
        s = s.rstrip("/").split("/")[-1]
    s = s.lstrip("@").strip().lower()
    return s if re.fullmatch(r"[a-z0-9_-]{3,25}", s) else ""

# ─── Chess.com fetchers ──────────────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def check_player(username: str) -> int:
    _, status = http_json(f"https://api.chess.com/pub/player/{username}")
    return status

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_all_games(username: str, months: int) -> list[dict]:
    """Fetch games for the last `months` months (0 = all). Cached 1 hour."""
    data, status = http_json(f"https://api.chess.com/pub/player/{username}/games/archives")
    if status != 200 or not data:
        return []
    urls = data.get("archives") or []

    if months > 0:  # only fetch archive months inside the window
        now = datetime.now(timezone.utc)
        idx = now.year * 12 + (now.month - 1) - months
        cy, cm = divmod(idx, 12)
        cutoff = (cy, cm + 1)
        urls = [u for u in urls if (month_key(u) or cutoff) >= cutoff]

    if not urls:
        return []

    all_games = []
    progress = st.progress(0.0, text="Fetching games from Chess.com…")
    for i, url in enumerate(urls):
        data, status = http_json(url)
        if status == 200 and data:
            all_games.extend(data.get("games") or [])
        progress.progress((i + 1) / len(urls),
                          text=f"Fetching… {i+1}/{len(urls)} months")
        time_mod.sleep(0.05)  # be polite to the API
    progress.empty()
    return all_games

# ─── Parse a single game ──────────────────────────────────────────────────────
def parse_game(game: dict, username: str, tz) -> dict | None:
    white, black = game.get("white", {}), game.get("black", {})
    wname = white.get("username", "").lower()
    bname = black.get("username", "").lower()

    if wname == username:
        color, me, opp = "white", white, black
    elif bname == username:
        color, me, opp = "black", black, white
    else:
        return None

    result_str = me.get("result", "") or ""
    opp_result = opp.get("result", "") or ""
    won   = result_str == "win"
    lost_ = (opp_result == "win") or (result_str in LOSS_RESULTS)
    drew  = not won and not lost_

    pgn = game.get("pgn", "") or ""
    plies = count_plies(pgn)
    if plies < MIN_PLIES:            # auto-aborted game → not real data
        return None

    end_ts = game.get("end_time") or 0
    dt = datetime.fromtimestamp(end_ts, tz) if end_ts else None

    eco_m = re.search(r'\[ECO "([A-E]\d{2})"\]', pgn)
    eco = eco_m.group(1) if eco_m else ""
    eco_url_m = re.search(r'\[ECOUrl "([^"]+)"\]', pgn)
    opening = (eco_url_m.group(1).split("/")[-1].replace("-", " ").title()
               if eco_url_m else (eco or "Unknown"))

    acc = game.get("accuracies", {}).get(color)
    try:
        acc = float(acc) if acc is not None else None
    except (TypeError, ValueError):
        acc = None

    return {
        "end_time": end_ts, "dt": dt, "color": color,
        "won": won, "drew": drew, "lost": lost_, "result": result_str,
        "loss_type": LOSS_TYPE.get(result_str, "Other") if lost_ else None,
        "player_rating": me.get("rating", 0) or 0,
        "opp_rating": opp.get("rating", 0) or 0,
        "time_class": game.get("time_class", ""),
        "rules": game.get("rules", "chess"),
        "rated": game.get("rated", True),
        "plies": plies, "moves": (plies + 1) // 2,   # fullmoves
        "opening": opening, "eco": eco, "accuracy": acc,
    }

def tag_sessions(games: list[dict]):
    """Mark session id, index within session, and prior consecutive losses."""
    prev_t, sid, sidx, streak = None, 0, 0, 0
    for g in games:
        t = g["end_time"]
        if prev_t is None or t - prev_t > SESSION_GAP_SECONDS:
            sid += 1; sidx = 1; streak = 0
        g["sidx"] = sidx
        g["prior_loss_streak"] = streak
        streak = streak + 1 if g["lost"] else 0
        sidx += 1; prev_t = t

def get_games(raw: list, username: str, time_class: str,
              tz_label: str, rated_only: bool) -> list[dict]:
    tz = get_tz(tz_label)
    games = []
    for g in raw:
        p = parse_game(g, username, tz)
        if not p: continue
        if p["time_class"] != time_class:  continue
        if p["rules"] != "chess":          continue   # exclude variants
        if rated_only and not p["rated"]: continue
        games.append(p)
    games.sort(key=lambda g: g["end_time"])
    tag_sessions(games)
    return games

# ─── Analysis 1: One Fix This Week ───────────────────────────────────────────
def one_fix(games: list[dict]) -> dict:
    if not games: return {}
    now = time_mod.time()
    recent = [g for g in games if g["end_time"] >= now - 7 * 86400]
    if len(recent) >= 5:
        window = "last 7 days"
    else:
        recent, window = games[-20:], "last 20 games"
    if not recent: return {}

    total  = len(recent)
    wins   = sum(1 for g in recent if g["won"])
    losses = sum(1 for g in recent if g["lost"])
    draws  = total - wins - losses
    score  = (wins + 0.5 * draws) / total if total else 0

    candidates = []

    # 1) Fatigue: games 1–3 vs 4+ of a session
    early = [g for g in recent if g["sidx"] <= 3]
    late  = [g for g in recent if g["sidx"] >= 4]
    if len(early) >= 8 and len(late) >= 5:
        er, lr = prate(early), prate(late)
        if er - lr >= 0.12:
            candidates.append({
                "priority": (er - lr) * 100,
                "fix": "Cap sessions at 3 games",
                "reason": f"You score {er*100:.0f}% in games 1–3 of a session, but only "
                          f"{lr*100:.0f}% from game 4 onward ({len(late)} games). Fatigue is costing real points.",
                "action": "Hard limit: 3 games per sitting. Stand up, walk away, come back tomorrow fresh.",
                "icon": "🛑",
            })

    # 2) Tilt: playing on after 2 straight losses
    tilted = [g for g in recent if g["prior_loss_streak"] >= 2]
    fresh  = [g for g in recent if g["prior_loss_streak"] == 0]
    if len(tilted) >= 5 and len(fresh) >= 8:
        tr, fr = prate(tilted), prate(fresh)
        if fr - tr >= 0.15:
            candidates.append({
                "priority": (fr - tr) * 90,
                "fix": "Stop after 2 straight losses",
                "reason": f"Right after two losses in a row you score only {tr*100:.0f}% "
                          f"vs {fr*100:.0f}% otherwise. Classic tilt.",
                "action": "Rule: 2 losses in a row → session over. Review one loss instead of re-queuing.",
                "icon": "🧊",
            })

    # 3) Color gap
    wg = [g for g in recent if g["color"] == "white"]
    bg = [g for g in recent if g["color"] == "black"]
    if len(wg) >= 6 and len(bg) >= 6:
        wr, br = prate(wg), prate(bg)
        gap = abs(wr - br)
        if gap >= 0.18:
            weak = "Black" if br < wr else "White"
            candidates.append({
                "priority": gap * 80,
                "fix": f"Fix your {weak} openings",
                "reason": f"You score {max(wr,br)*100:.0f}% as {'White' if weak == 'Black' else 'Black'} "
                          f"but only {min(wr,br)*100:.0f}% as {weak} — a {gap*100:.0f}-point gap.",
                "action": f"Pick ONE {weak} opening system and play it exclusively for 30 games. Depth beats variety.",
                "icon": "♟️",
            })

    # 4) Long-game conversion
    long_g = [g for g in recent if g["moves"] >= 35]
    if len(long_g) >= 8:
        lp = prate(long_g)
        short_g = [g for g in recent if g["moves"] <= 12]
        if lp < 0.45 and (not short_g or prate(short_g) - lp >= 0.15):
            candidates.append({
                "priority": (0.5 - lp) * 100,
                "fix": "Practice basic endgames",
                "reason": f"In games reaching 35+ moves you score only {lp*100:.0f}% "
                          f"({len(long_g)} games) — you get there but can't convert.",
                "action": "10 minutes of king+pawn endgames today (Lucena, Philidor, square rule). Highest-ROI study at your level.",
                "icon": "📚",
            })

    # 5) Loss type — clock vs checkmate
    ltypes = [g["loss_type"] for g in recent if g["lost"]]
    if len(ltypes) >= 5:
        t_share = ltypes.count("Out of time") / len(ltypes)
        m_share = ltypes.count("Checkmated")  / len(ltypes)
        if t_share >= 0.30:
            candidates.append({
                "priority": t_share * 60,
                "fix": "Fix your clock management",
                "reason": f"{ltypes.count('Out of time')} of your last {len(ltypes)} losses "
                          f"({t_share*100:.0f}%) were on time. The position doesn't matter if the flag falls.",
                "action": "Play one time-control slower (blitz→rapid) for two weeks. Use the extra time to check for hanging pieces.",
                "icon": "⏱️",
            })
        elif m_share >= 0.45:
            candidates.append({
                "priority": m_share * 55,
                "fix": "Blunder-check before every move",
                "reason": f"{ltypes.count('Checkmated')} of your last {len(ltypes)} losses "
                          f"({m_share*100:.0f}%) ended in checkmate — tactical oversights under pressure.",
                "action": "Before each move, one question: 'What did my opponent's last move threaten?' Kills most club-level mates.",
                "icon": "⏸️",
            })

    # 6) Accuracy gap (when Chess.com provides it)
    acc_g = [g for g in recent if g["accuracy"] is not None]
    if len(acc_g) >= 6:
        wacc = [g["accuracy"] for g in acc_g if g["won"]]
        lacc = [g["accuracy"] for g in acc_g if g["lost"]]
        if wacc and lacc:
            aw, al = sum(wacc)/len(wacc), sum(lacc)/len(lacc)
            if aw - al > 8 and al < 75:
                candidates.append({
                    "priority": (aw - al) * 0.8,
                    "fix": "Slow down — 5 extra seconds per move",
                    "reason": f"Winning games: {aw:.0f}% accuracy. Losing games: {al:.0f}%. The gap is rushing, not knowledge.",
                    "action": "Before moving: 'Is anything of mine hanging? Is anything of theirs?' One habit, real rating points.",
                    "icon": "🐢",
                })

    if not candidates:
        candidates.append({
            "priority": 10,
            "fix": "Keep it up — no big leak detected",
            "reason": f"{wins}W / {draws}D / {losses}L in your {window} ({score*100:.0f}% score). Nothing broken enough to prioritize.",
            "action": "Review one loss per session without an engine — find the move where it went wrong, describe it in one sentence.",
            "icon": "✅",
        })

    best = max(candidates, key=lambda c: c["priority"])
    others = [c["fix"] for c in sorted(candidates, key=lambda c: -c["priority"])
              if c is not best and c["fix"] != best["fix"]]
    return {
        "window": window, "total": total, "wins": wins,
        "losses": losses, "draws": draws, "score": round(score * 100, 1),
        **best, "others": others,
    }

# ─── Analysis 2: Best Time to Play ───────────────────────────────────────────
def best_time(games: list[dict]) -> dict:
    blocks = defaultdict(lambda: [0, 0.0])
    days   = defaultdict(lambda: [0, 0.0])
    seqs   = defaultdict(lambda: [0, 0.0])

    for g in games:
        if not g["dt"]: continue
        p = pts(g)
        blocks[(g["dt"].hour // 3) * 3][0] += 1; blocks[(g["dt"].hour // 3) * 3][1] += p
        days[g["dt"].weekday()][0] += 1;         days[g["dt"].weekday()][1] += p
        s = min(g["sidx"], 5)
        seqs[s][0] += 1;                          seqs[s][1] += p

    DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    block_rows = [(block_label(k), n, round(p / n * 100, 1))
                  for k, (n, p) in sorted(blocks.items()) if n >= 3]
    day_rows   = [(DAY_NAMES[k], n, round(p / n * 100, 1))
                  for k, (n, p) in sorted(days.items())]
    seq_rows   = [({1: "#1", 2: "#2", 3: "#3", 4: "#4"}.get(k, "5+"), n, round(p / n * 100, 1))
                  for k, (n, p) in sorted(seqs.items())]

    insights = []

    elig = {k: v for k, v in blocks.items() if v[0] >= 10}
    if elig:
        best_b = max(elig.items(), key=lambda kv: wilson_lo(kv[1][1]/kv[1][0], kv[1][0]))
        wr = best_b[1][1] / best_b[1][0] * 100
        insights.append(("good", f"**{block_label(best_b[0])}** is your sharpest window — "
                                 f"**{wr:.0f}%** score over {best_b[1][0]} games."))
        worst_b = min(elig.items(), key=lambda kv: wilson_lo(kv[1][1]/kv[1][0], kv[1][0]))
        wrr = worst_b[1][1] / worst_b[1][0] * 100
        if worst_b[0] != best_b[0] and wrr <= wr - 12:
            insights.append(("bad", f"Avoid **{block_label(worst_b[0])}** — only **{wrr:.0f}%** "
                                     f"score over {worst_b[1][0]} games."))

    seq_e = [g for g in games if g["sidx"] <= 3]
    seq_l = [g for g in games if g["sidx"] >= 4]
    if len(seq_e) >= 15 and len(seq_l) >= 10:
        er, lr = prate(seq_e), prate(seq_l)
        if er - lr >= 0.10:
            insights.append(("bad", f"Score drops **{(er-lr)*100:.0f} pts** from game 4 onward "
                                     f"({er*100:.0f}% → {lr*100:.0f}%). Cap sessions at 3."))

    tilted = [g for g in games if g["prior_loss_streak"] >= 2]
    fresh  = [g for g in games if g["prior_loss_streak"] == 0]
    if len(tilted) >= 10 and len(fresh) >= 15:
        tr, fr = prate(tilted), prate(fresh)
        if fr - tr >= 0.10:
            insights.append(("bad", f"After 2 straight losses your score is **{tr*100:.0f}%** vs "
                                     f"**{fr*100:.0f}%** normally. Tilt is real — take the break."))

    elig_days = {k: v for k, v in days.items() if v[0] >= 12}
    if elig_days:
        bd = max(elig_days.items(), key=lambda kv: kv[1][1] / kv[1][0])
        insights.append(("good", f"**{DAY_NAMES[bd[0]]}** is your best day — "
                                 f"**{bd[1][1]/bd[1][0]*100:.0f}%** score over {bd[1][0]} games."))

    return {"block_rows": block_rows, "day_rows": day_rows,
            "seq_rows": seq_rows, "insights": insights}

# ─── Analysis 3: Losing Recipe ────────────────────────────────────────────────
def losing_recipe(games: list[dict]) -> dict:
    if not games: return {}
    total = len(games)
    losses = [g for g in games if g["lost"]]
    lr_overall = len(losses) / total

    patterns = []
    def check(sub, label, min_n=15):
        n = len(sub)
        if n < min_n: return
        l = sum(1 for g in sub if g["lost"]) / n
        lift = l / lr_overall if lr_overall else 1
        if lift >= 1.15 and l >= lr_overall + 0.08:
            patterns.append({"label": label, "games": n, "loss_rate": round(l*100, 1),
                             "overall": round(lr_overall*100, 1), "lift": round(lift, 2)})

    check([g for g in games if g["color"] == "black"], "Playing as Black")
    check([g for g in games if g["color"] == "white"], "Playing as White")
    check([g for g in games if g["moves"] >= 35], "Long games (35+ moves)")
    check([g for g in games if g["moves"] <= 12], "Short games (≤12 moves)")
    check([g for g in games if g["sidx"] >= 4], "4th+ game of a session", 12)
    check([g for g in games if g["prior_loss_streak"] >= 2], "Right after 2+ straight losses", 12)
    check([g for g in games if g["dt"] and (g["dt"].hour >= 22 or g["dt"].hour < 5)],
          "Late night (10 PM – 5 AM)", 10)

    oc = defaultdict(lambda: [0, 0])
    for g in games:
        key = f"{g['color']}|{g['opening']}"
        oc[key][0] += 1
        oc[key][1] += 1 if g["lost"] else 0
    for key, (n, l) in oc.items():
        if n < 10: continue
        rate = l / n
        lift = rate / lr_overall if lr_overall else 1
        if lift >= 1.25 and rate >= lr_overall + 0.08:
            c, o = key.split("|", 1)
            patterns.append({"label": f"{o} as {c.title()}", "games": n,
                             "loss_rate": round(rate*100, 1),
                             "overall": round(lr_overall*100, 1), "lift": round(lift, 2)})

    patterns.sort(key=lambda p: p["lift"], reverse=True)
    sentences = [f"When **{p['label'].lower()}**, you lose **{p['loss_rate']}%** "
                 f"(vs {p['overall']}% overall, {p['games']} games)."
                 for p in patterns[:5]]

    # How losses actually happen
    lt = defaultdict(int)
    for g in losses:
        lt[g["loss_type"] or "Other"] += 1
    loss_type_rows = [(k, v, round(v / len(losses) * 100, 1))
                      for k, v in sorted(lt.items(), key=lambda kv: -kv[1])] if losses else []

    # Actual vs Elo-expected performance by opponent strength
    BUCKETS = [(-10**9, -200, "200+ pts weaker"), (-200, -50, "50–200 weaker"),
               (-50, 50, "Even (±50)"), (50, 200, "50–200 stronger"),
               (200, 10**9, "200+ pts stronger")]
    elo_rows, elo_note = [], None
    rated_pairs = [g for g in games if g["player_rating"] and g["opp_rating"]]
    if rated_pairs:
        for lo, hi, label in BUCKETS:
            sub = [g for g in rated_pairs if lo <= g["opp_rating"] - g["player_rating"] < hi]
            if not sub: continue
            act = prate(sub)
            exp = sum(expected_score(g["player_rating"], g["opp_rating"]) for g in sub) / len(sub)
            elo_rows.append({"Opponent strength": label, "Games": len(sub),
                             "Actual": f"{act*100:.0f}%", "Expected": f"{exp*100:.0f}%",
                             "Δ": f"{(act-exp)*100:+.0f} pts"})
        act_all = prate(rated_pairs)
        exp_all = sum(expected_score(g["player_rating"], g["opp_rating"])
                      for g in rated_pairs) / len(rated_pairs)
        d = act_all - exp_all
        if abs(d) >= 0.03:
            word = "overperforming" if d > 0 else "underperforming"
            elo_note = (f"Overall you score {act_all*100:.0f}% vs {exp_all*100:.0f}% expected "
                        f"— you're **{word}** by {abs(d)*100:.0f} pts per game on average.")

    # Endgame conversion
    long_all = [g for g in games if g["moves"] >= 35]
    endgame_note = None
    if len(long_all) >= 15:
        lp = prate(long_all)
        if lp < 0.45:
            endgame_note = (f"You score only **{lp*100:.0f}%** in 35+ move games "
                            f"({len(long_all)} games). Endgame conversion is your biggest leak.")

    return {"patterns": patterns[:8], "sentences": sentences,
            "loss_type_rows": loss_type_rows, "elo_rows": elo_rows, "elo_note": elo_note,
            "endgame_note": endgame_note,
            "total": total, "total_losses": len(losses),
            "overall_loss_rate": round(lr_overall * 100, 1)}

# ─── Opening Recipe module ────────────────────────────────────────────────────

# ── PGN → move tokens ────────────────────────────────────────────────────────
def movetext_tokens(pgn: str) -> list:
    if not pgn: return []
    parts = pgn.split("\n\n", 1)
    mt = parts[1] if len(parts) == 2 else parts[0]
    mt = re.sub(r"(1-0|0-1|1/2-1/2|\*)\s*$", " ", mt.strip())
    mt = re.sub(r"\{[^}]*\}", " ", mt)
    mt = re.sub(r";[^\n]*", " ", mt)
    mt = re.sub(r"\$\d+", " ", mt)
    mt = re.sub(r"\b\d+\.*", " ", mt)
    out = []
    for t in mt.split():
        t = t.rstrip("+#!?")
        if SAN_RE.fullmatch(t):
            out.append(t)
    return out

# ── Mini opening book (mainlines, in plies) ──────────────────────────────────
BOOK_LINES = [
    "e4 e5 Nf3 Nc6 Bb5 a6", "e4 e5 Nf3 Nc6 Bb5 Nf6", "e4 e5 Nf3 Nc6 Bc4 Bc5",
    "e4 e5 Nf3 Nc6 Bc4 Nf6", "e4 e5 Nf3 Nc6 d4 exd4 Nxd4",
    "e4 e5 Nf3 Nc6 Nc3", "e4 e5 Nf3 Nf6 Nxe5", "e4 e5 Nf3 d6 d4",
    "e4 e5 Nc3 Nf6", "e4 e5 f4 exf4", "e4 e5 f4 d5", "e4 e5 Bc4 Nf6",
    "e4 e5 Nf3 Nc6 d3", "e4 e5 d4 exd4 Qxd4", "e4 e5 c4", "e4 e5 c3",
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 a6",
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 e6",
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 g6",
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6 Nc3 Nc6",
    "e4 c5 Nf3 Nc6 d4 cxd4 Nxd4", "e4 c5 Nf3 e6 d4 cxd4 Nxd4",
    "e4 c5 Nf3 e6 d4 cxd4 Nxd4 Nc6", "e4 c5 Nc3", "e4 c5 c3", "e4 c5 g3",
    "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Bf5", "e4 c6 d4 d5 Nc3 dxe4 Nxe4 Nf6",
    "e4 c6 d4 d5 e5 c5", "e4 c6 d4 d5 exd5 cxd5 c4", "e4 c6 Nc3 d5 Nf3",
    "e4 e6 d4 d5 Nc3 Bb4", "e4 e6 d4 d5 Nc3 Nf6", "e4 e6 d4 d5 Nd2",
    "e4 e6 d4 d5 e5", "e4 e6 d4 d5 exd5 exd5", "e4 e6 d4 Nf6",
    "e4 d5 exd5 Qxd5 Nc3", "e4 d5 exd5 Nf6", "e4 d5 Nf3", "e4 d5 e5",
    "e4 Nf6", "e4 d6 d4 Nf6 Nc3 g6", "e4 g6 d4 Nf6 Nc3 d6",
    "e4 g6 d4 Bg7", "e4 b6 d4 Bb7", "e4 a6", "e4 Nc6", "e4 b5",
    "d4 d5 c4 e6 Nc3 Nf6 Bg5 Be7", "d4 d5 c4 e6 Nc3 Nf6 Nf3",
    "d4 d5 c4 e6 cxd5 exd5", "d4 d5 c4 e6 Bg5",
    "d4 d5 c4 c6 Nf3 Nf6 Nc3", "d4 d5 c4 c6 Nc3 Nf6",
    "d4 d5 c4 dxc4", "d4 d5 c4 e5", "d4 d5 Nf3 Nf6 Bf4",
    "d4 d5 Nf3 Nf6 c4", "d4 d5 Bf4", "d4 d5 e4",
    "d4 Nf6 c4 e6 Nc3 Bb4", "d4 Nf6 c4 e6 Nf3 b6", "d4 Nf6 c4 e6 g3",
    "d4 Nf6 c4 g6 Nc3 Bg7 e4 d6", "d4 Nf6 c4 g6 Nc3 d5",
    "d4 Nf6 c4 g6 Nf3 Bg7", "d4 Nf6 Bf4", "d4 Nf6 c4 e6 Nc3 Bb4 e3",
    "d4 f5 g3 Nf6 Bg2", "d4 f5", "d4 e6", "d4 c5", "d4 b6", "d4 d6",
    "c4 e5", "c4 Nf6", "c4 c5", "c4 e6", "c4 c6",
    "Nf3 d5", "Nf3 Nf6", "Nf3 c5",
    "g3 d5", "g3 Nf6", "b3 e5", "b3 d5", "f4 d5", "f4 Nf6",
    "e4", "d4",
]
BOOK_TOKENS = [l.split() for l in BOOK_LINES]

def book_depth(tokens: list) -> int:
    """How many plies of a known mainline the game follows."""
    best, n = 0, len(tokens)
    for line in BOOK_TOKENS:
        L = min(len(line), n)
        if L > best and tokens[:L] == line[:L]:
            best = L
    return best

# ── Opening family detection ──────────────────────────────────────────────────
FAMILY_RULES = [
    ("caro kann", "Caro-Kann"), ("sicilian", "Sicilian Defense"),
    ("french", "French Defense"), ("scandinavian", "Scandinavian"),
    ("alekhine", "Alekhine's Defense"), ("pirc", "Pirc Defense"),
    ("modern", "Modern Defense"), ("kings gambit", "King's Gambit"),
    ("ruy lopez", "Ruy Lopez"), ("spanish", "Ruy Lopez"),
    ("italian", "Italian Game"), ("giuoco", "Italian Game"),
    ("evans gambit", "Italian Game"), ("scotch", "Scotch Game"),
    ("vienna", "Vienna Game"), ("petroff", "Petrov Defense"),
    ("petrov", "Petrov Defense"), ("russian game", "Petrov Defense"),
    ("philidor", "Philidor Defense"), ("london", "London System"),
    ("catalan", "Catalan Opening"), ("queens gambit", "Queen's Gambit"),
    ("semi slav", "Slav / Semi-Slav"), ("slav", "Slav / Semi-Slav"),
    ("nimzo indian", "Nimzo-Indian"), ("queens indian", "Queen's Indian"),
    ("kings indian", "King's Indian"), ("gruenfeld", "Grunfeld Defense"),
    ("grunfeld", "Grunfeld Defense"), ("benoni", "Benoni Defense"),
    ("budapest", "Budapest Gambit"), ("dutch", "Dutch Defense"),
    ("english", "English Opening"), ("reti", "Reti Opening"),
    ("birds", "Bird's Opening"), ("four knights", "Four Knights"),
    ("two knights", "Two Knights Defense"), ("danish", "Danish Gambit"),
    ("center game", "Center Game"), ("ponziani", "Ponziani"),
    ("bishops opening", "Bishop's Opening"), ("bowdler", "Bishop's Opening"),
    ("kings pawn", "1.e4 Games"), ("king pawn", "1.e4 Games"),
    ("queens pawn", "1.d4 Games"),
]
REPLY_FAMILY = {
    ("e4","e5"): "Open Games (1.e4 e5)", ("e4","c5"): "Sicilian Defense",
    ("e4","c6"): "Caro-Kann", ("e4","e6"): "French Defense",
    ("e4","d5"): "Scandinavian", ("e4","d6"): "Pirc Defense",
    ("e4","g6"): "Modern Defense", ("e4","Nf6"): "Alekhine's Defense",
    ("e4","b6"): "Owen's Defense", ("e4","a6"): "St. George Defense",
    ("e4","Nc6"): "Nimzowitsch Defense", ("e4","b5"): "1.e4 b5",
    ("d4","d5"): "d4 d5 Closed", ("d4","Nf6"): "Indian Defenses",
    ("d4","f5"): "Dutch Defense", ("d4","e6"): "1.d4 e6 Systems",
    ("d4","c5"): "1.d4 c5", ("d4","d6"): "1.d4 d6 Systems",
    ("d4","g6"): "Modern vs d4", ("d4","b6"): "1.d4 b6",
    ("c4",None): "English Opening", ("Nf3",None): "Reti Opening",
    ("g3",None): "King's Fianchetto", ("b3",None): "Nimzo-Larsen Attack",
    ("f4",None): "Bird's Opening", ("b4",None): "Polish Opening",
    ("e4",None): "1.e4 (other)", ("d4",None): "1.d4 (other)",
}

def classify_opening(name: str, tokens: list) -> tuple:
    """Return (family, variation-with-family-name-stripped)."""
    n = name.lower()
    fam = "Other"
    for key, f in FAMILY_RULES:
        if key in n:
            fam = f
            break
    else:
        if len(tokens) >= 2 and (tokens[0], tokens[1]) in REPLY_FAMILY:
            fam = REPLY_FAMILY[(tokens[0], tokens[1])]
        elif tokens and (tokens[0], None) in REPLY_FAMILY:
            fam = REPLY_FAMILY[(tokens[0], None)]
    var = name
    if name.lower().startswith(fam.lower()):
        rest = name[len(fam):].strip()
        if rest: var = rest
    return fam, var

# ── Lab game parser ───────────────────────────────────────────────────────────
def prepare_lab_games(raw_games, username, time_class, tz_label, rated_only):
    tz, u, out = get_tz(tz_label), username.lower(), []
    for g in raw_games:
        white, black = g.get("white", {}), g.get("black", {})
        if white.get("username","").lower() == u:   color, me, opp = "white", white, black
        elif black.get("username","").lower() == u: color, me, opp = "black", black, white
        else: continue
        if g.get("time_class") != time_class: continue
        if g.get("rules", "chess") != "chess": continue
        if rated_only and not g.get("rated", True): continue

        r, o = me.get("result","") or "", opp.get("result","") or ""
        won, lost = r == "win", (o == "win") or (r in LOSS_RESULTS)
        drew = not won and not lost

        pgn = g.get("pgn","") or ""
        tokens = movetext_tokens(pgn)
        if not tokens: continue

        eco_m = re.search(r'\[ECOUrl "([^"]+)"\]', pgn)
        vname = eco_m.group(1).split("/")[-1].replace("-"," ").title() if eco_m else "Unknown"
        family, variation = classify_opening(vname, tokens)

        ts = g.get("end_time") or 0
        dt = datetime.fromtimestamp(ts, tz) if ts else None
        out.append({
            "end_time": ts, "dt": dt, "color": color,
            "won": won, "drew": drew, "lost": lost,
            "loss_type": LOSS_TYPE.get(r, "Other") if lost else None,
            "player_rating": me.get("rating",0) or 0,
            "opp_rating": opp.get("rating",0) or 0,
            "plies": len(tokens), "moves": (len(tokens)+1)//2,
            "first": tokens[0], "reply": tokens[1] if len(tokens)>1 else "",
            "family": family, "variation": variation, "variation_raw": vname,
            "book": book_depth(tokens[:12]),
            "month": dt.strftime("%Y-%m") if dt else "",
        })
    out.sort(key=lambda g: g["end_time"])
    return out

# ── Stats helpers ─────────────────────────────────────────────────────────────
def phase_shares(losses):
    keys = ("Opening (≤12)", "Middlegame (13–30)", "Endgame (31+)")
    if not losses:
        return {k: 0 for k in keys}
    t = len(losses)
    return {
        keys[0]: round(sum(1 for g in losses if g["moves"] <= 12) / t * 100),
        keys[1]: round(sum(1 for g in losses if 12 < g["moves"] <= 30) / t * 100),
        keys[2]: round(sum(1 for g in losses if g["moves"] > 30) / t * 100),
    }

def grade_of(score, overall):
    d = score - overall
    return "A" if d >= .12 else "B" if d >= .05 else "C" if d >= -.05 else "D" if d >= -.12 else "F"

GRADE_COLORS = {"A":"#4fffb0","B":"#7ee787","C":"#7c6eff","D":"#ffb454","F":"#ff5e5e"}

def family_cards(lab, min_games=6):
    overall = prate(lab)
    groups = defaultdict(list)
    for g in lab:
        groups[(g["color"], g["family"])].append(g)
    cards = []
    for (color, fam), gs in groups.items():
        n = len(gs)
        if n < min_games: continue
        sc = prate(gs)
        losses = [g for g in gs if g["lost"]]
        half = n // 2
        trend = round((prate(gs[half:]) - prate(gs[:half])) * 100) if n >= 12 else None
        cards.append({
            "family": fam, "color": color, "games": n,
            "score": round(sc*100, 1), "delta": round((sc-overall)*100, 1),
            "grade": grade_of(sc, overall),
            "median_moves": round(statistics.median(g["moves"] for g in gs)),
            "phases": phase_shares(losses), "losses": len(losses),
            "avg_book": statistics.mean(g["book"] for g in gs),
            "trend": trend,
        })
    cards.sort(key=lambda c: c["games"], reverse=True)
    return cards, overall

def card_verdict(c):
    ph = c["phases"]
    if c["losses"] == 0:
        return "No losses here yet — keep it in the rotation."
    if ph["Opening (≤12)"] >= 45:
        return "Most losses come by move 12 — you're not surviving the theory. Learn the first 10 moves cold."
    if ph["Endgame (31+)"] >= 45:
        return "Losses pile up late — good positions, bad conversion. Study this structure's typical endgames."
    if c["avg_book"] < 8:
        return f"You leave known theory around move {c['avg_book']/2:.0f} — too early. 20 min of prep here pays off."
    if c["delta"] >= 5:
        return "This is a weapon — make it your main line."
    return "Decent results — sharpen the typical middlegame plans."

# ── Recipe builder ────────────────────────────────────────────────────────────
STYLE_WHY = {
    "trap_prone":    "Most of your losses happen EARLY (by move 12) — you need trap-proof, low-theory lines, not more sharpness.",
    "leaky_endgame": "Most of your losses happen LATE (move 31+) — you need clean structures you can convert.",
    "attacker":     "Your games are short and your wins come fast — lean into sharp, fighting lines.",
    "balanced":     "Your losses are spread evenly across the game — solid all-round lines will serve you best.",
}
STYLE_RECS = {
    "trap_prone": {
        "white": ("London System", "1.d4 2.Nf3 3.Bf4 — the same setup vs almost everything", "one setup, almost no forced traps to memorize"),
        "e4":    ("Caro-Kann", "1.e4 c6 2.d4 d5 3.Nc3 dxe4 4.Nxe4 Bf5", "rock-solid, trap-proof, clear plans"),
        "d4":    ("Slav", "1.d4 d5 2.c4 c6 3.Nf3 Nf6 4.Nc3", "solid structure, very hard to blow off the board"),
    },
    "leaky_endgame": {
        "white": ("London System", "1.d4 2.Nf3 3.Bf4 — trade pieces, grind endgames", "reaches simple favorable endgames constantly"),
        "e4":    ("Caro-Kann", "1.e4 c6 2.d4 d5 3.Nc3 dxe4 4.Nxe4 Bf5", "great endgame structures and pawn skeletons"),
        "d4":    ("Queen's Gambit Declined", "1.d4 d5 2.c4 e6 3.Nc3 Nf6 4.Nf3 Be7", "simple, clear plans all the way to the ending"),
    },
    "attacker": {
        "white": ("Italian Game, c3–d4 plan", "1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.c3", "clean attacking setup, low theory, high initiative"),
        "e4":    ("Najdorf Sicilian", "1.e4 c5 2.Nf3 d6 3.d4 cxd4 4.Nxd4 Nf6 5.Nc3 a6", "maximum fight — you decide where the game burns"),
        "d4":    ("King's Indian Defense", "1.d4 Nf6 2.c4 g6 3.Nc3 Bg7 4.e4 d6", "counterattack the king — fits your fast-win profile"),
    },
    "balanced": {
        "white": ("Italian Game", "1.e4 e5 2.Nf3 Nc6 3.Bc4", "main lines, best all-round results at club level"),
        "e4":    ("Open Sicilian", "1.e4 c5 2.Nf3 Nc6 3.d4 cxd4 4.Nxd4", "the serious answer to 1.e4 — scores bump lives here"),
        "d4":    ("Nimzo-Indian", "1.d4 Nf6 2.c4 e6 3.Nc3 Bb4", "fights for the initiative without huge theory"),
    },
}

def infer_style(phases, avg_moves):
    if phases["Opening (≤12)"] >= 45: return "trap_prone"
    if phases["Endgame (31+)"] >= 40: return "leaky_endgame"
    if avg_moves <= 22:                return "attacker"
    return "balanced"

def pick_core(stats, min_n=8):
    ok = [s for s in stats if s["games"] >= min_n] or stats
    if not ok: return None
    return max(ok, key=lambda s: wilson_lo(s["score"]/100, s["games"]))

def build_recipe(lab, cards, overall):
    white_groups, e4_groups, d4_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    for g in lab:
        if g["color"] == "white":
            white_groups[f"1.{g['first']}"].append(g)
        elif g["first"] == "e4":
            e4_groups[g["family"]].append(g)
        elif g["first"] == "d4":
            d4_groups[g["family"]].append(g)

    def to_stats(d):
        return [{"label": k, "games": len(v), "score": round(prate(v)*100,1)} for k, v in d.items()]

    white_stats, e4_stats, d4_stats = to_stats(white_groups), to_stats(e4_groups), to_stats(d4_groups)
    junk = ("Other", "Unknown")
    core_white = pick_core([s for s in white_stats if s["label"] not in junk])
    core_e4 = pick_core([s for s in e4_stats if s["label"] not in junk])
    core_d4 = pick_core([s for s in d4_stats if s["label"] not in junk])

    losses = [g for g in lab if g["lost"]]
    phases = phase_shares(losses)
    style = infer_style(phases, statistics.mean(g["moves"] for g in lab))

    keep = [c for c in cards if c["delta"] >= 3 and c["games"] >= 8]
    cut  = [c for c in cards if c["delta"] <= -8 and c["games"] >= 6]
    fix = []
    traps = defaultdict(int)
    for g in losses:
        if g["moves"] <= 12 and g["loss_type"] == "Checkmated":
            traps[(g["color"], g["family"])] += 1
    for (color, fam), cnt in sorted(traps.items(), key=lambda kv: -kv[1])[:3]:
        if cnt >= 2:
            fix.append(f"<b>{fam}</b> as {color}: mated inside 12 moves {cnt}× — that's a known trap. Learn the refutation before playing it again.")
    for c in cards:
        if c["phases"]["Opening (≤12)"] >= 45 and c["losses"] >= 4:
            fix.append(f"<b>{c['family']}</b> as {c['color']}: {c['phases']['Opening (≤12)']}% of losses come by move 12 — you need the first 10 moves of this line, not more ideas.")

    return {
        "style": style, "style_why": STYLE_WHY[style], "recs": STYLE_RECS[style],
        "overall": round(overall*100, 1), "games": len(lab),
        "core_white": core_white, "core_e4": core_e4, "core_d4": core_d4,
        "keep": keep, "cut": cut, "fix": fix[:4],
    }

# ── Animated HTML page (pure CSS/JS, no dependencies) ────────────────────────
_LAB_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0e1117;color:#e8e8f0;font-family:'Source Sans Pro',-apple-system,sans-serif;padding:14px}
.hero{display:flex;align-items:center;gap:18px;background:linear-gradient(135deg,#141422,#1a1a2e);
border:1px solid #2a2a44;border-radius:14px;padding:16px 20px;margin-bottom:12px}
.hero h1{font-size:1.35rem;font-weight:700}
.hero .sub{color:#8b8ba3;font-size:.82rem;margin-top:2px}
.stylechip{display:inline-block;margin-top:8px;background:#7c6eff22;color:#b8b0ff;border:1px solid #7c6eff55;
border-radius:20px;padding:3px 12px;font-size:.75rem}
.chips{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:14px}
.chip{background:#16161f;border:1px solid #26263a;border-radius:10px;padding:10px 14px;animation:cardin .5s ease both}
.chip-k{font-size:.68rem;text-transform:uppercase;letter-spacing:.06em;color:#8b8ba3}
.chip-v{font-size:1.05rem;font-weight:700;margin:2px 0}
.chip-s{font-size:.78rem;color:#4fffb0}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:11px;margin-bottom:14px}
.card{background:#14141c;border:1px solid #26263a;border-radius:12px;padding:13px 15px;
animation:cardin .5s ease both;transition:transform .2s,border-color .2s}
.card:hover{transform:translateY(-3px);border-color:#7c6eff66}
.card-head{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px}
.fam{font-weight:700;font-size:.95rem}
.sub{display:block;color:#8b8ba3;font-size:.74rem;margin-top:2px}
.grade{font-weight:800;font-size:.9rem;border-radius:8px;padding:2px 10px}
.card-body{display:flex;align-items:center;gap:14px}
.gauge{position:relative;width:86px;height:86px;flex-shrink:0}
.gauge svg{width:86px;height:86px;transform:rotate(-90deg)}
.ring-bg{fill:none;stroke:#26263a;stroke-width:8}
.ring-fg{fill:none;stroke-width:8;stroke-linecap:round}
.ring-val{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
font-weight:800;font-size:1.05rem}
.ph{flex:1}
.ph-bar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:#26263a;margin-bottom:4px}
.ph-lbl{font-size:.7rem;color:#8b8ba3;margin-bottom:8px}
.verdict{font-size:.78rem;color:#b9b9cc;line-height:1.45;border-top:1px solid #26263a55;padding-top:7px;margin-top:9px}
.trend{font-size:.72rem;color:#8b8ba3;margin-top:7px}
.rgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:11px;margin-bottom:12px}
.rbox{border-radius:12px;padding:12px 14px;animation:cardin .5s ease both}
.rbox h3{font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;margin-bottom:7px}
.rbox li{font-size:.8rem;line-height:1.45;margin:5px 0 5px 14px}
.rbox .mv{color:#4fffb0;font-size:.76rem}
.rkeep{background:#111f18;border:1px solid #4fffb044}
.rfix{background:#1f1a12;border:1px solid #ffb45444}
.rcut{background:#1f1212;border:1px solid #ff5e5e44}
.radd{background:#15142a;border:1px solid #7c6eff66}
.steps{background:#16161f;border:1px solid #26263a;border-radius:12px;padding:13px 16px;margin-bottom:12px}
.steps li{font-size:.84rem;line-height:1.5;margin:6px 0 6px 18px}
.book{display:flex;align-items:center;gap:16px;background:#111f18;border:1px solid #4fffb033;
border-radius:12px;padding:12px 16px}
.bk{flex:1}
.bk-t{font-size:.78rem;color:#b9b9cc;margin-bottom:5px}
.bk-bar{height:10px;border-radius:5px;background:#1c1c22;overflow:hidden;margin-bottom:3px}
.bk-bar>div{height:100%;border-radius:5px}
.muted{color:#8b8ba3;font-size:.78rem}
@keyframes cardin{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}
@media(max-width:640px){.grid,.rgrid,.chips{grid-template-columns:1fr}}
"""
_LAB_JS = """
const C=2*Math.PI*34;
document.querySelectorAll(".ring-fg").forEach(c=>{
  const pct=Math.max(0,Math.min(100,parseFloat(c.dataset.pct)||0));
  c.style.strokeDasharray=C;c.style.strokeDashoffset=C;
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    c.style.transition="stroke-dashoffset 1.1s cubic-bezier(.22,.9,.35,1)";
    c.style.strokeDashoffset=C*(1-pct/100);
  }));
});
document.querySelectorAll(".count").forEach(el=>{
  const t=parseFloat(el.dataset.target)||0,d=+(el.dataset.dec||0),t0=performance.now();
  (function tick(now){const p=Math.min(1,(now-t0)/900),e=1-Math.pow(1-p,3);
   el.textContent=(t*e).toFixed(d);if(p<1)requestAnimationFrame(tick);})(t0);
});
document.querySelectorAll(".grow").forEach(el=>{
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    el.style.transition="width 1s ease .2s";el.style.width=el.dataset.w+"%";
  }));
});
"""

def _ring(score):
    color = "#4fffb0" if score >= 55 else "#7c6eff" if score >= 45 else "#ff5e5e"
    return (f'<div class="gauge"><svg viewBox="0 0 90 90">'
            f'<circle class="ring-bg" cx="45" cy="45" r="34"/>'
            f'<circle class="ring-fg" cx="45" cy="45" r="34" stroke="{color}" data-pct="{score}"/></svg>'
            f'<div class="ring-val"><span class="count" data-target="{score:.0f}" data-dec="0">0</span>%</div></div>')

def _recipe_page_html(recipe, cards, book_stat):
    e = _html.escape
    chips = []
    for key, label in (("core_white","AS WHITE"),("core_e4","VS 1.e4"),("core_d4","VS 1.d4")):
        c = recipe[key]
        v = e(c["label"]) if c else "—"
        s = f'{c["score"]}% · {c["games"]} games' if c else "not enough games"
        chips.append(f'<div class="chip"><div class="chip-k">{label}</div>'
                     f'<div class="chip-v">{v}</div><div class="chip-s">{s}</div></div>')
    chip_html = '<div class="chips">' + "".join(chips) + '</div>'

    card_html = ""
    for i, c in enumerate(cards[:8]):
        gc = GRADE_COLORS[c["grade"]]
        ph = c["phases"]
        keys = list(ph.keys())
        phbar = "".join(
            f'<div class="grow" data-w="{ph[k]}" style="width:0%;'
            f'background:{["#ff5e5e","#ffb454","#7c6eff"][j]}"></div>'
            for j, k in enumerate(keys))
        trend = "—" if c["trend"] is None else (
            f'{"▲" if c["trend"]>=3 else "▼" if c["trend"]<=-3 else "▬"} {c["trend"]:+d} pts (recent vs earlier)')
        star = "*" if c["games"] < 10 else ""
        card_html += f'''
<div class="card" style="animation-delay:{i*0.06}s">
 <div class="card-head">
  <div><span class="fam">{e(c["family"])}</span>
   <span class="sub">as {c["color"]} · {c["games"]} games · median {c["median_moves"]} moves</span></div>
  <span class="grade" style="background:{gc}22;color:{gc};border:1px solid {gc}66">{c["grade"]}{star}</span>
 </div>
 <div class="card-body">
  {_ring(c["score"])}
  <div class="ph">
   <div class="ph-lbl">where your {c["losses"]} losses happen</div>
   <div class="ph-bar">{phbar}</div>
   <div class="ph-lbl">≤12 · {ph[keys[0]]}% &nbsp; 13–30 · {ph[keys[1]]}% &nbsp; 31+ · {ph[keys[2]]}%</div>
  </div>
 </div>
 <div class="trend">{trend} · leaves theory ~move {c["avg_book"]/2:.0f}</div>
 <div class="verdict">{e(card_verdict(c))}</div>
</div>'''

    def box(cls, title, items):
        body = "".join(f"<li>{it}</li>" for it in items) or '<li class="muted">Nothing here — good news.</li>'
        return f'<div class="rbox {cls}"><h3>{title}</h3><ul>{body}</ul></div>'

    keep_items = [f'<b>{e(c["family"])}</b> as {c["color"]} — {c["score"]}% over {c["games"]} games' for c in recipe["keep"][:3]]
    cut_items  = [f'<b>{e(c["family"])}</b> as {c["color"]} — only {c["score"]}% over {c["games"]} games' for c in recipe["cut"][:3]]
    r = recipe["recs"]
    add_items = []
    for slot, pre in (("white","As White"), ("e4","vs 1.e4"), ("d4","vs 1.d4")):
        name, moves, why = r[slot]
        add_items.append(f'{pre} → <b>{e(name)}</b><br><span class="mv">{e(moves)}</span><br><span class="muted">{e(why)}</span>')

    keep_box = box("rkeep","KEEP — your proven weapons", keep_items)
    fix_box  = box("rfix","FIX — where the blood is", recipe["fix"])
    cut_box  = box("rcut","CUT — stop playing these", cut_items)
    add_box  = box("radd","ADD — concrete new lines", add_items)

    cw, ce, cd = recipe["core_white"], recipe["core_e4"], recipe["core_d4"]
    core_line = (f'{e(cw["label"]) if cw else "—"} as White · '
                 f'{e(ce["label"]) if ce else "—"} vs 1.e4 · '
                 f'{e(cd["label"]) if cd else "—"} vs 1.d4')
    steps = f'''<div class="steps"><b>How to use this recipe</b><ol>
<li>For your next <b>30 games</b>, play only your core lines: {core_line}.</li>
<li>Fix one bleeding opening from the FIX box — learn its first 10 moves + the trap that keeps killing you.</li>
<li>Re-scan here after 30 games. The recipe updates itself from your results.</li></ol></div>'''

    bk = book_stat
    book_html = ""
    if bk:
        book_html = f'''<div class="book"><div class="bk">
<div class="bk-t"><b>Book-club effect:</b> when you\'re still on a known mainline at move 5, you score
<b>{bk["deep"]}%</b> ({bk["deep_n"]} games) vs <b>{bk["shallow"]}%</b> when you leave theory early ({bk["shallow_n"]} games).</div>
<div class="bk-bar"><div class="grow" data-w="{bk["deep"]}" style="width:0%;background:#4fffb0"></div></div>
<div class="bk-bar"><div class="grow" data-w="{bk["shallow"]}" style="width:0%;background:#ff5e5e"></div></div>
</div></div>'''

    return f'''<!DOCTYPE html><html><head><style>{_LAB_CSS}</style></head><body>
<div class="hero">
 <div style="font-size:2.2rem">🎯</div>
 <div><h1>Your Opening Recipe</h1>
  <div class="sub">{recipe["games"]} games · overall score <span class="count" data-target="{recipe["overall"]}" data-dec="1">0</span>%</div>
  <div class="stylechip">Style read: {recipe["style"].replace("_"," ")} — {e(recipe["style_why"])}</div>
 </div>
</div>
{chip_html}
<div class="grid">{card_html}</div>
<div class="rgrid">{keep_box}{fix_box}{cut_box}{add_box}</div>
{steps}
{book_html}
<script>{_LAB_JS}</script></body></html>'''

# ── Plotly: repertoire map & danger chart ─────────────────────────────────────
def _branch(g):
    if g["color"] == "white": return "As White"
    return {"e4": "vs 1.e4", "d4": "vs 1.d4"}.get(g["first"], "vs flank/other")

def _sunburst_fig(lab):
    bg, mg, lg = defaultdict(list), defaultdict(list), defaultdict(list)
    for g in lab:
        b = _branch(g)
        m = f"1.{g['first']}" if g["color"] == "white" else g["family"]
        l = f"vs {g['family']}" if g["color"] == "white" else (g["variation_raw"] or "—")
        bg[b].append(g); mg[(b, m)].append(g); lg[(b, m, l)].append(g)
    merged = defaultdict(list)
    for k, v in lg.items():
        (merged[(k[0], k[1], "(rare)")].extend(v) if len(v) < 3 else merged[k].extend(v))

    ids, labels, parents, values, texts = [], [], [], [], []
    for b, gs in bg.items():
        ids.append(b); labels.append(b); parents.append(""); values.append(len(gs))
        texts.append(f"{prate(gs)*100:.0f}% · {len(gs)} games")
    for (b, m), gs in mg.items():
        i = f"{b}|{m}"; ids.append(i); labels.append(m); parents.append(b)
        values.append(len(gs)); texts.append(f"{prate(gs)*100:.0f}% · {len(gs)} games")
    for (b, m, l), gs in merged.items():
        i = f"{b}|{m}|{l}"; ids.append(i); labels.append(l); parents.append(f"{b}|{m}")
        values.append(len(gs)); texts.append(f"{prate(gs)*100:.0f}% · {len(gs)} games")

    color_map = {}
    for b, gs in bg.items(): color_map[b] = prate(gs)*100
    for (b, m), gs in mg.items(): color_map[f"{b}|{m}"] = prate(gs)*100
    for (b, m, l), gs in merged.items(): color_map[f"{b}|{m}|{l}"] = prate(gs)*100
    colors = [color_map.get(i, 50) for i in ids]

    fig = go.Figure(go.Sunburst(
        ids=ids, labels=labels, parents=parents, values=values, branchvalues="total",
        marker=dict(colors=colors, colorscale="RdYlGn", cmin=30, cmax=70,
                    showscale=True, colorbar=dict(title="score %", thickness=10, len=0.9)),
        hovertemplate="%{label}<br>%{customdata}<extra></extra>", customdata=texts))
    fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=440)
    return fig

def _danger_fig(lab, cards):
    rows = [("Your overall", phase_shares([g for g in lab if g["lost"]]))]
    for c in cards[:4]:
        if c["losses"] >= 3:
            rows.append((f"{c['family']} ({c['color']})", c["phases"]))
    keys = list(rows[0][1].keys())
    colors = ["#ff5e5e", "#ffb454", "#7c6eff"]
    fig = go.Figure()
    for j, k in enumerate(keys):
        fig.add_bar(y=[r[0] for r in rows], x=[r[1][k] for r in rows],
                    orientation="h", name=k, marker_color=colors[j],
                    text=[f"{r[1][k]}%" for r in rows], textposition="inside")
    fig.update_layout(barmode="stack", height=60*len(rows)+110,
                      margin=dict(t=10,l=10,r=10,b=10),
                      xaxis_title="% of losses", legend=dict(orientation="h", y=-0.18))
    return fig

# ── Main render ───────────────────────────────────────────────────────────────
def render_opening_lab(raw_games, username, time_class, tz_label, rated_only):
    lab = prepare_lab_games(raw_games, username, time_class, tz_label, rated_only)
    if len(lab) < 10:
        st.info(f"The Opening Recipe needs at least 10 {time_class} games in this period — "
                f"play a few more and come back.")
        return

    cards, overall = family_cards(lab)
    recipe = build_recipe(lab, cards, overall)

    deep    = [g for g in lab if g["book"] >= 10]
    shallow = [g for g in lab if g["book"] < 10]
    book_stat = None
    if len(deep) >= 5 and len(shallow) >= 5:
        book_stat = {"deep": round(prate(deep)*100), "deep_n": len(deep),
                     "shallow": round(prate(shallow)*100), "shallow_n": len(shallow)}

    show = cards if cards else []
    rows = (len(show[:8]) + 1) // 2
    height = min(3200, 185 + rows*195 + 400 + 140 + 40)
    components.html(_recipe_page_html(recipe, show, book_stat), height=height, scrolling=False)

    if len(show) > 8:
        with st.expander(f"More openings ({len(show)-8} smaller samples)"):
            for c in show[8:]:
                gc = GRADE_COLORS[c["grade"]]
                st.markdown(f"<span style='color:{gc}'>**{c['grade']}**</span> · "
                            f"**{c['family']}** as {c['color']} — {c['score']}% over {c['games']} games",
                            unsafe_allow_html=True)

    if HAS_PLOTLY:
        st.markdown("**Your repertoire map** — size = games played, color = score")
        st.caption("Click a slice to expand it. '(rare)' = lines you've played fewer than 3 times.")
        st.plotly_chart(_sunburst_fig(lab), use_container_width=True)

        st.markdown("**Danger zones — where your losses happen**")
        st.plotly_chart(_danger_fig(lab, cards), use_container_width=True)
    else:
        st.info("Install plotly for the interactive repertoire map: `pip install plotly`")

    # ── Zoom mode ──────────────────────────────────────────────────────────────
    groups = defaultdict(list)
    for g in lab:
        groups[(g["color"], g["family"])].append(g)
    options = [(k, v) for k, v in groups.items() if len(v) >= 8]
    if options:
        labels = [f"{fam} · as {color} · {len(v)} games" for (color, fam), v in options]
        pick = st.selectbox("🔍 Zoom into one opening", labels, key="lab_zoom")
        if pick is not None:
            idx = labels.index(pick)
            (color, fam) = options[idx][0]
            gsel = groups[(color, fam)]

            monthly = defaultdict(list)
            for g in gsel: monthly[g["month"]].append(g)
            months = sorted(m for m in monthly if m)
            if HAS_PLOTLY and len(months) >= 2:
                scores = [prate(monthly[m])*100 for m in months]
                sizes  = [min(24, 7 + len(monthly[m])/1.5) for m in months]
                fig = go.Figure(go.Scatter(
                    x=months, y=scores, mode="lines+markers",
                    line=dict(color="#7c6eff", width=3),
                    marker=dict(size=sizes, color="#4fffb0", line=dict(width=0)),
                    hovertemplate="%{x}<br>%{y:.0f}%<extra></extra>"))
                fig.update_layout(height=300, margin=dict(t=10,l=10,r=10,b=10),
                                  yaxis_title="score %")
                st.markdown(f"**{fam} — month by month** (bigger dot = more games)")
                st.plotly_chart(fig, use_container_width=True)

            ph = phase_shares([g for g in gsel if g["lost"]])
            st.markdown(f"**{fam}: danger zones** ({sum(1 for g in gsel if g['lost'])} losses)")
            render_bars([(k, 0, v) for k, v in ph.items()], kind="loss")

            avg_book  = statistics.mean(g["book"] for g in gsel)
            your_book = statistics.mean(g["book"] for g in lab)
            st.caption(f"Theory depth here: ~move {avg_book/2:.0f} (your overall average: ~move {your_book/2:.0f})")

            var_groups = defaultdict(list)
            for g in gsel: var_groups[g["variation_raw"]].append(g)
            var_rows = [{"Variation": v, "Games": len(gs),
                         "Score %": round(prate(gs)*100),
                         "Avg opp delta": round(statistics.mean(
                             g["opp_rating"] - g["player_rating"] for g in gs))}
                        for v, gs in var_groups.items() if len(gs) >= 3 and v != "Unknown"]
            if var_rows:
                var_rows.sort(key=lambda r: -r["Games"])
                st.markdown(f"**{fam}: variation breakdown**")
                st.dataframe(pd.DataFrame(var_rows), hide_index=True, use_container_width=True)

    with st.expander("How the Opening Recipe works"):
        st.markdown("""
        - **Families** come from Chess.com's ECO data, with a move-based fallback from the PGN.
        - **Leaves theory ~move X** compares your first 12 moves against a built-in book of ~90 mainlines.
        - **Grades** are relative to your own overall score (A = +12 pts or better, F = −12 or worse).
        - **Danger zones** bucket your losses by game length: ≤12 moves, 13–30, 31+.
        - **Style read** picks your recommended lines from where your losses cluster.
        - Nothing here uses an engine — it's your own results, honestly counted (score = wins + ½·draws).
        """)

# ─── Bar chart helper ─────────────────────────────────────────────────────────
def render_bars(rows: list, kind: str = "score"):
    """rows = [(label, n, pct)] — pct is absolute, always scaled to 100%."""
    if not rows:
        st.caption("Not enough data yet.")
        return
    for label, n, pct in rows:
        c1, c2, c3 = st.columns([2.4, 4.2, 1.8])
        with c1: st.caption(label)
        with c2:
            if kind == "score":
                color = "#4fffb0" if pct >= 55 else "#ff5e5e" if pct <= 40 else "#7c6eff"
            else:  # loss rates
                color = "#ff5e5e" if pct >= 40 else "#ffb454" if pct >= 25 else "#7c6eff"
            width = min(int(round(pct)), 100)
            st.markdown(
                f'<div style="background:#1c1c22;border-radius:4px;height:18px;overflow:hidden;margin-top:4px">'
                f'<div style="background:{color};width:{width}%;height:100%;border-radius:4px"></div></div>',
                unsafe_allow_html=True)
        with c3: st.caption(f"**{pct:.0f}%** · {n}g")

# ─── Main App ─────────────────────────────────────────────────────────────────
st.markdown("# ♟️ Chess Insights")
st.markdown("*3 things about your chess that actually matter — no engine, no noise.*")
st.divider()

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

username = clean_username(username)
if not username:
    st.error("That doesn't look like a valid Chess.com username (3–25 chars, letters/numbers/`-`/`_`).")
    st.stop()

# ─── Controls ────────────────────────────────────────────────────────────────
time_class = st.radio("Time class", ["rapid", "blitz", "bullet", "daily"],
                      horizontal=True, label_visibility="collapsed", index=0)

try:
    detected_tz = st.context.timezone          # browser tz (Streamlit ≥ 1.38)
except Exception:
    detected_tz = None

tz_list = list(TIMEZONES)
if detected_tz and detected_tz not in tz_list:
    tz_list.insert(0, detected_tz)
default_tz = detected_tz if detected_tz in tz_list else "UTC"

c1, c2, c3 = st.columns([1.4, 1.6, 1])
with c1:
    depth_label = st.selectbox("History", list(DEPTH_OPTIONS), index=2)
with c2:
    tz_label = st.selectbox("Timezone", tz_list, index=tz_list.index(default_tz))
with c3:
    rated_only = st.checkbox("Rated only", value=True)

months = DEPTH_OPTIONS[depth_label]

# ─── Fetch ───────────────────────────────────────────────────────────────────
status = check_player(username)
if status == 404:
    st.error(f"❌ Could not find **{username}** on Chess.com. Check the username and try again.")
    st.stop()
if status != 200:
    st.error("⚠️ Chess.com API is unreachable right now. Please try again in a moment.")
    st.stop()

with st.spinner(f"Loading {username}'s games…"):
    raw_games = fetch_all_games(username, months)

if not raw_games:
    st.error(f"No games found for **{username}** in the selected period. Try a longer history.")
    st.stop()

games = get_games(raw_games, username, time_class, tz_label, rated_only)

if not games:
    st.warning(f"No rated {time_class} games found for **{username}** in this period. "
               f"Try a different time class, disable 'Rated only', or extend the history.")
    st.stop()

# ─── Overview ─────────────────────────────────────────────────────────────────
wins   = sum(1 for g in games if g["won"])
losses = sum(1 for g in games if g["lost"])
draws  = len(games) - wins - losses
total  = len(games)
score  = (wins + 0.5 * draws) / total

latest_r = next((g["player_rating"] for g in reversed(games) if g["player_rating"] > 0), None)
opp_ratings = [g["opp_rating"] for g in games if g["opp_rating"] > 0]
avg_opp = round(sum(opp_ratings) / len(opp_ratings)) if opp_ratings else None

m1, m2, m3, m4 = st.columns(4)
m1.metric("Games", f"{total:,}")
m2.metric("Score (W=1, D=½)", f"{score*100:.0f}%")
m3.metric("Latest Rating", f"{latest_r}" if latest_r else "—")
m4.metric("Avg Opponent", f"{avg_opp}" if avg_opp else "—")

if games[0]["dt"] and games[-1]["dt"]:
    st.caption(f"{wins}W / {draws}D / {losses}L · "
               f"{games[0]['dt'].strftime('%b %d, %Y')} → {games[-1]['dt'].strftime('%b %d, %Y')} · "
               f"times shown in **{tz_label}**")

chart_data = [(g["dt"], g["player_rating"]) for g in games[-200:]
              if g["dt"] and g["player_rating"] > 0]
if len(chart_data) >= 2:
    st.markdown("**Rating over time** (last 200 games)")
    df = pd.DataFrame(chart_data, columns=["date", "rating"]).set_index("date")
    df["20-game trend"] = df["rating"].rolling(20, min_periods=5).mean()
    st.line_chart(df, height=230, use_container_width=True)

st.divider()

# ─── Tabs ────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs([
    "📌 One Fix This Week", "🕐 Best Time to Play",
    "🧬 Losing Recipe", "🎯 Opening Recipe",
])

# ── Tab 1 ────────────────────────────────────────────────────────────────────
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
            Based on your {fix['window']} · {fix['wins']}W / {fix['losses']}L / {fix['draws']}D ({fix['score']}% score)
        </div>
        """, unsafe_allow_html=True)

        if fix.get("others"):
            with st.expander("Other things to work on"):
                for other in fix["others"]:
                    st.markdown(f"- {other}")

# ── Tab 2 ────────────────────────────────────────────────────────────────────
with tab2:
    bt = best_time(games)

    for cls, text in bt.get("insights", []):
        box_cls = "good" if cls == "good" else "bad"
        st.markdown(f'<div class="insight-box {box_cls}">{text}</div>', unsafe_allow_html=True)
    if bt.get("insights"):
        st.markdown("")

    st.markdown("**Score by time of day**")
    render_bars(bt.get("block_rows", []))
    st.caption("3-hour blocks. Treat any bar under ~10 games as noise.")

    st.markdown("")
    st.markdown("**Score by day of week**")
    render_bars(bt.get("day_rows", []))

    st.markdown("")
    st.markdown("**Score by game # in session**")
    render_bars(bt.get("seq_rows", []))
    st.caption("A session = games with no gap longer than 45 minutes. Score = wins + ½·draws ÷ games.")

# ── Tab 3 ────────────────────────────────────────────────────────────────────
with tab3:
    lr = losing_recipe(games)
    if not lr:
        st.info("Not enough data.")
    else:
        if lr["total_losses"] == 0:
            st.success(f"You haven't lost a single one of your last {lr['total']} games. 🎉")
        else:
            st.markdown(f"You lose **{lr['overall_loss_rate']}%** of your {lr['total']} {time_class} "
                        f"games. Here's when it's much worse.")

        if lr.get("sentences"):
            st.markdown("")
            st.markdown("**Your losing patterns**")
            for s in lr["sentences"]:
                st.markdown(f'<div class="insight-box bad">{s}</div>', unsafe_allow_html=True)

        if lr.get("loss_type_rows"):
            st.markdown("")
            st.markdown("**How your losses happen**")
            render_bars(lr["loss_type_rows"], kind="loss")

        if lr.get("elo_note"):
            st.markdown("")
            st.info(lr["elo_note"])
        if lr.get("elo_rows"):
            st.markdown("**Actual vs expected (Elo)**")
            st.caption("Expected score from the rating difference — 'Δ' is your over/underperformance.")
            st.dataframe(pd.DataFrame(lr["elo_rows"]), hide_index=True, use_container_width=True)

        if lr.get("patterns"):
            st.markdown("")
            st.markdown("**Loss rate breakdown**")
            render_bars([(p["label"], p["games"], p["loss_rate"]) for p in lr["patterns"]], kind="loss")

        if lr.get("endgame_note"):
            st.markdown("")
            st.warning(lr["endgame_note"])

# ── Tab 4 — Opening Recipe ────────────────────────────────────────────────────
with tab4:
    render_opening_lab(raw_games, username, time_class, tz_label, rated_only)

st.divider()

with st.expander("How these stats work"):
    st.markdown("""
    - **Score** = wins + ½·draws ÷ games (draws count, unlike raw win rate).
    - **Draws** are detected from both players' results — neither has `win`.
    - **Variants** (Chess960, crazyhouse…), unrated and 0-move auto-aborted games are excluded.
    - **Sessions** = games separated by gaps under 45 minutes (not calendar days).
    - **Best/worst hours** are ranked by Wilson lower bound, so small samples can't fake a "best hour".
    - **Expected score** uses the standard Elo formula: 1 / (1 + 10^((opp−you)/400)).
    - Games come from the Chess.com public API and are cached for 1 hour.
    """)

st.caption("Data fetched from Chess.com public API · No engine · No personal data stored")
