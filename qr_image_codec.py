#!/usr/bin/env python3
"""Encode an image into multiple QR codes and decode them back.

Usage examples:
  python qr_image_codec.py encode --input ./photo.png --output-dir ./qr_chunks
  python qr_image_codec.py decode --input-dir ./qr_chunks --output ./recovered.png
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Dict, List

from PIL import Image
import cv2
import qrcode


DEFAULT_CHUNK_SIZE = 700


def encode_image_to_qrs(input_image: Path, output_dir: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
    """Read an image, split it into chunks, and save each chunk as one QR image."""
    image_bytes = input_image.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    total_chunks = (len(image_b64) + chunk_size - 1) // chunk_size

    sha256 = hashlib.sha256(image_bytes).hexdigest()

    output_dir.mkdir(parents=True, exist_ok=True)

    for index in range(total_chunks):
        start = index * chunk_size
        end = start + chunk_size
        data_slice = image_b64[start:end]

        payload = {
            "index": index,
            "total": total_chunks,
            "filename": input_image.name,
            "sha256": sha256,
            "data": data_slice,
        }

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(json.dumps(payload, separators=(",", ":")))
        qr.make(fit=True)

        qr_image = qr.make_image(fill_color="black", back_color="white")
        qr_path = output_dir / f"chunk_{index:04d}.png"
        qr_image.save(qr_path)

    print(f"Created {total_chunks} QR code images in: {output_dir}")


def decode_qr_image(image_path: Path) -> Dict:
    """Decode a single QR image and return the JSON payload."""
    detector = cv2.QRCodeDetector()
    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError(f"Could not open image: {image_path}")

    decoded_text, points, _ = detector.detectAndDecode(image)

    if not decoded_text or points is None:
        # Try multi-QR decode fallback.
        ok, decoded_info, _, _ = detector.detectAndDecodeMulti(image)
        if not ok or not decoded_info:
            raise ValueError(f"No QR data found in: {image_path}")

        decoded_text = next((entry for entry in decoded_info if entry), "")
        if not decoded_text:
            raise ValueError(f"No decodable QR payload in: {image_path}")

    try:
        return json.loads(decoded_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid payload in {image_path}: {exc}") from exc


def decode_qrs_to_image(input_dir: Path, output_image: Path) -> None:
    """Read all QR images in a folder and reconstruct the original image."""
    qr_files: List[Path] = sorted(
        [
            path
            for path in input_dir.iterdir()
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        ]
    )

    if not qr_files:
        raise ValueError(f"No image files found in: {input_dir}")

    chunks: Dict[int, str] = {}
    expected_total = None
    expected_sha256 = None

    for qr_file in qr_files:
        payload = decode_qr_image(qr_file)

        for key in ("index", "total", "sha256", "data"):
            if key not in payload:
                raise ValueError(f"Missing '{key}' in payload from {qr_file}")

        index = int(payload["index"])
        total = int(payload["total"])
        sha256 = str(payload["sha256"])
        data = str(payload["data"])

        if expected_total is None:
            expected_total = total
        elif expected_total != total:
            raise ValueError(f"Inconsistent total chunk count in {qr_file}")

        if expected_sha256 is None:
            expected_sha256 = sha256
        elif expected_sha256 != sha256:
            raise ValueError(f"Inconsistent image hash in {qr_file}")

        chunks[index] = data

    assert expected_total is not None
    missing = [idx for idx in range(expected_total) if idx not in chunks]
    if missing:
        raise ValueError(f"Missing chunks: {missing}")

    full_b64 = "".join(chunks[idx] for idx in range(expected_total))
    image_bytes = base64.b64decode(full_b64)

    actual_sha256 = hashlib.sha256(image_bytes).hexdigest()
    if expected_sha256 != actual_sha256:
        raise ValueError("Reconstructed data hash mismatch. Input QR set may be incomplete or corrupted.")

    output_image.parent.mkdir(parents=True, exist_ok=True)
    output_image.write_bytes(image_bytes)

    # Validate output as an image.
    with Image.open(output_image) as img:
        img.verify()

    print(f"Reconstructed image saved to: {output_image}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Encode an image into multiple QR images and decode back to the original image."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    encode_parser = subparsers.add_parser("encode", help="Encode an image into multiple QR image files")
    encode_parser.add_argument("--input", required=True, type=Path, help="Path to input image")
    encode_parser.add_argument("--output-dir", required=True, type=Path, help="Directory for QR image output")
    encode_parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Base64 characters per QR payload (default: {DEFAULT_CHUNK_SIZE})",
    )

    decode_parser = subparsers.add_parser("decode", help="Decode QR image files into the original image")
    decode_parser.add_argument("--input-dir", required=True, type=Path, help="Directory containing QR images")
    decode_parser.add_argument("--output", required=True, type=Path, help="Path for reconstructed image")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "encode":
        if not args.input.is_file():
            raise FileNotFoundError(f"Input image not found: {args.input}")
        if args.chunk_size <= 0:
            raise ValueError("--chunk-size must be > 0")
        encode_image_to_qrs(args.input, args.output_dir, args.chunk_size)

    elif args.command == "decode":
        if not args.input_dir.is_dir():
            raise FileNotFoundError(f"Input directory not found: {args.input_dir}")
        decode_qrs_to_image(args.input_dir, args.output)


if __name__ == "__main__":
    main()
