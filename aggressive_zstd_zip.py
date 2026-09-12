"""High-reclamation Zstandard ZIP extractor (Windows, intentionally destructive).

Streams ZIP method 93 entries, writes output, then punches consumed compressed
ranges. It favors low peak storage over recovery after interruption.
"""
import argparse
import archive_checks
import os
from pathlib import Path
import struct
import sys
import zipfile

if not hasattr(zipfile, 'ZIP_ZSTANDARD'):
    try:
        from backports.zstd import zipfile
    except ImportError:
        pass

from ntfs_reclaim import reclaim_range

try:
    from backports import zstd
except ImportError:
    try:
        from compression import zstd
    except ImportError:
        zstd = None

IN_CHUNK = 4 * 1024 * 1024
OUT_CHUNK = 16 * 1024 * 1024


def data_offset(source, info):
    with source.open('rb') as f:
        f.seek(info.header_offset)
        header = f.read(30)
    fields = struct.unpack('<4s5H3I2H', header)
    if fields[0] != b'PK\x03\x04': raise ValueError('Invalid ZIP local header')
    return info.header_offset + 30 + fields[-2] + fields[-1]


def decode_entry(source, info, target, punch=True):
    start = data_offset(source, info); remaining = info.compress_size
    if info.compress_type == zipfile.ZIP_STORED:
        decoder = None
    elif info.compress_type == 93:
        if zstd is None: raise RuntimeError('Install backports.zstd or use Python 3.14+')
        decoder = zstd.ZstdDecompressor()
    else: raise ValueError(f'Unsupported aggressive method {info.compress_type}')
    crc = 0; written = 0; pending = bytearray()
    with source.open('rb', buffering=0) as inp, target.open('wb') as out:
        inp.seek(start)
        while remaining or (decoder is not None and not decoder.needs_input):
            if remaining:
                take = min(IN_CHUNK, remaining); compressed = inp.read(take)
                if len(compressed) != take: raise OSError('Short ZIP entry read')
                remaining -= take; pending.extend(compressed)
            else: compressed = b''
            finished = False
            while True:
                if decoder is None:
                    output = compressed; compressed = b''
                else:
                    try:
                        output = decoder.decompress(compressed, OUT_CHUNK)
                    except EOFError:
                        if remaining == 0:
                            finished = True; output = b''
                        else:
                            raise
                    compressed = b''
                if output:
                    out.write(output); crc = __import__('zlib').crc32(output, crc) & 0xffffffff; written += len(output)
                if decoder is None or decoder.needs_input or finished: break
            out.flush(); os.fsync(out.fileno())
            if punch and pending:
                # The decoder has requested more input, so the buffered frame
                # bytes have been consumed. Reclaim them as one source range.
                reclaim_range(source, start + (info.compress_size - remaining - len(pending)), len(pending))
                pending.clear()
            if not remaining and (decoder is None or decoder.needs_input or finished): break
    if written != info.file_size or crc != info.CRC: raise ValueError(f'CRC/size verification failed: {info.filename}')


def run(source, destination, accept=False):
    if sys.platform != 'win32': raise OSError('Aggressive mode requires Windows')
    if not accept: raise ValueError('Requires --accept-data-loss-risk')
    source = Path(source).absolute(); destination = Path(destination).absolute()
    if destination.exists(): raise ValueError('Destination already exists')
    destination.mkdir(parents=True)
    with zipfile.ZipFile(source) as z:
        infos = z.infolist()
        archive_checks.targets(destination, [i.filename for i in infos])
        if any(i.compress_type not in (zipfile.ZIP_STORED, 93) for i in infos if not i.is_dir()):
            raise ValueError('Archive contains a compression method unsupported by aggressive mode')
        for index, info in enumerate(infos):
            target = destination.joinpath(*info.filename.rstrip('/').split('/'))
            if info.is_dir(): target.mkdir(parents=True, exist_ok=True); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            print(f'[{index+1}/{len(infos)}] {info.filename}', flush=True)
            decode_entry(source, info, target)
    print('Complete. The ZIP remains the same logical length but reclaimed ranges are zeroed and it is no longer a valid normal ZIP.', flush=True)


def main():
    p=argparse.ArgumentParser(); p.add_argument('zip'); p.add_argument('destination'); p.add_argument('--accept-data-loss-risk',action='store_true'); a=p.parse_args()
    try: run(a.zip,a.destination,a.accept_data_loss_risk)
    except Exception as e: print(f'Stopped: {e}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': sys.exit(main())
