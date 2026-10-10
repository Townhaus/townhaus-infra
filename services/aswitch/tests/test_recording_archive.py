import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from audio_activity import RecordingWriter
from recording_archive import RecordingArchive, make_transfer


class CompletionTests(unittest.TestCase):
    def test_recording_only_becomes_wav_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = RecordingWriter(logging.getLogger("test"), directory, 2, 44100)
            writer._open_wave_file()
            partial = Path(writer.current_file())
            self.assertEqual(partial.suffix, ".part")
            self.assertEqual(list(Path(directory).glob("*.wav")), [])
            audio = np.array([[1000, -1000], [2000, -2000]], dtype=np.int16)
            writer._write_audio(audio.tobytes())
            writer._close_wave_file()
            self.assertFalse(partial.exists())
            with wave.open(str(partial.with_suffix("")), "rb") as recording:
                self.assertEqual(recording.getnchannels(), 2)
                self.assertEqual(recording.getsampwidth(), 2)
                self.assertEqual(recording.getframerate(), 44100)
                self.assertEqual(recording.getnframes(), 2)

    def test_failed_recording_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = RecordingWriter(logging.getLogger("test"), directory, 2, 44100)
            writer._open_wave_file()
            partial = Path(writer.current_file())
            writer._close_wave_file(finalize=False)
            self.assertTrue(partial.exists())
            self.assertEqual(list(Path(directory).glob("*.wav")), [])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.recording = self.directory / "audio-20261010-143000-123456.wav"
        self.recording.write_bytes(b"completed recording")
        self.transfer = Mock()
        self.publish = Mock()
        self.archive = RecordingArchive(
            self.directory,
            "/srv/storage/archive/recordings/vinyl",
            self.transfer,
            self.publish,
        )

    def test_archives_completed_files_and_does_not_repeat_success(self):
        (self.directory / "audio-20261010-150000.wav.part").write_bytes(b"still recording")
        self.assertTrue(self.archive.run())
        self.assertTrue(self.archive.run())
        self.transfer.assert_called_once_with(self.recording)
        self.assertTrue(self.recording.exists())
        self.assertEqual(self.publish.call_args.args[0], "ready")

    def test_failed_transfer_retains_original_and_retries(self):
        self.transfer.side_effect = RuntimeError("connection interrupted")
        with self.assertLogs("recording_archive", level="ERROR"):
            self.assertFalse(self.archive.run())
        self.assertTrue(self.recording.exists())
        self.assertFalse(self.archive.state_path.exists())
        self.assertEqual(self.publish.call_args.args[0], "error")
        self.transfer.side_effect = None
        self.assertTrue(self.archive.run())
        self.assertEqual(self.transfer.call_count, 2)

    def test_retention_starts_at_success_not_recording_age(self):
        with patch("recording_archive.time.time", return_value=1000000):
            self.archive.run()
        with patch("recording_archive.time.time", return_value=1000000 + 6 * 86400):
            self.archive.run()
        self.assertTrue(self.recording.exists())
        with patch("recording_archive.time.time", return_value=1000000 + 7 * 86400):
            self.archive.run()
        self.assertFalse(self.recording.exists())
        self.transfer.assert_called_once()

    def test_changed_local_file_must_be_archived_again_before_cleanup(self):
        with patch("recording_archive.time.time", return_value=1000000):
            self.archive.run()
        self.recording.write_bytes(b"changed recording content")
        with patch("recording_archive.time.time", return_value=1000000 + 8 * 86400):
            self.archive.run()
        self.assertTrue(self.recording.exists())
        self.assertEqual(self.transfer.call_count, 2)

    def test_changed_archive_target_requires_a_new_success_before_cleanup(self):
        with patch("recording_archive.time.time", return_value=1000000):
            self.archive.run()
        self.archive.target_id = "replacement-server:/archive"
        with patch("recording_archive.time.time", return_value=1000000 + 8 * 86400):
            self.archive.run()
        self.assertTrue(self.recording.exists())
        self.assertEqual(self.transfer.call_count, 2)

    def test_damaged_receipts_never_trigger_deletion(self):
        self.archive.state_path.write_text("{bad json")
        with self.assertRaises(json.JSONDecodeError):
            self.archive.run()
        self.assertTrue(self.recording.exists())
        self.transfer.assert_not_called()

    def test_file_changed_during_transfer_has_no_success_receipt(self):
        self.transfer.side_effect = lambda path: path.write_bytes(b"changed")
        with self.assertLogs("recording_archive", level="ERROR"):
            self.assertFalse(self.archive.run())
        self.assertFalse(self.archive.state_path.exists())
        self.assertTrue(self.recording.exists())

    def test_old_unarchived_recording_is_never_cleaned_on_failure(self):
        self.transfer.side_effect = RuntimeError("offline")
        with patch("recording_archive.time.time", return_value=9999999999):
            with self.assertLogs("recording_archive", level="ERROR"):
                self.assertFalse(self.archive.run())
        self.assertTrue(self.recording.exists())


class TransferTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("rsync"), "rsync must be installed")
    def test_real_rsync_transfer_and_receipt_with_local_ssh_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "recordings"
            source.mkdir()
            destination = root / "archive"
            destination.mkdir()
            recording = source / "audio-20261010-143000.wav"
            recording.write_bytes(os.urandom(16384))
            binaries = root / "bin"
            binaries.mkdir()
            # Execute rsync's remote server locally, exercising the real protocol
            # and permissions without requiring SSH keys or a running server.
            ssh = binaries / "ssh"
            ssh.write_text(
                '#!/bin/sh\nwhile [ "$1" != "archive.test" ]; do shift; done\nshift\nexec "$@"\n'
            )
            ssh.chmod(0o700)
            transfer = make_transfer("archive.test", "saegey", str(destination), "/key", "/hosts")
            archive = RecordingArchive(source, str(destination), transfer, Mock())
            with patch.dict(os.environ, {"PATH": f"{binaries}:{os.environ['PATH']}"}):
                self.assertTrue(archive.run())
            archived = destination / recording.name
            self.assertEqual(archived.read_bytes(), recording.read_bytes())
            self.assertEqual(archived.stat().st_mode & 0o777, 0o660)
            self.assertIn(recording.name, json.loads(archive.state_path.read_text()))
            self.assertTrue(recording.exists())

    def test_ssh_pinning_permissions_and_resumable_partial_transfer(self):
        transfer = make_transfer(
            "192.168.2.151",
            "saegey",
            "/srv/storage/archive/recordings/vinyl",
            "/tmp/archive-key",
            "/tmp/archive-known-hosts",
        )
        with patch("recording_archive.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            transfer(Path("/tmp/audio-20261010-143000.wav"))
        command = run.call_args.args[0]
        self.assertIn("--partial-dir=.rsync-partial", command)
        self.assertIn("--chmod=F660", command)
        self.assertIn("StrictHostKeyChecking=yes", command[command.index("-e") + 1])
        self.assertTrue(command[-1].endswith("/recordings/vinyl/"))

    def test_transfer_error_is_not_success(self):
        transfer = make_transfer("beelink", "saegey", "/archive", "/key", "/known-hosts")
        with patch("recording_archive.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 23, "", "permission denied")
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                transfer(Path("/tmp/audio-20261010-143000.wav"))


if __name__ == "__main__":
    unittest.main()
