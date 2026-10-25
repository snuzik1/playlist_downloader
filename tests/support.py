from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

# The application stores settings next to the executable/source tree by default.
# Every test process must point it at a disposable directory instead.
TEST_HOME = tempfile.TemporaryDirectory(prefix="pldl-tests-")
os.environ["PLDL_HOME"] = TEST_HOME.name

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def make_track(
    index: int = 0,
    *,
    title: str = "Song",
    artist: str = "Artist",
    **kwargs: Any,
):
    from pldl.models import Track

    artists = kwargs.pop("artists", [artist] if artist else [])
    return Track(
        id=kwargs.pop("id", f"track-{index}"),
        title=title,
        artists=artists,
        **kwargs,
    )


def make_playlist(
    tracks: list[Any],
    *,
    title: str = "Test Playlist",
    platform: str = "test",
    kind: str = "playlist",
    source_url: str = "https://example.com/playlist",
    download_mode: str = "direct",
    **kwargs: Any,
):
    from pldl.models import Playlist

    return Playlist(
        platform=platform,
        kind=kind,
        title=title,
        tracks=tracks,
        source_url=source_url,
        download_mode=download_mode,
        **kwargs,
    )


def make_options(**overrides: Any) -> dict[str, Any]:
    options = {
        "format": "mp3",
        "quality": 320,
        "playlist_folder": True,
        "index_prefix": True,
        "concurrency": 2,
    }
    options.update(overrides)
    return options
