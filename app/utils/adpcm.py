"""IMA-ADPCM 解码 / IMA-ADPCM codec helpers (matches firmware)."""

from __future__ import annotations

import struct

import numpy as np

from ..config import USB_ASR_ADPCM_HEADER_SIZE


# IMA-ADPCM step / index tables (must match firmware encoder).
_ADPCM_STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544,
    598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707,
    1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871,
    5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635,
    13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]

_ADPCM_INDEX_TABLE = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def adpcm_decode(adpcm_data: bytes) -> bytes:
    """Decode IMA-ADPCM nibbles to 16-bit mono PCM @ 16 kHz."""
    if len(adpcm_data) < USB_ASR_ADPCM_HEADER_SIZE + 1:
        return b""

    predictor = int.from_bytes(adpcm_data[0:2], "little", signed=True)
    step_index = min(max(adpcm_data[2], 0), 88)
    data = adpcm_data[USB_ASR_ADPCM_HEADER_SIZE:]

    samples: list[int] = []
    for byte in data:
        for nibble in (byte >> 4, byte & 0x0F):
            step = _ADPCM_STEP_TABLE[step_index]
            delta = step >> 3
            if nibble & 4:
                delta += step
            if nibble & 2:
                delta += step >> 1
            if nibble & 1:
                delta += step >> 2
            if nibble & 8:
                predictor -= delta
            else:
                predictor += delta
            predictor = max(-32768, min(32767, predictor))
            step_index = max(0, min(88, step_index + _ADPCM_INDEX_TABLE[nibble & 7]))
            samples.append(predictor)

    return np.asarray(samples, dtype="<i2").tobytes()


def adpcm_to_wav(adpcm_data: bytes) -> bytes:
    """Decode ADPCM and wrap in a proper WAV header (16 kHz / mono / s16le)."""
    pcm = adpcm_decode(adpcm_data)
    if not pcm:
        return b""

    header = bytearray(44)
    pcm_bytes = len(pcm)
    header[0:4] = b"RIFF"
    struct.pack_into("<I", header, 4, pcm_bytes + 36)
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    struct.pack_into("<I", header, 16, 16)
    struct.pack_into("<H", header, 20, 1)        # PCM
    struct.pack_into("<H", header, 22, 1)        # mono
    struct.pack_into("<I", header, 24, 16000)    # sample rate
    struct.pack_into("<I", header, 28, 32000)    # byte rate
    struct.pack_into("<H", header, 32, 2)        # block align
    struct.pack_into("<H", header, 34, 16)       # bits per sample
    header[36:40] = b"data"
    struct.pack_into("<I", header, 40, pcm_bytes)

    return bytes(header) + pcm
