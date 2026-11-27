"""Reading movie files: metadata and a poster frame, via the `ffprobe` and
`ffmpeg` binaries (see docs/video-support.md).

Read-only by construction -- these functions are handed a path on the *local*
disk (the photos backend's own path when it is a local mount, otherwise a
temp copy sync.py made and will delete) and only ever read it. Nothing here
touches the photos backend.

The binaries are optional: the app must run, and sync must still index a
video's row, without them. `available()` says whether they are on PATH;
`probe`/`poster` raise `VideoToolMissing` when they aren't and `VideoError`
when ffmpeg can't make sense of a file. sync.py catches both and falls back
to metadata-only indexing.
"""

import io
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# Generous: a poster seek into a large remote-backed file can be slow, but
# it must never hang a sync worker forever.
_PROBE_TIMEOUT_SECONDS = 60
_POSTER_TIMEOUT_SECONDS = 120


class VideoToolMissing(RuntimeError):
    """ffmpeg/ffprobe isn't installed."""


class VideoError(RuntimeError):
    """ffmpeg/ffprobe couldn't read the file."""


@dataclass
class VideoInfo:
    duration: Optional[float] = None
    width: Optional[int] = None  # display size: rotation already applied
    height: Optional[int] = None
    created_at: Optional[datetime] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _require(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise VideoToolMissing(f"{tool} is not installed")
    return path


def _run(args: list[str], timeout: int) -> bytes:
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VideoError(f"{args[0]} timed out after {timeout}s") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise VideoError(f"{args[0]} failed: {detail[-1] if detail else result.returncode}")
    return result.stdout


# ISO 6709 as QuickTime writes it: "+35.9132-081.5398+1000.500/".
_ISO6709 = re.compile(r"^\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def parse_location(value: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    if not value:
        return None, None
    match = _ISO6709.match(value)
    if not match:
        return None, None
    latitude, longitude = float(match.group(1)), float(match.group(2))
    if abs(latitude) > 90 or abs(longitude) > 180:
        return None, None
    # A camera with no fix writes 0/0; that is "no location", not the Gulf
    # of Guinea.
    if latitude == 0 and longitude == 0:
        return None, None
    return latitude, longitude


def parse_creation_time(value: Optional[str]) -> Optional[datetime]:
    """A container timestamp as a datetime tagged UTC -- the same convention
    sync.py uses for EXIF times (the wall-clock reading, not a true
    instant; see trip_detection.parse_timestamp). Epoch-zero (what editors
    write for "unknown") and future times are discarded."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # "2019-08-10T14:22:31-0400" (Apple) -> a colon in the offset.
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Keep the wall clock; drop whatever offset it was written with.
    parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.year < 1990 or parsed > datetime.now(timezone.utc):
        return None
    return parsed


def _rotation(stream: dict) -> int:
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            try:
                return int(round(float(side_data["rotation"])))
            except (TypeError, ValueError):
                pass
    try:
        return int(float((stream.get("tags") or {}).get("rotate", 0)))
    except (TypeError, ValueError):
        return 0


def probe(path: str) -> VideoInfo:
    """Duration, display size, capture time and GPS of the movie at `path`."""
    raw = _run(
        [_require("ffprobe"), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        _PROBE_TIMEOUT_SECONDS,
    )
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise VideoError("ffprobe returned unreadable output") from exc

    info = VideoInfo()
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration"))
        info.duration = duration if duration >= 0 else None
    except (TypeError, ValueError):
        pass

    # A cover-art stream (attached_pic) isn't the movie.
    video_streams = [
        s
        for s in data.get("streams") or []
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if video_streams:
        stream = video_streams[0]
        width, height = stream.get("width"), stream.get("height")
        if width and height:
            if abs(_rotation(stream)) % 180 == 90:
                width, height = height, width
            info.width, info.height = int(width), int(height)
        if info.duration is None:
            try:
                info.duration = float(stream.get("duration"))
            except (TypeError, ValueError):
                pass

    tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
    info.created_at = parse_creation_time(tags.get("com.apple.quicktime.creationdate")) or parse_creation_time(
        tags.get("creation_time")
    )
    info.latitude, info.longitude = parse_location(
        tags.get("com.apple.quicktime.location.iso6709") or tags.get("location")
    )
    return info


def poster(path: str, duration: Optional[float] = None) -> Image.Image:
    """One frame from the movie as an RGB image, rotation applied. Taken a
    little way in (a clip's first frame is often black), or at the start for
    a very short clip or if seeking fails."""
    ffmpeg = _require("ffmpeg")
    seek = 0.0
    if duration and duration > 0:
        seek = min(1.0, duration * 0.1)

    last_error: Optional[Exception] = None
    for position in dict.fromkeys((seek, 0.0)):  # the seek, then the very start
        try:
            frame = _run(
                [
                    ffmpeg, "-v", "error", "-ss", f"{position:.3f}", "-i", path,
                    "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-",
                ],
                _POSTER_TIMEOUT_SECONDS,
            )
        except VideoError as exc:
            last_error = exc
            continue
        if frame:
            with Image.open(io.BytesIO(frame)) as decoded:
                return decoded.convert("RGB")
    raise VideoError(f"no frame could be read: {last_error}" if last_error else "no frame could be read")

