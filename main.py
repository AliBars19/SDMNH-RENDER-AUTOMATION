import os
import yt_dlp
import ffmpeg
import yaml
import json

config_file = "config.yaml"

#-------------------------------LOAD CONFIG FILE & DATABASE ----------------------------------------------------------------------

if not os.path.exists(config_file):
    raise FileNotFoundError(f"your config file has been moved or deleted")

with open(config_file,'r') as f:
    cfg = yaml.safe_load(f)

db_path = cfg.get("db_path", "videodb.json")

required_keys = ['channels', 'download_path', 'output_path', 'db_path', 'topics', 'target_seconds', 'yt_dlp_format']
missing = [key for key in required_keys if key not in cfg ]

if missing:
    raise KeyError("missing key bro ")

if os.path.exists(db_path):
    with open(db_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
else:
    data = []


video_topic = str(input("Enter available video topics " + required_keys))