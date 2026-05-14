"""Color utilities: Floyd-Steinberg error-diffusion dithering."""
from __future__ import annotations

from typing import Callable

import numpy as np


def floyd_steinberg_to_6bit(img8: np.ndarray) -> np.ndarray:
    """Error-diffuse an (8, 8, 3) uint8 image (0..255) toward the 6-bit grid.

    Returns an (8, 8, 3) uint8 image already quantized to 0..63 — caller does
    NOT need to ``>> 2`` afterwards. Diffusion happens in gamma-corrected
    8-bit space so banding from naive ``>> 2`` is avoided.

    Standard Floyd-Steinberg 7/3/5/1 weights, per-channel independent.
    """
    if img8.shape != (8, 8, 3) or img8.dtype != np.uint8:
        raise ValueError(f"expected (8,8,3) uint8 image, got {img8.shape} {img8.dtype}")
    buf = img8.astype(np.float32)  # in 0..255
    out = np.zeros((8, 8, 3), dtype=np.uint8)
    # Quantize each pixel and diffuse the residual to neighbors.
    # We quantize "toward the 6-bit grid expressed in 8-bit space": values
    # at multiples of 4 (since 64 levels of 4 each span 0..252).
    for y in range(8):
        for x in range(8):
            old = buf[y, x]
            # Quantize: nearest multiple of 4, clamped to 0..252 -> then >>2
            q8 = np.clip(np.round(old / 4.0) * 4.0, 0.0, 252.0)
            err = old - q8
            out[y, x] = (q8 / 4.0).astype(np.uint8)
            # Diffuse
            if x + 1 < 8:
                buf[y, x + 1] += err * (7.0 / 16.0)
            if y + 1 < 8:
                if x > 0:
                    buf[y + 1, x - 1] += err * (3.0 / 16.0)
                buf[y + 1, x] += err * (5.0 / 16.0)
                if x + 1 < 8:
                    buf[y + 1, x + 1] += err * (1.0 / 16.0)
    return out


def floyd_steinberg_with_quantizer(
    img8: np.ndarray,
    quantizer: Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]],
) -> np.ndarray:
    """Generic FS dither.

    ``quantizer`` takes a (3,) float pixel in 0..255 and returns
    ``(rendered_8bit_pixel, output_byte_or_index)`` — the first is used for
    error computation, the second is stored in the result.

    Used by palette mode to dither against the LP Pro's 128-entry palette.
    """
    if img8.shape != (8, 8, 3) or img8.dtype != np.uint8:
        raise ValueError(f"expected (8,8,3) uint8 image, got {img8.shape} {img8.dtype}")
    buf = img8.astype(np.float32)
    out = np.zeros((8, 8), dtype=np.uint8)
    for y in range(8):
        for x in range(8):
            old = buf[y, x]
            rendered, stored = quantizer(old)
            err = old - rendered
            out[y, x] = stored
            if x + 1 < 8:
                buf[y, x + 1] += err * (7.0 / 16.0)
            if y + 1 < 8:
                if x > 0:
                    buf[y + 1, x - 1] += err * (3.0 / 16.0)
                buf[y + 1, x] += err * (5.0 / 16.0)
                if x + 1 < 8:
                    buf[y + 1, x + 1] += err * (1.0 / 16.0)
    return out
