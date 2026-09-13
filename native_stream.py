"""Reclaim decoder-consumed payloads with a bounded, synchronous handshake."""
from collections import deque
import os
import secrets
import subprocess

import ntfs_reclaim


class Reclaimer:
    def __init__(self, segments):
        self.remaining = [(p, o, n) for p, o, n in segments if n]
        self.reclaimed = 0
        self.events = 0
        self.consumed = {}

    def consume(self, segments):
        # Validate the entire event before changing any file. Removing consumed
        # spans also rejects duplicate reads instead of silently decoding zeros.
        remaining = self.remaining[:]
        for path, offset, length in segments:
            if length <= 0:
                raise RuntimeError('Invalid decoder read length')
            for i, (p, start, count) in enumerate(remaining):
                end = start + count
                if p == path and start <= offset and offset + length <= end:
                    replacement = []
                    if start < offset:
                        replacement.append((p, start, offset-start))
                    if offset + length < end:
                        replacement.append((p, offset+length, end-offset-length))
                    remaining[i:i+1] = replacement
                    break
            else:
                raise RuntimeError('Decoder read lies outside remaining payload ranges')
        for path, offset, length in segments:
            # Re-punch shared allocation-unit edges once both sides have been
            # consumed. Otherwise tiny unaligned edges can retain an NTFS sparse
            # unit at every read boundary (several percent of a large archive).
            intervals = sorted(self.consumed.get(path, []) + [(offset, offset+length)])
            merged = []
            for start, end in intervals:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            start, end = next((a, b) for a, b in merged if a <= offset and offset+length <= b)
            unit = 65536
            punch_start = max(start, offset // unit * unit)
            punch_end = min(end, (offset+length+unit-1) // unit * unit)
            ntfs_reclaim.reclaim_range(path, punch_start, punch_end-punch_start)
            self.consumed[path] = merged
            self.reclaimed += length
        self.remaining = remaining
        self.events += 1

    def finish(self):
        # Older decoders and unread codec padding fall back to group completion.
        if self.remaining:
            self.consume(self.remaining[:])


def run(command, mode, reclaimer, translate, bounds=None):
    token = secrets.token_hex(16)
    env = os.environ.copy()
    env.update(PEELZIP_READ_MODE=mode, PEELZIP_READ_TOKEN=token)
    if bounds:
        env.update(PEELZIP_READ_START=str(bounds[0]), PEELZIP_READ_END=str(bounds[1]))
    prefix = 'PEELZIP_READ ' + token + ' '
    tail = deque(maxlen=12)
    # Turn off ordinary stdout/progress so only protocol records reach stdout.
    command = list(command)
    command[2:2] = ['-bso0', '-bsp0', '-bse1']
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                          errors='replace', env=env) as process:
        try:
            for line in process.stdout:
                if line.startswith(prefix):
                    fields = line[len(prefix):].split()
                    if len(fields) != 3:
                        raise RuntimeError('Malformed decoder read event')
                    volume, offset, length = map(int, fields)
                    if volume < 0 or offset < 0 or length <= 0:
                        raise RuntimeError('Invalid decoder read event')
                    reclaimer.consume(translate(volume, offset, length))
                    process.stdin.write('K')
                    process.stdin.flush()
                else:
                    tail.append(line[-2000:])
            if process.wait():
                raise RuntimeError('Native extraction failed: ' + ''.join(tail)[-2000:])
        except BaseException:
            process.kill()
            process.wait()
            raise
