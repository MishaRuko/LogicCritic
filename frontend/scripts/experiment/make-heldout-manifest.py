#!/usr/bin/env python3
"""Write src/lib/experiment/fixtures/heldout.json: held-out LSV clips for scoring the agent, never shown in the app.

  make-heldout-manifest.py                                   # the original 7 held-out clips
  make-heldout-manifest.py --name extra --dir data/fresh DJI-035 DJI-036 ...   # any other slices

Rows are copied verbatim from data/dji.parquet at the revision pinned in source.json. Source SHA-256s
come from Hugging Face's LFS metadata, so fetch-demo-data.py --heldout can verify the downloads.
One-off: needs `pip install huggingface_hub pandas pyarrow`. The output is committed; the videos are not.
"""
import json
import sys
from pathlib import Path

import pandas as pd
from huggingface_hub import HfApi, hf_hub_download

ROOT = Path(__file__).resolve().parents[2]
source = json.loads((ROOT / 'src/lib/experiment/fixtures/source.json').read_text())
REPO, revision = 'cong-lab/lsv', source['revision']
args = sys.argv[1:]
def option(flag, default):
    return args[args.index(flag) + 1] if flag in args else default
NAME, DIR = option('--name', 'heldout'), option('--dir', 'data/heldout')
HELDOUT = [a for i, a in enumerate(args) if not a.startswith('--') and (i == 0 or not args[i - 1].startswith('--'))] or ['DJI-092', 'DJI-093', 'DJI-094', 'DJI-095', 'DJI-096', 'DJI-098', 'DJI-099']

rows = pd.read_parquet(hf_hub_download(REPO, 'data/dji.parquet', repo_type='dataset', revision=revision))
rows = rows[rows.slice_id.isin(HELDOUT)].sort_values('slice_id')
assert len(rows) == len(HELDOUT), 'missing held-out rows in the pinned manifest'
info = {p.path: p for p in HfApi().get_paths_info(REPO, list(rows.video_640_path), repo_type='dataset', revision=revision)}

examples = []
for row in json.loads(rows.to_json(orient='records')):
    row['source_sha256'] = info[row['video_640_path']].lfs.sha256
    row['cached_path'] = f'{DIR}/{row["clip_id"]}.mp4'
    examples.append(row)
manifest = {key: source[key] for key in ('dataset', 'source', 'revision', 'license')} | {'examples': examples}
(ROOT / f'src/lib/experiment/fixtures/{NAME}.json').write_text(json.dumps(manifest, indent=2) + '\n')
print('wrote', len(examples), 'examples to', f'src/lib/experiment/fixtures/{NAME}.json')
