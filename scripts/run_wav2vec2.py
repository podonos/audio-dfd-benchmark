"""
Wav2Vec2 inference script for audio deepfake detection benchmark.
Uses HuggingFace model: Gustking/wav2vec2-large-xlsr-deepfake-audio-classification
"""
import os
import time
import csv

import torch
import librosa
from transformers import AutoModelForAudioClassification, AutoFeatureExtractor

DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset_wav16k")
ORIG_DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset")
LABELS_CSV = os.path.join(os.path.dirname(__file__), "..", "dataset", "labels_speech.csv")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "results", "predictions_wav2vec2.csv")


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

MODEL_NAME = "Gustking/wav2vec2-large-xlsr-deepfake-audio-classification"


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    print(f"Loading model: {MODEL_NAME}")
    model = AutoModelForAudioClassification.from_pretrained(MODEL_NAME)
    feature_extractor = AutoFeatureExtractor.from_pretrained(MODEL_NAME)
    model.to(device)
    model.eval()
    print("Wav2Vec2 model loaded.")

    # id2label mapping from model config
    id2label = model.config.id2label
    print(f"Label mapping: {id2label}")

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
                "latency_ms": "",
            })
            continue

        try:
            audio, sr = librosa.load(wav_path, sr=16000, mono=True)

            inputs = feature_extractor(
                audio, sampling_rate=16000, return_tensors="pt", padding=True
            )
            inputs = {k: v.to(device) for k, v in inputs.items()}

            start_time = time.time()
            with torch.no_grad():
                outputs = model(**inputs)
                logits = outputs.logits
            elapsed_ms = (time.time() - start_time) * 1000

            probs = torch.softmax(logits, dim=-1)
            pred_id = logits.argmax(dim=-1).item()
            pred_label_raw = id2label.get(pred_id, str(pred_id)).lower()

            # Normalize label to real/fake
            if "real" in pred_label_raw or "bonafide" in pred_label_raw or "genuine" in pred_label_raw:
                label = "real"
            elif "fake" in pred_label_raw or "spoof" in pred_label_raw or "synthetic" in pred_label_raw:
                label = "fake"
            else:
                label = pred_label_raw

            # Find confidence scores - handle varying label orders
            conf_real = ""
            conf_fake = ""
            for idx, lbl in id2label.items():
                lbl_lower = lbl.lower()
                if "real" in lbl_lower or "bonafide" in lbl_lower or "genuine" in lbl_lower:
                    conf_real = f"{probs[0][idx].item():.6f}"
                elif "fake" in lbl_lower or "spoof" in lbl_lower or "synthetic" in lbl_lower:
                    conf_fake = f"{probs[0][idx].item():.6f}"

            results.append({
                "filename": orig_filename,
                "label": label,
                "confidence_real": conf_real,
                "confidence_fake": conf_fake,
                "latency_ms": f"{elapsed_ms:.2f}",
            })

            if (i + 1) % 100 == 0:
                print(f"  [{i+1}/{total}] {orig_filename} -> {label} "
                      f"(real={conf_real}, fake={conf_fake}) {elapsed_ms:.1f}ms")

        except Exception as e:
            print(f"  ERROR on {orig_filename}: {e}")
            results.append({
                "filename": orig_filename,
                "label": "error",
                "confidence_real": "",
                "confidence_fake": "",
                "latency_ms": "",
            })

    # Write results
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "filename", "label", "confidence_real", "confidence_fake", "latency_ms"
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
