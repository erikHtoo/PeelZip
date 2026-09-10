"""Destructive, resumable ZIP extraction. Python 3.10+; see requirements.txt for Zstd."""
import argparse
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import zipfile
import zlib

# Python 3.14 includes Zstandard ZIP support. On older Python versions,
# use the maintained CPython backport when installed, preserving the regular
# ZIP functionality without dependencies when it is absent.
if not hasattr(zipfile, 'ZIP_ZSTANDARD'):
    try:
        from backports.zstd import zipfile
    except ImportError:
        pass

HAS_ZSTANDARD = hasattr(zipfile, 'ZIP_ZSTANDARD')
SUPPORTED_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED,
                     zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
if HAS_ZSTANDARD:
    SUPPORTED_METHODS.add(93)

CHUNK = 1024 * 1024
RESERVE = 64 * CHUNK


@contextlib.contextmanager
def process_lock(source):
    """Prevent two copies of this utility from editing the same archive."""
    path = Path(str(Path(source).absolute()) + '.shrink-lock')
    with path.open('a+b') as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Closing releases the operating-system lock even after an exception.
        yield


def sync_json(path, value):
    temporary = path.with_suffix('.new')
    with temporary.open('w', encoding='utf-8') as f:
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        while data := f.read(CHUNK):
            digest.update(data)
    return digest.hexdigest()


def write_initial(path, state):
    # Immutable bootstrap remains available if the first journal commit is interrupted.
    with path.open('x', encoding='utf-8') as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())


class RecoveryView(io.RawIOBase):
    """Present the original ZIP directory above the remaining physical prefix."""
    def __init__(self, source, tail, offset, length):
        self.source = source
        self.tail = tail
        self.offset = offset
        self.length = length
        self.position = 0

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = (0 if whence == 0 else self.position if whence == 1 else self.length) + offset
        if self.position < 0:
            raise ValueError('Negative seek')
        return self.position

    def read(self, size=-1):
        end = self.length if size < 0 else min(self.length, self.position + size)
        chunks = []
        physical = os.fstat(self.source.fileno()).st_size
        while self.position < end:
            if self.position >= self.offset:
                stop = end
                self.tail.seek(self.position - self.offset)
                data = self.tail.read(stop - self.position)
            elif self.position < physical:
                stop = min(end, physical, self.offset)
                self.source.seek(self.position)
                data = self.source.read(stop - self.position)
            else:
                stop = min(end, self.offset)
                data = b'\0' * (stop - self.position)
            if len(data) != stop - self.position:
                raise OSError('Unexpected short read')
            chunks.append(data)
            self.position = stop
        return b''.join(chunks)


def entries(z):
    result = sorted(z.infolist(), key=lambda x: x.header_offset, reverse=True)
    paths = set()
    offsets = set()
    files = set()
    spelling = {}
    for info in result:
        if '\x00' in info.orig_filename or '\\' in info.orig_filename:
            raise ValueError('Unsafe original archive filename')
        if info.is_dir() and info.file_size:
            raise ValueError('Directory entry contains file data')
        name = info.filename.rstrip('/')
        parts = name.split('/')
        if not name or any(p in ('', '.', '..') for p in parts):
            raise ValueError('Unsafe or empty archive path: ' + repr(name))
        for p in parts:
            if any(ord(c) < 32 or c in '\\:<>"|?*' for c in p) or p.endswith((' ', '.')):
                raise ValueError('Unsupported Windows path: ' + repr(name))
            if p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$', *('COM'+str(i) for i in '0123456789¹²³'), *('LPT'+str(i) for i in '0123456789¹²³')}:
                raise ValueError('Reserved Windows path: ' + repr(name))
        for depth in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:depth])
            previous = spelling.setdefault(prefix.casefold(), prefix)
            if previous != prefix:
                raise ValueError('Case-colliding file or directory paths: ' + name)
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError('Links and special files are unsupported: ' + name)
        if info.flag_bits & 1:
            raise ValueError('Encrypted ZIPs are unsupported')
        if info.volume != 0:
            raise ValueError('Split ZIP archives are unsupported')
        if info.compress_type == 93 and not HAS_ZSTANDARD:
            raise ValueError('This ZIP uses Zstandard. Use Python 3.14+ or install '
                             'the dependency with: python -m pip install -r requirements.txt')
        if info.compress_type not in SUPPORTED_METHODS:
            raise ValueError(f'Unsupported ZIP compression method: {info.compress_type}')
        key = name.casefold()
        if key in paths or info.header_offset in offsets:
            raise ValueError('Duplicate paths or overlapping entries are unsupported')
        paths.add(key)
        offsets.add(info.header_offset)
        if not info.is_dir():
            files.add(key)
    for key in paths:
        pieces = key.split('/')
        if any('/'.join(pieces[:i]) in files for i in range(1, len(pieces))):
            raise ValueError('File/directory path conflict')
    if result and result[-1].header_offset != 0:
        raise ValueError('Self-extracting or prefixed ZIPs are unsupported')
    return result


def safe_target(destination, info):
    target = destination.joinpath(*info.filename.rstrip('/').split('/'))
    current = target
    while True:
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('Destination contains a link or junction: ' + str(current))
        if current == current.parent:
            break
        current = current.parent
    return target


def verify(path, info):
    if info.is_dir():
        return path.is_dir()
    if not path.is_file() or path.stat().st_size != info.file_size:
        return False
    crc = 0
    with path.open('rb') as f:
        while data := f.read(CHUNK):
            crc = zlib.crc32(data, crc)
    return crc == info.CRC


def gib(size):
    return f'{size / 1024**3:.2f} GiB'


def run(args):
    source = Path(args.zip).absolute()
    state_dir = Path(str(source) + '.shrink-state')
    state_path = state_dir / 'state.json'
    if source.is_symlink():
        raise ValueError('Source cannot be a symbolic link')
    if source.stat().st_nlink != 1:
        raise ValueError('Hardlinked sources are unsupported; truncation would affect other names')
    if args.resume:
        record = state_path if state_path.exists() else state_dir / 'initial.json'
        if not record.exists():
            raise ValueError('Setup did not finish; no ZIP bytes were intentionally removed. '
                             'Verify the original ZIP, then remove the incomplete recovery folder and retry')
        state = json.loads(record.read_text(encoding='utf-8'))
        if state.get('version') != 2:
            raise ValueError('Unsupported recovery record version')
        if state['source'] != str(source):
            raise ValueError('Source path does not match recovery record')
        if digest_file(state_dir / 'directory.bin') != state['directory_sha256']:
            raise ValueError('Recovery directory checksum mismatch; refusing to modify the ZIP')
        destination = Path(state['destination'])
    else:
        if not args.destination:
            raise ValueError('Provide a new destination folder')
        destination = Path(args.destination).absolute()
        if state_dir.exists():
            raise ValueError('Recovery records exist; use --resume')
        if destination.exists():
            raise ValueError('Destination must be a new folder')
        if not destination.parent.is_dir():
            raise ValueError('Destination parent must already exist')
        if source.stat().st_dev != destination.parent.stat().st_dev:
            raise ValueError('Source and destination must be on the same volume')
        with zipfile.ZipFile(source) as z:
            items = entries(z)
            size = source.stat().st_size
            offset = z.start_dir
            remaining = size
            peak = 0
            total = 0
            for info in items:
                peak = max(peak, total + info.file_size - (size - remaining))
                total += info.file_size
                remaining = info.header_offset
            needed = peak + (size - offset) + RESERVE + len(items) * 8192
            free = shutil.disk_usage(source.parent).free
            print(f'ZIP: {gib(size)} | Output: {gib(total)} | Entries: {len(items)}')
            print(f'Estimated free space needed: {gib(needed)} | Available: {gib(free)}')
            print('Estimate includes metadata, a 64 MiB reserve, and per-entry overhead.')
            if not args.execute:
                print('Preview only. Add --execute --accept-data-loss-risk to proceed.')
                return
            if not args.accept_data_loss_risk:
                raise ValueError('Execution requires --accept-data-loss-risk')
            if free < needed:
                raise ValueError('Insufficient free space for this archive/order')
            print('Checking every ZIP entry before modifying the archive...', flush=True)
            bad = z.testzip()
            if bad:
                raise ValueError('CRC check failed: ' + bad)
        state_dir.mkdir()
        with source.open('rb') as f, (state_dir / 'directory.bin').open('xb') as out:
            f.seek(offset)
            shutil.copyfileobj(f, out, CHUNK)
            out.flush()
            os.fsync(out.fileno())
        state = dict(version=2, source=str(source), destination=str(destination), length=size,
                     offset=offset, completed=0, done=False,
                     directory_sha256=digest_file(state_dir / 'directory.bin'),
                     identity=[source.stat().st_dev, source.stat().st_ino])
        write_initial(state_dir / 'initial.json', state)
        destination.mkdir()
        sync_json(state_path, state)

    if not args.accept_data_loss_risk:
        raise ValueError('Resume requires --accept-data-loss-risk')
    if state['done']:
        print('Already completed. Recovery records may be deleted after checking the output.')
        return
    current_stat = source.stat()
    if [current_stat.st_dev, current_stat.st_ino] != state['identity']:
        raise ValueError('Source file identity changed; refusing to modify it')
    with source.open('r+b', buffering=0) as physical, (state_dir / 'directory.bin').open('rb') as tail:
        view = RecoveryView(physical, tail, state['offset'], state['length'])
        with zipfile.ZipFile(view) as z:
            items = entries(z)
            count = state['completed']
            if type(count) is not int or not 0 <= count <= len(items):
                raise ValueError('Invalid completed-entry count in recovery records')
            destination.mkdir(exist_ok=True)
            if args.resume:
                print('Verifying previously extracted files before resuming...', flush=True)
                for info in items[:count]:
                    if not verify(safe_target(destination, info), info):
                        raise ValueError('Previously extracted file changed or is missing: ' + info.filename)
            cut = items[count - 1].header_offset if count else state['length'] if items else 0
            previous_cut = items[count - 2].header_offset if count > 1 else state['length']
            if physical.seek(0, 2) not in {cut, previous_cut}:
                raise ValueError('Unexpected ZIP size; refusing to truncate')
            physical.truncate(cut)
            os.fsync(physical.fileno())
            for index in range(count, len(items)):
                info = items[index]
                target = safe_target(destination, info)
                temporary = state_dir / 'extracting.part'
                if temporary.exists():
                    temporary.unlink()
                if target.exists():
                    if not verify(target, info):
                        raise ValueError('Existing output does not match: ' + str(target))
                elif info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    if shutil.disk_usage(destination).free < info.file_size + RESERVE:
                        raise OSError('Not enough space for next file; free space and resume')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    print(f'[{index+1}/{len(items)}] {info.filename} ({gib(info.file_size)})', flush=True)
                    with z.open(info) as inp, temporary.open('xb') as out:
                        shutil.copyfileobj(inp, out, CHUNK)
                        out.flush()
                        os.fsync(out.fileno())
                    if not verify(temporary, info):
                        raise ValueError('Extracted file verification failed')
                    # Same-volume rename adds no second output copy.
                    os.rename(temporary, target)
                # Commit durable output before releasing its original compressed bytes.
                state['completed'] = index + 1
                sync_json(state_path, state)
                physical.truncate(info.header_offset)
                os.fsync(physical.fileno())
                print(f'  ZIP now {gib(info.header_offset)}', flush=True)
    # Keep an empty placeholder so a final interrupted run can still resume.
    with source.open('r+b') as f:
        f.truncate(0)
        f.flush()
        os.fsync(f.fileno())
    state['done'] = True
    sync_json(state_path, state)
    print(f'Complete: {destination}')
    print(f'The original ZIP is now empty. Keep recovery records until you check the output: {state_dir}')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(errors='backslashreplace')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('zip', help='Local ZIP file to shrink')
    p.add_argument('destination', nargs='?', help='New output directory on the same volume')
    p.add_argument('--execute', action='store_true')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--accept-data-loss-risk', action='store_true')
    args = p.parse_args()
    try:
        if args.execute or args.resume:
            with process_lock(args.zip):
                run(args)
        else:
            run(args)
    except (Exception, KeyboardInterrupt) as exc:
        print(f'Stopped: {exc or "interrupted"}. If recovery records exist, keep them and use --resume.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
