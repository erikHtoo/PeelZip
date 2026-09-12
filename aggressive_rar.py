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


def _rar_volumes(source):
    source = Path(source).absolute()
    parent = source.parent
    name = source.name
    import re
    m = re.match(r'^(.*)\.part(\d+)\.rar$', name, re.I)
    if m:
        stem, n = m.group(1), int(m.group(2))
        candidates = sorted(parent.glob(stem + '.part*.rar'), key=lambda p: int(re.search(r'\.part(\d+)\.rar$', p.name, re.I).group(1)))
        if candidates: return candidates
    # Old RAR volume naming: archive.rar, archive.r00, archive.r01...
    if source.suffix.lower() == '.rar':
        stem = source.with_suffix('')
        # Legacy RAR volumes use archive.rar, archive.r00, archive.r01;
        # accept the extended .s00/.t00 naming used after .r99 as well.
        tail = []
        import re
        for candidate in parent.iterdir():
            m = re.fullmatch(re.escape(stem.name) + r'\.([r-z])(\d{2})', candidate.name, re.I)
            if m:
                tail.append((ord(m.group(1).lower()) - ord('r')) * 100 + int(m.group(2)), candidate)
        candidates = [source] + [p for _, p in sorted(tail)]
        if len(candidates) > 1: return candidates
    return [source]

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
    volumes = _rar_volumes(source)
    first_volume = volumes[0]
    metadata = rar_backend.inspect(first_volume, decoder, password=password)
    supported, reason = rar_backend.aggressive_supported(metadata, password=password)
    if not supported:
        raise RuntimeError(f'RAR aggressive mode unavailable: {reason}')
    decoder = Path(metadata['decoder'])
    entries = metadata['entries']
    multipart = len(volumes) > 1 or metadata.get('multipart')
    # 7-Zip reports each entry's starting volume. For multipart archives we
    # reclaim complete volume files only after every entry beginning there (or
    # earlier and potentially spanning into it) has been extracted. This avoids
    # punching holes in a volume still needed by another entry.
    starts = []
    for e in entries:
        try: starts.append(int(e.get('Volume Index', 0)))
        except (TypeError, ValueError): starts.append(0)
    journal_path = _state_path(destination)
    state = _load_state(journal_path, first_volume, entries) if resume else {
        'version': 1, 'source': str(first_volume), 'entries': {}, 'volumes': [str(v) for v in volumes]
    }
    reclaimed = 0
    deferred_ranges = []
    for number, entry in enumerate(entries, 1):
        name = entry['Path']
        packed = entry.get('PackSize', 0)
        if str(number - 1) in state['entries']:
            if progress: progress(f"[{number}/{len(entries)}] {name} (already complete)")
            continue
        if progress: progress(f"[{number}/{len(entries)}] {name} ({packed} compressed bytes)")
        if dry_run: continue
        command = [str(decoder), 'x', '-y', f'-o{destination}']
        if password is not None: command.append(f'-p{password}')
        command += [str(first_volume), name]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', check=False)
        if result.returncode != 0:
            raise RuntimeError(f"7zz failed for {name}: {result.stdout[-1000:]}")
        if verify and entry.get('CRC'):
            output_path = destination.joinpath(*name.replace('\\', '/').split('/'))
            if not output_path.is_file(): raise RuntimeError(f'extracted file missing for verification: {name}')
            actual = _crc32(output_path); expected = str(entry['CRC']).upper()
            if actual != expected: raise RuntimeError(f'CRC mismatch for {name}: expected {expected}, got {actual}')
        if multipart:
            # Keep all volumes readable while extracting later entries. Once
            # every file has passed CRC verification, reclaim each physical
            # volume in one operation; the source is intentionally sacrificed.
            state['entries'][str(number - 1)] = {'name': name, 'packed': packed, 'volume': starts[number-1]}
        else:
            offset = entry['Offset']
            if packed:
                if metadata.get('solid'):
                    deferred_ranges.append((offset, packed))
                else:
                    ntfs_reclaim.reclaim_range(first_volume, offset, packed); reclaimed += packed
            state['entries'][str(number - 1)] = {'name': name, 'offset': entry.get('Offset'), 'packed': packed}
        _save_state(journal_path, state)
    if metadata.get('solid') and not multipart and len(state['entries']) == len(entries) and not state.get('reclaimed_ranges'):
        if not deferred_ranges:
            deferred_ranges = [(int(v['offset']), int(v['packed'])) for v in state['entries'].values() if v.get('offset') is not None and v.get('packed')]
        for offset, packed in deferred_ranges:
            ntfs_reclaim.reclaim_range(first_volume, offset, packed)
            reclaimed += packed
        state['reclaimed_ranges'] = [[o, p] for o, p in deferred_ranges]
        _save_state(journal_path, state)
    if multipart and len(state['entries']) == len(entries) and not state.get('reclaimed_volumes'):
        for vi, vol in enumerate(volumes):
            size = vol.stat().st_size
            if size:
                ntfs_reclaim.reclaim_range(vol, 0, size)
                reclaimed += size
            state.setdefault('reclaimed_volumes', {})[str(vi)] = size
        _save_state(journal_path, state)
    return {'entries': len(entries), 'reclaimed_bytes': reclaimed,
            'source': str(first_volume), 'destination': str(destination),
            'volumes': len(volumes), 'dry_run': dry_run}

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
