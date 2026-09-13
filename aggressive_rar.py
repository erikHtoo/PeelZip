"""Experimental low-space extractor for eligible RAR archives.

The native 7zz bridge exposes each RAR5 entry's compressed-data offset. This
tool reclaims decoder-consumed RAR5 input during extraction. Other decoders
fall back to completed file/group reclamation. The archive keeps its logical
length but is no longer a valid normal RAR after reclamation.
"""
from __future__ import annotations

import argparse
import binascii
import json
from pathlib import Path

import ntfs_reclaim
import rar_backend
import archive_checks
import rar_parts
import native_stream


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
    if state.get('active_group'):
        raise RuntimeError('Cannot resume an interrupted streaming RAR group; compressed input may be consumed')
    known = {str(i) for i in range(len(entries))}
    if not set(state.get('entries', {})).issubset(known):
        raise RuntimeError('aggressive RAR journal contains unknown entries')
    return state


def _rar_volumes(source):
    import re
    source = Path(source).absolute()
    m = re.fullmatch(r'(.*)\.part(\d+)\.rar', source.name, re.I)
    if m:
        pattern = re.compile(re.escape(m[1]) + r'\.part(\d+)\.rar', re.I)
        indexed = [(int(match[1]), p) for p in source.parent.iterdir()
                   if (match := pattern.fullmatch(p.name))]
        first = 1
    else:
        m = re.fullmatch(r'(.*)\.(?:rar|[r-z]\d{2})', source.name, re.I)
        if not m: return [source]
        pattern = re.compile(re.escape(m[1]) + r'\.(rar|([r-z])(\d{2}))', re.I)
        indexed = []
        for p in source.parent.iterdir():
            match = pattern.fullmatch(p.name)
            if match:
                n = 0 if match[1].lower() == 'rar' else 1 + (ord(match[2].lower()) - ord('r')) * 100 + int(match[3])
                indexed.append((n, p))
        first = 0
    indexed.sort()
    if [n for n, _ in indexed] != list(range(first, first + len(indexed))):
        raise ValueError('Missing or duplicate RAR volume numbers')
    return [p for _, p in indexed]

def _save_state(path, state):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding='utf-8')
    temporary.replace(path)


def extract_aggressive(source, destination, decoder=None, progress=None,
                       dry_run=False, verify=False, resume=False, password=None):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if not dry_run and destination.exists() and any(destination.iterdir()) and not resume:
        raise FileExistsError(f"destination must be empty: {destination}")
    volumes = _rar_volumes(source)
    first_volume = volumes[0]
    metadata = rar_backend.inspect(first_volume, decoder, password=password)
    supported, reason = rar_backend.aggressive_supported(metadata, password=password)
    if not supported:
        raise RuntimeError(f'RAR aggressive mode unavailable: {reason}')
    decoder = Path(metadata['decoder'])
    entries = metadata['entries']
    archive_checks.targets(destination, [e['Path'] for e in entries])
    multipart = len(volumes) > 1 or metadata.get('multipart')
    if multipart:
        expected = metadata.get('header', {}).get('Volumes')
        if expected and int(expected) != len(volumes):
            raise RuntimeError(f'incomplete RAR volume set: found {len(volumes)}, expected {expected}')
        total = metadata.get('header', {}).get('Total Physical Size')
        if total and int(total) != sum(v.stat().st_size for v in volumes):
            raise RuntimeError('RAR volume sizes do not match archive metadata')
    parts = rar_parts.payloads(volumes, entries) if multipart else [
        [[0, int(e['Offset']), int(e['PackSize'])]] for e in entries]
    if not multipart:
        ranges = sorted((int(e['Offset']), int(e['Offset']) + int(e.get('PackSize', 0)), e['Path']) for e in entries if e.get('PackSize'))
        for (a0, a1, an), (b0, b1, bn) in zip(ranges, ranges[1:]):
            if b0 < a1:
                raise RuntimeError(f'overlapping RAR ranges: {an} and {bn}')
    if not multipart:
        archive_checks.ranges([(a,b) for a,b,_ in ranges], source.stat().st_size)
    if dry_run:
        return {'entries': len(entries), 'dry_run': True, 'reclaimed_bytes': 0,
                'volumes': len(volumes)}
    destination.mkdir(parents=True, exist_ok=True)
    journal_path = _state_path(destination)
    state = _load_state(journal_path, first_volume, entries) if resume else {
        'version': 1, 'source': str(first_volume), 'entries': {}, 'volumes': [str(v) for v in volumes]
    }
    # Older journals checkpointed individual members inside a solid group.
    # Starting in the middle cannot recreate the decoder dictionary.
    if resume and metadata.get('solid'):
        begin = 0
        for end in range(1, len(entries) + 1):
            if end == len(entries) or entries[end].get('Solid') != '+':
                done = sum(str(i) in state['entries'] for i in range(begin, end))
                if 0 < done < end - begin:
                    raise RuntimeError('Cannot resume a partially completed solid RAR group')
                begin = end
    mapping = {'volumes': [{'path': str(v), 'size': v.stat().st_size} for v in volumes], 'parts': parts}
    if multipart and state.get('mapping', mapping) != mapping:
        raise RuntimeError('RAR volume mapping differs from the resume journal')
    state['mapping'] = mapping
    reclaimed = 0
    allocation_before = sum(ntfs_reclaim.allocated_bytes(v) for v in volumes)
    group_reclaimer = None
    streamed = 0
    for number, entry in enumerate(entries, 1):
        name = entry['Path']
        packed = entry.get('PackSize', 0)
        if str(number - 1) in state['entries']:
            output = destination.joinpath(*name.replace('\\', '/').split('/'))
            if not output.is_file() or output.stat().st_size != int(entry['Size']):
                raise RuntimeError(f'Completed output missing or wrong size: {name}')
            if entry.get('CRC') and _crc32(output) != entry['CRC'].upper():
                raise RuntimeError(f'Completed output CRC mismatch: {name}')
            if progress: progress(f"[{number}/{len(entries)}] {name} (already complete)")
            continue
        if progress: progress(f"[{number}/{len(entries)}] {name} ({packed} compressed bytes)")
        if not metadata.get('solid') or number == 1 or entry.get('Solid') != '+':
            names = [name]
            if metadata.get('solid'):
                for following in entries[number:]:
                    if following.get('Solid') != '+': break
                    names.append(following['Path'])
            group_parts = [part for entry_parts in parts[number-1:number-1+len(names)] for part in entry_parts]
            group_reclaimer = native_stream.Reclaimer([(volumes[vi], o, n) for vi, o, n in group_parts])
            state['active_group'] = names
            _save_state(journal_path, state)
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', encoding='utf-8', delete=False) as listing:
                listing.write('\n'.join(names))
                list_path = Path(listing.name)
            try:
                command = [str(decoder), 'x', '-y', '-spd', '-scsUTF-8', f'-o{destination}', f'-i@{list_path}']
                if password is not None: command.append(f'-p{password}')
                command += ['--', str(first_volume)]
                def translate(vi, offset, length):
                    if not 0 <= vi < len(volumes):
                        raise RuntimeError('Decoder reported an unknown RAR volume')
                    return [(volumes[vi], offset, length)]
                native_stream.run(command, 'rar5', group_reclaimer, translate)
                streamed += group_reclaimer.reclaimed
            finally:
                list_path.unlink(missing_ok=True)
        output = destination.joinpath(*name.replace('\\', '/').split('/'))
        if not output.is_file() or output.stat().st_size != int(entry['Size']):
            raise RuntimeError(f'Output missing or wrong size: {name}')
        if verify and entry.get('CRC'):
            output_path = destination.joinpath(*name.replace('\\', '/').split('/'))
            if not output_path.is_file(): raise RuntimeError(f'extracted file missing for verification: {name}')
            actual = _crc32(output_path); expected = str(entry['CRC']).upper()
            if actual != expected: raise RuntimeError(f'CRC mismatch for {name}: expected {expected}, got {actual}')
        state['entries'][str(number - 1)] = {'name': name, 'offset': entry.get('Offset'), 'packed': packed}
        # Only unread padding/fallback decoder data remains after streaming.
        if not metadata.get('solid') or number == len(entries) or entries[number].get('Solid') != '+':
            group_reclaimer.finish()
            reclaimed += group_reclaimer.reclaimed
            state.pop('active_group', None)
        _save_state(journal_path, state)
    return {'entries': len(entries), 'reclaimed_bytes': reclaimed, 'streamed_bytes': streamed,
            'source': str(first_volume), 'destination': str(destination),
            'volumes': len(volumes), 'dry_run': dry_run,
            'allocated_bytes_freed': allocation_before - sum(ntfs_reclaim.allocated_bytes(v) for v in volumes)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--decoder')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verify', action='store_true',
                        help='reread output CRC; streaming input is already consumed')
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
