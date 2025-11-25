# SDMNH Render Automation

A small collection of Python scripts to build a simple workflow that:
- discovers videos from configured YouTube channels and builds a local JSON database (`videodb.json`),
- selects and downloads videos by topic, and
- concatenates them into a single compilation MP4 (using `ffmpeg`).

This repository contains helper scripts and a minimal YouTube upload example.

**Project files**
- `update_db.py`: Scans configured YouTube channels (via `yt-dlp` JSON output), extracts metadata, assigns topics, and writes the database file referenced by `config.yaml`.
- `combine.py`: Interactive script that selects videos by topic from the DB, downloads them with `yt-dlp`, and concatenates them into one MP4 using `ffmpeg`.
- `main.py`: Minimal example code to upload a video to YouTube using OAuth credentials in `client.json` (requires Google API client setup).
- `config.yaml`: Project configuration (channels, paths, topics, formats).
- `videodb.json` (or whatever `db_path` in `config.yaml` points to): The video metadata database produced by `update_db.py`.

**Prerequisites**
- Python 3.8+ installed.
- `ffmpeg` available on your `PATH` (used to concat videos). Install from https://ffmpeg.org/ if missing.
- `yt-dlp` (for metadata and downloading). Install via `pip` or use the system binary.

Python dependencies (install with pip):

```
pip install pyyaml yt-dlp google-api-python-client google-auth-httplib2 google-auth-oauthlib
```

Note: if you do not need YouTube upload functionality, you can omit the Google client packages.

**Quick start**

1. Configure `config.yaml`
   - Ensure `channels` contains the channel URLs you want to scan.
   - `download_path` and `output_path` are directories for downloads and final compilations.
   - `db_path` is the JSON file used to store metadata.
   - `topics` maps topic names to lists of keyword triggers used when assigning topics.

2. Create or update the database:

```
python update_db.py
```

This creates or updates the file named in `db_path` (default: `videodb.json`).

3. Build a compilation:

```
python combine.py
```

Follow the interactive prompts: choose a topic and the number of videos to include. The script will download matching videos into `download_path` and produce a single concatenated MP4 in `output_path`.

4. (Optional) Upload to YouTube

 - Put your OAuth client secret in `client.json` (create credentials in Google Cloud Console, enable YouTube Data API v3).
 - Run:

```
python main.py
```

This example opens a local auth server to obtain credentials and then uploads a hard-coded file path — update the `media_file` and `request_body` in `main.py` to match your file and metadata.

**Configuration details (`config.yaml`)**
- `channels`: array of YouTube channel URLs to scan.
- `download_path`: folder where `yt-dlp` will save videos.
- `output_path`: folder where compiled MP4s will be written.
- `db_path`: path to the JSON database file.
- `topics`: mapping of `topic_name: [keyword, ...]` used to auto-assign topics based on title match.
- `target_seconds`: intended total runtime target for compilations (not currently enforced in `combine.py`).
- `yt_dlp_format`: `yt-dlp` format string used for selecting video + audio streams. If you have download issues, try the default fallback format in `combine.py`.

Example excerpt from the repository `config.yaml`:

```
channels:
  - "https://www.youtube.com/@MoreSidemen"
  - "https://www.youtube.com/@Sidemen"

download_path: "downloads"
output_path: "outputs"
db_path: videodb.json

topics:
  among_us: ["among us"]
  try_not_to_laugh: ["try not to laugh"]
  general: []

target_seconds: 18000
yt_dlp_format: "bv*[vcodec~*=avc1][height=1080]+ba[acodec~*=mp4a]/b[vcodec~*=avc1][height=1080]/b[ext=mp4]"
```

**Important notes & troubleshooting**
- `ffmpeg concat` works without re-encoding only when input files share compatible codecs, pixel formats, and container metadata. If `combine.py` fails during merge with codec errors, re-encoding may be required.
- If no downloaded MP4s appear, check `yt-dlp`'s format string (`yt_dlp_format`) in `config.yaml`. The script falls back to a safe default if it detects outdated syntax.
- `update_db.py` and `combine.py` use slightly different keys in the DB for marking usage (`used in compilation` vs `used_in_compilation`). The inconsistency does not break the basic flow but may create duplicate keys — you can normalize them to a single key name if desired.
- `main.py` demonstrates OAuth flow for YouTube uploads. You must create `client.json` credentials in the Google Cloud Console and enable the YouTube Data API v3. The script will open a browser to authenticate.

**Security & legal**
- This tool downloads and uploads third-party content. Ensure you have the right to download, modify, and upload any videos you process. Respect YouTube's Terms of Service and content owners' copyright.

**Contributing**
- Bug reports and PRs are welcome. Suggested improvements:
  - Normalize DB field names (e.g., `used_in_compilation`).
  - Add CLI flags for non-interactive usage.
  - Add safe re-encoding fallback when concatenation fails.

**Contact**
- Repository: local workspace — `SDMNH-RENDER-AUTOMATION`

**License**
- No license file included. Add a license if you plan to share this project publicly.
