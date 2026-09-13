"""Read-only aggressive space estimates. No extraction or range reclamation."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import stat
import subprocess

import aggressive_zip as az
import aggressive_rar as ar
import aggressive_7z as a7
import archive_checks
import archive_kind
import ntfs_reclaim
import rar_backend
import rar_parts

MIB = 1024 ** 2
# Only assume the streaming handshake for the tested bundled binary.
STREAMING_SHA256 = '86b4daf5e1fddf82c6e0f204528a551afbef4e31f36d7157c586d861fe502444'


def password_required(source, decoder):
    """Preview needs a password only when the metadata itself is encrypted."""
    kind = archive_kind.detect(source)
    if kind == 'zip':
        return False
    if kind == 'rar':
        source = ar._rar_volumes(Path(source))[0]
    result = subprocess.run([str(decoder), 'l', '-slt', '-p-', '--', str(source)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=60)
    text = result.stdout + result.stderr
    if result.returncode and 'password' in text.lower():
        return True
    if result.returncode:
        raise ValueError('Cannot inspect archive: ' + text[-800:])
    return False


def _streaming_decoder(decoder):
    if not decoder.is_file():
        return False
    with decoder.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(MIB), b''):
            digest.update(chunk)
        return digest.hexdigest() == STREAMING_SHA256


def _existing_parent(path):
    path = Path(path).resolve()
    while not path.exists():
        path = path.parent
    return path


def model(groups, same_disk=True, reserve=64*MIB):
    """Groups are (output bytes, packed bytes, streaming).

    Expected assumes input consumption proportional to output within streaming
    groups. Cautious assumes each group finishes before any of it is reclaimed.
    Neither is a guarantee: allocation granularity and codec buffering vary.
    """
    net = expected = cautious = total = 0
    for output, packed, streaming in groups:
        if min(output, packed) < 0:
            raise ValueError('Negative archive size')
        reclaimed = packed if same_disk else 0
        cautious = max(cautious, net + output)
        expected = max(expected, net, net + output - reclaimed) if streaming else max(expected, net + output)
        net += output - reclaimed
        total += output
    return {'expected_extra': max(0, expected) + reserve,
            'cautious_extra': max(0, cautious) + reserve,
            'ordinary_extra': total, 'final_growth': max(0, net), 'reserve': reserve}


def estimate(source, destination, decoder=None, password=None):
    source, destination = Path(source).absolute(), Path(destination).absolute()
    decoder = Path(decoder or Path(__file__).parent/'tools'/'7zz.exe')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError('Preview currently covers fresh extraction. Choose an empty destination.')
    kind = archive_kind.detect(source)
    groups, names, spans = [], [], 0
    streaming_decoder = _streaming_decoder(decoder)
    if kind == 'zip':
        volumes = az._split_volumes(source)
        split = len(volumes) > 1
        if split:
            # The ZIP directory validates concatenated layout without a decoder
            # password or reading compressed payloads.
            az._split_entries(source, decoder, password='metadata-only')
        with az.zipfile.ZipFile(az.SplitReader(volumes) if split else source) as archive:
            entries = archive.infolist()
            boundary = archive.start_dir
            methods = (0, 8, 12, 14, getattr(az.zipfile, 'ZIP_ZSTANDARD', -1))
            for entry in sorted(entries, key=lambda e: e.header_offset, reverse=True):
                if entry.volume:
                    raise ValueError('Disk-relative split ZIP preview is not supported')
                if stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError('ZIP links/special files are not supported')
                offset = az._split_data_offset(volumes, entry.header_offset) if split else az._data_offset(source, entry)
                if offset + entry.compress_size > boundary:
                    raise ValueError('ZIP payload overlaps a header/directory')
                boundary = entry.header_offset
                names.append(entry.filename)
                if not entry.is_dir():
                    streaming = not split and not entry.flag_bits & 1 and entry.compress_type in methods
                    groups.append((entry.file_size, entry.compress_size, streaming))
                    # ZIP currently retains some partial allocation units at
                    # chunk boundaries; budget these rather than ignore them.
                    spans += 1 + (math.ceil(entry.file_size/MIB) if streaming else 0)
    elif kind == 'rar':
        volumes = ar._rar_volumes(source)
        meta = rar_backend.inspect(volumes[0], decoder, password=password if password is not None else '-')
        supported, reason = rar_backend.aggressive_supported(meta, password='metadata-only')
        if not supported:
            raise ValueError(reason)
        entries = meta['entries']
        if len(volumes) > 1 or meta.get('multipart'):
            expected = meta.get('header', {}).get('Volumes')
            if expected and int(expected) != len(volumes):
                raise ValueError('Incomplete RAR volume set')
            mapped = rar_parts.payloads(volumes, entries)
            spans = sum(len(parts) for parts in mapped)
        else:
            archive_checks.ranges([(e['Offset'], e['Offset']+e['PackSize']) for e in entries], source.stat().st_size)
            spans = len(entries)
        streaming = streaming_decoder and meta.get('header', {}).get('Type', '').lower() == 'rar5'
        for entry in entries:
            names.append(entry['Path'])
            if meta.get('solid') and entry.get('Solid') == '+' and groups:
                output, packed, _ = groups[-1]
                groups[-1] = (output+int(entry['Size']), packed+int(entry['PackSize']), streaming)
            else:
                groups.append((int(entry['Size']), int(entry['PackSize']), streaming))
    elif kind == '7z':
        volumes = a7._volumes(source)
        _, entries, blocks = a7.inspect(source, decoder, password if password is not None else '-')
        if any(e.get(k) for e in entries for k in ('Symbolic Link', 'Hard Link')):
            raise ValueError('7z links are not supported')
        names = [e['Path'] for e in entries]
        archive_checks.ranges([(b['offset'], b['offset']+b['packed']) for b in blocks], sum(v.stat().st_size for v in volumes))
        for block in blocks:
            spans += len(a7._physical_segments(source, block['offset'], block['packed']))
            groups.append((sum(int(e['Size']) for e in block['entries']), block['packed'], streaming_decoder))
    else:
        raise ValueError('Aggressive preview supports ZIP, RAR and 7z only')
    archive_checks.targets(destination, names)
    logical = sum(v.stat().st_size for v in volumes)
    allocated = sum(ntfs_reclaim.allocated_bytes(v) for v in volumes)
    if allocated + len(volumes)*65536 < logical:
        raise ValueError('Source is sparse or already reclaimed; a fresh-extraction estimate would be unreliable.')
    parent = _existing_parent(destination)
    if not parent.is_dir():
        raise ValueError('Destination parent is a file')
    same_disk = all(v.stat().st_dev == parent.stat().st_dev for v in volumes)
    # Explicit heuristic reserve: 64 MiB working room, per-file allocation,
    # and two potentially retained sparse units per physical payload span.
    reserve = 64*MIB + len(names)*4096 + (min(sum(g[1] for g in groups), spans*131072) if same_disk else 0)
    result = model(groups, same_disk, reserve)
    free = shutil.disk_usage(parent).free
    result.update(format=kind, source_bytes=logical, destination_free=free,
                  same_disk=same_disk, streaming_groups=sum(g[2] for g in groups),
                  groups=len(groups), confidence='Estimate, not a guarantee',
                  status='below_estimate' if free < result['expected_extra'] else
                  'tight' if free < result['cautious_extra'] else 'room_for_cautious_budget')
    return result


def describe(result):
    def gib(n):
        return f'{n/1024**3:.2f} GiB'
    lines = [f'Estimated extra space: ~{gib(result["expected_extra"])}',
             f'Cautious budget: {gib(result["cautious_extra"])}',
             f'Free at destination: {gib(result["destination_free"])}',
             f'Ordinary extraction: ~{gib(result["ordinary_extra"])}', '']
    if result['status'] == 'below_estimate':
        lines.append('Free space is below the estimate.')
    elif result['status'] == 'tight':
        lines.append('Fits the estimate, but has less room for uneven compression.')
    else:
        lines.append('There is room for the cautious budget.')
    if not result['same_disk']:
        lines.append('Different drives: reclaiming the source cannot fund the destination.')
    lines.append('Estimate only. Streaming assumes even consumption; the cautious budget waits for each file/group. Includes a working-space allowance. No files were changed.')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    parser.add_argument('--decoder')
    parser.add_argument('--password')
    args = parser.parse_args()
    try:
        print('PEELZIP_PREVIEW ' + json.dumps(estimate(args.source, args.destination, args.decoder, args.password)))
    except Exception as exc:
        print(f'Preview unavailable: {exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
