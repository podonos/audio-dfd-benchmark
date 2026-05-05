# Dataset Description

## Overview

The benchmark dataset contains **4,524 audio files** for binary classification (real vs. AI-generated speech), distributed across six audio formats reflecting real-world conditions: telephony-grade MP3, studio WAV, lossless FLAC, compressed OGG and M4A, and browser-recorded WebM.

## File Formats

| Format | Count | Notes |
|--------|------:|-------|
| `.mp3` | 773 | Lossy compressed |
| `.wav` | 724 | Lossless PCM |
| `.flac` | 762 | Lossless compressed |
| `.ogg` | 757 | Lossy compressed (Vorbis) |
| `.m4a` | 749 | Lossy compressed (AAC) |
| `.webm` | 759 | Lossy compressed (Opus, browser-recorded) |

**Total**: 4,524 files

## Real (Bonafide) Audio Sources

The bonafide speech recordings are drawn from three established public corpora:

1. **VCTK Corpus** — 110 English speakers, multiple accents (https://datashare.ed.ac.uk/handle/10283/3443).
2. **LJSPEECH** — single-speaker dataset of 13,100 short audio clips of a single speaker reading non-fiction passages (https://keithito.com/LJ-Speech-Dataset/).
3. **LibriTTS-360** — 360-hour subset of LibriTTS, 904 English speakers (https://www.openslr.org/60/).

## Fake (AI-Generated) Audio Sources

The synthetic speech is generated using approximately **25 state-of-the-art text-to-speech and voice-cloning models**, all accessed via API from their respective vendors. Examples include:

- **ElevenLabs**
- **Microsoft F5-TTS**
- **Chatterbox**
- ...and others

The full list of generators reflects the current commercial TTS landscape as of dataset release.

## Quality Verification

All synthesized audio files undergo a verification process:

1. **Whisper transcription**: Each generated clip is transcribed back using OpenAI's Whisper model.
2. **Text alignment check**: The Whisper output is compared with the input prompt text to verify that the TTS system actually synthesized the intended utterance.
3. **Failed clips are discarded**, ensuring the dataset only contains intelligible synthetic speech.

This step prevents the inclusion of garbled or hallucinated TTS outputs that would unfairly inflate detector accuracy.

## Format Conversion

After verification, audio files are converted to multiple formats (MP3, WAV, FLAC, OGG, M4A, WebM) to evaluate detector robustness across distribution channels:

- **MP3 / OGG / M4A**: Common lossy formats used in telephony, streaming, and mobile.
- **WAV / FLAC**: Studio-quality lossless formats.
- **WebM**: Browser-recorded format (Opus codec), reflecting real-time web capture.

## Class Balance

The dataset is **balanced 50/50**:
- **2,262 real** audio files
- **2,262 fake** (AI-generated) audio files

## File Naming

Files are sequentially numbered starting from `0`. The format extension indicates the encoding (e.g., `0.flac`, `1.webm`, `2.mp3`).

## Audio Statistics

- **Average duration**: 4.80 seconds
- **Total audio**: ~362 minutes (~6 hours)
- **Sample rates**: Variable (resample to 16 kHz mono for open-source detectors)

## Gold Standard Labels

The ground-truth labels (`labels_speech.csv`) are **NOT included in this public release** to prevent overfitting and to maintain the benchmark's role as an independent evaluation set. Submit your `predictions.csv` to be scored against the private gold standard.

## Submission Format

Produce a `predictions.csv` with two columns:

```csv
filename,label
0.flac,real
1.webm,fake
2.mp3,real
...
```

Labels must be exactly `real` or `fake` (lowercase).
