from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from typing import Optional
import json, os, asyncio

from pipeline.config import RAW_GAMES_DIR, CHESS_COM_USERNAME
from pipeline.insights import best_time_to_play, losing_recipe, one_fix_this_week
from pipeline.fetcher import (
    get_local_game_count, get_remote_game_count,
    get_player_archives, fetch_monthly_games,
)

app = FastAPI(title="Chess Insights API", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _u(username: Optional[str]) -> str:
    return username or CHESS_COM_USERNAME

# ─── Player overview ──────────────────────────────────────────────────────────
@app.get("/api/player")
def get_player(username: Optional[str] = None):
    u        = _u(username)
    raw_path = os.path.join(RAW_GAMES_DIR, f"{u}_games.json")
    if not os.path.exists(raw_path):
        raise HTTPException(404, "No games found. Sync first.")
    with open(raw_path) as f:
        games = json.load(f)

    wins = losses = draws = 0
    ratings = []
    for g in games:
        white = g.get("white", {})
        black = g.get("black", {})
        if white.get("username", "").lower() == u.lower():
            r = white.get("result", "")
            ratings.append(white.get("rating", 0))
        elif black.get("username", "").lower() == u.lower():
            r = black.get("result", "")
            ratings.append(black.get("rating", 0))
        else:
            continue
        if r == "win":
            wins += 1
        elif r in ("agreed", "stalemate", "repetition", "insufficient", "50move"):
            draws += 1
        else:
            losses += 1

    total = wins + losses + draws
    current_rating = ratings[-1] if ratings else 0
    return {
        "username":       u,
        "total_games":    len(games),
        "wins":           wins,
        "losses":         losses,
        "draws":          draws,
        "win_rate":       round(wins / total * 100, 1) if total else 0,
        "current_rating": current_rating,
    }

# ─── 3 core endpoints ─────────────────────────────────────────────────────────
@app.get("/api/best-time")
def get_best_time(username: Optional[str] = None, time_class: str = "rapid"):
    data = best_time_to_play(_u(username), time_class)
    if not data:
        raise HTTPException(404, "No games found.")
    return data

@app.get("/api/losing-recipe")
def get_losing_recipe(username: Optional[str] = None, time_class: str = "rapid"):
    data = losing_recipe(_u(username), time_class)
    if not data:
        raise HTTPException(404, "No games found.")
    return data

@app.get("/api/one-fix")
def get_one_fix(username: Optional[str] = None, time_class: str = "rapid"):
    data = one_fix_this_week(_u(username), time_class)
    if not data:
        raise HTTPException(404, "No games found.")
    return data

# ─── Sync (SSE stream) ────────────────────────────────────────────────────────
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"

@app.get("/api/sync/stream")
async def sync_stream(username: Optional[str] = None):
    u = _u(username)

    async def generate():
        try:
            yield _sse("progress", {"msg": "Checking local games…", "pct": 5})
            local_count, known_uuids = await asyncio.get_event_loop().run_in_executor(
                None, get_local_game_count, u
            )
            yield _sse("progress", {"msg": f"Local: {local_count:,} games", "pct": 15, "local": local_count})

            yield _sse("progress", {"msg": "Checking Chess.com…", "pct": 25})
            remote_total, breakdown = await asyncio.get_event_loop().run_in_executor(
                None, get_remote_game_count, u
            )

            if remote_total is not None:
                yield _sse("progress", {"msg": f"Remote: ~{remote_total:,} games", "pct": 35,
                                        "remote": remote_total, "breakdown": breakdown})

            raw_path    = os.path.join(RAW_GAMES_DIR, f"{u}_games.json")
            new_estimate = (remote_total or 0) - local_count

            if remote_total is not None and new_estimate <= 0:
                yield _sse("done", {"msg": "✅ Already up to date!", "added": 0, "total": local_count})
                return

            if local_count > 0 and remote_total and new_estimate < 500:
                yield _sse("progress", {"msg": f"~{new_estimate} new games. Fetching last 2 months…", "pct": 45})
                archives = (await asyncio.get_event_loop().run_in_executor(None, get_player_archives, u))[-2:]
            else:
                yield _sse("progress", {"msg": "Running full sync…", "pct": 45})
                archives = await asyncio.get_event_loop().run_in_executor(None, get_player_archives, u)

            fetched = []
            for i, url in enumerate(archives):
                month = "/".join(url.split("/")[-2:])
                pct   = 50 + int((i / max(len(archives), 1)) * 35)
                yield _sse("progress", {"msg": f"Fetching {month}…", "pct": pct})
                games = await asyncio.get_event_loop().run_in_executor(None, fetch_monthly_games, url)
                fetched.extend(games)
                await asyncio.sleep(0.3)

            yield _sse("progress", {"msg": "Merging…", "pct": 88})
            if local_count > 0 and os.path.exists(raw_path):
                with open(raw_path) as f:
                    existing = json.load(f)
            else:
                existing = []

            existing_uuids = {g.get("uuid") for g in existing}
            added   = [g for g in fetched if g.get("uuid") not in existing_uuids]
            merged  = existing + added
            merged.sort(key=lambda g: g.get("end_time", 0))

            os.makedirs(RAW_GAMES_DIR, exist_ok=True)
            with open(raw_path, "w") as f:
                json.dump(merged, f, indent=2)

            yield _sse("done", {"msg": f"✅ Done! Added {len(added):,} new game(s).",
                                "added": len(added), "total": len(merged)})
        except Exception as e:
            yield _sse("error", {"msg": f"❌ {e}"})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
