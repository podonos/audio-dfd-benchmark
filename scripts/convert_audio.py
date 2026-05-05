"""
Convert all audio files to 16kHz mono WAV format.
Required for the open-source models.
Also used as a fallback for commercial APIs that don't support .webm or .m4a.
"""
import os
import subprocess

DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset_wav16k")

os.makedirs(OUT_DIR, exist_ok=True)
files = [f for f in os.listdir(DATASET_DIR)
         if f.split(".")[-1].lower() in ("mp3", "wav", "flac", "ogg", "m4a", "webm")]
print(f"Converting {len(files)} files to 16kHz mono WAV...")
for i, fname in enumerate(sorted(files)):
    name_no_ext = os.path.splitext(fname)[0]
    out_path = os.path.join(OUT_DIR, f"{name_no_ext}.wav")
    if os.path.exists(out_path):
        continue
    in_path = os.path.join(DATASET_DIR, fname)
    subprocess.run(
        ["ffmpeg", "-y", "-i", in_path, "-ar", "16000", "-ac", "1", "-f", "wav", out_path],
        capture_output=True,
    )
    if (i + 1) % 100 == 0:
        print(f"  [{i+1}/{len(files)}] {fname}")
print(f"Done. {len(os.listdir(OUT_DIR))} files in {OUT_DIR}")
