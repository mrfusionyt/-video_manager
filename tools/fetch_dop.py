#!/usr/bin/env python3
"""Fetch the helper binaries in _dop/ needed by a clean CI checkout.

_dop/ is gitignored (ffmpeg/ffprobe/deno/yt-dlp are ~470 MB), so a GitHub
Actions runner does not have them. This script downloads the same set of
tools into _dop/ before PyInstaller runs.

Existing files are never overwritten. Every URL can be overridden with an
environment variable, e.g. DOP_FFMPEG_URL, so a private mirror can be used.

Usage:
    python tools/fetch_dop.py            # download what is missing
    python tools/fetch_dop.py --check    # only report what is present
"""

import argparse
import io
import os
import sys
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DOP = Path(os.environ.get('DOP_DIR') or (ROOT / '_dop'))

# Verified reachable on 2026-11-25.
DEFAULTS = {
    'ffmpeg.exe': (
        'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
        'ffmpeg-release-essentials.zip',
        ('bin/ffmpeg.exe',),
    ),
    'ffprobe.exe': (
        'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
        'ffmpeg-release-essentials.zip',
        ('bin/ffprobe.exe',),
    ),
    'yt-dlp.exe': (
        'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe',
        None, None,
    ),
    'deno.exe': (
        'https://github.com/denoland/deno/releases/latest/download/'
        'deno-x86_64-pc-windows-msvc.zip',
        'deno.zip', ('deno.exe',),
    ),
    'fpcalc.exe': (
        'https://github.com/acoustid/chromaprint/releases/download/v1.6.2/'
        'chromaprint-fpcalc-1.6.2-windows-x86_64.zip',
        'fpcalc.zip', ('fpcalc.exe',),
    ),
}

ENV_OVERRIDES = {
    'ffmpeg.exe': 'DOP_FFMPEG_URL',
    'ffprobe.exe': 'DOP_FFPROBE_URL',
    'yt-dlp.exe': 'DOP_YTDLP_URL',
    'deno.exe': 'DOP_DENO_URL',
    'fpcalc.exe': 'DOP_FPCALC_URL',
}


def env_url(name, default):
    """Use a DOP_*_URL override only when it is actually set to a non-empty value."""
    var = ENV_OVERRIDES.get(name)
    if var:
        value = (os.environ.get(var) or '').strip()
        if value:
            return value
    return default


def download(url, timeout=(15, 600)):
    if not url or not str(url).lower().startswith(('http://', 'https://')):
        raise RuntimeError(f'invalid download URL: {url!r}')
    print(f'  downloading {url}', flush=True)
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=timeout, stream=True)
            r.raise_for_status()
            total = int(r.headers.get('content-length') or 0)
            buf = io.BytesIO()
            started = time.time()
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    buf.write(chunk)
            size = buf.tell()
            speed = size / 1048576 / max(time.time() - started, 0.001)
            print(f'  got {size / 1048576:.1f} MB in {time.time() - started:.0f}s '
                  f'({speed:.1f} MB/s)' + (f' / expected {total / 1048576:.1f} MB' if total else ''),
                  flush=True)
            return buf.getvalue()
        except Exception as exc:
            if attempt == 2:
                raise
            print(f'  attempt {attempt + 1} failed: {exc} - retrying', flush=True)
            time.sleep(2)


def extract_member(data, suffixes, wanted):
    """Find a member by path suffix or basename and return its bytes."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        for suffix in suffixes:
            lowered = suffix.lower().replace('\\', '/')
            for name in names:
                if name.lower().replace('\\', '/').endswith(lowered):
                    return z.read(name), name
        base = wanted.lower()
        for name in names:
            if name.lower().replace('\\', '/').rsplit('/', 1)[-1] == base:
                return z.read(name), name
    raise RuntimeError(f'{wanted} not found inside archive')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true',
                        help='only report which binaries are present')
    parser.add_argument('--force', action='store_true',
                        help='overwrite files that already exist')
    args = parser.parse_args()

    DOP.mkdir(parents=True, exist_ok=True)
    missing = []
    for name in DEFAULTS:
        path = DOP / name
        if args.check or path.is_file() and not args.force:
            print(f'{"OK " if path.is_file() else "-- "} {path}'
                  + (f'  ({path.stat().st_size / 1048576:.1f} MB)' if path.is_file() else ''),
                  flush=True)
        if args.check:
            continue
        if path.is_file() and not args.force:
            continue
        missing.append(name)

    if args.check:
        return 0

    if not missing:
        print('[_dop] all helper binaries are present')
        return 0

    # FFmpeg contains both ffmpeg.exe and ffprobe.exe: fetch it once.
    cached = {}
    for name in missing:
        url, archive, suffixes = DEFAULTS[name]
        url = env_url(name, url)
        print(f'[_dop] fetching {name}', flush=True)
        if url not in cached:
            cached[url] = download(url)
        data = cached[url]
        if archive and suffixes:
            data, member = extract_member(data, suffixes, name)
            print(f'  extracted {member}', flush=True)
        target = DOP / name
        target.write_bytes(data)
        print(f'  -> {target}  ({len(data) / 1048576:.1f} MB)', flush=True)

    print('[_dop] done:', ', '.join(p.name for p in sorted(DOP.glob('*.exe'))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
