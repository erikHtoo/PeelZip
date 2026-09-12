"""Read-only encryption detection; never allow a decoder to wait on stdin."""
import subprocess
import zipfile


def required(source, decoder):
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            return any(entry.flag_bits & 1 for entry in archive.infolist())
    result = subprocess.run(
        [str(decoder), 'l', '-slt', '-p-', '--', str(source)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=60)
    output = result.stdout + '\n' + result.stderr
    if any(line.strip() == 'Encrypted = +' for line in output.splitlines()):
        return True
    if result.returncode and 'password' in output.lower():
        return True
    if result.returncode:
        raise RuntimeError('Cannot inspect archive: ' + output[-800:])
    return False
