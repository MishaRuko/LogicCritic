#!/usr/bin/env python3
"""Fetch the pinned LSV mirrors listed in src/lib/experiment/fixtures/source.json; verify source hashes, transcode to browser H.264.
Standard library + ffmpeg. No dataset library or full dataset download required.

Clips whose cached H.264 copy already matches the receipt in assets.json are left untouched, so adding
new examples never re-encodes (and re-hashes) the existing ones.

The original two clips (DJI_10, DJI_16) use a plain 640px-wide scale. Every later clip (listed in
NATIVE_CROP_CLIPS) keeps its native aspect ratio, never pads/letterboxes, and crops burned-in black
bars when ffmpeg cropdetect finds them.

--heldout (or --extra) fetches src/lib/experiment/fixtures/heldout.json (or extra.json) instead: clips for scoring the agent
only. Each goes to its manifest cached_path (gitignored) with receipts in <name>-assets.json, and gets no poster frames.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
NAME = 'heldout' if '--heldout' in sys.argv else 'extra' if '--extra' in sys.argv else None
HELDOUT = NAME is not None
manifest = json.loads((ROOT / f'src/lib/experiment/fixtures/{NAME or "source"}.json').read_text())
revision = manifest['revision']
ASSETS = ROOT / (f'src/lib/experiment/fixtures/{NAME}-assets.json' if HELDOUT else 'src/lib/experiment/fixtures/assets.json')
previous = {r['clip']: r for r in json.loads(ASSETS.read_text())} if ASSETS.exists() else {}

LEGACY_CLIPS = {'DJI_10', 'DJI_16'}

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def detect_crop(raw, duration):
    """Return 'w:h:x:y' if cropdetect finds black bars worth removing, else None."""
    crops = []
    for frac in (0.15, 0.4, 0.65, 0.9):
        out = subprocess.run(['ffmpeg', '-hide_banner', '-ss', f'{duration * frac:.2f}', '-i', str(raw),
                              '-t', '3', '-vf', 'cropdetect=limit=24:round=2:reset=0', '-an', '-f', 'null', '-'],
                             capture_output=True, text=True).stderr
        found = re.findall(r'crop=(\d+):(\d+):(\d+):(\d+)', out)
        if found:
            crops.append(tuple(map(int, found[-1])))
    if not crops:
        return None
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                            '-of', 'csv=p=0', str(raw)], capture_output=True, text=True, check=True).stdout
    width, height = map(int, probe.strip().split(','))
    # Union of detected content regions (most conservative crop).
    x0 = min(c[2] for c in crops); y0 = min(c[3] for c in crops)
    x1 = max(c[2] + c[0] for c in crops); y1 = max(c[3] + c[1] for c in crops)
    w, h = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    if width - w <= 8 and height - h <= 8:
        return None
    return f'{w}:{h}:{x0}:{y0}'

def fetch(example):
    clip = example['clip_id']
    raw = ROOT / 'data/downloads' / (clip + '.mp4')
    cached = ROOT / example['cached_path'] if HELDOUT else ROOT / 'public/experiment/demo' / (clip + '.mp4')
    raw.parent.mkdir(parents=True, exist_ok=True)
    cached.parent.mkdir(parents=True, exist_ok=True)
    prior = previous.get(clip)
    if prior and cached.exists() and digest(cached) == prior['cachedSha256'] and prior['sourceSha256'] == example['source_sha256']:
        print('Up to date', clip, flush=True)
        return prior
    if not raw.exists() or digest(raw) != example['source_sha256']:
        print('Fetching', clip, flush=True)
        url = f'https://huggingface.co/datasets/cong-lab/lsv/resolve/{revision}/{example["video_640_path"]}'
        urllib.request.urlretrieve(url, raw.with_suffix('.partial'))
        raw.with_suffix('.partial').replace(raw)
    if digest(raw) != example['source_sha256']:
        raise RuntimeError('Source SHA-256 mismatch: ' + clip)
    if clip in LEGACY_CLIPS:
        vf, transform = 'scale=640:-2', 'H.264 CRF26, 640px width, 15fps, original timebase, no audio'
    else:
        crop = detect_crop(raw, example['duration_s'])
        vf = (f'crop={crop},' if crop else '') + 'scale=640:-2'
        transform = ('H.264 CRF26, 640px width, native aspect (no padding), 15fps, original timebase, no audio'
                     + (f', cropped {crop} (w:h:x:y, cropdetect)' if crop else ', no crop needed'))
    subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', str(raw),
                    '-map', '0:v:0', '-vf', vf, '-r', '15', '-c:v', 'libx264', '-crf', '26', '-preset', 'fast',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', str(cached)], check=True)
    protocol = json.loads(example['protocol_json'])
    times = [] if HELDOUT else [s['t_start'] for s in protocol['steps'] if s['t_start'] is not None]
    for index, seconds in enumerate(times):
        subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-ss', str(seconds + 1),
                        '-i', str(cached), '-frames:v', '1', '-q:v', '2',
                        str(cached.parent / f'{clip}-{index}.jpg')], check=True)
    print('Ready', clip, flush=True)
    return {'clip': clip, 'sourceSha256': digest(raw), 'cachedSha256': digest(cached),
            'bytes': cached.stat().st_size, 'transform': transform,
            'localPath': str(cached.relative_to(ROOT))}

if __name__ == '__main__':
    if not shutil.which('ffmpeg'):
        raise SystemExit('ffmpeg is required. On macOS: brew install ffmpeg')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(fetch, manifest['examples']))
    ASSETS.write_text(json.dumps(receipts, indent=2) + '\n')
