"""Experimental low-space extractor for 7z packed folders."""
from __future__ import annotations

import argparse
import binascii
import json
from pathlib import Path
import subprocess

import ntfs_reclaim


def _parse_listing(text):
    archive = {}
    entries = []
    record = {}
    in_items = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == '----------':
            in_items = True
            continue
        if not line:
            if record.get('Path'):
                entries.append(record)
                record = {}
            continue
        if ' = ' not in line:
            continue
        key, value = line.split(' = ', 1)
        if not in_items:
            archive[key] = value
        else:
            record[key] = value
    if record.get('Path'):
        entries.append(record)
    return archive, entries


def inspect(source, decoder):
    result = subprocess.run([str(decoder), 'l', '-slt', str(source)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(f'7zz listing failed: {result.stdout[-1000:]}')
    archive, entries = _parse_listing(result.stdout)
    if archive.get('Type') not in ('7z', 'Split'):
        raise RuntimeError('aggressive 7z mode requires a 7z archive')
    blocks = {}
    for entry in entries:
        if 'Block' not in entry:
            continue
        if entry.get('Encrypted') == '+':
            raise RuntimeError('encrypted 7z archives are not supported in aggressive mode')
        if entry.get('Block') is None:
            raise RuntimeError(f'missing block metadata for {entry.get("Path", "entry")}')
        block = int(entry['Block'])
        if block not in blocks:
            if not entry.get('Offset') or not entry.get('Packed Size'):
                raise RuntimeError(f'missing packed size for block {block}')
            blocks[block] = {
                'block': block,
                'offset': int(entry['Offset']),
                'packed': int(entry['Packed Size']),
                'entries': [],
            }
        blocks[block]['entries'].append(entry)
    return archive, entries, [blocks[k] for k in sorted(blocks)]


def _volumes(source):
    if source.name.lower().endswith('.7z.001'):
        prefix = source.name[:-4]
        paths = []
        number = 1
        while True:
            candidate = source.with_name(f'{prefix}.{number:03d}')
            if not candidate.is_file():
                break
            paths.append(candidate)
            number += 1
        if not paths:
            raise RuntimeError('split 7z volume set is incomplete')
        return paths
    return [source]


def _physical_segments(source, offset, length):
    segments = []
    logical = 0
    remaining = length
    for volume in _volumes(source):
        size = volume.stat().st_size
        begin = max(offset, logical)
        end = min(offset + length, logical + size)
        if end > begin:
            segments.append((volume, begin - logical, end - begin))
            remaining -= end - begin
        logical += size
        if remaining <= 0:
            break
    if remaining:
        raise RuntimeError('packed range extends beyond split volume set')
    return segments


def _crc32(path):
    value = 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value = binascii.crc32(chunk, value)
    return f'{value & 0xffffffff:08X}'


def _state_path(destination):
    return destination / '.peelzip-7z-state.json'


def _save_state(path, state):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


def extract_aggressive(source, destination, decoder=None, progress=None,
                       verify=False, resume=False):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    decoder = Path(decoder or (Path(__file__).resolve().parent / 'tools' / '7zz.exe'))
    if destination.exists() and any(destination.iterdir()) and not resume:
        raise FileExistsError(f'destination must be empty: {destination}')
    destination.mkdir(parents=True, exist_ok=True)
    _, _, blocks = inspect(source, decoder)
    archive_source = _volumes(source)[0]
    journal = _state_path(destination)
    if resume and journal.exists():
        state = json.loads(journal.read_text(encoding='utf-8'))
        if state.get('source') != str(source):
            raise RuntimeError('7z journal does not match this source archive')
    else:
        state = {'version': 1, 'source': str(source), 'blocks': {}}
    reclaimed = 0
    for number, block in enumerate(blocks, 1):
        key = str(block['block'])
        names = [entry['Path'] for entry in block['entries']]
        if key in state['blocks']:
            if progress:
                progress(f'[{number}/{len(blocks)}] block {key} (already complete)')
            continue
        if progress:
            progress(f'[{number}/{len(blocks)}] block {key}: {len(names)} file(s)')
        command = [str(decoder), 'x', '-y', f'-o{destination}', str(archive_source), *names]
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace')
        if result.returncode:
            raise RuntimeError(f'7zz failed for block {key}: {result.stdout[-1000:]}')
        if verify:
            for entry in block['entries']:
                expected = entry.get('CRC')
                output = destination.joinpath(*entry['Path'].replace('\\', '/').split('/'))
                if expected and (not output.is_file() or _crc32(output) != expected.upper()):
                    raise RuntimeError(f'CRC mismatch for {entry["Path"]}')
        segments = _physical_segments(source, block['offset'], block['packed'])
        for volume, local_offset, segment_length in segments:
            ntfs_reclaim.reclaim_range(volume, local_offset, segment_length)
            reclaimed += segment_length
        state['blocks'][key] = {
            'offset': block['offset'], 'packed': block['packed'],
            'names': names,
            'segments': [[str(v), o, n] for v, o, n in segments],
        }
        _save_state(journal, state)
    return {'blocks': len(blocks), 'reclaimed_bytes': reclaimed,
            'source': str(source), 'destination': str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--decoder')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    print(extract_aggressive(args.source, args.destination, args.decoder,
                             print, args.verify, args.resume))


if __name__ == '__main__':
    main()
