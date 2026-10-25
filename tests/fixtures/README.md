# Test fixtures

Tiny silent audio files used by `tests/test_tagging.py`.

They were generated once with FFmpeg (0.15 seconds of silence) and are committed so
the test suite does not require FFmpeg or network access:

```powershell
ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 0.15 -q:a 9 silence.mp3
ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 0.15 -c:a aac -b:a 64k silence.m4a
ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 0.15 -c:a flac silence.flac
ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 0.15 -c:a libvorbis -q:a 2 silence.ogg
ffmpeg -f lavfi -i anullsrc=r=44100:cl=stereo -t 0.15 -c:a libopus -b:a 32k silence.opus
```

The files contain no third-party music or artwork.
