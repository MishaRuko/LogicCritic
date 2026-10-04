# Demo provenance

Five videos, from [cong-lab/lsv](https://huggingface.co/datasets/cong-lab/lsv), pinned to the revision in `source.json`. CC BY-NC 4.0; credit LabOS / LSV authors. Research and noncommercial demonstration use.

- **DJI_10 / DJI-034**: CRISPR delivery, 95.36 seconds, no documented procedural error. Exact slice identifier is authoritative in `source.json`.
- **DJI_16 / DJI-040**: CRISPR delivery, 25.33 seconds. Published error: “Added reagent 2 to reagent 3 instead of mixing tube”.
- **DJI_08 / DJI-027**: cell splitting in a biosafety cabinet, 270.80 seconds, 19 of 22 steps time-aligned. Published error: “didnt incubate after adding trypleE”. Note the per-clip protocol JSON has no incubation step; the canonical step (“Incubate at 37°C for 1 minutes”) appears in DJI_06's protocol.
- **DJI_17 / DJI-091**: E. coli transformation and plating, 139.07 seconds, 7 of 7 steps aligned. Published error: “Skipped step 3 in Mock_Transfornation.txt” (step 3 still carries a 1-second 45–46s window in the manifest).
- **DJI_23 / DJI-097**: adding cytokine into cells, 141.77 seconds, 7 of 7 steps aligned. Published error: “didnt chage tips in step 2”.
- DJI_10 and DJI_16 use `Mock_Cas9_Delivery.txt`. These are demonstrations of a wet-lab method, including mock reagents; they are not evidence of a biological result.

`source.json` preserves the exact published manifest rows, source media hashes and protocol JSON. `method.json` operationalises its seven requirements and adds explicit evidentiary predicates for conditions already present in the method (sterility, room temperature, cell identity, confluency). No invented volume or incubation deviation.

`observations.json` is **annotation-assisted model visual review**, prepared during development by inspecting sampled real frames with the coding model, then using published protocol alignment and error annotations for semantic vessel/reagent identity. Action descriptions are frame review; exact windows and reagent identity come from metadata. It is **not a cached output of Workers AI**, and **not a held-out model prediction**. 0.86 is an uncalibrated review confidence estimate, not a measured probability. `establishes` reflects what the combined sources can support. Inspector separates video and annotation evidence.

No incubation observation is fabricated. Absent footage is unverifiable. No sterility, temperature, biological identity, confluency or liquid volume is inferred from a transfer motion. Wrong-target evidence comes from the published deviation label, supported by a visible transfer.

The source mirrors use MPEG-4 Part 2, which has poor browser support. `fetch-demo-data.py` checks original SHA-256, then creates H.264 copies at 640px width and 15fps with the original timebase, no audio, CRF26. Clips after DJI_10/DJI_16 keep native aspect ratio (no padding) and are cropped only if ffmpeg cropdetect finds black bars (none did). Already-cached copies whose hash matches `assets.json` are not re-encoded. Temporal precision is limited by this sampling (about 67ms) and dataset annotations (seconds). `assets.json` records both source and transformed hashes. Selected frames are extracted from these local copies. Transcoding bytes can vary with ffmpeg version; original source hashes remain reproducible.

Evaluation reports deterministic engine agreement on these two annotation-assisted fixtures. Labels contributed to fixtures, so this is a real-data integration check with label leakage, **not a measure of model generalisation**. Temporal alignment accuracy and localization error are deliberately not reported: fixture timestamps are copied from the same labels.
