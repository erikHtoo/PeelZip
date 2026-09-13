"""Map RAR5 file payloads without reading or modifying compressed data."""
import binascii
import io


def _vint(stream):
    value = 0
    raw = bytearray()
    for shift in range(0, 70, 7):
        byte = stream.read(1)
        if not byte:
            raise ValueError('Truncated RAR header integer')
        raw += byte
        value |= (byte[0] & 127) << shift
        if not byte[0] & 128:
            if value >= 1 << 64:
                break
            return value, bytes(raw)
    raise ValueError('Invalid RAR header integer')


def payloads(volumes, entries):
    """Return per-entry [volume index, offset, length] parts; headers stay intact.

    Only clear-header RAR5 is supported. Match native decoder offsets and total
    packed lengths against independently checked physical headers before use.
    """
    files = {}
    pending = None
    for vi, path in enumerate(volumes):
        size = path.stat().st_size
        with path.open('rb') as stream:
            if stream.read(8) != b'Rar!\x1a\x07\x01\x00':
                raise ValueError('Incremental multipart RAR requires RAR5 with visible headers')
            ended = False
            while stream.tell() < size:
                crc = stream.read(4)
                length, encoded_length = _vint(stream)
                if len(crc) != 4 or not 2 <= length <= 2 * 1024 * 1024:
                    raise ValueError('Invalid RAR header size')
                header = stream.read(length)
                if len(header) != length or binascii.crc32(encoded_length + header) != int.from_bytes(crc, 'little'):
                    raise ValueError('RAR header checksum mismatch')
                fields = io.BytesIO(header)
                kind, _ = _vint(fields)
                flags, _ = _vint(fields)
                extra = _vint(fields)[0] if flags & 1 else 0
                packed = _vint(fields)[0] if flags & 2 else 0
                offset = stream.tell()
                if extra > length - fields.tell() or packed > size - offset:
                    raise ValueError('RAR payload exceeds volume bounds')
                if kind == 4:
                    raise ValueError('Incremental multipart RAR does not support encrypted headers yet')
                if kind == 2:
                    before, after = bool(flags & 8), bool(flags & 16)
                    if before != (pending is not None):
                        raise ValueError('Missing or inconsistent split RAR file part')
                    if pending is None:
                        pending = []
                    pending.append([vi, offset, packed])
                    if not after:
                        files[tuple(pending[0][:2])] = pending
                        pending = None
                stream.seek(packed, 1)
                if kind == 5:
                    ended = True
                    break
            if not ended:
                raise ValueError('RAR volume is missing its end header')
    if pending is not None:
        raise ValueError('Missing final split RAR file part')
    result = []
    used = set()
    for entry in entries:
        key = (int(entry.get('Volume Index', 0)), int(entry['Offset']))
        parts = files.get(key)
        if parts is None or key in used or sum(p[2] for p in parts) != int(entry['PackSize']):
            raise ValueError(f'RAR physical payload does not match decoder metadata: {entry["Path"]}')
        used.add(key)
        result.append(parts)
    return result
