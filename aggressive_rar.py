"""Experimental low-space extractor for eligible non-solid RAR archives.

The native 7zz bridge exposes each RAR5 entry's compressed-data offset. This
tool extracts one entry at a time, waits for 7zz to finish successfully, then
returns that entry's compressed range to NTFS. The archive keeps its logical
length but is no longer a valid normal RAR after reclamation.
"""
from __future__ import annotations

import argparse
import binascii
import json
from pathlib import Path
import subprocess

import ntfs_reclaim
import rar_backend


def _crc32(path):
    value = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value = binascii.crc32(block, value)
    return f'{value & 0xffffffff:08X}'


def _state_path(destination):
    return destination / '.peelzip-rar-state.json'


def _load_state(path, source, entries):
    if not path.is_file():
        return {'version': 1, 'source': str(source), 'entries': {}}
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'invalid aggressive RAR journal: {path}') from exc
    if state.get('version') != 1 or state.get('source') != str(source):
        raise RuntimeError('aggressive RAR journal does not match this source archive')
    known = {str(i) for i in range(len(entries))}
    if not set(state.get('entries', {})).issubset(known):
        raise RuntimeError('aggressive RAR journal contains unknown entries')
    return state


def _save_state(path, state):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


def extract_aggressive(source, destination, decoder=None, progress=None,
                       dry_run=False, verify=False, resume=False, password=None):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if destination.exists() and any(destination.iterdir()) and not resume:
        raise FileExistsError(f"destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    metadata = rar_backend.inspect(source, decoder, password=password)
    supported, reason = rar_backend.aggressive_supported(metadata, password=password)
    if not supported:
        raise RuntimeError(f"RAR aggressive mode unavailable: {reason}")
    decoder = Path(metadata['decoder'])
    entries = metadata['entries']
    journal_path = _state_path(destination)
    state = _load_state(journal_path, source, entries) if resume else {
        'version': 1, 'source': str(source), 'entries': {}
    }
    reclaimed = 0
    for number, entry in enumerate(entries, 1):
        name = entry['Path']
        offset = entry['Offset']
        packed = entry['PackSize']
        if str(number - 1) in state['entries']:
            if progress:
                progress(f"[{number}/{len(entries)}] {name} (already complete)")
            continue
        if progress:
            progress(f"[{number}/{len(entries)}] {name} ({packed} compressed bytes)")
        if dry_run:
            continue
        command = [str(decoder), 'x', '-y', f'-o{destination}']
        if password is not None:
            command.append(f'-p{password}')
        command += [str(source), name]
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', check=False)
        if result.returncode != 0:
            raise RuntimeError(f"7zz failed for {name}: {result.stdout[-1000:]}")
        if verify and entry.get('CRC'):
            output_path = destination.joinpath(*name.replace('\\', '/').split('/'))
            if not output_path.is_file():
                raise RuntimeError(f"extracted file missing for verification: {name}")
            actual = _crc32(output_path)
            expected = str(entry['CRC']).upper()
            if actual != expected:
                raise RuntimeError(f"CRC mismatch for {name}: expected {expected}, got {actual}")
        if packed:
            ntfs_reclaim.reclaim_range(source, offset, packed)
            reclaimed += packed
        if not dry_run:
            state['entries'][str(number - 1)] = {
                'name': name, 'offset': offset, 'packed': packed,
            }
            _save_state(journal_path, state)
    return {'entries': len(entries), 'reclaimed_bytes': reclaimed,
            'source': str(source), 'destination': str(destination),
            'dry_run': dry_run}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--decoder')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verify', action='store_true',
                        help='verify each extracted file CRC before reclaiming its RAR range')
    parser.add_argument('--resume', action='store_true',
                        help='resume using the destination aggressive RAR journal')
    parser.add_argument('--password')
    args = parser.parse_args()
    result = extract_aggressive(args.source, args.destination,
                                args.decoder, print, args.dry_run, args.verify,
                                args.resume, args.password)
    print(result)


if __name__ == '__main__':
    main()
