"""
Chess Insights — Streamlit App
3 purely metadata-based analyses. No engine. No noise.

v2 — accuracy + usefulness pass
"""

import math
import re
import time as time_mod
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

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
tab1, tab2, tab3 = st.tabs(["📌 One Fix This Week", "🕐 Best Time to Play", "🧬 Losing Recipe"])

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
