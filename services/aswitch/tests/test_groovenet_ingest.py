import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import groovenet_ingest as ingest

CHUNK = "20261117T045658690651Z_869cfb02-fe6e-4d85-a5c4-d7667a2e6aa5_000000000056.wav"


def body(code):
    return json.dumps({"error": code, "message": "x"})


class ClassifyResponseTests(unittest.TestCase):
    def test_success_is_accepted(self):
        for status in (200, 202):
            self.assertEqual(ingest.classify_response(status, "{}"), ingest.ACCEPTED)

    def test_permanent_codes_are_discarded(self):
        for code in sorted(ingest.PERMANENT_REJECTIONS[400]):
            with self.subTest(code=code):
                self.assertEqual(ingest.classify_response(400, body(code)), ingest.DISCARD)
        self.assertEqual(
            ingest.classify_response(413, body("audio_too_large")), ingest.DISCARD
        )

    def test_incomplete_upload_is_retried(self):
        # groovenet#1227: a stalled or truncated upload, not a bad chunk.
        self.assertEqual(
            ingest.classify_response(408, body("upload_incomplete")), ingest.RETRY
        )

    def test_rate_limit_and_server_errors_are_retried(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(ingest.classify_response(status, ""), ingest.RETRY)

    def test_unknown_or_unreadable_client_errors_are_unrecognized(self):
        cases = [
            (400, body("something_new")),
            (400, "<html>Bad Request</html>"),
            (400, ""),
            (400, json.dumps(["missing_audio_file"])),
            (404, body("missing_audio_file")),
            (413, body("missing_audio_file")),
            (401, body("unauthorized")),
        ]
        for status, text in cases:
            with self.subTest(status=status, body=text):
                self.assertEqual(ingest.classify_response(status, text), ingest.UNRECOGNIZED)


class UploadLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.spool = Path(self.tmp.name)
        patches = [
            patch.object(ingest, "INGEST_URL", "http://groovenet.test/api/audio/ingest"),
            patch.object(ingest, "SPOOL_DIR", self.spool),
            patch.object(ingest, "RETRY_MIN_SECONDS", 0),
            patch.object(ingest, "MAX_UNRECOGNIZED_ATTEMPTS", 3),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.chunk = self.spool / CHUNK
        self.chunk.write_bytes(b"RIFF")
        self.uploader = ingest.GroovenetIngest(sample_rate=44100, channels=2)

    def run_responses(self, *responses):
        """Run the upload loop over `responses`, stopping after the last one."""
        remaining = list(responses)

        def upload(_chunk):
            status, text = remaining.pop(0)
            if not remaining:
                self.uploader.stop_event.set()
            return status, text

        with patch.object(self.uploader, "_upload", side_effect=upload) as mock_upload:
            self.uploader._upload_loop()
        return mock_upload.call_count

    def test_accepted_chunk_is_deleted(self):
        self.run_responses((202, "{}"))
        self.assertFalse(self.chunk.exists())

    def test_permanent_rejection_is_deleted(self):
        self.run_responses((400, body("invalid_audio_stream")))
        self.assertFalse(self.chunk.exists())

    def test_incomplete_upload_is_kept_and_retried_until_accepted(self):
        calls = self.run_responses(
            (408, body("upload_incomplete")), (503, ""), (202, "{}")
        )
        self.assertEqual(calls, 3)
        self.assertFalse(self.chunk.exists())

    def test_retryable_response_keeps_the_chunk(self):
        self.run_responses((408, body("upload_incomplete")))
        self.assertTrue(self.chunk.exists())

    def test_network_error_keeps_the_chunk(self):
        def upload(_chunk):
            self.uploader.stop_event.set()
            raise TimeoutError("The write operation timed out")

        with patch.object(self.uploader, "_upload", side_effect=upload):
            self.uploader._upload_loop()
        self.assertTrue(self.chunk.exists())

    def test_unrecognized_rejection_is_kept_until_the_cap(self):
        self.run_responses((400, body("something_new")), (400, body("something_new")))
        self.assertTrue(self.chunk.exists())
        self.assertEqual(self.uploader.unrecognized_attempts[CHUNK], 2)

    def test_unrecognized_rejection_is_moved_aside_after_the_cap(self):
        self.run_responses(*[(400, "<html>Bad Request</html>")] * 3)
        self.assertFalse(self.chunk.exists())
        self.assertTrue((self.spool / "rejected" / CHUNK).exists())
        self.assertNotIn(CHUNK, self.uploader.unrecognized_attempts)
        # The rejected folder is out of the upload queue.
        self.assertIsNone(self.uploader._next_chunk())

    def test_accepting_clears_the_unrecognized_count(self):
        self.run_responses((400, body("something_new")), (202, "{}"))
        self.assertEqual(self.uploader.unrecognized_attempts, {})


if __name__ == "__main__":
    unittest.main()
