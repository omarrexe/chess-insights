import requests
import json
import os
import time
from datetime import datetime
from pipeline.config import RAW_GAMES_DIR

HEADERS = {"User-Agent": "Chess Intelligence Pipeline (contact: omarrrexe on chess.com)"}

def get_player_archives(username):
    url = f"https://api.chess.com/pub/player/{username}/games/archives"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json().get("archives", [])

def fetch_monthly_games(archive_url):
    response = requests.get(archive_url, headers=HEADERS)
    response.raise_for_status()
    return response.json().get("games", [])

def get_local_game_count(username):
    """Return the number of games stored locally and the set of known UUIDs."""
    path = os.path.join(RAW_GAMES_DIR, f"{username}_games.json")
    if not os.path.exists(path):
        return 0, set()
    with open(path) as f:
        games = json.load(f)
    uuids = {g.get("uuid") for g in games if g.get("uuid")}
    return len(games), uuids

def get_remote_game_count(username):
    """
    Quickly estimate the total games on Chess.com by summing up
    archive sizes WITHOUT downloading all PGNs.
    Uses the /stats endpoint to get totals per time class.
    """
    url = f"https://api.chess.com/pub/player/{username}/stats"
    try:
        resp = requests.get(url, headers=HEADERS)
        resp.raise_for_status()
        stats = resp.json()

        total = 0
        breakdown = {}
        for key, data in stats.items():
            if isinstance(data, dict) and "record" in data:
                record = data["record"]
                count = record.get("win", 0) + record.get("loss", 0) + record.get("draw", 0)
                if count > 0:
                    breakdown[key] = count
                    total += count
        return total, breakdown
    except Exception as e:
        print(f"  Warning: could not fetch remote stats — {e}")
        return None, {}

def sync_games(username):
    """
    Smart sync:
    1. Check how many games are stored locally.
    2. Fetch remote total from Chess.com stats API (fast, no game data).
    3. If counts differ, fetch only the archives that contain new games.
    4. Merge new games (dedup by UUID) and save.
    """
    os.makedirs(RAW_GAMES_DIR, exist_ok=True)
    local_path = os.path.join(RAW_GAMES_DIR, f"{username}_games.json")

    # ── Step 1: Check local state ──────────────────────────────────────────
    local_count, known_uuids = get_local_game_count(username)
    print(f"📂 Local:  {local_count:,} games stored")

    # ── Step 2: Check remote total (fast) ─────────────────────────────────
    remote_total, breakdown = get_remote_game_count(username)
    if remote_total is not None:
        print(f"🌐 Remote: ~{remote_total:,} games on Chess.com")
        for cls, cnt in breakdown.items():
            print(f"   └─ {cls}: {cnt:,}")

    # ── Step 3: Decide whether sync is needed ─────────────────────────────
    new_game_estimate = (remote_total or 0) - local_count
    if remote_total is not None and new_game_estimate <= 0:
        print(f"\n✅ Already up to date! No sync needed.")
        return local_count, 0

    if remote_total is not None:
        print(f"\n🔄 Detected ~{new_game_estimate:,} new game(s). Starting sync…\n")
    else:
        print(f"\n🔄 Could not verify remote count. Running full sync…\n")

    # ── Step 4: Fetch archives (only recent ones if we have local data) ────
    archives = get_player_archives(username)

    # If we have local games, only re-fetch the last 2 archives (current + prev month)
    # to catch all new games without re-downloading everything.
    if local_count > 0 and remote_total is not None and new_game_estimate < 500:
        archives_to_fetch = archives[-2:]  # last 2 months
        print(f"  ⚡ Incremental sync: fetching last {len(archives_to_fetch)} archive(s) only")
    else:
        archives_to_fetch = archives  # full sync
        print(f"  📥 Full sync: fetching all {len(archives_to_fetch)} archive(s)")

    fetched_games = []
    for url in archives_to_fetch:
        month = url.split("/")[-2] + "/" + url.split("/")[-1]
        games = fetch_monthly_games(url)
        fetched_games.extend(games)
        print(f"  ✓ {month}: {len(games)} games")
        time.sleep(0.5)

    # ── Step 5: Merge with local games (dedup by UUID) ─────────────────────
    if local_count > 0:
        with open(local_path) as f:
            existing_games = json.load(f)
    else:
        existing_games = []

    # Build merged set - existing games first, then add any new ones
    existing_uuids = {g.get("uuid") for g in existing_games if g.get("uuid")}
    added = [g for g in fetched_games if g.get("uuid") not in existing_uuids]
    merged = existing_games + added

    # Sort chronologically
    merged.sort(key=lambda g: g.get("end_time", 0))

    # ── Step 6: Save ───────────────────────────────────────────────────────
    with open(local_path, "w") as f:
        json.dump(merged, f, indent=2)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n✅ Sync complete at {timestamp}")
    print(f"   Before: {local_count:,} games")
    print(f"   Added:  {len(added):,} new games")
    print(f"   Total:  {len(merged):,} games")
    print(f"   Saved → {local_path}")

    return len(merged), len(added)

def fetch_all_games(username):
    """Full initial fetch — downloads all archives from scratch."""
    os.makedirs(RAW_GAMES_DIR, exist_ok=True)
    archives = get_player_archives(username)

    all_games = []
    for archive_url in archives:
        print(f"Fetching {archive_url}...")
        games = fetch_monthly_games(archive_url)
        all_games.extend(games)
        time.sleep(1)

    output_path = os.path.join(RAW_GAMES_DIR, f"{username}_games.json")
    with open(output_path, 'w') as f:
        json.dump(all_games, f, indent=2)

    print(f"Saved {len(all_games)} games to {output_path}")
    return all_games
