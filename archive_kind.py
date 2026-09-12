from pathlib import Path


def detect(path):
    """Identify an archive by magic bytes, with extension fallback."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            sig = f.read(8)
    except OSError:
        sig = b""
    if sig.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if sig.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if sig.startswith(b"PK"):
        return "zip"
    if sig.startswith(b"ustar") or sig[257:262] == b"ustar":
        return "tar"
    if sig.startswith(b"MSCF"):
        return "cab"
    if sig.startswith(b"MSWIM"):
        return "wim"
    if sig.startswith(b"CD001") or sig[1:6] == b"CD001":
        return "iso"
    n = path.name.lower()
    if n.endswith(".7z"): return "7z"
    if n.endswith(".rar") or ".part" in n and n.endswith(".rar"): return "rar"
    if n.endswith((".zip", ".zip.001")) or ".z" in n: return "zip"
    if n.endswith((".tar", ".tar.gz", ".tgz", ".gz", ".cab", ".wim", ".iso")): return n.rsplit(".", 1)[-1]
    return None
