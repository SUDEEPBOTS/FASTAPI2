
import os
import time
import datetime
import subprocess
import requests
import re
import asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp

# ─────────────────────────────
# CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CONTACT = "@Kaito_3_2"
CATBOX_UPLOAD = "https://catbox.moe/user/api.php"
COOKIES_PATH = "/app/cookies.txt"

# ─────────────────────────────
# FASTAPI APP
# ─────────────────────────────
app = FastAPI(title="⚡ Sudeep Music API")

# MongoDB
mongo = AsyncIOMotorClient(MONGO_URL)
db = mongo["MusicAPI_DB1"]
videos_col = db["videos_cachet"]
keys_col = db["api_users"]

# ⚡ ULTRA-FAST RAM CACHE (No DB lookup needed)
RAM_CACHE = {}

# ⚡ BACKGROUND DOWNLOAD QUEUE
download_queue = {}
queue_lock = asyncio.Lock()

# ─────────────────────────────
# CORE FUNCTIONS - OPTIMIZED
# ─────────────────────────────
def extract_video_id(q: str) -> str:
    """Extract video ID from any input - FAST"""
    q = q.strip()
    
    # Direct video ID (11 chars, no spaces/specials)
    if len(q) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', q):
        return q
    
    # URL patterns
    if "v=" in q:
        return q.split("v=")[1][:11]
    if "youtu.be/" in q:
        return q.split("youtu.be/")[1][:11]
    
    return None

def format_time(seconds: int) -> str:
    """Fast duration formatter"""
    if not seconds: return "0:00"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

def quick_search(query: str) -> dict:
    """⚡ FAST SEARCH - Returns {id, title, duration}"""
    try:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'default_search': 'ytsearch1',
            'extract_flat': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
            
            if info and info.get('entries'):
                video = info['entries'][0]
                return {
                    "id": video.get('id'),
                    "title": video.get('title', 'Unknown'),
                    "duration": format_time(video.get('duration', 0))
                }
    except:
        pass
    return None

def get_video_info_fast(video_id: str) -> dict:
    """⚡ FAST video info fetcher"""
    try:
        ydl_opts = {'quiet': True, 'skip_download': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
            return {
                "id": video_id,
                "title": info.get('title', f'Video {video_id}'),
                "duration": format_time(info.get('duration', 0))
            }
    except:
        return None

async def verify_key_fast(key: str) -> bool:
    """⚡ FAST API key verification"""
    try:
        doc = await keys_col.find_one({"api_key": key, "active": True})
        if not doc: return False
        
        # Check expiry
        if time.time() > doc["expires_at"]:
            return False
        
        # Daily limit check
        today = datetime.date.today().isoformat()
        if doc.get("last_reset") != today:
            await keys_col.update_one(
                {"_id": doc["_id"]},
                {"$set": {"used_today": 0, "last_reset": today}}
            )
            doc["used_today"] = 0
        
        if doc["used_today"] >= doc["daily_limit"]:
            return False
        
        # Increment counter
        await keys_col.update_one(
            {"_id": doc["_id"]},
            {"$inc": {"used_today": 1}}
        )
        return True
    except:
        return False

def download_and_upload(video_id: str, title: str) -> str:
    """Download + Upload to Catbox"""
    try:
        # Download
        out_file = f"/tmp/{video_id}.mp4"
        cmd = [
            "python", "-m", "yt_dlp",
            "--cookies", COOKIES_PATH,
            "--no-playlist",
            "-f", "best[height<=720]",
            "--merge-output-format", "mp4",
            f"https://youtube.com/watch?v={video_id}",
            "-o", out_file
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        
        # Upload
        with open(out_file, "rb") as f:
            r = requests.post(
                CATBOX_UPLOAD,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                timeout=60
            )
        
        if r.text.startswith("https://"):
            os.remove(out_file)
            return r.text.strip()
    except Exception as e:
        print(f"Download/Upload error: {e}")
    return None

async def process_in_background(video_id: str, title: str, duration: str):
    """Background processing for new videos"""
    try:
        # Download + Upload
        catbox_url = download_and_upload(video_id, title)
        
        if catbox_url:
            # Save to DB
            await videos_col.update_one(
                {"video_id": video_id},
                {"$set": {
                    "video_id": video_id,
                    "title": title,
                    "duration": duration,
                    "catbox_link": catbox_url,
                    "cached_at": datetime.datetime.utcnow()
                }},
                upsert=True
            )
            
            # Update RAM cache
            RAM_CACHE[video_id] = {
                "status": 200,
                "title": title,
                "duration": duration,
                "link": catbox_url,
                "video_id": video_id,
                "cached": True
            }
    except:
        pass

# ─────────────────────────────
# SINGLE MAIN ENDPOINT - ULTRA FAST
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    """
    ⚡ SINGLE ULTRA-FAST ENDPOINT
    Returns in 0.4s for cached, processes new in background
    """
    
    # 1. FAST API KEY CHECK (50ms)
    if not await verify_key_fast(key):
        return {
            "status": 403,
            "title": None,
            "duration": None,
            "link": None,
            "video_id": None,
            "error": "Invalid or expired API key"
        }
    
    start_time = time.time()
    
    # 2. EXTRACT VIDEO ID (5ms)
    video_id = extract_video_id(query)
    
    # 3. IF SEARCH QUERY, GET VIDEO ID (200ms)
    if not video_id:
        search_data = quick_search(query)
        if not search_data:
            return {
                "status": 404,
                "title": None,
                "duration": None,
                "link": None,
                "video_id": None,
                "error": "Video not found"
            }
        video_id = search_data["id"]
        title = search_data["title"]
        duration = search_data["duration"]
    else:
        # Direct video ID
        title = f"Video {video_id}"
        duration = "unknown"
    
    # 4. ⚡⚡⚡ RAM CACHE CHECK (1ms) - INSTANT RESPONSE
    if video_id in RAM_CACHE:
        response = RAM_CACHE[video_id].copy()
        response["response_time"] = f"{(time.time() - start_time)*1000:.1f}ms"
        return response
    
    # 5. ⚡ DB CACHE CHECK (50ms)
    cached = await videos_col.find_one({"video_id": video_id})
    if cached:
        response = {
            "status": 200,
            "title": cached["title"],
            "duration": cached.get("duration", "unknown"),
            "link": cached["catbox_link"],
            "video_id": video_id,
            "cached": True
        }
        RAM_CACHE[video_id] = response
        response["response_time"] = f"{(time.time() - start_time)*1000:.1f}ms"
        return response
    
    # 6. NEW VIDEO - BACKGROUND PROCESS + IMMEDIATE RESPONSE
    # Get proper title/duration if missing
    if title == f"Video {video_id}":
        info = get_video_info_fast(video_id)
        if info:
            title = info["title"]
            duration = info["duration"]
    
    # Start background download
    asyncio.create_task(process_in_background(video_id, title, duration))
    
    # Immediate response (under 300ms)
    response = {
        "status": 202,  # Accepted - processing in background
        "title": title,
        "duration": duration,
        "link": None,
        "video_id": video_id,
        "message": "Video is being processed. Please try again in 30 seconds.",
        "note": "First time download may take 2-3 minutes. Next time will be instant.",
        "response_time": f"{(time.time() - start_time)*1000:.1f}ms"
    }
    
    return response

# ─────────────────────────────
# STARTUP - PRELOAD CACHE
# ─────────────────────────────
@app.on_event("startup")
async def startup_cache_preload():
    """Preload popular videos into RAM cache on startup"""
    try:
        # Get top 100 most accessed videos
        popular = await videos_col.find().sort("access_count", -1).limit(100).to_list(None)
        
        for doc in popular:
            RAM_CACHE[doc["video_id"]] = {
                "status": 200,
                "title": doc["title"],
                "duration": doc.get("duration", "unknown"),
                "link": doc["catbox_link"],
                "video_id": doc["video_id"],
                "cached": True
            }
        
        print(f"⚡ Preloaded {len(RAM_CACHE)} videos into RAM cache")
    except:
        print("⚠️ Could not preload cache")

# ─────────────────────────────
# MINIMAL REQUIREMENTS
# ─────────────────────────────
"""
requirements.txt:
fastapi==0.104.1
uvicorn==0.24.0
motor==3.3.2
yt-dlp==2023.11.16
requests==2.31.0
"""
