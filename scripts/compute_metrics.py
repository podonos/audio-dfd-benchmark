"""
Compute all benchmark metrics for all models (open-source + commercial).
Metrics: Accuracy, F1, FPR, FNR, Latency, Per-format, Real-time factor.
"""
import csv
import os
import json
from collections import defaultdict

LABELS_CSV = os.path.join(os.path.dirname(__file__), "..", "dataset", "labels_speech.csv")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "results")

ALL_MODELS = [
    "aasist", "rawnet2", "lcnn", "wav2vec2",
    "hive", "resemble", "reality_defender", "aurigin",
]


def load_gold_labels():
    if not os.path.exists(LABELS_CSV):
        raise FileNotFoundError(
            f"Gold-standard labels not found at {LABELS_CSV}.\n"
            "The benchmark gold labels are private. "
            "Submit your predictions.csv to the leaderboard host for scoring.")
    gold = {}
    with open(LABELS_CSV) as f:
        for row in csv.DictReader(f):
            gold[row["filename"]] = {
                "label": row["label"],
                "format": row["format"].lower(),
            }
    return gold


def load_predictions(model_name):
    path = os.path.join(RESULTS_DIR, f"predictions_{model_name}.csv")
    if not os.path.exists(path):
        return None
    preds = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            latency = row.get("latency_ms", "")
            duration = row.get("audio_duration_sec", "")
            preds[row["filename"]] = {
                "label": row["label"],
                "latency_ms": float(latency) if latency else None,
                "audio_duration_sec": float(duration) if duration else None,
            }
    return preds


def compute_metrics(gold, preds, subset_files=None):
    tp = fp = tn = fn = 0
    latencies = []
    rtfs = []
    files = subset_files if subset_files else gold.keys()

    n_skipped = 0
    n_not_applicable = 0
    n_error = 0
    n_missing = 0
    for fname in files:
        true_label = gold[fname]["label"]
        pred_data = preds.get(fname)
        if not pred_data:
            n_missing += 1
            n_skipped += 1
            continue
        if pred_data["label"] == "NOT_APPLICABLE":
            n_not_applicable += 1
            n_skipped += 1
            continue
        if pred_data["label"] == "error":
            n_error += 1
            n_skipped += 1
            continue
        pred_label = pred_data["label"]

        if true_label == "fake" and pred_label == "fake":
            tp += 1
        elif true_label == "real" and pred_label == "fake":
            fp += 1
        elif true_label == "real" and pred_label == "real":
            tn += 1
        elif true_label == "fake" and pred_label == "real":
            fn += 1

        if pred_data["latency_ms"] is not None:
            latencies.append(pred_data["latency_ms"])
        if pred_data["latency_ms"] is not None and pred_data["audio_duration_sec"] and pred_data["audio_duration_sec"] > 0:
            rtf = (pred_data["latency_ms"] / 1000.0) / pred_data["audio_duration_sec"]
            rtfs.append(rtf)

    total = tp + fp + tn + fn
    if total == 0:
        return None

    accuracy = (tp + tn) / total
    precision = tp / (tp + fp) if (tp + fp) else 0
    recall = tp / (tp + fn) if (tp + fn) else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0
    fpr = fp / (fp + tn) if (fp + tn) else 0
    fnr = fn / (fn + tp) if (fn + tp) else 0
    avg_latency = sum(latencies) / len(latencies) if latencies else None
    avg_rtf = sum(rtfs) / len(rtfs) if rtfs else None

    # Rejection ratio = (NOT_APPLICABLE + error) / attempted
    # Attempted = files the API was actually called on (excludes missing/not-yet-run).
    n_attempted = total + n_not_applicable + n_error
    n_rejected = n_not_applicable + n_error
    rejection_ratio = n_rejected / n_attempted if n_attempted > 0 else 0
    return {
        "total": total,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "accuracy": accuracy,
        "f1": f1,
        "precision": precision,
        "recall": recall,
        "fpr": fpr,
        "fnr": fnr,
        "avg_latency_ms": avg_latency,
        "avg_rtf": avg_rtf,
        "n_errors": n_error,
        "n_not_applicable": n_not_applicable,
        "n_missing": n_missing,
        "n_skipped": n_skipped,
        "n_attempted": n_attempted,
        "rejection_ratio": rejection_ratio,
    }


def main():
    gold = load_gold_labels()

    # Group files by format
    files_by_format = defaultdict(list)
    for fname, info in gold.items():
        files_by_format[info["format"]].append(fname)

    formats = ["mp3", "wav", "flac", "ogg", "m4a", "webm"]

    # Collect all results for summary table
    summary_rows = []

    for model_name in ALL_MODELS:
        preds = load_predictions(model_name)
        if preds is None:
            continue

        m = compute_metrics(gold, preds)
        if m is None:
            continue

        print(f"{'='*70}")
        print(f"  {model_name.upper()}")
        print(f"{'='*70}")
        print(f"  Accuracy:          {m['accuracy']*100:6.2f}%")
        print(f"  F1 Score:          {m['f1']:.4f}")
        print(f"  Precision:         {m['precision']:.4f}")
        print(f"  Recall:            {m['recall']:.4f}")
        print(f"  FPR:               {m['fpr']*100:.2f}%")
        print(f"  FNR:               {m['fnr']*100:.2f}%")
        if m["avg_latency_ms"] is not None:
            print(f"  Avg Latency:       {m['avg_latency_ms']:.1f} ms/file")
        else:
            print(f"  Avg Latency:       N/A")
        if m["avg_rtf"] is not None:
            print(f"  Real-time Factor:  {m['avg_rtf']:.4f}")
        else:
            print(f"  Real-time Factor:  N/A")
        print(f"  Files processed:   {m['total']} (errors: {m['n_errors']})")
        print()

        # Per-format breakdown
        print(f"  {'Format':<8} {'Acc%':>6} {'F1':>6} {'FPR%':>6} {'FNR%':>6} {'Lat(ms)':>8} {'RTF':>7} {'N':>5}")
        print(f"  {'-'*55}")
        for fmt in formats:
            files = files_by_format.get(fmt, [])
            if not files:
                continue
            fm = compute_metrics(gold, preds, files)
            if fm is None:
                continue
            lat_str = f"{fm['avg_latency_ms']:.1f}" if fm["avg_latency_ms"] else "N/A"
            rtf_str = f"{fm['avg_rtf']:.4f}" if fm["avg_rtf"] else "N/A"
            print(f"  .{fmt:<7} {fm['accuracy']*100:5.1f}% {fm['f1']:.4f} {fm['fpr']*100:5.1f}% {fm['fnr']*100:5.1f}% {lat_str:>8} {rtf_str:>7} {fm['total']:>5}")
        print()

        summary_rows.append({
            "model": model_name,
            "n": m["total"],
            "rejection_ratio": m["rejection_ratio"],
            "accuracy": m["accuracy"],
            "f1": m["f1"],
            "fpr": m["fpr"],
            "fnr": m["fnr"],
            "avg_latency_ms": m["avg_latency_ms"],
            "avg_rtf": m["avg_rtf"],
        })

    # Print summary leaderboard
    if summary_rows:
        summary_rows.sort(key=lambda x: x["accuracy"], reverse=True)
        print(f"\n{'='*70}")
        print(f"  LEADERBOARD (sorted by accuracy)")
        print(f"{'='*70}")
        print(f"  {'#':<3} {'Model':<20} {'N':>6} {'Rej%':>6} {'Acc%':>7} {'F1':>7} {'FPR%':>7} {'FNR%':>7} {'Lat(ms)':>9} {'RTF':>7}")
        print(f"  {'-'*82}")
        for i, row in enumerate(summary_rows, 1):
            lat_str = f"{row['avg_latency_ms']:.0f}" if row["avg_latency_ms"] else "N/A"
            rtf_str = f"{row['avg_rtf']:.4f}" if row["avg_rtf"] else "N/A"
            print(f"  {i:<3} {row['model']:<20} {row['n']:>6} {row['rejection_ratio']*100:5.1f}% {row['accuracy']*100:6.2f}% {row['f1']:.4f} {row['fpr']*100:5.1f}% {row['fnr']*100:5.1f}% {lat_str:>9} {rtf_str:>7}")

    # Save summary to CSV
    summary_path = os.path.join(RESULTS_DIR, "benchmark_summary.csv")
    with open(summary_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "model", "n", "rejection_ratio", "accuracy", "f1", "fpr", "fnr", "avg_latency_ms", "avg_rtf"
        ])
        writer.writeheader()
        writer.writerows(summary_rows)
    print(f"\nSummary saved to {summary_path}")


if __name__ == "__main__":
    main()
