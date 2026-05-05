"""
Run all commercial DFD APIs on the dataset.
Measures round-trip latency per file.
Outputs predictions CSV with: filename, label, confidence, latency_ms, audio_duration_sec

API keys are read from environment variables:
  HIVE_API_KEY
  RESEMBLE_API_KEY
  REALITY_DEFENDER_API_KEY
  AURIGIN_API_KEY

Usage:
  python scripts/run_commercial_apis.py                   # all APIs on all files
  python scripts/run_commercial_apis.py --api hive        # one API only
  python scripts/run_commercial_apis.py --limit 100       # first 100 files
"""
import os
import csv
import json
import time
import base64
import argparse
import requests
import soundfile as sf

DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset")
WAV16K_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset_wav16k")
LABELS_CSV = os.path.join(os.path.dirname(__file__), "..", "dataset", "labels_speech.csv")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# API Keys from environment
HIVE_API_KEY = os.environ.get("HIVE_API_KEY", "")
RESEMBLE_API_KEY = os.environ.get("RESEMBLE_API_KEY", "")
REALITY_DEFENDER_API_KEY = os.environ.get("REALITY_DEFENDER_API_KEY", "")
AURIGIN_API_KEY = os.environ.get("AURIGIN_API_KEY", "")

MIME_MAP = {
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
    ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".webm": "audio/webm",
}

# Throttling intervals (seconds)
HIVE_MIN_INTERVAL = 5.0
RESEMBLE_MIN_INTERVAL = 5.0
REALITY_DEFENDER_MIN_INTERVAL = 5.0
AURIGIN_MIN_INTERVAL = 5.0

_last_hive_call = [0.0]
_last_resemble_call = [0.0]
_last_reality_defender_call = [0.0]
_last_aurigin_call = [0.0]


def get_audio_duration(filepath):
    try:
        return sf.info(filepath).duration
    except Exception:
        name_no_ext = os.path.splitext(os.path.basename(filepath))[0]
        wav_path = os.path.join(WAV16K_DIR, f"{name_no_ext}.wav")
        try:
            return sf.info(wav_path).duration
        except Exception:
            return None


def get_converted_wav(filename):
    name_no_ext = os.path.splitext(filename)[0]
    return os.path.join(WAV16K_DIR, f"{name_no_ext}.wav")


def load_filenames(limit=None):
    """Load filenames from labels_speech.csv if present, otherwise from dataset dir."""
    filenames = []
    if os.path.exists(LABELS_CSV):
        with open(LABELS_CSV) as f:
            for row in csv.DictReader(f):
                filenames.append(row["filename"])
    else:
        # Fall back: list dataset directory
        for fname in sorted(os.listdir(DATASET_DIR), key=lambda x: int(os.path.splitext(x)[0]) if os.path.splitext(x)[0].isdigit() else 0):
            if fname.split(".")[-1].lower() in ("mp3", "wav", "flac", "ogg", "m4a", "webm"):
                filenames.append(fname)
    if limit:
        filenames = filenames[:limit]
    return filenames


# ============================================================
# RESEMBLE AI
# ============================================================
def run_resemble(filepath):
    """POST https://app.resemble.ai/api/v2/detect with file upload."""
    elapsed = time.time() - _last_resemble_call[0]
    if elapsed < RESEMBLE_MIN_INTERVAL:
        time.sleep(RESEMBLE_MIN_INTERVAL - elapsed)
    _last_resemble_call[0] = time.time()

    if not RESEMBLE_API_KEY:
        return {"label": "error", "confidence": "", "latency_ms": 0,
                "raw": {"error": "RESEMBLE_API_KEY not set"}}

    url = "https://app.resemble.ai/api/v2/detect"
    headers = {"Authorization": f"Bearer {RESEMBLE_API_KEY}", "Prefer": "wait"}
    ext = os.path.splitext(filepath)[1].lower()
    mime = MIME_MAP.get(ext, "audio/wav")
    if ext == ".webm":
        filepath = get_converted_wav(os.path.basename(filepath))
        mime = "audio/wav"

    with open(filepath, "rb") as f:
        start = time.time()
        resp = requests.post(url, headers=headers,
                           files={"file": (os.path.basename(filepath), f, mime)}, timeout=120)
        latency_ms = (time.time() - start) * 1000

    if resp.status_code == 200:
        data = resp.json()
        metrics = data.get("item", {}).get("metrics", {})
        label_raw = metrics.get("label", "")
        score = metrics.get("aggregated_score", 0)
        label = "fake" if label_raw == "fake" else "real"
        return {"label": label, "confidence": score, "latency_ms": latency_ms, "raw": data}
    else:
        return {"label": "error", "confidence": "", "latency_ms": latency_ms,
                "raw": {"status": resp.status_code, "text": resp.text[:200]}}


# ============================================================
# HIVE AI
# ============================================================
def run_hive(filepath):
    """POST Hive deepfake detection v3 endpoint.
    On 429 (rate-limit) or other API failure, the script stops immediately.
    """
    import sys
    elapsed = time.time() - _last_hive_call[0]
    if elapsed < HIVE_MIN_INTERVAL:
        time.sleep(HIVE_MIN_INTERVAL - elapsed)
    _last_hive_call[0] = time.time()

    if not HIVE_API_KEY:
        return {"label": "error", "confidence": "", "latency_ms": 0,
                "raw": {"error": "HIVE_API_KEY not set"}}

    url = "https://api.thehive.ai/api/v3/hive/ai-generated-and-deepfake-content-detection"
    headers = {"Authorization": f"Bearer {HIVE_API_KEY}", "Content-Type": "application/json"}
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".m4a", ".webm"):
        filepath = get_converted_wav(os.path.basename(filepath))

    with open(filepath, "rb") as f:
        audio_bytes = f.read()
    b64 = base64.b64encode(audio_bytes).decode("utf-8")
    mime = MIME_MAP.get(os.path.splitext(filepath)[1].lower(), "audio/wav")
    payload_json = json.dumps({"input": [{"media_base64": f"data:{mime};base64,{b64}"}]})

    start = time.time()
    resp = requests.post(url, headers=headers, data=payload_json, timeout=120)
    latency_ms = (time.time() - start) * 1000

    if resp.status_code == 200:
        data = resp.json()
        outputs = data.get("output", [])
        ai_score = 0
        for output in outputs:
            for cls in output.get("classes", []):
                if cls["class"] == "ai_generated_audio":
                    ai_score = cls["value"]
        label = "fake" if ai_score > 0.5 else "real"
        return {"label": label, "confidence": ai_score, "latency_ms": latency_ms, "raw": data}

    print(f"\n[HIVE] API failure — stopping. Status: {resp.status_code}, Body: {resp.text[:200]}")
    if resp.status_code == 429:
        print("[HIVE] Rate limit / quota hit. Resume later when quota resets.")
    sys.exit(1)


# ============================================================
# REALITY DEFENDER
# ============================================================
def run_reality_defender(filepath):
    """Use Reality Defender Python SDK. Skip files <1.5s as NOT_APPLICABLE."""
    elapsed = time.time() - _last_reality_defender_call[0]
    if elapsed < REALITY_DEFENDER_MIN_INTERVAL:
        time.sleep(REALITY_DEFENDER_MIN_INTERVAL - elapsed)
    _last_reality_defender_call[0] = time.time()

    if not REALITY_DEFENDER_API_KEY:
        return {"label": "error", "confidence": "", "latency_ms": 0,
                "raw": {"error": "REALITY_DEFENDER_API_KEY not set"}}

    from realitydefender import RealityDefender
    client = RealityDefender(api_key=REALITY_DEFENDER_API_KEY)

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".webm":
        filepath = get_converted_wav(os.path.basename(filepath))

    duration = get_audio_duration(filepath)
    if duration is not None and duration < 1.5:
        return {"label": "NOT_APPLICABLE", "confidence": "", "latency_ms": 0.0,
                "raw": {"skip_reason": f"audio duration {duration:.3f}s < 1.5s"}}

    start = time.time()
    result = client.detect_file(filepath)
    latency_ms = (time.time() - start) * 1000

    status = result.get("status", "")
    score = result.get("score")
    if status in ("MANIPULATED", "SUSPICIOUS"):
        label = "fake"
    elif status == "AUTHENTIC":
        label = "real"
    elif status in ("NOT_APPLICABLE", "UNABLE_TO_EVALUATE"):
        return {"label": "NOT_APPLICABLE", "confidence": "",
                "latency_ms": latency_ms, "raw": result}
    elif score is not None:
        label = "fake" if score > 0.5 else "real"
    else:
        return {"label": "error", "confidence": "",
                "latency_ms": latency_ms, "raw": result}

    return {"label": label, "confidence": score if score is not None else "",
            "latency_ms": latency_ms, "raw": result}


# ============================================================
# AURIGIN AI
# ============================================================
def run_aurigin(filepath):
    """POST https://api.aurigin.ai/v1/predict"""
    elapsed = time.time() - _last_aurigin_call[0]
    if elapsed < AURIGIN_MIN_INTERVAL:
        time.sleep(AURIGIN_MIN_INTERVAL - elapsed)
    _last_aurigin_call[0] = time.time()

    if not AURIGIN_API_KEY:
        return {"label": "error", "confidence": "", "latency_ms": 0,
                "raw": {"error": "AURIGIN_API_KEY not set"}}

    url = "https://api.aurigin.ai/v1/predict"
    headers = {"x-api-key": AURIGIN_API_KEY}
    ext = os.path.splitext(filepath)[1].lower()
    mime = MIME_MAP.get(ext, "audio/wav")
    if ext == ".webm":
        filepath = get_converted_wav(os.path.basename(filepath))
        mime = "audio/wav"

    with open(filepath, "rb") as f:
        start = time.time()
        resp = requests.post(url, headers=headers,
                           files={"file": (os.path.basename(filepath), f, mime)}, timeout=120)
        latency_ms = (time.time() - start) * 1000

    if resp.status_code == 200:
        data = resp.json()
        result_str = data.get("global", {}).get("result", "")
        confidence = data.get("global", {}).get("confidence", 0)
        label = "fake" if result_str == "spoofed" else "real"
        return {"label": label, "confidence": confidence, "latency_ms": latency_ms, "raw": data}
    else:
        return {"label": "error", "confidence": "", "latency_ms": latency_ms,
                "raw": {"status": resp.status_code, "text": resp.text[:200]}}


API_RUNNERS = {
    "resemble": run_resemble,
    "hive": run_hive,
    "reality_defender": run_reality_defender,
    "aurigin": run_aurigin,
}


def run_api(api_name, filenames):
    runner = API_RUNNERS[api_name]
    output_csv = os.path.join(RESULTS_DIR, f"predictions_{api_name}.csv")
    fieldnames = ["filename", "label", "confidence", "latency_ms", "audio_duration_sec"]

    existing = {}
    if os.path.exists(output_csv):
        with open(output_csv) as f:
            for row in csv.DictReader(f):
                if row["label"] != "error":
                    existing[row["filename"]] = row
        print(f"  Found {len(existing)} existing results, resuming...")

    results = list(existing.values())
    total = len(filenames)
    errors = 0

    for i, fname in enumerate(filenames):
        if fname in existing:
            continue

        filepath = os.path.join(DATASET_DIR, fname)
        if not os.path.exists(filepath):
            print(f"  SKIP: {filepath} not found")
            continue

        duration = get_audio_duration(filepath)

        try:
            res = runner(filepath)
            results.append({
                "filename": fname,
                "label": res["label"],
                "confidence": f"{res['confidence']:.6f}" if isinstance(res["confidence"], float) else res["confidence"],
                "latency_ms": f"{res['latency_ms']:.2f}",
                "audio_duration_sec": f"{duration:.4f}" if duration else "",
            })

            if res["label"] == "error":
                errors += 1

            if (i + 1) % 10 == 0 or (i + 1) == total:
                conf = f"{res['confidence']:.4f}" if isinstance(res["confidence"], (int, float)) else "N/A"
                print(f"  [{i+1}/{total}] {fname} -> {res['label']} (conf={conf}) {res['latency_ms']:.0f}ms")
                with open(output_csv, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(results)

        except Exception as e:
            print(f"  ERROR on {fname}: {e}")
            errors += 1
            results.append({
                "filename": fname, "label": "error", "confidence": "",
                "latency_ms": "", "audio_duration_sec": f"{duration:.4f}" if duration else "",
            })

    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    n_real = sum(1 for r in results if r["label"] == "real")
    n_fake = sum(1 for r in results if r["label"] == "fake")
    n_na = sum(1 for r in results if r["label"] == "NOT_APPLICABLE")
    print(f"\n  Done: {len(results)} files, {n_real} real, {n_fake} fake, "
          f"{n_na} NOT_APPLICABLE, {errors} errors")
    print(f"  Saved to {output_csv}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", choices=list(API_RUNNERS.keys()))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    filenames = load_filenames(args.limit)
    apis = [args.api] if args.api else list(API_RUNNERS.keys())

    print(f"Files: {len(filenames)}")
    print(f"APIs: {apis}")
    print()

    for api_name in apis:
        print(f"{'='*50}")
        print(f"  {api_name.upper()}")
        print(f"{'='*50}")
        run_api(api_name, filenames)
        print()


if __name__ == "__main__":
    main()
