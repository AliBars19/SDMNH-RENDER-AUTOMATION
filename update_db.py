import os
import json
import yaml

config = "config.yaml"

if not os.path.exists(config):
    raise FileNotFoundError(f"your config file has been moved or deleted")

with open(config,'r') as f:
    cfg = yaml.load(f, Loader=yaml.SafeLoader)

db_path = cfg.get("db_path", "videodb.json")

configkeys = ['channels','download_path','output_path','db_path','topics','target_seconds','yt_dlp_format']
for key in config:
    if key not in config:
        raise KeyError(f"not all keys are in config.yaml, you might have accidentally deleted one")
    


# if os.path.exists(db_path):
#     with open(db_path, 'r', encoding='utf-8') as f:
#         try:
#             data = json.load(f)
#         except:
#             print("data is corrupted or dont exist")
#             data = []
# else:
#     data = []