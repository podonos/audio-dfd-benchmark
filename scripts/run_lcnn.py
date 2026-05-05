"""
LCNN inference script for audio deepfake detection benchmark.
Uses HuggingFace checkpoint: MattyB95/pre_trained_DF_LFCC-LCNN

The checkpoint uses the project-NN-Pytorch-scripts architecture with:
- Built-in LFCC frontend (m_frontend)
- LCNN CNN backbone with MaxFeatureMap (m_transform)
- BLSTM pooling (m_before_pooling)
- Single scalar output (m_output_act)
"""
import os
import time
import csv
import math

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchaudio
import numpy as np
from huggingface_hub import hf_hub_download

DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset_wav16k")
ORIG_DATASET_DIR = os.path.join(os.path.dirname(__file__), "..", "dataset")
LABELS_CSV = os.path.join(os.path.dirname(__file__), "..", "dataset", "labels_speech.csv")
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), "..", "results", "predictions_lcnn.csv")


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

HF_REPO = "MattyB95/pre_trained_DF_LFCC-LCNN"
HF_FILENAME = "pre_trained_DF_LFCC-LCNN.pt"


# ---------- Model Architecture (matching checkpoint) ----------

class MaxFeatureMap2D(nn.Module):
    """Max feature map activation: split channels in half, take element-wise max."""
    def forward(self, x):
        x1, x2 = torch.chunk(x, 2, dim=1)
        return torch.max(x1, x2)


class BLSTMLayer(nn.Module):
    def __init__(self, input_dim, hidden_dim):
        super().__init__()
        self.l_blstm = nn.LSTM(input_dim, hidden_dim, bidirectional=True, batch_first=True)

    def forward(self, x):
        out, _ = self.l_blstm(x)
        return out


def compute_delta(x, order=1):
    """Compute delta features over the time axis (last dim)."""
    # x: (batch, n_lfcc, frames)
    if order == 0:
        return x
    # Simple delta: difference of adjacent frames, padded
    padded = F.pad(x, (1, 1), mode='replicate')
    delta = (padded[:, :, 2:] - padded[:, :, :-2]) / 2.0
    if order == 1:
        return delta
    return compute_delta(delta, order - 1)


class LFCCFrontend(nn.Module):
    """LFCC feature extraction frontend matching the checkpoint.
    Outputs 60 features: 20 LFCC + 20 delta + 20 double-delta.
    """
    def __init__(self, n_lfcc=20, n_fft=512, sr=16000):
        super().__init__()
        n_filters = n_fft // 2
        self.lfcc_fb = nn.Parameter(torch.zeros(n_filters, n_lfcc))
        self.l_dct = nn.Linear(n_lfcc, n_lfcc, bias=False)
        self.n_fft = n_fft
        self.sr = sr

    def forward(self, x):
        # x: (batch, 1, time)
        x_stft = torch.stft(x.squeeze(1), n_fft=self.n_fft, hop_length=self.n_fft // 4,
                           win_length=self.n_fft, window=torch.hann_window(self.n_fft).to(x.device),
                           return_complex=True)
        x_mag = torch.abs(x_stft)  # (batch, n_fft//2+1, frames)
        x_mag = x_mag[:, :self.n_fft // 2, :]  # (batch, 256, frames)
        # Apply filterbank + log
        x_lfcc = torch.matmul(x_mag.transpose(1, 2), self.lfcc_fb)  # (batch, frames, n_lfcc)
        x_lfcc = torch.log(x_lfcc + 1e-8)
        # Apply DCT
        x_lfcc = self.l_dct(x_lfcc)  # (batch, frames, n_lfcc)
        x_lfcc = x_lfcc.transpose(1, 2)  # (batch, n_lfcc, frames)
        # Compute delta and double-delta
        delta = compute_delta(x_lfcc, order=1)
        ddelta = compute_delta(x_lfcc, order=2)
        # Concatenate: (batch, 60, frames)
        x_out = torch.cat([x_lfcc, delta, ddelta], dim=1)
        return x_out.unsqueeze(1)  # (batch, 1, 60, frames)


class LCNN_LFCC(nn.Module):
    """Full LCNN model matching the MattyB95 checkpoint structure."""
    def __init__(self):
        super().__init__()

        # Input normalization params
        self.input_mean = nn.Parameter(torch.zeros(1), requires_grad=False)
        self.input_std = nn.Parameter(torch.ones(1), requires_grad=False)
        self.output_mean = nn.Parameter(torch.zeros(1), requires_grad=False)
        self.output_std = nn.Parameter(torch.ones(1), requires_grad=False)

        # LFCC frontend
        self.m_frontend = nn.ModuleList([LFCCFrontend(n_lfcc=20, n_fft=512)])

        # LCNN transform (CNN layers with MaxFeatureMap)
        self.m_transform = nn.ModuleList([nn.Sequential(
            # Block 1
            nn.Conv2d(1, 64, 5, padding=2),      # 0
            MaxFeatureMap2D(),                     # 1 -> 32ch
            nn.MaxPool2d(2, 2),                    # 2
            # Block 2
            nn.Conv2d(32, 64, 1),                  # 3
            MaxFeatureMap2D(),                      # 4 -> 32ch
            nn.BatchNorm2d(32, affine=False),        # 5
            # Block 3
            nn.Conv2d(32, 96, 3, padding=1),       # 6
            MaxFeatureMap2D(),                      # 7 -> 48ch
            nn.MaxPool2d(2, 2),                     # 8
            nn.BatchNorm2d(48, affine=False),       # 9
            # Block 4
            nn.Conv2d(48, 96, 1),                  # 10
            MaxFeatureMap2D(),                      # 11 -> 48ch
            nn.BatchNorm2d(48, affine=False),       # 12
            # Block 5
            nn.Conv2d(48, 128, 3, padding=1),      # 13
            MaxFeatureMap2D(),                      # 14 -> 64ch
            nn.MaxPool2d(2, 2),                     # 15
            # Block 6
            nn.Conv2d(64, 128, 1),                 # 16
            MaxFeatureMap2D(),                      # 17 -> 64ch
            nn.BatchNorm2d(64, affine=False),       # 18
            # Block 7
            nn.Conv2d(64, 64, 3, padding=1),       # 19
            MaxFeatureMap2D(),                      # 20 -> 32ch
            nn.BatchNorm2d(32, affine=False),       # 21
            # Block 8
            nn.Conv2d(32, 64, 1),                  # 22
            MaxFeatureMap2D(),                      # 23 -> 32ch
            nn.BatchNorm2d(32, affine=False),       # 24
            # Block 9
            nn.Conv2d(32, 64, 3, padding=1),       # 25
            MaxFeatureMap2D(),                      # 26 -> 32ch
            nn.MaxPool2d(2, 2),                     # 27
            nn.Dropout(0.7),                        # 28
        )])

        # BLSTM pooling: 2 layers, input 96 (32ch * 3 from reshape), hidden 48
        self.m_before_pooling = nn.ModuleList([nn.Sequential(
            BLSTMLayer(input_dim=96, hidden_dim=48),   # bidirectional -> output 96
            BLSTMLayer(input_dim=96, hidden_dim=48),
        )])

        # Output: single scalar
        self.m_output_act = nn.ModuleList([nn.Linear(96, 1)])

    def forward(self, x):
        # x: (batch, samples) raw waveform
        batch = x.shape[0]

        # Normalize input
        x = (x - self.input_mean) / (self.input_std + 1e-8)
        x = x.unsqueeze(1)  # (batch, 1, samples)

        # Frontend: extract LFCC
        x = self.m_frontend[0](x)  # (batch, 1, n_lfcc, frames)

        # CNN transform
        x = self.m_transform[0](x)  # (batch, 32, h, w)

        # Reshape for BLSTM: (batch, time, features)
        batch_size, channels, h, w = x.shape
        x = x.permute(0, 3, 1, 2).contiguous()  # (batch, w, channels, h)
        x = x.view(batch_size, w, channels * h)  # (batch, w, channels*h)

        # BLSTM
        x = self.m_before_pooling[0][0](x)
        x = self.m_before_pooling[0][1](x)

        # Pooling: mean over time
        x = x.mean(dim=1)  # (batch, 96)

        # Output
        x = self.m_output_act[0](x)  # (batch, 1)

        # Denormalize output
        x = x * self.output_std + self.output_mean

        return x.squeeze(1)  # (batch,) scalar score


def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    # Download checkpoint
    print(f"Downloading checkpoint from {HF_REPO}...")
    ckpt_path = hf_hub_download(repo_id=HF_REPO, filename=HF_FILENAME)
    checkpoint = torch.load(ckpt_path, map_location=device, weights_only=False)

    # Build model and load weights
    model = LCNN_LFCC().to(device)

    # Try strict load
    try:
        model.load_state_dict(checkpoint, strict=True)
        print("Weights loaded successfully (strict=True).")
    except RuntimeError as e:
        print(f"Strict load failed: {e}")
        # Report key mismatches
        model_keys = set(model.state_dict().keys())
        ckpt_keys = set(checkpoint.keys())
        missing = model_keys - ckpt_keys
        unexpected = ckpt_keys - model_keys
        if missing:
            print(f"  Missing keys ({len(missing)}): {list(missing)[:10]}")
        if unexpected:
            print(f"  Unexpected keys ({len(unexpected)}): {list(unexpected)[:10]}")
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
                "confidence_score": "",
                "latency_ms": "",
            })
            continue

        try:
            waveform, sr = torchaudio.load(wav_path)
            if sr != 16000:
                waveform = torchaudio.transforms.Resample(sr, 16000)(waveform)
            if waveform.shape[0] > 1:
                waveform = waveform.mean(dim=0, keepdim=True)
            waveform = waveform.squeeze(0)  # (samples,)
            waveform = waveform.unsqueeze(0).to(device)  # (1, samples)

            start_time = time.time()
            with torch.no_grad():
                score = model(waveform)
            elapsed_ms = (time.time() - start_time) * 1000

            # Score interpretation: higher = bonafide (real), lower = spoof (fake)
            # Use 0 as threshold (model outputs centered scores)
            score_val = score.item()
            label = "real" if score_val > 0 else "fake"

            results.append({
                "filename": orig_filename,
                "label": label,
                "confidence_score": f"{score_val:.6f}",
                "latency_ms": f"{elapsed_ms:.2f}",
            })

            if (i + 1) % 100 == 0:
                print(f"  [{i+1}/{total}] {orig_filename} -> {label} "
                      f"(score={score_val:.4f}) {elapsed_ms:.1f}ms")

        except Exception as e:
            print(f"  ERROR on {orig_filename}: {e}")
            results.append({
                "filename": orig_filename,
                "label": "error",
                "confidence_score": "",
                "latency_ms": "",
            })

    # Write results
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "filename", "label", "confidence_score", "latency_ms"
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
