import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


class RecordingPreflightTests(unittest.TestCase):
    def setUp(self):
        project = Path(__file__).resolve().parents[3]
        plays = yaml.safe_load((project / "ansible/deploy-vinyl-live.yml").read_text())
        task = next(
            task
            for task in plays[1]["tasks"]
            if task["name"] == "Check that a recording is not currently open"
        )
        self.script = task["ansible.builtin.command"]["argv"][-1]
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.process = self.root / "proc/123"
        (self.process / "fd").mkdir(parents=True)
        self.recordings = self.root / "app/recordings"
        self.recordings.mkdir(parents=True)
        (self.process / "environ").write_bytes(f"RECORDINGS_DIR={self.recordings}\0".encode())

    def check(self):
        script = self.script.replace(
            "pathlib.Path('/proc')", f"pathlib.Path({str(self.root / 'proc')!r})"
        )
        with patch.object(subprocess, "check_output", return_value="123\n"):
            with self.assertRaises(SystemExit) as result:
                exec(compile(script, "recording-preflight", "exec"), {})
        return result.exception.code

    def test_groovenet_analysis_window_does_not_block_deployment(self):
        (self.process / "fd/1").symlink_to(self.root / "app/groovenet-spool/.analysis.wav.part")
        self.assertEqual(self.check(), 0)

    def test_active_recording_blocks_capture_restart(self):
        (self.process / "fd/1").symlink_to(self.recordings / "audio-20261015-120000.wav.part")
        self.assertEqual(self.check(), 1)

    def test_other_directory_with_same_prefix_does_not_block(self):
        (self.process / "fd/1").symlink_to(self.root / "app/recordings-other/audio.wav.part")
        self.assertEqual(self.check(), 0)

    def test_relative_recording_directory_uses_capture_working_directory(self):
        (self.process / "environ").write_bytes(b"RECORDINGS_DIR=./recordings\0")
        (self.process / "cwd").symlink_to(self.root / "app", target_is_directory=True)
        (self.process / "fd/1").symlink_to(self.recordings / "audio.wav.part")
        self.assertEqual(self.check(), 1)

    def test_completed_wav_does_not_block_deployment(self):
        (self.process / "fd/1").symlink_to(self.recordings / "audio.wav")
        self.assertEqual(self.check(), 0)


if __name__ == "__main__":
    unittest.main()
