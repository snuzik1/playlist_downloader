from __future__ import annotations

import base64
import shutil
import tempfile
import unittest
from pathlib import Path

import support
from pldl.tagging import _clean, _picture_mime, tag_file

PNG_COVER = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
META = {
    "title": "Song",
    "artist": "Artist",
    "album": "Album",
    "album_artist": "Album Artist",
    "year": "2024",
    "track_number": 2,
    "total_tracks": 5,
    "disc_number": 1,
    "comment": "Источник: test",
}


class TagHelperTests(unittest.TestCase):
    def test_clean_removes_nulls_and_collapses_whitespace(self) -> None:
        self.assertEqual(_clean("  A\x00  B  "), "A B")

    def test_picture_mime_detects_supported_formats(self) -> None:
        self.assertEqual(_picture_mime(b"\xff\xd8\xffdata"), "image/jpeg")
        self.assertEqual(_picture_mime(b"\x89PNG\r\n\x1a\ndata"), "image/png")
        self.assertEqual(_picture_mime(b"RIFFxxxxWEBPdata"), "image/webp")
        self.assertEqual(_picture_mime(b"unknown", "image/gif"), "image/gif")
        self.assertEqual(_picture_mime(b"unknown"), "image/jpeg")


class TagFileTests(unittest.TestCase):
    def copy_fixture(self, directory: str, name: str) -> Path:
        destination = Path(directory) / name
        shutil.copy2(support.FIXTURES / name, destination)
        return destination

    def test_mp3_writes_full_metadata_and_cover(self) -> None:
        from mutagen.id3 import ID3

        with tempfile.TemporaryDirectory() as directory:
            path = self.copy_fixture(directory, "silence.mp3")
            warnings = tag_file(path, META, PNG_COVER, "image/png")
            tags = ID3(path)

        self.assertEqual(warnings, [])
        self.assertEqual(str(tags["TIT2"]), "Song")
        self.assertEqual(str(tags["TPE1"]), "Artist")
        self.assertEqual(str(tags["TALB"]), "Album")
        self.assertEqual(str(tags["TPE2"]), "Album Artist")
        self.assertEqual(str(tags["TDRC"]), "2024")
        self.assertEqual(str(tags["TRCK"]), "2/5")
        self.assertEqual(str(tags["TPOS"]), "1")
        self.assertEqual(str(tags["TSSE"]), "PLDL")
        self.assertEqual(str(tags.getall("COMM")[0].text[0]), "Источник: test")
        pictures = tags.getall("APIC")
        self.assertEqual(len(pictures), 1)
        self.assertEqual(pictures[0].mime, "image/png")
        self.assertEqual(pictures[0].data, PNG_COVER)

    def test_m4a_writes_metadata_and_cover(self) -> None:
        from mutagen.mp4 import MP4, MP4Cover

        with tempfile.TemporaryDirectory() as directory:
            path = self.copy_fixture(directory, "silence.m4a")
            warnings = tag_file(path, META, PNG_COVER, "image/png")
            tags = MP4(path)

        self.assertEqual(warnings, [])
        self.assertEqual(tags["\xa9nam"], ["Song"])
        self.assertEqual(tags["\xa9ART"], ["Artist"])
        self.assertEqual(tags["aART"], ["Album Artist"])
        self.assertEqual(tags["\xa9alb"], ["Album"])
        self.assertEqual(tags["\xa9day"], ["2024"])
        self.assertEqual(tags["trkn"], [(2, 5)])
        self.assertEqual(tags["disk"], [(1, 0)])
        self.assertEqual(tags["covr"][0].imageformat, MP4Cover.FORMAT_PNG)
        self.assertEqual(tags["covr"][0], PNG_COVER)

    def test_flac_writes_metadata_and_picture(self) -> None:
        from mutagen.flac import FLAC

        with tempfile.TemporaryDirectory() as directory:
            path = self.copy_fixture(directory, "silence.flac")
            warnings = tag_file(path, META, PNG_COVER, "image/png")
            tags = FLAC(path)

        self.assertEqual(warnings, [])
        self.assertEqual(tags["title"], ["Song"])
        self.assertEqual(tags["artist"], ["Artist"])
        self.assertEqual(tags["albumartist"], ["Album Artist"])
        self.assertEqual(tags["album"], ["Album"])
        self.assertEqual(tags["date"], ["2024"])
        self.assertEqual(tags["tracknumber"], ["2"])
        self.assertEqual(tags["tracktotal"], ["5"])
        self.assertEqual(tags["discnumber"], ["1"])
        self.assertEqual(len(tags.pictures), 1)
        self.assertEqual(tags.pictures[0].mime, "image/png")
        self.assertEqual(tags.pictures[0].data, PNG_COVER)

    def test_ogg_writes_metadata_and_picture(self) -> None:
        from mutagen.flac import Picture
        from mutagen.oggvorbis import OggVorbis

        with tempfile.TemporaryDirectory() as directory:
            path = self.copy_fixture(directory, "silence.ogg")
            warnings = tag_file(path, META, PNG_COVER, "image/png")
            tags = OggVorbis(path)

        self.assertEqual(warnings, [])
        self.assertEqual(tags["title"], ["Song"])
        self.assertEqual(tags["artist"], ["Artist"])
        self.assertEqual(tags["tracknumber"], ["2"])
        encoded = tags["metadata_block_picture"][0]
        picture = Picture(base64.b64decode(encoded))
        self.assertEqual(picture.mime, "image/png")
        self.assertEqual(picture.data, PNG_COVER)

    def test_opus_writes_metadata(self) -> None:
        from mutagen.oggopus import OggOpus

        with tempfile.TemporaryDirectory() as directory:
            path = self.copy_fixture(directory, "silence.opus")
            warnings = tag_file(path, META, PNG_COVER, "image/png")
            tags = OggOpus(path)

        self.assertEqual(warnings, [])
        self.assertEqual(tags["title"], ["Song"])
        self.assertEqual(tags["album"], ["Album"])
        self.assertIn("metadata_block_picture", tags)

    def test_unsupported_extension_is_unchanged_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "track.wav"
            path.write_bytes(b"audio")
            warnings = tag_file(path, META)

            self.assertEqual(path.read_bytes(), b"audio")
        self.assertTrue(any("не поддерживаются" in warning for warning in warnings))

    def test_corrupt_file_returns_warning_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.m4a"
            path.write_bytes(b"not an mp4")
            warnings = tag_file(path, META)

        self.assertTrue(any("Не удалось записать теги" in warning for warning in warnings))

    def test_existing_tags_and_cover_are_replaced(self) -> None:
        from mutagen.id3 import ID3

        with tempfile.TemporaryDirectory() as directory:
            path = self.copy_fixture(directory, "silence.mp3")
            tag_file(path, META, PNG_COVER, "image/png")
            tag_file(
                path,
                {**META, "title": "New Title"},
                b"\xff\xd8\xffnew-jpeg",
                "image/jpeg",
            )
            tags = ID3(path)

        self.assertEqual(str(tags["TIT2"]), "New Title")
        pictures = tags.getall("APIC")
        self.assertEqual(len(pictures), 1)
        self.assertEqual(pictures[0].mime, "image/jpeg")
        self.assertEqual(pictures[0].data, b"\xff\xd8\xffnew-jpeg")

    def test_year_is_truncated_and_invalid_year_is_ignored(self) -> None:
        from mutagen.id3 import ID3

        with tempfile.TemporaryDirectory() as directory:
            path = self.copy_fixture(directory, "silence.mp3")
            tag_file(path, {**META, "year": "2024-05-01"})
            self.assertEqual(str(ID3(path)["TDRC"]), "2024")

            tag_file(path, {**META, "year": "not-a-year"})
            tags = ID3(path)
            self.assertNotIn("TDRC", tags)
            self.assertNotIn("TYER", tags)


if __name__ == "__main__":
    unittest.main()
