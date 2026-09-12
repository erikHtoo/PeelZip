"""Aggressive ZIP extractor using native 7-Zip decoding and range reclamation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import re
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


def _split_volumes(source):
    lower = source.name.lower()
    if lower.endswith('.zip.001'):
        prefix = source.name[:-4]
    elif re.search(r'\.z\d\d$', lower):
        prefix = source.name[:-3]
    else:
        return [source]
    volumes = []
    if lower.endswith('.zip.001'):
        number = 1
        while True:
            path = source.with_name(f'{prefix}.{number:03d}')
            if not path.is_file():
                break
            volumes.append(path)
            number += 1
    else:
        number = 1
        while True:
            path = source.with_name(f'{prefix}z{number:02d}')
            if not path.is_file():
                break
            volumes.append(path)
            number += 1
        final = source.with_name(f'{prefix}zip')
        if final.is_file():
            volumes.append(final)
    if not volumes:
        raise RuntimeError('split ZIP volume set is incomplete')
    return volumes


def _validate_split_volumes(volumes, decoder):
    result = subprocess.run([str(decoder), 'l', '-slt', str(volumes[0])],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')
    if result.returncode not in (0, 1):
        raise RuntimeError('7zz could not inspect split ZIP volumes')
    volume_match = re.search(r'^Volumes = (\d+)$', result.stdout, re.MULTILINE)
    total_match = re.search(r'^Total Physical Size = (\d+)$', result.stdout, re.MULTILINE)
    if not volume_match or not total_match:
        raise RuntimeError('selected file is not a recognized split ZIP volume')
    expected_count = int(volume_match.group(1))
    expected_size = int(total_match.group(1))
    actual_size = sum(path.stat().st_size for path in volumes)
    if len(volumes) != expected_count:
        raise RuntimeError(f'incomplete split ZIP set: found {len(volumes)}, expected {expected_count}')
    if actual_size != expected_size:
        raise RuntimeError(f'split ZIP size mismatch: found {actual_size}, expected {expected_size}')


def _split_volumes_legacy(source):
    """Kept as a named compatibility hook for callers importing the helper."""
    return _split_volumes(source)


def _split_read(volumes, offset, length):
    result = bytearray()
    logical = 0
    end = offset + length
    for volume in volumes:
        size = volume.stat().st_size
        begin = max(offset, logical)
        finish = min(end, logical + size)
        if finish > begin:
            with volume.open('rb') as stream:
                stream.seek(begin - logical)
                result.extend(stream.read(finish - begin))
        logical += size
        if logical >= end:
            break
    if len(result) != length:
        raise OSError('short read from split ZIP volumes')
    return bytes(result)


def _split_data_offset(volumes, local_offset):
    header = _split_read(volumes, local_offset, 30)
    fields = struct.unpack('<4s5H3I2H', header)
    if fields[0] != b'PK\x03\x04':
        raise ValueError('invalid split ZIP local header')
    return local_offset + 30 + fields[-2] + fields[-1]


def _split_segments(volumes, offset, length):
    segments = []
    logical = 0
    end = offset + length
    for volume in volumes:
        size = volume.stat().st_size
        begin = max(offset, logical)
        finish = min(end, logical + size)
        if finish > begin:
            segments.append((volume, begin - logical, finish - begin))
        logical += size
        if logical >= end:
            break
    if not segments or sum(item[2] for item in segments) != length:
        raise RuntimeError('ZIP range extends beyond split volume set')
    return segments


def _split_entries(source, decoder, password=None):
    command = [str(decoder), 'l', '-slt']
    if password is not None: command.append(f'-p{password}')
    command.append(str(source))
    result = subprocess.run(command,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')
    if result.returncode not in (0, 1):
        raise RuntimeError(f'7zz split ZIP listing failed: {result.stdout[-1000:]}')
    records = []
    current = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            if current.get('Path') and current.get('Offset') is not None:
                records.append(current)
            current = {}
        elif ' = ' in line:
            key, value = line.split(' = ', 1)
            current[key] = value
    if current.get('Path') and current.get('Offset') is not None:
        records.append(current)
    entries = []
    for item in records:
        if not item.get('Packed Size'):
            continue
        if item.get('Encrypted') == '+' and password is None:
            continue
        entries.append({
            'filename': item['Path'], 'file_size': int(item['Size']),
            'compress_size': int(item['Packed Size']),
            'CRC': int(item['CRC'], 16), 'header_offset': int(item['Offset']),
        })
    if not entries:
        raise RuntimeError('no split ZIP entries with offset metadata found')
    return entries


def _crc32(path):
    value = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value = zlib.crc32(chunk, value)
    return value & 0xffffffff


def _state_path(destination):
    return destination / '.peelzip-zip-state.json'


def _save_state(path, state):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


def run(source, destination, decoder=None, verify=True, progress=None,
        resume=False, password=None):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    decoder = Path(decoder or (Path(__file__).resolve().parent / 'tools' / '7zz.exe'))
    if destination.exists() and any(destination.iterdir()) and not resume:
        raise FileExistsError(f'destination must be empty: {destination}')
    destination.mkdir(parents=True, exist_ok=True)
    volumes = _split_volumes(source)
    split = len(volumes) > 1
    if split:
        _validate_split_volumes(volumes, decoder)
    if split:
        infos = _split_entries(source, decoder, password)
    else:
        with zipfile.ZipFile(source) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
        infos.sort(key=lambda item: item.header_offset, reverse=True)
    if split:
        infos.sort(key=lambda item: item['header_offset'], reverse=True)
    # Validate all compressed ranges before modifying the source. Overlap means
    # the decoder metadata is not a trustworthy physical map.
    ranges = []
    for info in infos:
        packed = info['compress_size'] if split else info.compress_size
        if not packed: continue
        local = (_split_data_offset(volumes, info['header_offset']) if split else _data_offset(source, info))
        ranges.append((local, local + packed, info['filename'] if split else info.filename))
    for (a0, a1, an), (b0, b1, bn) in zip(sorted(ranges), sorted(ranges)[1:]):
        if b0 < a1:
            raise RuntimeError(f'overlapping compressed ranges: {an} and {bn}')
    journal_path = _state_path(destination)
    if resume and journal_path.exists():
        state = json.loads(journal_path.read_text(encoding='utf-8'))
        if state.get('source') != str(source):
            raise RuntimeError('ZIP journal does not match this source archive')
    else:
        state = {'version': 1, 'source': str(source), 'entries': {}}
    reclaimed = 0
    for number, info in enumerate(infos, 1):
        name = info['filename'] if split else info.filename
        target = destination.joinpath(*name.replace('\\', '/').split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        key = str(info['header_offset'] if split else info.header_offset)
        if key in state['entries']:
            expected_size = info['file_size'] if split else info.file_size
            if not target.is_file() or target.stat().st_size != expected_size:
                raise RuntimeError(f'completed ZIP output is missing or incomplete: {name}')
            expected_crc = info['CRC'] if split else info.CRC
            if verify and _crc32(target) != expected_crc:
                raise RuntimeError(f'completed ZIP output failed verification: {name}')
            if progress:
                progress(f'[{number}/{len(infos)}] {name} (already complete)')
            continue
        if progress:
            progress(f'[{number}/{len(infos)}] {name}')
        command = [str(decoder), 'x', '-y', f'-o{destination}']
        if password is not None:
            command.append(f'-p{password}')
        command += [str(volumes[0] if split else source), name]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', check=False)
        if result.returncode:
            raise RuntimeError(f'7zz failed for {name}: {result.stdout[-1000:]}')
        if verify:
            expected_size = info['file_size'] if split else info.file_size
            expected_crc = info['CRC'] if split else info.CRC
            if not target.is_file() or target.stat().st_size != expected_size:
                raise RuntimeError(f'size verification failed for {name}')
            if _crc32(target) != expected_crc:
                raise RuntimeError(f'CRC verification failed for {name}')
        packed = info['compress_size'] if split else info.compress_size
        local_offset = None
        segments = []
        if packed:
            local_offset = (_split_data_offset(volumes, info['header_offset']) if split
                            else _data_offset(source, info))
            segments = _split_segments(volumes, local_offset, packed) if split else [(source, local_offset, packed)]
            for volume, segment_offset, segment_length in segments:
                ntfs_reclaim.reclaim_range(volume, segment_offset, segment_length)
                reclaimed += segment_length
        state['entries'][key] = {
            'name': name, 'offset': local_offset, 'packed': packed,
            'volumes': [str(volume) for volume, _, _ in segments],
        }
        _save_state(journal_path, state)
    return {'entries': len(infos), 'reclaimed_bytes': reclaimed,
            'source': str(source), 'destination': str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--decoder')
    parser.add_argument('--no-verify', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--password')
    args = parser.parse_args()
    try:
        print(run(args.source, args.destination, args.decoder,
                  not args.no_verify, print, args.resume, args.password))
    except Exception as exc:
        print(f'Stopped: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
