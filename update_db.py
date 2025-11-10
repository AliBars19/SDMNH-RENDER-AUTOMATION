import os
import json
import yaml
import yt_dlp
import subprocess
import datetime

config_file = "config.yaml"

#-------------------------------LOAD CONFIG FILE & KEY CHECKER ----------------------------------------------------------------------

if not os.path.exists(config_file):
    raise FileNotFoundError(f"your config file has been moved or deleted")

with open(config_file,'r') as f:
    cfg = yaml.safe_load(f)

db_path = cfg.get("db_path", "videodb.json")

required_keys = ['channels', 'download_path', 'output_path', 'db_path', 'topics', 'target_seconds', 'yt_dlp_format']
missing = [key for key in required_keys if key not in cfg ]

if missing:
    raise KeyError("missing key bro ")



#---------------------------------- LOAD EXISTING DATABASE IF IT EXISTS-----------------------------------------------------------
#note: if it exists then your updating ur data base, if not (aka ur first time) then ur initialiseing it
if os.path.exists(db_path):
    with open(db_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
else:
    data = []

#---------------------------------FETCH METADATA FROM YOUTUBE (BOTH CHANNELS)---------------------------------------------------------------------
# note: assign each video an id, this is gna help check if the video already exists in DB

bothchannels = []
cutoff_year = 2020

for channel_url in cfg["channels"]: # loop through 2 channels and dump json

    cmd = ["python","-m","yt_dlp","--flat-playlist","--dump-json",channel_url]
    result = subprocess.run(cmd, capture_output=True, text=True)

    for line in result.stdout.splitlines():
        vid_info = json.loads(line)
        url = vid_info.get("url") or vid_info.get("webpage_url") or ""

        if "shorts" in url.lower():
            continue

        print(result.stdout[:500])

        upload_date = vid_info.get("upload_date")
        if upload_date:
            year = int(str(upload_date)[:4])
            if year < cutoff_year:
                continue
            
        bothchannels.append(vid_info)
        

def clean_data(raw):
    url = raw.get("webpage_url") or raw.get("original_url") or raw.get("url")
    return{
        "id": raw.get("id"),
        "title": raw.get("title"),
        "url": raw.get("webpage_url") or raw.get("original_url"),
        "duration": raw.get("duration"),
        "upload_date": raw.get("upload_date"),
        "channel": raw.get("playlist_uploader_id") or raw.get("channel")
    }

clean_videos = [clean_data(v) for v in bothchannels]

print(f"cleaned data for {len(clean_videos)} videos. \n")
#--------------------------------ASSIGN TOPICS TO EACH VIDEO & SAVE DATABASE(.json)-----------------------------------------------------------------------
#note: check id if exists dont add

existing_ids = {video["id"] for video in data if "id" in video}

new_videos = [v for v in clean_videos if v.get("id") not in existing_ids]

def assign_topic(title,topics_cfg):
    title_lower = (title or "").lower()
    for topics_name, keywords in topics_cfg.items():
        if topics_name == "general":
            continue
        for kw in keywords:
            if kw.lower() in title_lower:
                return topics_name
    return "general"

for v in new_videos:
    v["topics"] = assign_topic(v.get("title"), cfg["topics"])
    v["used in compilation"] = []

combined = data + new_videos
combined.sort(key=lambda x: x.get("upload date", ""), reverse=True)   

with open(db_path,'w', encoding='utf-8') as f:
    json.dump(combined, f , ensure_ascii=False, indent=2)

print(f" Added {len(new_videos)} new videos")
print(f" Total videos in DB: {len(combined)}")
topics_count = {}
print(" Topic distribution:")
for t, c in topics_count.items():
    print(f"   - {t}: {c}")
