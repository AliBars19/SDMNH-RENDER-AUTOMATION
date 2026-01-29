# SDMNH Render Automation

An automated workflow system for discovering, downloading, and compiling YouTube videos into themed MP4 compilations. This project streamlines the process of building video compilations from multiple YouTube channels with intelligent topic-based filtering and video management.

## Overview

The project implements a three-stage workflow:

1. **Database Management**: Scan YouTube channels and build a SQLite database of video metadata with automatic topic classification
2. **Selective Downloading**: Choose videos by topic with cooldown-based rotation to ensure variety
3. **Video Compilation**: Concatenate selected videos into a single MP4 using FFmpeg

## Project Structure

```
SDMNH-RENDER-AUTOMATION/
├── combine.py              # Interactive script to select, download, and compile videos
├── update_db.py            # Scans channels and updates the database
├── config/
│   └── config.yaml         # Configuration file (channels, paths, topics, settings)
├── data/
│   ├── downloads/          # Temporary storage for downloaded videos
│   ├── outputs/            # Final compiled MP4 files
│   └── videos.db           # SQLite database with video metadata
├── src/
│   ├── __init__.py
│   ├── database.py         # SQLAlchemy models and database utilities
│   └── __pycache__/
└── README.md               # This file
```

## Features

- **Automatic Channel Scanning**: Uses `yt-dlp` to fetch video metadata from configured YouTube channels
- **Smart Topic Assignment**: Auto-categorizes videos based on title keyword matching
- **Cooldown System**: Prevents recently used videos from being selected again (configurable days)
- **Parallel Downloads**: Concurrent video downloading with retry logic and anti-403 protection
- **Database Persistence**: SQLite database tracks video metadata, compilations, and usage history
- **Rich CLI**: Progress bars and styled console output for better user experience
- **Flexible Video Selection**: Interactive prompts to choose topic and video count per compilation

## Prerequisites

### System Requirements
- **Python 3.8+**
- **FFmpeg**: Required for video concatenation. Install from [ffmpeg.org](https://ffmpeg.org/)
- **yt-dlp**: For YouTube channel scanning and video downloading

### Python Dependencies

Install required packages:

```bash
pip install pyyaml yt-dlp google-api-python-client google-auth-httplib2 google-auth-oauthlib sqlalchemy
```

**Note**: Google client packages are only needed if you plan to use the YouTube upload functionality. Core functionality works without them.

## Installation & Setup

### 1. Clone or Download the Repository

```bash
git clone <repository-url>
cd SDMNH-RENDER-AUTOMATION
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt  # if available
# OR manually install packages listed above
```

### 3. Configure `config.yaml`

Edit `config/config.yaml` to match your setup:

```yaml
channels:
  - "https://www.youtube.com/@MoreSidemen"
  - "https://www.youtube.com/@Sidemen"

download_path: "data/downloads"
output_path: "data/outputs"
db_path: "data/videos.db"

cooldown_days: 30

topics:
  among_us: ["among us"]
  try_not_to_laugh: ["try not to laugh", "tntl"]
  mukbang: ["mukbang", "eating"]
  hide_and_seek: ["hide and seek", "hide & seek"]
  general: []

# Download settings
max_concurrent_downloads: 3
retry_attempts: 3
download_timeout: 3600

yt_dlp_format: "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/b[ext=mp4]"
```

**Configuration Options**:
- **channels**: Array of YouTube channel URLs to scan
- **download_path**: Directory where videos are temporarily saved
- **output_path**: Directory where compiled MP4s are written
- **db_path**: SQLite database file location
- **cooldown_days**: Minimum days before a video can be used again
- **topics**: Mapping of topic names to keyword triggers for auto-classification
- **max_concurrent_downloads**: Number of parallel downloads (recommended: 2-5)
- **retry_attempts**: Number of retry attempts for failed downloads
- **download_timeout**: Timeout in seconds for individual downloads
- **yt_dlp_format**: FFmpeg format string for video/audio stream selection

## Usage

### Step 1: Update the Video Database

Scan configured YouTube channels and update the database:

```bash
python update_db.py
```

This script:
- Connects to each channel and fetches metadata
- Filters videos (skips Shorts, older than 2020)
- Auto-assigns topics based on title keywords
- Stores everything in the SQLite database
- Skips videos already in the database

### Step 2: Create a Video Compilation

Generate a themed video compilation interactively:

```bash
python combine.py
```

The script will:
1. Display available topics
2. Prompt you to select a topic
3. Ask how many videos to include
4. Download selected videos (with cooldown consideration)
5. Concatenate them using FFmpeg
6. Save the final MP4 to `output_path`

## Database Schema

The SQLite database uses three main entities:

### Videos Table
- `id`: Primary key
- `youtube_id`: YouTube video ID (unique)
- `title`: Video title
- `url`: YouTube video URL
- `duration`: Video duration in seconds
- `upload_date`: Upload date (YYYY-MM-DD format)
- `channel`: Channel name
- `topic`: Assigned topic
- `created_at`: When the video was added to database

### Compilations Table
- `id`: Primary key
- `topic`: Topic of the compilation
- `filename`: Output MP4 filename
- `video_count`: Number of videos in compilation
- `created_at`: Compilation creation timestamp

### compilation_videos Table (Junction)
- Links videos to compilations for usage tracking
- Enables cooldown filtering

## Troubleshooting

### FFmpeg Errors During Concatenation
FFmpeg's concat demuxer requires all input files to have compatible codecs, pixel formats, and container metadata. If concatenation fails:
- Ensure all downloaded videos use the same codec (verify with `ffprobe`)
- Check that `yt_dlp_format` is valid for the YouTube source quality
- Enable re-encoding in `combine.py` if needed (slower but always works)

### 403 Forbidden Errors
If downloads fail with 403 errors:
- Update `yt-dlp` to the latest version: `pip install --upgrade yt-dlp`
- The script includes anti-403 headers and client rotation
- Reduce `max_concurrent_downloads` to 1-2 if issues persist

### No Videos Download
- Verify `yt_dlp_format` in `config.yaml` is valid
- Test format manually: `yt-dlp -f "format-string" [video-url]`
- Check that channels are correct and publicly accessible
- Review console output for specific errors

### Database Issues
- Delete `data/videos.db` to start fresh (loses all metadata)
- Check that `db_path` directory exists and is writable
- Use SQLite CLI to inspect: `sqlite3 data/videos.db`.

## Advanced Configuration

### Custom Video Format Selection
Adjust `yt_dlp_format` to control download quality:
- `bv*[height<=1080]`: Best video ≤1080p
- `ba[acodec^=mp4a]`: Best audio with MP4A codec
- `b[ext=mp4]`: Best fallback MP4 overall

### Parallel Download Tuning
- **High speed internet**: Increase `max_concurrent_downloads` to 5-10
- **Limited bandwidth**: Set to 1-2
- **Stability issues**: Reduce by 1-2

### Video Filtering
To filter videos further, modify `update_db.py`:
- Minimum duration: Add duration check in `scrape_channel()`
- Channel whitelist: Restrict `channels` list in config
- Date range: Adjust year check in `scrape_channel()`

## Project Files Reference

### [combine.py](combine.py)
- Main user-facing script for creating compilations
- Handles video selection with cooldown logic
- Manages parallel downloads with progress tracking
- Concatenates videos using FFmpeg

### [update_db.py](update_db.py)
- Scans YouTube channels using `yt-dlp`
- Assigns topics based on keyword matching
- Updates SQLite database with new videos
- Validates and filters video metadata

### [src/database.py](src/database.py)
- SQLAlchemy ORM models (Video, Compilation)
- Database connection and session management
- Junction table for video-compilation relationships

### [config/config.yaml](config/config.yaml)
- Centralized configuration for all scripts
- Channel sources and path definitions
- Topic keywords and download settings

## Performance Tips

- **First run**: Initial database scan may take 10-30 minutes depending on channel size
- **Subsequent runs**: Only fetches new videos, typically faster
- **Large compilations**: Downloading and concatenating 30+ videos takes time; monitor progress
- **Disk space**: Ensure `download_path` has sufficient space (videos are retained after compilation)

## Security & Legal Considerations

- Respect YouTube's Terms of Service and channel creators' content policies
- Downloaded videos are for personal use; respect copyright and licensing
- Do not re-distribute compilations without proper permissions
- Keep `client.json` credentials private if using YouTube upload features
- Review channel content before automated scraping

## Future Enhancements

Potential improvements:
- Web UI for easier topic and video selection
- Automatic compilation scheduling (cron-based)
- Machine learning-based topic classification
- Video preview/thumbnail extraction
- Direct YouTube upload integration
- Support for multiple video sources (not just YouTube)

## License

This project is provided as-is for personal use. Ensure compliance with YouTube's Terms of Service and respect content creators' rights.

## Support & Contributions

For issues or suggestions, please review:
1. Configuration file for correctness
2. FFmpeg and yt-dlp installation and versions
3. Python version compatibility (3.8+)
4. Disk space and internet connectivity

Contributions are welcome! Feel free to fork and submit improvements.
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
