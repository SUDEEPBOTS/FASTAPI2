import os
import shutil
import subprocess
import requests
from fastapi import FastAPI, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

# ─────────────────────────────
# ENV CONFIG
# ─────────────────────────────
MONGO_URL = os.getenv("MONGO_DB_URI")
BOT_TOKEN = os.getenv("BOT_TOKEN")          # Telegram logger bot token
LOG_GROUP_ID = os.getenv("LOG_GROUP_ID")    # Logger GC ID (-100xxxx)

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"

# 👉 yt-dlp ke liye writable cookies path
COOKIES_SRC = "/app/cookies.txt"   # repo root se aati hai
COOKIES_PATH = "/tmp/cookies.txt"  # yt-dlp yahin use karega

# ─────────────────────────────
# APP
# ─────────────────────────────
app = FastAPI(title="Sudeep Music API ⚡ Auto Download")

client = AsyncIOMotorClient(MONGO_URL)
db = client["MusicAPI_DB1"]
collection = db["songs_cachee"]

# Ultra-fast RAM cache
MEM_CACHE = {}

# ─────────────────────────────
# INIT: copy cookies to /tmp (WRITABLE)
# ─────────────────────────────
if os.path.exists(COOKIES_SRC):
    try:
        shutil.copy(COOKIES_SRC, COOKIES_PATH)
    except Exception as e:
        print("Cookies copy failed:", e)
else:
    print("⚠️ cookies.txt not found in /app")

# ─────────────────────────────
# HELPERS
# ─────────────────────────────
def yt_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"

def extract_video_id(query: str):
    q = query.strip()
    if len(q) == 11 and " " not in q:
        return q
    if "v=" in q:
        return q.split("v=")[1].split("&")[0]
    if "youtu.be/" in q:
        return q.split("youtu.be/")[1].split("?")[0]
    return None

def send_logger(text: str):
    if not BOT_TOKEN or not LOG_GROUP_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": LOG_GROUP_ID, "text": text},
            timeout=10
        )
    except:
        pass

def upload_catbox(file_path: str) -> str:
    with open(file_path, "rb") as f:
        r = requests.post(
            CATBOX_UPLOAD,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": f},
            timeout=60
        )
    if r.status_code == 200 and r.text.startswith("https://"):
        return r.text.strip()
    raise Exception("Catbox upload failed")

def auto_download(video_id: str) -> str:
    """
    yt-dlp + ffmpeg
    Output: /tmp/<video_id>.mp3
    """
    if not os.path.exists(COOKIES_PATH):
        raise Exception("cookies.txt missing in container")

    out = f"/tmp/{video_id}.mp3"
    cmd = [
    "python", "-m", "yt_dlp",
    "--cookies", COOKIES_PATH,
    "--js-runtimes", "node",
    "--no-playlist",
    "--geo-bypass",
    "--force-ipv4",
    "-f", "bestaudio",
    "--extract-audio",
    "--audio-format", "mp3",
    "--audio-quality", "0",
    yt_url(video_id),
    "-o", out
    ]

    subprocess.run(cmd, check=True, timeout=300)
    return out

# ─────────────────────────────
# MAIN API
# ─────────────────────────────
@app.get("/get")
async def get_music(query: str):
    video_id = extract_video_id(query)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube video id")

    # 1️⃣ RAM CACHE (FASTEST)
    if video_id in MEM_CACHE:
        return MEM_CACHE[video_id]

    # 2️⃣ DB CACHE
    cached = await collection.find_one({"video_id": video_id}, {"_id": 0})
    if cached:
        resp = {
            "t": cached["title"],
            "u": cached["catbox_link"],
            "id": video_id
        }
        MEM_CACHE[video_id] = resp
        return resp

    # 3️⃣ AUTO DOWNLOAD
    try:
        local_file = auto_download(video_id)
        catbox_link = upload_catbox(local_file)

        # cleanup temp file
        try:
            os.remove(local_file)
        except:
            pass

        doc = {
            "video_id": video_id,
            "title": video_id,
            "catbox_link": catbox_link
        }

        # safe upsert
        await collection.update_one(
            {"video_id": video_id},
            {"$set": doc},
            upsert=True
        )

        resp = {
            "t": video_id,
            "u": catbox_link,
            "id": video_id
        }
        MEM_CACHE[video_id] = resp

        # Telegram logger
        send_logger(
            f"🎵 New Song Added Automatically\n\n"
            f"🆔 {video_id}\n"
            f"📦 Catbox\n"
            f"🔗 {catbox_link}"
        )

        return resp

    except Exception as e:
        raise HTTPException(500, f"Auto download failed: {e}")

# ─────────────────────────────
# HEALTH / UPTIME
# ─────────────────────────────
@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {
        "status": "ok",
        "cache_items": len(MEM_CACHE)
    }
