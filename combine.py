import os
import json
import yaml
import random
import subprocess
from datetime import datetime, timedelta

# ----------------------------- LOAD CONFIG + DATABASE --------------------------------

config_file = "config.yaml"
if not os.path.exists(config_file):
    raise FileNotFoundError("Config file missing — make sure config.yaml exists")

with open(config_file, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

db_path = cfg.get("db_path", "videos_db.json")

cooldown_time = cfg.get("cooldown_time")

required_keys = [
    "channels", "download_path", "output_path",
    "db_path","cooldown_time", "topics", "target_seconds", "yt_dlp_format"
]
missing = [key for key in required_keys if key not in cfg]
if missing:
    raise KeyError(f"Missing keys in config: {missing}")

# ----------------------------- SAFETY PATCH: yt-dlp FORMAT ----------------------------
default_format = "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/b[ext=mp4]"
yt_format = cfg.get("yt_dlp_format", default_format)
if "vcodec~*=" in yt_format:
    print(" Detected outdated yt_dlp_format syntax. Using safe fallback format.")
    yt_format = default_format

cfg["yt_dlp_format"] = yt_format

# ----------------------------- LOAD DATABASE ------------------------------------------

if not os.path.exists(db_path):
    raise FileNotFoundError("videos_db.json not found — run update_db.py first")

with open(db_path, "r", encoding="utf-8") as f:
    data = json.load(f)

# Ensure folders exist
os.makedirs(cfg["download_path"], exist_ok=True)
os.makedirs(cfg["output_path"], exist_ok=True)

# ----------------------------- USER INPUT -------------------------------------------

topics_available = list(cfg["topics"].keys())
print(f"\nAvailable topics: {topics_available}")
video_topic = input("Enter topic to compile: ").strip().lower()
if video_topic not in topics_available:
    raise ValueError(f"'{video_topic}' not in {topics_available}")

try:
    video_count = int(input("How many videos would you like to download & compile? "))
except ValueError:
    video_count = 10

# ----------------------------- SELECT VIDEOS ----------------------------------------
def is_on_cooldown(video):
    used_list = video.get("used_in_compilation", [])
    if not used_list:
        return False  

    last_used_str = used_list[-1]  
    try:
        ts_part = last_used_str.split("_compilation_")[-1].replace(".mp4", "")
        last_used_dt = datetime.strptime(ts_part, "%Y-%m-%d_%H-%M-%S")
    except Exception:
        return False  
    return datetime.now() - last_used_dt < timedelta(days=cooldown_time)

topic_videos = [v for v in data if v.get("topic") == video_topic]
topic_videos.sort(key=lambda v: v.get("upload_date") or "", reverse=True)
print(f"\nFound {len(topic_videos)} '{video_topic}' videos in database.")

exclude_words = cfg.get("exclude_from_filler", [])

def is_excluded(v):
    title = (v.get("title") or "").lower()
    return any(bad.lower() in title for bad in exclude_words)


unused_videos = [v for v in topic_videos if not is_on_cooldown(v)]
cooled_videos = [v for v in unused_videos]

used_but_allowed = [v for v in topic_videos if v not in cooled_videos]  # still on cooldown

print(f" Found {len(cooled_videos)} cooled/down videos for topic '{video_topic}'.")

if len(cooled_videos) >= video_count:
    selected_videos = cooled_videos[:video_count]

else:
    needed = video_count - len(cooled_videos)
    selected_videos = cooled_videos + used_but_allowed[:needed]

print(f"\nSelected {len(selected_videos)} videos for topic '{video_topic}'.\n")


print(f"\nSelected {len(selected_videos)} videos for topic '{video_topic}'.\n")

# ----------------------------- DOWNLOAD VIDEOS --------------------------------------

print("Starting downloads...\n")
download_dir = cfg["download_path"]

for i, v in enumerate(selected_videos, 1):
    url = v.get("url")
    title = v.get("title")
    print(f"({i}/{len(selected_videos)}) Downloading: {title}")
    cmd = [
        "yt-dlp",
        "-f", yt_format,
        "-o", os.path.join(download_dir, "%(title).200s [%(id)s].%(ext)s"),
        url,
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f" Download failed for: {title}")

# After downloads, check folder
downloaded_files = [
    os.path.join(download_dir, f) for f in os.listdir(download_dir)
    if f.endswith(".mp4")
]

if not downloaded_files:
    raise RuntimeError(
        " No downloaded files found! Check your yt_dlp_format or network connection."
    )

print("\n All videos downloaded.\n")

# ----------------------------- MERGE VIDEOS (NO RE-ENCODE) --------------------------

final_file_list = []
for file in downloaded_files:
    for v in selected_videos:
        if v["id"] in file:
            final_file_list.append(os.path.abspath(file))
            break

if not final_file_list:
    raise RuntimeError(" No matching files found for selected videos!")

concat_list_path = os.path.join(cfg["output_path"], "concat_list.txt")
with open(concat_list_path, "w", encoding="utf-8") as f:
    for path in final_file_list:
        f.write(f"file '{path}'\n")

timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
output_filename = f"{video_topic}_compilation_{timestamp}.mp4"
output_path = os.path.join(cfg["output_path"], output_filename)

print("Merging videos...\n")
merge_cmd = [
    "ffmpeg", "-hide_banner", "-loglevel", "warning",
    "-f", "concat", "-safe", "0",
    "-i", concat_list_path,
    "-c", "copy", "-movflags", "+faststart",
    output_path
]
result = subprocess.run(merge_cmd)
if result.returncode != 0:
    raise RuntimeError(" FFmpeg merge failed. Possibly mismatched codecs/resolution.")

print(f"\n Compilation created: {output_path}")

# ----------------------------- UPDATE DATABASE --------------------------------------

for v in selected_videos:
    used = v.setdefault("used_in_compilation", [])
    if output_path not in used:
        used.append(output_path)

with open(db_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Database updated — marked {len(selected_videos)} videos as used.\n")
print(" DONE! Your compilation is ready.")
