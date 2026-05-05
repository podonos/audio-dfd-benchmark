"""
RawNet2 inference script for audio deepfake detection benchmark.
Uses the RawNet2Spoof architecture from the AASIST repo with
HuggingFace checkpoint: MattyB95/pre_trained_DF_RawNet2
"""
import sys
import os
import json
import time
import csv
import copy

import torch
import soundfile as sf
import numpy as np
from huggingface_hub import hf_hub_download

# Add AASIST repo to path (it contains RawNet2Spoof model)
AASIST_DIR = os.path.join(os.path.dirname(__file__), "..", "models", "aasist")
sys.path.insert(0, AASIST_DIR)

from models.RawNet2Spoof import Model

DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset_wav16k")
ORIG_DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset")
LABELS_CSV = os.path.join(os.path.dirname(__file__), "..", "dataset", "labels_speech.csv")
CONFIG_PATH = os.path.join(AASIST_DIR, "config", "RawNet2_baseline.conf")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "results", "predictions_rawnet2.csv")


def load_filenames():
    if os.path.exists(LABELS_CSV):
        names = []
        with open(LABELS_CSV) as f:
            for row in csv.DictReader(f):
                names.append(row["filename"])
        return names
    valid_exts = {"mp3", "wav", "flac", "ogg", "m4a", "webm"}
    files = [f for f in os.listdir(ORIG_DATASET_DIR)
             if f.split(".")[-1].lower() in valid_exts]
    files.sort(key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else 0)
    return files

HF_REPO = "MattyB95/pre_trained_DF_RawNet2"
HF_FILENAME = "pre_trained_DF_RawNet2.pth"

NB_SAMP = 64600  # ~4 seconds at 16kHz


def pad_or_truncate(wav, nb_samp):
    if len(wav) > nb_samp:
        wav = wav[:nb_samp]
    elif len(wav) < nb_samp:
        wav = np.pad(wav, (0, nb_samp - len(wav)), "constant")
    return wav


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Load config for RawNet2Spoof architecture
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)
    model_config = copy.deepcopy(config["model_config"])

    # Download HuggingFace checkpoint
    print(f"Downloading checkpoint from {HF_REPO}...")
    ckpt_path = hf_hub_download(repo_id=HF_REPO, filename=HF_FILENAME)
    checkpoint = torch.load(ckpt_path, map_location=device, weights_only=False)
    print(f"Checkpoint loaded. Keys: {list(checkpoint.keys())[:5]}...")

    # Build model and try loading weights
    model = Model(model_config).to(device)
    try:
        model.load_state_dict(checkpoint, strict=True)
        print("Weights loaded successfully (strict=True).")
    except RuntimeError as e:
        print(f"Strict load failed: {e}")
        print("Trying strict=False...")
        # Inspect shapes
        model_keys = set(model.state_dict().keys())
        ckpt_keys = set(checkpoint.keys())
        missing = model_keys - ckpt_keys
        unexpected = ckpt_keys - model_keys
        if missing:
            print(f"  Missing keys ({len(missing)}): {list(missing)[:5]}")
        if unexpected:
            print(f"  Unexpected keys ({len(unexpected)}): {list(unexpected)[:5]}")
        model.load_state_dict(checkpoint, strict=False)
        print("Weights loaded (strict=False).")

    model.eval()

    original_filenames = load_filenames()

    results = []
    total = len(original_filenames)

    for i, orig_filename in enumerate(original_filenames):
        name_no_ext = os.path.splitext(orig_filename)[0]
        wav_path = os.path.join(DATASET_DIR, f"{name_no_ext}.wav")

        if not os.path.exists(wav_path):
            print(f"  SKIP: {wav_path} not found")
            results.append({
                "filename": orig_filename,
                "label": "error",
                "confidence_real": "",
                "confidence_fake": "",
                "raw_score_bonafide": "",
                "raw_score_spoof": "",
                "latency_ms": "",
            })
            continue

        try:
            wav, sr = sf.read(wav_path, dtype="float32")
            wav = pad_or_truncate(wav, NB_SAMP)
            wav_tensor = torch.FloatTensor(wav).unsqueeze(0).to(device)

            start_time = time.time()
            with torch.no_grad():
                _, output = model(wav_tensor)
            elapsed_ms = (time.time() - start_time) * 1000

            # RawNet2Spoof returns log-softmax output
            probs = torch.exp(output)  # convert log-softmax to probabilities
            pred = output.argmax(dim=1).item()  # 0=bonafide(real), 1=spoof(fake)
            label = "real" if pred == 0 else "fake"

            results.append({
                "filename": orig_filename,
                "label": label,
                "confidence_real": f"{probs[0][0].item():.6f}",
                "confidence_fake": f"{probs[0][1].item():.6f}",
                "raw_score_bonafide": f"{output[0][0].item():.6f}",
                "raw_score_spoof": f"{output[0][1].item():.6f}",
                "latency_ms": f"{elapsed_ms:.2f}",
            })

            if (i + 1) % 100 == 0:
                print(f"  [{i+1}/{total}] {orig_filename} -> {label} "
                      f"(real={probs[0][0].item():.4f}, fake={probs[0][1].item():.4f}) "
                      f"{elapsed_ms:.1f}ms")

        except Exception as e:
            print(f"  ERROR on {orig_filename}: {e}")
            results.append({
                "filename": orig_filename,
                "label": "error",
                "confidence_real": "",
                "confidence_fake": "",
                "raw_score_bonafide": "",
                "raw_score_spoof": "",
                "latency_ms": "",
            })

    # Write results
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "filename", "label", "confidence_real", "confidence_fake",
            "raw_score_bonafide", "raw_score_spoof", "latency_ms"
        ])
        writer.writeheader()
        writer.writerows(results)

    n_real = sum(1 for r in results if r["label"] == "real")
    n_fake = sum(1 for r in results if r["label"] == "fake")
    n_err = sum(1 for r in results if r["label"] == "error")
    print(f"\nDone. {total} files processed: {n_real} real, {n_fake} fake, {n_err} errors")
    print(f"Results saved to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
