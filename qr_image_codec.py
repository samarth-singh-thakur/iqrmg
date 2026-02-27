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
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
from PIL import Image
import qrcode
from qrcode.exceptions import DataOverflowError


DEFAULT_PREFERRED_CHUNK_SIZE = 2200
DEFAULT_ERROR_CORRECTION = "L"
ERROR_CORRECTION_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}


def make_qr_image(payload: str, error_correction: int, box_size: int = 10, border: int = 4):
    """Create and return a QR image for a payload string."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=error_correction,
        box_size=box_size,
        border=border,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def fits_qr(payload: str, error_correction: int) -> bool:
    """Return True if payload fits into a single QR code."""
    try:
        make_qr_image(payload, error_correction)
        return True
    except DataOverflowError:
        return False


def choose_encoding(image_bytes: bytes) -> Tuple[str, bytes]:
    """Pick the smaller transport representation: raw bytes or zlib-compressed bytes."""
    compressed = zlib.compress(image_bytes, level=9)
    if len(compressed) < len(image_bytes):
        return "z", compressed
    return "r", image_bytes


def estimate_max_chunk_size(
    filename: str,
    sha256_hex: str,
    mode: str,
    error_correction: int,
    preferred: int,
) -> int:
    """Estimate largest safe chunk size for current QR settings by binary search."""
    # Probe payload uses worst-case metadata chunk and placeholder data.
    low, high = 200, max(200, min(5000, preferred * 2))

    def probe(size: int) -> bool:
        candidate = {
            "v": 2,
            "i": 0,
            "m": {"t": 9999, "n": filename, "h": sha256_hex, "e": mode},
            "d": "A" * size,
        }
        return fits_qr(json.dumps(candidate, separators=(",", ":")), error_correction)

    while low < high:
        mid = (low + high + 1) // 2
        if probe(mid):
            low = mid
        else:
            high = mid - 1

    return min(low, preferred) if preferred > 0 else low


def encode_image_to_qrs(
    input_image: Path,
    output_dir: Path,
    preferred_chunk_size: int = DEFAULT_PREFERRED_CHUNK_SIZE,
    error_correction: str = DEFAULT_ERROR_CORRECTION,
) -> None:
    """Read an image, pack efficiently, and save chunked QR images."""
    image_bytes = input_image.read_bytes()
    sha256_hex = hashlib.sha256(image_bytes).hexdigest()

    mode, payload_bytes = choose_encoding(image_bytes)
    payload_b85 = base64.b85encode(payload_bytes).decode("ascii")

    ec_level = ERROR_CORRECTION_MAP[error_correction]
    chunk_size = estimate_max_chunk_size(input_image.name, sha256_hex, mode, ec_level, preferred_chunk_size)

    total_chunks = (len(payload_b85) + chunk_size - 1) // chunk_size
    if total_chunks == 0:
        raise ValueError("Input image appears empty.")

    output_dir.mkdir(parents=True, exist_ok=True)

    for index in range(total_chunks):
        start = index * chunk_size
        end = start + chunk_size
        data_slice = payload_b85[start:end]

        payload = {"v": 2, "i": index, "d": data_slice}
        if index == 0:
            # Put shared metadata once to reduce repeated overhead.
            payload["m"] = {"t": total_chunks, "n": input_image.name, "h": sha256_hex, "e": mode}

        payload_str = json.dumps(payload, separators=(",", ":"))
        if not fits_qr(payload_str, ec_level):
            raise ValueError(
                "Chunk payload exceeds QR capacity with current settings. "
                "Try lower error correction (L/M) or smaller --chunk-size."
            )

        qr_image = make_qr_image(payload_str, ec_level)
        qr_path = output_dir / f"chunk_{index:04d}.png"
        qr_image.save(qr_path)

    print(
        f"Created {total_chunks} QR images in: {output_dir} "
        f"(encoding={mode}, ec={error_correction}, chunk_size={chunk_size})"
    )


def decode_qr_image(image_path: Path) -> Dict:
    """Decode a single QR image and return parsed JSON payload."""
    detector = cv2.QRCodeDetector()
    image = cv2.imread(str(image_path))

    if image is None:
        raise ValueError(f"Could not open image: {image_path}")

    decoded_text, points, _ = detector.detectAndDecode(image)

    if not decoded_text or points is None:
        ok, decoded_info, _, _ = detector.detectAndDecodeMulti(image)
        if not ok or not decoded_info:
            raise ValueError(f"No QR data found in: {image_path}")

        decoded_text = next((entry for entry in decoded_info if entry), "")
        if not decoded_text:
            raise ValueError(f"No decodable QR payload in: {image_path}")

    try:
        payload = json.loads(decoded_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid payload in {image_path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected payload format in {image_path}")

    return payload


def decode_qrs_to_image(input_dir: Path, output_image: Path) -> None:
    """Read all QR images in a folder and reconstruct the original image."""
    qr_files: List[Path] = sorted(
        p for p in input_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    )
    if not qr_files:
        raise ValueError(f"No image files found in: {input_dir}")

    chunks: Dict[int, str] = {}
    total_chunks: Optional[int] = None
    expected_sha256: Optional[str] = None
    encoding_mode: Optional[str] = None

    for qr_file in qr_files:
        payload = decode_qr_image(qr_file)

        if "i" not in payload or "d" not in payload:
            raise ValueError(f"Missing required keys in {qr_file}")

        index = int(payload["i"])
        chunks[index] = str(payload["d"])

        metadata = payload.get("m")
        if metadata is not None:
            for key in ("t", "h", "e"):
                if key not in metadata:
                    raise ValueError(f"Metadata missing '{key}' in {qr_file}")

            m_total = int(metadata["t"])
            m_sha = str(metadata["h"])
            m_enc = str(metadata["e"])

            if total_chunks is None:
                total_chunks = m_total
                expected_sha256 = m_sha
                encoding_mode = m_enc
            elif total_chunks != m_total or expected_sha256 != m_sha or encoding_mode != m_enc:
                raise ValueError(f"Inconsistent metadata found in {qr_file}")

    if total_chunks is None or expected_sha256 is None or encoding_mode is None:
        raise ValueError("Missing metadata chunk (index 0).")

    missing = [i for i in range(total_chunks) if i not in chunks]
    if missing:
        raise ValueError(f"Missing chunks: {missing}")

    joined = "".join(chunks[i] for i in range(total_chunks))
    packed = base64.b85decode(joined.encode("ascii"))

    if encoding_mode == "z":
        image_bytes = zlib.decompress(packed)
    elif encoding_mode == "r":
        image_bytes = packed
    else:
        raise ValueError(f"Unsupported encoding mode: {encoding_mode}")

    actual_sha = hashlib.sha256(image_bytes).hexdigest()
    if actual_sha != expected_sha256:
        raise ValueError("Reconstructed data hash mismatch. Input QR set may be incomplete or corrupted.")

    output_image.parent.mkdir(parents=True, exist_ok=True)
    output_image.write_bytes(image_bytes)

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
        default=DEFAULT_PREFERRED_CHUNK_SIZE,
        help=f"Preferred payload chars per QR (default: {DEFAULT_PREFERRED_CHUNK_SIZE})",
    )
    encode_parser.add_argument(
        "--error-correction",
        choices=sorted(ERROR_CORRECTION_MAP.keys()),
        default=DEFAULT_ERROR_CORRECTION,
        help="QR error correction level. Lower gives higher data capacity (default: L).",
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
        encode_image_to_qrs(
            args.input,
            args.output_dir,
            preferred_chunk_size=args.chunk_size,
            error_correction=args.error_correction,
        )
    elif args.command == "decode":
        if not args.input_dir.is_dir():
            raise FileNotFoundError(f"Input directory not found: {args.input_dir}")
        decode_qrs_to_image(args.input_dir, args.output)


if __name__ == "__main__":
    main()
