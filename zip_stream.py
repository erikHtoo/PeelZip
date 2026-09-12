"""Bounded-memory ZIP extraction with reclamation of bytes already in memory.

No restart checkpoint is promised inside a compressed stream. CRC and size are
checked at EOF; optional output rereading verifies the bytes written to disk.
"""
import os
from pathlib import Path
import zipfile
try:
    from backports.zstd import zipfile
except ImportError:
    pass
import zlib
import ntfs_reclaim

CHUNK = 1024 * 1024


def extract(source, info, target, offset, progress=None):
    end = offset + info.compress_size
    consumed = offset
    written = 0
    crc = 0
    with Path(source).open('rb', buffering=0) as raw:
        with zipfile.ZipFile(raw) as archive, archive.open(info) as inp, target.open('xb') as out:
            while True:
                data = inp.read(CHUNK)
                if not data:
                    break
                out.write(data)
                out.flush()
                written += len(data)
                crc = zlib.crc32(data, crc)
                # ZipExtFile has read these bytes into its decoder/buffers. No
                # other member is read concurrently; never include a header.
                upto = min(end, max(consumed, raw.tell()))
                if upto > consumed:
                    ntfs_reclaim.reclaim_range(source, consumed, upto - consumed)
                    consumed = upto
                if progress:
                    progress(f'Bytes: {written}/{info.file_size} {info.filename}')
            if written != info.file_size or crc & 0xffffffff != info.CRC:
                raise ValueError(f'CRC/size mismatch: {info.filename}')
    return consumed - offset
