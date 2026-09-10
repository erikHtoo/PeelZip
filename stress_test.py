"""Build a realistic fixture and test the actual CLI, including forced termination.

Run: py stress_test.py --work-dir PATH
Creates about 1 GiB of disposable test data. Does not use any existing user ZIP.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time
import zipfile

MIB = 1024 * 1024


class StreamingWriter:
    def __init__(self, file):
        self.file = file
    def write(self, data):
        return self.file.write(data)
    def flush(self):
        self.file.flush()
    def tell(self):
        return self.file.tell()
    def seek(self, *args):
        raise io.UnsupportedOperation('Streaming fixture uses data descriptors')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while block := f.read(MIB):
            h.update(block)
    return h.hexdigest()


def build(root):
    fixture = root / 'realistic.zip'
    expected = {}
    rng = random.Random(20260911)
    with fixture.open('xb') as raw, zipfile.ZipFile(StreamingWriter(raw), 'w') as z:
        prefix = b'Realistic streaming ZIP fixture. '
        z.comment = prefix + b'x' * (65535 - len(prefix))
        def entry(name, blocks, method=zipfile.ZIP_DEFLATED):
            info = zipfile.ZipInfo(name, date_time=(2025, 12, 31, 23, 59, 58))
            info.compress_type = method
            info.comment = b'Fixture entry'
            info.extra = b'\xfe\xca\x04\x00TEST'
            h = hashlib.sha256()
            size = 0
            with z.open(info, 'w', force_zip64=True) as out:
                for block in blocks:
                    out.write(block)
                    h.update(block)
                    size += len(block)
            if not name.endswith('/'):
                expected[name] = {'sha256': h.hexdigest(), 'size': size}
        log = (b'2026-09-11T10:20:30Z INFO request=12345 status=200 duration_ms=17\n' * 18000)[:MIB]
        log = (log + b' ' * MIB)[:MIB]
        entry('server/logs/application.log', (log for _ in range(64)))
        entry('media/camera archive.dat', (rng.randbytes(MIB) for _ in range(80)), zipfile.ZIP_STORED)
        entry('backups/encrypted-looking.bin', (rng.randbytes(MIB) for _ in range(80)))
        for i in range(320):
            name = f'project/module_{i % 20:02d}/file_{i:04d}.json'
            data = json.dumps({'id': i, 'text': 'example configuration ' * (i % 80),
                               'values': list(range(i % 100))}).encode()
            entry(name, [data], (0, 8, 12, 14)[i % 4])
        entry('資料/รายงาน résumé 😀.txt', ['Unicode content: ข้อมูล 日本語 café'.encode()])
        entry('empty-file.txt', [b''])
        entry('deep/' + '/'.join('level'+str(i) for i in range(12)) + '/readme.txt', [b'nested'])
        entry('empty-folder/', [])
        entry('project/', [])  # Explicit directories can appear after their children.
    (root / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding='utf-8')
    return fixture, expected


def total_size(*paths):
    total = 0
    for path in paths:
        if path.is_file():
            total += path.stat().st_size
        elif path.is_dir():
            for f in path.rglob('*'):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except FileNotFoundError:
                    pass  # An atomic rename may occur while sampling.
    return total


def verify_output(dest, expected):
    actual = {str(p.relative_to(dest)).replace('\\', '/') for p in dest.rglob('*') if p.is_file()}
    assert actual == set(expected), 'Output file list mismatch'
    for name, info in expected.items():
        path = dest / name
        assert path.stat().st_size == info['size'] and digest(path) == info['sha256'], name


def scenario(root, fixture, expected, mode):
    case = root / mode
    case.mkdir()
    source, dest = case / 'input.zip', case / 'output'
    shutil.copyfile(fixture, source)
    state_dir = Path(str(source) + '.shrink-state')
    script = Path(__file__).with_name('shrink_unzip.py')
    command = [sys.executable, '-u', str(script), str(source), str(dest),
               '--execute', '--accept-data-loss-risk']
    started = time.monotonic()
    peak = source.stat().st_size
    minimum_archive = peak
    interrupted = False
    with (case / 'cli.log').open('w', encoding='utf-8') as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 180
            while child.poll() is None:
                if time.monotonic() > deadline:
                    raise TimeoutError('CLI exceeded three minutes')
                current = source.stat().st_size
                minimum_archive = min(minimum_archive, current)
                measured = total_size(source, dest, state_dir)
                # Discard samples spanning a truncation/rename boundary.
                if source.stat().st_size == current:
                    peak = max(peak, measured)
                partial = state_dir / 'extracting.part'
                if mode == 'killed_mid_file' and partial.exists() and partial.stat().st_size > 8 * MIB:
                    child.kill()
                    interrupted = True
                    break
                if mode == 'killed_after_shrink' and 0 < current < fixture.stat().st_size - 10 * MIB:
                    child.kill()
                    interrupted = True
                    break
                time.sleep(0.015)
            child.wait(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        if mode != 'normal':
            assert interrupted, 'Did not reach interruption point'
            child = subprocess.run([sys.executable, '-u', str(script), str(source),
                                    '--resume', '--accept-data-loss-risk'], stdout=log,
                                   stderr=subprocess.STDOUT, timeout=180)
        assert child.returncode == 0, (case / 'cli.log').read_text(encoding='utf-8')[-3000:]
    verify_output(dest, expected)
    assert source.stat().st_size == 0
    return dict(scenario=mode, sha256_verified_files=len(expected),
                seconds=round(time.monotonic() - started, 2),
                sampled_peak_logical_bytes=peak if mode == 'normal' else None,
                observed_shrink=minimum_archive < fixture.stat().st_size,
                forced_process_termination=interrupted)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir', required=True)
    args = p.parse_args()
    root = Path(args.work_dir).absolute()
    root.mkdir(parents=True, exist_ok=False)
    fixture, expected = build(root)
    with zipfile.ZipFile(fixture) as z:
        baseline = fixture.stat().st_size
        output_so_far = 0
        remaining = baseline
        theoretical_extra = 0
        for info in sorted(z.infolist(), key=lambda x: x.header_offset, reverse=True):
            theoretical_extra = max(theoretical_extra, output_so_far + info.file_size + remaining - baseline)
            output_so_far += info.file_size
            remaining = info.header_offset
    print(f'Built {fixture.stat().st_size / MIB:.1f} MiB ZIP; {len(expected)} files', flush=True)
    results = []
    for mode in ('normal', 'killed_mid_file', 'killed_after_shrink'):
        result = scenario(root, fixture, expected, mode)
        results.append(result)
        print(json.dumps(result), flush=True)
    report = dict(zip_bytes=fixture.stat().st_size, output_bytes=sum(x['size'] for x in expected.values()),
                  file_count=len(expected), theoretical_peak_extra_file_bytes=theoretical_extra,
                  scenarios=results)
    (root / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
