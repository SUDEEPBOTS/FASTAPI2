import os
import time
import datetime
import requests
import re
import asyncio
import uuid
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
import config  # Config file import ki (Jisme keys hain)

app = FastAPI(title="⚡ Sudeep API (Hybrid: YT+Jio+Catbox)")

# ─────────────────────────────
# DATABASE & CONFIG
# ─────────────────────────────
if not config.MONGO_DB_URI:
    print("⚠️ MONGO_DB_URI not found.")

mongo = AsyncIOMotorClient(config.MONGO_DB_URI)
db = mongo["MusicAPI_DB12"]
videos_col = db["videos_cacht"]
keys_col = db["api_users"]
queries_col = db["query_mapping"]

CATBOX_UPLOAD = "https://catbox.moe/user/api.php"

# ─────────────────────────────
# 🔑 KEY ROTATION LOGIC (For Google API)
# ─────────────────────────────
current_key_index = 0

def get_next_key():
    global current_key_index
    keys = config.YOUTUBE_API_KEYS
    if not keys: return None
    key = keys[current_key_index]
    # Next time ke liye index badha do (Loop mein)
    current_key_index = (current_key_index + 1) % len(keys)
    return key

def format_duration(seconds):
    try:
        seconds = int(seconds)
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"
    except: return "0:00"

# 📸 FALLBACK THUMBNAIL
def get_fallback_thumb(vid_id):
    return f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

# 📢 SEND LOG TO TELEGRAM
def send_telegram_log(title, duration, link, vid_id):
    if not config.BOT_TOKEN: return
    try:
        msg = (
            f"🍫 **ɴᴇᴡ sᴏɴɢ (Hybrid)**\n\n"
            f"🫶 **ᴛɪᴛʟᴇ:** {title}\n"
            f"⏱ **ᴅᴜʀᴀᴛɪᴏɴ:** {duration}\n"
            f"🛡️ **ɪᴅ:** `{vid_id}`\n"
            f"👀 [ʟɪɴᴋ]({link})\n\n"
            f"🍭 @Kaito_3_2"
        )
        requests.post(
            f"https://api.telegram.org/bot{config.BOT_TOKEN}/sendMessage",
            data={"chat_id": config.LOGGER_ID, "text": msg, "parse_mode": "Markdown"}
        )
    except Exception as e:
        print(f"❌ Logger Error: {e}")

# ─────────────────────────────
# 🔥 STEP 1: GOOGLE OFFICIAL API (Metadata Only)
# ─────────────────────────────
def get_youtube_metadata(query):
    # 3 Baar Try karega (Alag keys se)
    for _ in range(3):
        api_key = get_next_key()
        if not api_key: break
        
        print(f"🔑 Using Google Key: ...{api_key[-4:]}")
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": api_key}

        try:
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()

            # Agar Quota Error aaye
            if "error" in data:
                print(f"⚠️ Key Error: {data['error']['message']}")
                continue # Agli key try karo

            if "items" in data and len(data["items"]) > 0:
                item = data["items"][0]
                return {
                    "id": item["id"]["videoId"],
                    "title": item["snippet"]["title"],
                    # High Quality Thumbnail
                    "thumbnail": item["snippet"]["thumbnails"]["high"]["url"]
                }
        except Exception as e:
            print(f"❌ YouTube API Error: {e}")
            continue
            
    return None

# ─────────────────────────────
# 🔥 STEP 2: JIOSAAVN AUDIO FINDER
# ─────────────────────────────
def get_jiosaavn_direct_link(song_title):
    print(f"🎵 Searching JioSaavn for: {song_title}")
    try:
        # Search
        search_url = f"{config.JIOSAAVN_URL}/search?query={song_title}"
        resp = requests.get(search_url, timeout=5).json()

        if not resp.get("results"): return None

        # ID nikalo aur Details maango
        song_id = resp["results"][0]["id"]
        details_url = f"{config.JIOSAAVN_URL}/song?id={song_id}"
        details = requests.get(details_url, timeout=5).json()
        
        # Format Handle (List vs Dict)
        target = details[0] if isinstance(details, list) else details

        # 320kbps Link Priority
        media_urls = target.get("media_urls", {})
        link = media_urls.get("320_KBPS") or media_urls.get("160_KBPS") or target.get("media_url")
        
        dur_sec = target.get("duration", 0)
        return {"link": link, "duration": format_duration(dur_sec)}

    except Exception as e:
        print(f"❌ JioSaavn Error: {e}")
        return None

# ─────────────────────────────
# 🔥 STEP 3: BRIDGE (Download -> Upload to Catbox)
# ─────────────────────────────
def bridge_jio_to_catbox(jio_url):
    """
    JioSaavn se download karke Catbox pe upload karega
    taaki Aria2 download kar sake.
    """
    random_name = str(uuid.uuid4())
    temp_file = f"/tmp/{random_name}.m4a"
    
    try:
        print("📥 Downloading Audio from JioSaavn...")
        with requests.get(jio_url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(temp_file, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        print("📤 Uploading to Catbox...")
        if os.path.exists(temp_file):
            with open(temp_file, "rb") as f:
                r = requests.post(CATBOX_UPLOAD, data={"reqtype": "fileupload"}, files={"fileToUpload": f}, timeout=120)
            
            os.remove(temp_file) # Safai
            
            if r.status_code == 200 and r.text.startswith("http"):
                return r.text.strip()
            else:
                print(f"❌ Catbox Error: {r.text}")
                
    except Exception as e:
        print(f"❌ Bridge Error: {e}")
        if os.path.exists(temp_file): os.remove(temp_file)
    
    return None

# ─────────────────────────────
# 🔐 AUTH CHECK
# ─────────────────────────────
async def verify_and_count(key: str):
    doc = await keys_col.find_one({"api_key": key})
    if not doc or not doc.get("active", True):
        return False, "Invalid Key"
    
    # Usage Count
    await keys_col.update_one({"api_key": key}, {"$inc": {"total_usage": 1}})
    return True, None

# ─────────────────────────────
# 🚀 MAIN API LOGIC
# ─────────────────────────────
@app.get("/getvideo")
async def get_video(query: str, key: str):
    start_time = time.time()

    # 1. Auth Check
    is_valid, err = await verify_and_count(key)
    if not is_valid: return {"status": 403, "error": err}

    clean_query = query.strip().lower()

    # 2. Get Metadata (Google API)
    # Humein pehle ID chahiye taaki DB check kar sakein
    print(f"🔍 Searching YouTube: {query}")
    yt_data = await asyncio.to_thread(get_youtube_metadata, query)
    
    if not yt_data:
        return {"status": 404, "error": "Song Not Found on YouTube"}

    video_id = yt_data["id"]
    title = yt_data["title"]
    thumbnail = yt_data["thumbnail"]

    # 3. DB Check (Cache)
    cached = await videos_col.find_one({"video_id": video_id})
    if cached and cached.get("catbox_link"):
        print(f"✅ Found in Cache: {title}")
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

    # 4. Fetch New Song (Hybrid Process)
    print(f"⏳ New Song Detected: {title}")
    
    # Step A: JioSaavn se Link lo
    jio_data = await asyncio.to_thread(get_jiosaavn_direct_link, title)
    
    if not jio_data or not jio_data["link"]:
        return {"status": 500, "error": "Audio Not Found on JioSaavn"}
    
    # Step B: Download & Upload to Catbox
    catbox_link = await asyncio.to_thread(bridge_jio_to_catbox, jio_data["link"])
    
    if not catbox_link:
        return {"status": 500, "error": "Upload Failed (Bridge Error)"}

    # Step C: Save to DB
    duration = jio_data["duration"]
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
    
    # Map Query for faster future searches
    await queries_col.update_one({"query": clean_query}, {"$set": {"video_id": video_id}}, upsert=True)

    # 📢 Log Send
    asyncio.create_task(asyncio.to_thread(send_telegram_log, title, duration, catbox_link, video_id))

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

# Stats & Home (Same as before)
@app.get("/stats")
async def get_stats():
    total_songs = await videos_col.count_documents({})
    return {"status": 200, "total_songs": total_songs}

@app.api_route("/", methods=["GET", "HEAD"])
async def home():
    return {"status": "Running", "mode": "Hybrid (Google+Jio+Catbox)"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
