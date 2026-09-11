"""Experimental low-space extractor for eligible non-solid RAR archives.

The native 7zz bridge exposes each RAR5 entry's compressed-data offset. This
tool extracts one entry at a time, waits for 7zz to finish successfully, then
returns that entry's compressed range to NTFS. The archive keeps its logical
length but is no longer a valid normal RAR after reclamation.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

import ntfs_reclaim
import rar_backend


def extract_aggressive(source, destination, decoder=None, progress=None, dry_run=False):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(f"destination must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    metadata = rar_backend.inspect(source, decoder)
    supported, reason = rar_backend.aggressive_supported(metadata)
    if not supported:
        raise RuntimeError(f"RAR aggressive mode unavailable: {reason}")
    decoder = Path(metadata['decoder'])
    entries = metadata['entries']
    reclaimed = 0
    for number, entry in enumerate(entries, 1):
        name = entry['Path']
        offset = entry['Offset']
        packed = entry['PackSize']
        if progress:
            progress(f"[{number}/{len(entries)}] {name} ({packed} compressed bytes)")
        if dry_run:
            continue
        command = [str(decoder), 'x', '-y', f'-o{destination}', str(source), name]
        result = subprocess.run(command, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding='utf-8', errors='replace', check=False)
        if result.returncode != 0:
            raise RuntimeError(f"7zz failed for {name}: {result.stdout[-1000:]}")
        if packed:
            ntfs_reclaim.reclaim_range(source, offset, packed)
            reclaimed += packed
    return {'entries': len(entries), 'reclaimed_bytes': reclaimed,
            'source': str(source), 'destination': str(destination),
            'dry_run': dry_run}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--decoder')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    result = extract_aggressive(args.source, args.destination,
                                args.decoder, print, args.dry_run)
    print(result)


if __name__ == '__main__':
    main()
