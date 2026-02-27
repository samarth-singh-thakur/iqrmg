# Image ↔ Multi-QR Encoder/Decoder

This repository contains a Python script that can:

1. Read an image and split it into multiple QR code images.
2. Read those QR code images and reconstruct the original image.

## Efficiency improvements

Compared to a basic JSON-per-chunk approach, this version packs more data per QR by:

- using **Base85** (smaller overhead than Base64),
- storing metadata only in the **first chunk**,
- using optional **zlib compression** (automatically enabled when it reduces size),
- defaulting to QR **error correction L** for maximum capacity.

## Requirements

Install dependencies:

```bash
pip install qrcode[pil] opencv-python Pillow
```

## Usage

### Encode an image into QR chunks

```bash
python qr_image_codec.py encode --input ./example.png --output-dir ./qr_chunks
```

Tune capacity / resilience tradeoff:

```bash
python qr_image_codec.py encode \
  --input ./example.png \
  --output-dir ./qr_chunks \
  --chunk-size 2500 \
  --error-correction L
```

- `--chunk-size`: preferred data chars per QR (script validates capacity).
- `--error-correction`: `L`, `M`, `Q`, `H` (lower level = more data capacity).

### Decode QR chunks back into an image

```bash
python qr_image_codec.py decode --input-dir ./qr_chunks --output ./recovered.png
```

## Notes

- Keep all generated QR chunk images together.
- Decoder validates chunk continuity and SHA-256 checksum before writing output.
- QR decoding supports common image extensions (`.png`, `.jpg`, `.jpeg`, `.bmp`, `.webp`).
