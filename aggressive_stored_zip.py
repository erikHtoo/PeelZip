"""Experimental chunk-reclaiming extractor for ZIP_STORED entries on Windows.

It preserves the ZIP's logical length but deallocates verified source ranges.
The resulting archive is intentionally no longer usable as a normal ZIP.
"""
import argparse
import archive_checks
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile
import zlib

from ntfs_reclaim import reclaim_range

CHUNK = 64 * 1024 * 1024


def journal(path, state):
    tmp = path.with_suffix('.new')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(state, f)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def data_offset(source, info):
    with source.open('rb') as f:
        f.seek(info.header_offset)
        header = f.read(30)
    sig, _, _, _, _, _, _, _, _, name_len, extra_len = __import__('struct').unpack('<4s5H3I2H', header)
    if sig != b'PK\x03\x04':
        raise ValueError('Invalid local header')
    return info.header_offset + 30 + name_len + extra_len


def run(source, destination, resume=False, accept=False, chunk_size=CHUNK):
    if sys.platform != 'win32':
        raise OSError('Aggressive mode requires Windows NTFS/ReFS')
    if not accept:
        raise ValueError('Requires --accept-data-loss-risk')
    source = Path(source).absolute(); destination = Path(destination).absolute()
    state_dir = Path(str(source) + '.aggressive-state'); state_path = state_dir / 'state.json'
    if resume:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        if state['source'] != str(source): raise ValueError('Source path mismatch')
        destination = Path(state['destination'])
    else:
        if destination.exists() or state_dir.exists(): raise ValueError('Destination/state already exists')
        destination.mkdir(parents=True)
        with zipfile.ZipFile(source) as z:
            infos = z.infolist()
            archive_checks.targets(destination, [i.filename for i in infos])
            if any(i.compress_type != zipfile.ZIP_STORED for i in infos if not i.is_dir()):
                raise ValueError('This experimental mode only supports ZIP_STORED entries; use normal mode for compressed entries')
        state_dir.mkdir()
        state = dict(version=1, source=str(source), destination=str(destination), completed=0,
                     current=None, identity=[source.stat().st_dev, source.stat().st_ino])
        journal(state_dir / 'initial.json', state); journal(state_path, state)
    if [source.stat().st_dev, source.stat().st_ino] != state['identity']:
        raise ValueError('Source identity changed')
    with zipfile.ZipFile(source) as z:
        infos = z.infolist()
        if any(i.compress_type != zipfile.ZIP_STORED for i in infos if not i.is_dir()):
            raise ValueError('Source contains compressed entries')
        for index, info in enumerate(infos):
            if index < state['completed']: continue
            target = destination.joinpath(*info.filename.rstrip('/').split('/'))
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True); state['completed'] = index + 1; journal(state_path, state); continue
            target.parent.mkdir(parents=True, exist_ok=True)
            part = state_dir / 'current.part'
            candidate = state.get('current') or {}
            current = candidate if candidate.get('index') == index else None
            done = current['bytes'] if current else 0
            crc = current['crc'] if current else 0
            if not current:
                part.unlink(missing_ok=True)
            mode = 'ab' if done else 'wb'
            offset = data_offset(source, info)
            with source.open('rb', buffering=0) as inp, part.open(mode) as out:
                inp.seek(offset + done)
                while done < info.file_size:
                    n = min(chunk_size, info.file_size - done)
                    data = inp.read(n)
                    if len(data) != n: raise OSError('Short source read')
                    out.write(data); out.flush(); os.fsync(out.fileno())
                    crc = zlib.crc32(data, crc) & 0xffffffff; done += n
                    state['current'] = dict(index=index, bytes=done, crc=crc)
                    journal(state_path, state)
                    reclaim_range(source, offset + done - n, n)
                    print(f'[{index+1}/{len(infos)}] {info.filename}: {done}/{info.file_size} bytes')
            if done != info.file_size or crc != info.CRC: raise ValueError('CRC/size verification failed')
            os.replace(part, target)
            state['completed'] = index + 1; state['current'] = None; journal(state_path, state)
    state['done'] = True; journal(state_path, state)
    print('Aggressive extraction complete. Source ZIP logical bytes remain, but reclaimed ranges are zeroed.')


def main():
    p = argparse.ArgumentParser(); p.add_argument('zip'); p.add_argument('destination');
    p.add_argument('--resume', action='store_true'); p.add_argument('--accept-data-loss-risk', action='store_true');
    p.add_argument('--chunk-mib', type=int, default=64)
    a = p.parse_args()
    try: run(a.zip, a.destination, a.resume, a.accept_data_loss_risk, a.chunk_mib * 1024 * 1024)
    except Exception as e: print(f'Stopped: {e}. Keep aggressive recovery state and use --resume.', file=sys.stderr); return 1
    return 0

if __name__ == '__main__': sys.exit(main())
