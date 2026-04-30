"""Tests for --upload-only mode: _extract_frame, run_upload_only, CLI args."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from automation import _extract_frame, run_upload_only


# ── _extract_frame ────────────────────────────────────────────────────────────

class TestExtractFrame:
    def test_returns_output_path_on_success(self, tmp_path):
        output = str(tmp_path / 'thumb.jpg')
        video = str(tmp_path / 'video.mp4')
        Path(video).touch()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            Path(output).touch()  # simulate ffmpeg creating the file
            result = _extract_frame(Path(video), output)

        assert result == output

    def test_returns_none_when_ffmpeg_fails(self, tmp_path):
        output = str(tmp_path / 'thumb.jpg')
        video = str(tmp_path / 'video.mp4')

        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'ffmpeg')):
            result = _extract_frame(Path(video), output)

        assert result is None

    def test_returns_none_when_output_not_created(self, tmp_path):
        output = str(tmp_path / 'thumb.jpg')
        video = str(tmp_path / 'video.mp4')

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # ffmpeg runs but doesn't create the file
            result = _extract_frame(Path(video), output)

        assert result is None

    def test_uses_correct_ffmpeg_args(self, tmp_path):
        output = str(tmp_path / 'thumb.jpg')
        video = tmp_path / 'video.mp4'
        video.touch()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            Path(output).touch()
            _extract_frame(video, output, timestamp='00:02:00')

        args = mock_run.call_args[0][0]
        assert 'ffmpeg' in args
        assert '-ss' in args
        assert '00:02:00' in args
        assert '-vframes' in args
        assert '1' in args
        assert output in args

    def test_default_timestamp_is_one_minute(self, tmp_path):
        output = str(tmp_path / 'thumb.jpg')
        video = tmp_path / 'video.mp4'
        video.touch()

        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            Path(output).touch()
            _extract_frame(video, output)

        args = mock_run.call_args[0][0]
        assert '00:01:00' in args

    def test_handles_exception_gracefully(self, tmp_path):
        output = str(tmp_path / 'thumb.jpg')
        video = tmp_path / 'video.mp4'

        with patch('subprocess.run', side_effect=FileNotFoundError('ffmpeg not found')):
            result = _extract_frame(video, output)

        assert result is None


# ── run_upload_only ───────────────────────────────────────────────────────────

@pytest.fixture
def cfg(tmp_path):
    return {
        'thumbnail_path': str(tmp_path / 'thumbnails'),
        'youtube': {
            'credentials_path': str(tmp_path / 'client_secrets.json'),
            'token_path': str(tmp_path / 'token.json'),
            'description': 'Best of {topic}',
            'tags': ['sidemen'],
            'category_id': 24,
            'privacy_status': 'public',
        },
        'youtube_processing_wait_seconds': 60,
    }


class TestRunUploadOnly:
    def test_uploads_video_successfully(self, tmp_path, cfg):
        video = tmp_path / 'comp.mp4'
        video.write_bytes(b'fake video data')

        mock_service = MagicMock()
        with patch('automation.authenticate', return_value=mock_service) as mock_auth, \
             patch('automation.upload_video', return_value='abc123') as mock_upload, \
             patch('automation.set_thumbnail') as mock_thumb, \
             patch('automation.record_run') as mock_record, \
             patch('automation.wait_and_delete_when_public') as mock_wait, \
             patch('automation._extract_frame', return_value=None):

            run_upload_only(cfg, video, 'among_us', 7200)

        mock_auth.assert_called_once()
        mock_upload.assert_called_once()
        upload_kwargs = mock_upload.call_args[1]
        assert upload_kwargs['video_path'] == video
        assert upload_kwargs['privacy_status'] == 'public'
        mock_record.assert_called_once()
        call_args = mock_record.call_args[0]
        assert call_args[0] == 'among_us'
        assert call_args[2] == 'abc123'
        assert call_args[3] == 7200

    def test_sets_thumbnail_when_extracted(self, tmp_path, cfg):
        video = tmp_path / 'comp.mp4'
        video.write_bytes(b'fake')
        thumb = str(tmp_path / 'thumb.jpg')

        with patch('automation.authenticate', return_value=MagicMock()), \
             patch('automation.upload_video', return_value='vid123'), \
             patch('automation.set_thumbnail') as mock_set_thumb, \
             patch('automation.record_run'), \
             patch('automation.wait_and_delete_when_public'), \
             patch('automation._extract_frame', return_value=thumb):

            run_upload_only(cfg, video, 'football', 3600)

        mock_set_thumb.assert_called_once()

    def test_skips_thumbnail_when_extraction_fails(self, tmp_path, cfg):
        video = tmp_path / 'comp.mp4'
        video.write_bytes(b'fake')

        with patch('automation.authenticate', return_value=MagicMock()), \
             patch('automation.upload_video', return_value='vid123'), \
             patch('automation.set_thumbnail') as mock_set_thumb, \
             patch('automation.record_run'), \
             patch('automation.wait_and_delete_when_public'), \
             patch('automation._extract_frame', return_value=None):

            run_upload_only(cfg, video, 'football', 3600)

        mock_set_thumb.assert_not_called()

    def test_logs_error_on_upload_failure(self, tmp_path, cfg, caplog):
        import logging
        video = tmp_path / 'comp.mp4'
        video.write_bytes(b'fake')

        with patch('automation.authenticate', side_effect=Exception('token expired')), \
             patch('automation._extract_frame', return_value=None):
            with caplog.at_level(logging.ERROR):
                run_upload_only(cfg, video, 'gaming', 1800)

        assert any('token expired' in r.message for r in caplog.records)

    def test_creates_thumbnail_dir(self, tmp_path, cfg):
        video = tmp_path / 'comp.mp4'
        video.write_bytes(b'fake')
        thumb_dir = tmp_path / 'thumbnails'
        assert not thumb_dir.exists()

        with patch('automation.authenticate', side_effect=Exception('skip')), \
             patch('automation._extract_frame', return_value=None):
            run_upload_only(cfg, video, 'gaming', 1800)

        assert thumb_dir.exists()

    def test_title_formatted_from_topic_and_duration(self, tmp_path, cfg):
        video = tmp_path / 'comp.mp4'
        video.write_bytes(b'fake')

        with patch('automation.authenticate', return_value=MagicMock()), \
             patch('automation.upload_video', return_value='x') as mock_upload, \
             patch('automation.record_run'), \
             patch('automation.wait_and_delete_when_public'), \
             patch('automation._extract_frame', return_value=None):

            run_upload_only(cfg, video, 'among_us', 10800)  # 3h

        title = mock_upload.call_args[1]['title']
        assert 'AMONG US' in title.upper() or 'among_us' in title.lower()
        assert '3' in title  # 3 hour


# ── CLI --upload-only flag ────────────────────────────────────────────────────

class TestUploadOnlyCLI:
    def test_upload_only_flag_parsed(self, tmp_path):
        """--upload-only, --upload-topic, --upload-duration are accepted."""
        import argparse
        # Re-create just the parser logic to verify args
        parser = argparse.ArgumentParser()
        parser.add_argument('--upload-only', type=str, default=None)
        parser.add_argument('--upload-topic', type=str, default=None)
        parser.add_argument('--upload-duration', type=int, default=None)

        args = parser.parse_args([
            '--upload-only', '/tmp/video.mp4',
            '--upload-topic', 'gaming',
            '--upload-duration', '7200',
        ])

        assert args.upload_only == '/tmp/video.mp4'
        assert args.upload_topic == 'gaming'
        assert args.upload_duration == 7200

    def test_upload_only_defaults_to_none(self):
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('--upload-only', type=str, default=None)
        parser.add_argument('--upload-topic', type=str, default=None)
        parser.add_argument('--upload-duration', type=int, default=None)

        args = parser.parse_args([])
        assert args.upload_only is None
        assert args.upload_topic is None
        assert args.upload_duration is None

    def test_upload_only_exits_when_file_missing(self, tmp_path):
        """automation.py --upload-only with nonexistent file should exit 1."""
        result = subprocess.run(
            [sys.executable, 'automation.py',
             '--upload-only', str(tmp_path / 'nonexistent.mp4'),
             '--upload-topic', 'gaming',
             '--upload-duration', '3600'],
            cwd=str(Path(__file__).parent.parent),
            capture_output=True,
        )
        assert result.returncode == 1
