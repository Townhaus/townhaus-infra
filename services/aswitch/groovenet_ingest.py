"""Continuous, spool-backed audio capture for the Groovenet ingest endpoint."""

import json
import logging
import os
import re
import threading
import time
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, request

import numpy as np

INGEST_URL = os.environ.get("GROOVENET_INGEST_URL", "")
SOURCE_ID = os.environ.get("GROOVENET_SOURCE_ID", "aswitch")
SPOOL_DIR = Path(os.environ.get("GROOVENET_SPOOL_DIR", "./groovenet-spool"))
CHUNK_SECONDS = int(os.environ.get("GROOVENET_CHUNK_SECONDS", "15"))
HTTP_TIMEOUT_SECONDS = float(os.environ.get("GROOVENET_HTTP_TIMEOUT_SECONDS", "30"))
RETRY_MIN_SECONDS = float(os.environ.get("GROOVENET_RETRY_MIN_SECONDS", "2"))
RETRY_MAX_SECONDS = float(os.environ.get("GROOVENET_RETRY_MAX_SECONDS", "300"))
MAX_UNRECOGNIZED_ATTEMPTS = int(os.environ.get("GROOVENET_MAX_UNRECOGNIZED_ATTEMPTS", "10"))
WAV_SAMPLE_WIDTH_BYTES = 2

# Groovenet's ingest contract (groovenet#1227): these codes mean the chunk itself
# is bad and will never be accepted, so it is safe to delete. Everything else,
# including 408 upload_incomplete, is retried.
PERMANENT_REJECTIONS = {
    400: frozenset(
        {
            "missing_audio_file",
            "missing_source_id",
            "unsupported_audio_format",
            "invalid_audio_stream",
            "audio_too_short",
            "audio_too_long",
        }
    ),
    413: frozenset({"audio_too_large"}),
}

ACCEPTED = "accepted"
DISCARD = "discard"
RETRY = "retry"
UNRECOGNIZED = "unrecognized"

CHUNK_NAME = re.compile(
    r"^(?P<captured_at>\d{8}T\d{12}Z)_(?P<session_id>[0-9a-f-]{36})_(?P<sequence>\d+)\.wav$"
)


def error_code(body):
    """The `error` field of a JSON error body, or None when there isn't one."""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return None
    code = payload.get("error") if isinstance(payload, dict) else None
    return code if isinstance(code, str) else None


def classify_response(status, body):
    """Decide what to do with a chunk after the server answered `status`."""
    if 200 <= status < 300:
        return ACCEPTED
    if error_code(body) in PERMANENT_REJECTIONS.get(status, ()):
        return DISCARD
    if 400 <= status < 500 and status not in (408, 429):
        # A client error the contract doesn't name: an unknown code, a body that
        # isn't JSON (a proxy's error page), or a known code on the wrong status.
        return UNRECOGNIZED
    return RETRY


class GroovenetIngest:
    """Writes exact mono WAV windows and uploads completed windows in order."""

    def __init__(self, sample_rate, channels, logger=None):
        if not INGEST_URL:
            raise ValueError("GROOVENET_INGEST_URL must be configured")
        if CHUNK_SECONDS < 10 or CHUNK_SECONDS > 60:
            raise ValueError("GROOVENET_CHUNK_SECONDS must be between 10 and 60")

        self.sample_rate = sample_rate
        self.channels = channels
        self.logger = logger or logging.getLogger("groovenet_ingest")
        self.frames_per_chunk = sample_rate * CHUNK_SECONDS
        self.session_id = str(uuid.uuid4())
        self.sequence = 0
        self.frames_written = 0
        self.wave_file = None
        self.partial_path = None
        self.final_path = None
        self.write_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.unrecognized_attempts = {}
        self.uploader = threading.Thread(
            target=self._upload_loop, name="groovenet-uploader", daemon=True
        )

    def start(self):
        SPOOL_DIR.mkdir(parents=True, exist_ok=True)
        self.uploader.start()
        self.logger.info(
            "Groovenet ingest enabled: source=%s chunk_seconds=%s spool=%s session=%s",
            SOURCE_ID,
            CHUNK_SECONDS,
            SPOOL_DIR,
            self.session_id,
        )

    def write(self, audio_frames):
        """Accept an int16 frame array, downmix it, and finalize exact windows."""
        with self.write_lock:
            mono_frames = self._downmix(audio_frames)
            position = 0
            while position < len(mono_frames):
                if self.wave_file is None:
                    self._open_chunk()
                remaining = self.frames_per_chunk - self.frames_written
                part = mono_frames[position : position + remaining]
                self.wave_file.writeframes(part.tobytes())
                frame_count = len(part)
                self.frames_written += frame_count
                position += frame_count
                if self.frames_written == self.frames_per_chunk:
                    self._finalize_chunk()

    def pause(self):
        """Discard an incomplete window before an intentional capture gap."""
        with self.write_lock:
            if self.wave_file is not None:
                self.logger.info("Discarding incomplete Groovenet chunk after audio became inactive")
                self._discard_partial_chunk()

    def stop(self):
        self.stop_event.set()
        with self.write_lock:
            self._discard_partial_chunk()
        self.uploader.join(timeout=5)

    def _downmix(self, audio_frames):
        frames = np.asarray(audio_frames, dtype=np.int16)
        if frames.ndim == 1:
            return frames
        if frames.shape[1] == 1:
            return frames[:, 0]
        # Sum in int32 to avoid overflow before converting the average to int16.
        return (frames.astype(np.int32).sum(axis=1) // frames.shape[1]).astype(np.int16)

    def _open_chunk(self):
        captured_at = datetime.now(UTC)
        timestamp = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
        basename = f"{timestamp}_{self.session_id}_{self.sequence:012d}.wav"
        self.final_path = SPOOL_DIR / basename
        self.partial_path = SPOOL_DIR / f".{basename}.part"
        self.wave_file = wave.open(str(self.partial_path), "wb")
        self.wave_file.setnchannels(1)
        self.wave_file.setsampwidth(WAV_SAMPLE_WIDTH_BYTES)
        self.wave_file.setframerate(self.sample_rate)
        self.frames_written = 0

    def _finalize_chunk(self):
        self.wave_file.close()
        self.wave_file = None
        self.partial_path.replace(self.final_path)
        self.logger.info(
            "Queued Groovenet chunk: file=%s sequence=%s captured_at=%s",
            self.final_path.name,
            self.sequence,
            self._metadata_for(self.final_path)["captured_at"],
        )
        self.sequence += 1
        self.partial_path = None
        self.final_path = None

    def _discard_partial_chunk(self):
        if self.wave_file is not None:
            self.wave_file.close()
            self.wave_file = None
        if self.partial_path is not None:
            self.partial_path.unlink(missing_ok=True)
        self.partial_path = None
        self.final_path = None

    def _upload_loop(self):
        delay = RETRY_MIN_SECONDS
        while not self.stop_event.is_set():
            chunk = self._next_chunk()
            if chunk is None:
                self.stop_event.wait(1)
                continue
            try:
                status, body = self._upload(chunk)
            except (OSError, error.URLError, TimeoutError) as exc:
                self.logger.warning("Groovenet upload failed for %s: %s", chunk.name, exc)
                self.stop_event.wait(delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)
                continue

            outcome = classify_response(status, body)
            if outcome == ACCEPTED:
                chunk.unlink(missing_ok=True)
                self.unrecognized_attempts.pop(chunk.name, None)
                self.logger.info("Groovenet accepted %s (status=%s)", chunk.name, status)
                delay = RETRY_MIN_SECONDS
                continue
            if outcome == DISCARD:
                chunk.unlink(missing_ok=True)
                self.unrecognized_attempts.pop(chunk.name, None)
                self.logger.error(
                    "Discarding permanently rejected Groovenet chunk %s (status=%s body=%s)",
                    chunk.name,
                    status,
                    body[:500],
                )
                delay = RETRY_MIN_SECONDS
                continue
            if outcome == UNRECOGNIZED and self._give_up_on(chunk, status, body):
                delay = RETRY_MIN_SECONDS
                continue

            self.logger.warning(
                "Groovenet will retry %s (status=%s body=%s)",
                chunk.name,
                status,
                body[:500],
            )
            self.stop_event.wait(delay)
            delay = min(delay * 2, RETRY_MAX_SECONDS)

    def _give_up_on(self, chunk, status, body):
        """Count an unrecognized rejection; after too many, move the chunk aside.

        Uploads go in order, so a chunk the server keeps refusing for a reason we
        don't understand would block every later chunk. It is kept in
        `rejected/` rather than deleted, so it can be inspected or replayed.
        """
        attempts = self.unrecognized_attempts.get(chunk.name, 0) + 1
        self.unrecognized_attempts[chunk.name] = attempts
        if attempts < MAX_UNRECOGNIZED_ATTEMPTS:
            return False
        rejected_dir = SPOOL_DIR / "rejected"
        rejected_dir.mkdir(parents=True, exist_ok=True)
        chunk.replace(rejected_dir / chunk.name)
        self.unrecognized_attempts.pop(chunk.name, None)
        self.logger.error(
            "Moved Groovenet chunk %s to %s after %s unrecognized rejections "
            "(status=%s body=%s)",
            chunk.name,
            rejected_dir,
            attempts,
            status,
            body[:500],
        )
        return True

    def _next_chunk(self):
        for chunk in sorted(SPOOL_DIR.glob("*.wav")):
            if CHUNK_NAME.match(chunk.name):
                return chunk
            self.logger.warning("Ignoring unrecognized Groovenet spool file: %s", chunk)
        return None

    def _upload(self, chunk):
        metadata = self._metadata_for(chunk)
        boundary = f"----groovenet-{uuid.uuid4().hex}"
        fields = {
            "source_id": SOURCE_ID,
            "session_id": metadata["session_id"],
            "sequence": str(metadata["sequence"]),
            "captured_at": metadata["captured_at"],
        }
        body = self._multipart_body(boundary, fields, chunk)
        upload_request = request.Request(
            INGEST_URL,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with request.urlopen(upload_request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                return response.status, response.read().decode(errors="replace")
        except error.HTTPError as exc:
            return exc.code, exc.read().decode(errors="replace")

    def _metadata_for(self, chunk):
        match = CHUNK_NAME.match(chunk.name)
        if match is None:
            raise ValueError(f"Invalid Groovenet spool filename: {chunk.name}")
        captured_at = datetime.strptime(match["captured_at"], "%Y%m%dT%H%M%S%fZ").replace(
            tzinfo=UTC
        )
        return {
            "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
            "session_id": match["session_id"],
            "sequence": int(match["sequence"]),
        }

    @staticmethod
    def _multipart_body(boundary, fields, chunk):
        lines = []
        for name, value in fields.items():
            lines.extend(
                [
                    f"--{boundary}\r\n".encode(),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                    f"{value}\r\n".encode(),
                ]
            )
        lines.extend(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="audio"; filename="chunk.wav"\r\n',
                b"Content-Type: audio/wav\r\n\r\n",
                chunk.read_bytes(),
                b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
        )
        return b"".join(lines)
