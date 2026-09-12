from pathlib import Path


def detect(path):
    """Identify an archive by magic bytes, with extension fallback."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            sig = f.read(512)
    except OSError:
        sig = b""
    if sig.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if sig.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if sig[:4] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        return "zip"
    if sig.startswith(b"ustar") or sig[257:262] == b"ustar":
        return "tar"
    if sig.startswith(b"\x1f\x8b"): return "gz"
    if sig.startswith(b"MSCF"):
        return "cab"
    if sig.startswith(b"MSWIM"):
        return "wim"
    if sig.startswith(b"CD001") or sig[1:6] == b"CD001":
        return "iso"
    import zipfile
    if zipfile.is_zipfile(path): return 'zip'
    with path.open('rb') as stream:
        stream.seek(32769)
        if stream.read(5) == b'CD001': return 'iso'
    return None
