import os
import time
import datetime
import requests
import re
import asyncio
import uuid
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import config

app = FastAPI(title="⚡ Sudeep API (YouTube Meta + Saavn File + Catbox)")

# ─────────────────────────────
# DATABASE
# ─────────────────────────────
mongo = AsyncIOMotorClient(config.MONGO_DB_URI)
db = mongo["MusicAPI_DB12"]

videos_col = db["videos_cacht"]   # same old collection
keys_col = db["api_users"]

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"
SAAVN_BASE = "https://saavn.sumit.co"

# ─────────────────────────────
# 🔑 KEY ROTATION (YouTube Metadata)
# ─────────────────────────────
current_key_index = 0

def get_next_key():
    global current_key_index
    keys = config.YOUTUBE_API_KEYS
    if not keys:
        return None
    key = keys[current_key_index]
    current_key_index = (current_key_index + 1) % len(keys)
    return key

def format_duration(seconds):
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    except:
        return "0:00"

# ─────────────────────────────
# 🔥 Step 1: YouTube API (Metadata only)
# ─────────────────────────────
def get_youtube_metadata(query):
    for _ in range(3):
        api_key = get_next_key()
        if not api_key:
            break

        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 1,
            "key": api_key
        }

        try:
            resp = requests.get(url, params=params, timeout=7)
            data = resp.json()

            if "error" in data:
                continue

            items = data.get("items", [])
            if not items:
                continue

            item = items[0]
            return {
                "id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "thumbnail": item["snippet"]["thumbnails"]["high"]["url"]
            }
        except:
            continue

    return None

# ─────────────────────────────
# 🔥 Step 2: Saavn search -> direct download url (320kbps)
# ─────────────────────────────
def saavn_search_top(query: str):
    url = f"{SAAVN_BASE}/api/search/songs"
    r = requests.get(url, params={"query": query}, timeout=10)
    return r.json()

def pick_best_download(song_obj: dict):
    dls = song_obj.get("downloadUrl", [])
    if not dls:
        return None

    for q in ["320kbps", "160kbps", "96kbps", "48kbps", "12kbps"]:
        for item in dls:
            if item.get("quality") == q:
                return item.get("url")

    return dls[-1].get("url")

# ─────────────────────────────
# 🔥 Step 3: Download file -> Upload Catbox
# ─────────────────────────────
def bridge_to_catbox(file_url: str):
    tmp_name = f"/tmp/{uuid.uuid4()}.mp4"  # saavn audio usually mp4/aac
    try:
        with requests.get(file_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp_name, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

        with open(tmp_name, "rb") as f:
            up = requests.post(
                CATBOX_UPLOAD,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                timeout=120
            )

        if os.path.exists(tmp_name):
            os.remove(tmp_name)

        if up.status_code == 200 and up.text.startswith("http"):
            return up.text.strip()

        return None

    except Exception as e:
        print("❌ Bridge Error:", e)
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except:
            pass
        return None

# ─────────────────────────────
# 🔐 AUTH CHECK
# ─────────────────────────────
async def verify_and_count(key: str):
    doc = await keys_col.find_one({"api_key": key})
    if not doc or not doc.get("active", True):
        return False, "Invalid Key"
    await keys_col.update_one({"api_key": key}, {"$inc": {"total_usage": 1}})
    return True, None

# ─────────────────────────────
# 🚀 MAIN API
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    start_time = time.time()

    # 1) Auth
    ok, err = await verify_and_count(key)
    if not ok:
        return {"status": 403, "error": err}

    # 2) YouTube metadata
    yt_data = await asyncio.to_thread(get_youtube_metadata, query)
    if not yt_data:
        return {"status": 404, "error": "Not found on YouTube (metadata)"}

    video_id = yt_data["id"]
    title = yt_data["title"]
    thumbnail = yt_data["thumbnail"]

    # 3) Cache check by video_id (old songs will return ✅)
    cached = await videos_col.find_one({"video_id": video_id})
    if cached and cached.get("catbox_link"):
        return {
            "status": 200,
            "title": cached.get("title", title),
            "duration": cached.get("duration", "0:00"),
            "link": cached["catbox_link"],
            "id": video_id,
            "thumbnail": cached.get("thumbnail", thumbnail),
            "cached": True,
            "response_time": f"{time.time()-start_time:.2f}s"
        }

    # 4) Saavn direct file url
    saavn_data = await asyncio.to_thread(saavn_search_top, query)
    results = (saavn_data.get("data") or {}).get("results") or []
    if not results:
        return {"status": 404, "error": "Not found on Saavn"}

    top = results[0]
    saavn_url = pick_best_download(top)
    if not saavn_url:
        return {"status": 500, "error": "Saavn downloadUrl missing"}

    duration = format_duration(top.get("duration", 0))

    # 5) Download + upload catbox
    catbox_link = await asyncio.to_thread(bridge_to_catbox, saavn_url)
    if not catbox_link:
        return {"status": 500, "error": "Catbox upload failed"}

    # 6) Save DB
    await videos_col.update_one(
        {"video_id": video_id},
        {"$set": {
            "title": title,
            "video_id": video_id,
            "catbox_link": catbox_link,
            "thumbnail": thumbnail,
            "duration": duration,
            "cached_at": datetime.datetime.now()
        }},
        upsert=True
    )

    return {
        "status": 200,
        "title": title,
        "duration": duration,
        "link": catbox_link,
        "id": video_id,
        "thumbnail": thumbnail,
        "cached": False,
        "response_time": f"{time.time()-start_time:.2f}s"
    }

# Stats & Home
@app.get("/stats")
async def get_stats():
    total_songs = await videos_col.count_documents({})
    return {"status": 200, "total_songs": total_songs}

@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "Running", "mode": "YouTube meta + Saavn file + Catbox"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)