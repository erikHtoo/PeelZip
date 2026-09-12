"""Aggressive ZIP extractor using native 7-Zip decoding and range reclamation."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct
import subprocess
import zlib
import zipfile

import ntfs_reclaim


def _data_offset(source, info):
    with source.open('rb') as stream:
        stream.seek(info.header_offset)
        header = stream.read(30)
    fields = struct.unpack('<4s5H3I2H', header)
    if fields[0] != b'PK\x03\x04':
        raise ValueError(f'invalid local header for {info.filename}')
    return info.header_offset + 30 + fields[-2] + fields[-1]


def _crc32(path):
    value = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value = zlib.crc32(chunk, value)
    return value & 0xffffffff


def run(source, destination, decoder=None, verify=True, progress=None):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    decoder = Path(decoder or (Path(__file__).resolve().parent / 'tools' / '7zz.exe'))
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f'destination must be empty: {destination}')
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
    infos.sort(key=lambda item: item.header_offset, reverse=True)
    reclaimed = 0
    for number, info in enumerate(infos, 1):
        target = destination.joinpath(*info.filename.replace('\\', '/').split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress(f'[{number}/{len(infos)}] {info.filename}')
        result = subprocess.run(
            [str(decoder), 'x', '-y', f'-o{destination}', str(source), info.filename],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', check=False)
        if result.returncode:
            raise RuntimeError(f'7zz failed for {info.filename}: {result.stdout[-1000:]}')
        if verify:
            if not target.is_file() or target.stat().st_size != info.file_size:
                raise RuntimeError(f'size verification failed for {info.filename}')
            if _crc32(target) != info.CRC:
                raise RuntimeError(f'CRC verification failed for {info.filename}')
        if info.compress_size:
            ntfs_reclaim.reclaim_range(source, _data_offset(source, info), info.compress_size)
            reclaimed += info.compress_size
    return {'entries': len(infos), 'reclaimed_bytes': reclaimed,
            'source': str(source), 'destination': str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--decoder')
    parser.add_argument('--no-verify', action='store_true')
    args = parser.parse_args()
    try:
        print(run(args.source, args.destination, args.decoder,
                  not args.no_verify, print))
    except Exception as exc:
        print(f'Stopped: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
