"""RAR backend using an installed 7-Zip or UnRAR decoder.

This deliberately separates decoding from NTFS reclamation. The command-line
decoders do not expose safe compressed byte ranges, so aggressive mode refuses
RAR until a native decoder API is connected.
"""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess


def find_decoder():
    # The release bundle includes a full 7-Zip build with the RAR5 handler.
    # Prefer it so the app works on a clean machine; fall back to installed
    # 7-Zip/WinRAR for users who already have a system decoder.
    bundled = Path(__file__).resolve().parent / 'tools' / '7zz.exe'
    if bundled.exists():
        return bundled
    candidates = ['7z.exe', '7za.exe', 'unrar.exe', 'rar.exe']
    for name in candidates:
        found = shutil.which(name)
        if found: return Path(found)
    common = [Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / '7-Zip/7z.exe',
              Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'WinRAR/UnRAR.exe',
              Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / '7-Zip/7z.exe']
    return next((p for p in common if p.exists()), None)


def inspect(source, decoder=None, password=None):
    decoder = decoder or find_decoder()
    if decoder is None: raise FileNotFoundError('Install 7-Zip or WinRAR to process RAR archives')
    command = [str(decoder), 'l', '-slt']
    if password is not None:
        command.append(f'-p{password}')
    command += ['--', str(Path(source).absolute())]
    result = subprocess.run(command,
                            capture_output=True, text=True, encoding='utf-8', errors='replace', check=False)
    if result.returncode not in (0, 1): raise RuntimeError(result.stderr.strip() or 'RAR listing failed')
    entries=[]; current={}
    for line in result.stdout.splitlines():
        if not line.strip():
            if current.get('Path') and current.get('Folder') != '+' and 'Physical Size' not in current: entries.append(current)
            current={}; continue
        if ' = ' in line:
            key,value=line.split(' = ',1); current[key]=value
    if current.get('Path') and current.get('Folder') != '+' and 'Physical Size' not in current: entries.append(current)
    header = {}
    for line in result.stdout.splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            if key in {'Solid', 'Multivolume', 'Encrypted', 'Type', 'Volumes', 'Blocks', 'Method', 'Total Physical Size'} and key not in header:
                header[key] = value.strip()
    # Native bridge builds may expose the physical compressed-data range. Keep
    # values as integers when present so the aggressive engine can validate
    # ranges without reparsing 7-Zip text output.
    for entry in entries:
        if 'Packed Size' in entry and 'PackSize' not in entry:
            entry['PackSize'] = entry['Packed Size']
        for key in ('Offset', 'PackSize', 'Size'):
            if key in entry:
                try:
                    entry[key] = int(entry[key])
                except ValueError:
                    pass
    return dict(decoder=str(decoder), entries=entries, solid=header.get('Solid') == '+',
                multipart=header.get('Multivolume') == '+', encrypted=header.get('Encrypted') == '+', header=header)


def extract(source, destination, decoder=None, progress=None):
    decoder = decoder or find_decoder()
    if decoder is None: raise FileNotFoundError('Install 7-Zip or WinRAR to process RAR archives')
    destination = Path(destination).absolute(); destination.mkdir(parents=True, exist_ok=False)
    command = [str(decoder), 'x', '-y', f'-o{destination}', '--', str(Path(source).absolute())]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace', bufsize=1)
    for line in process.stdout:
        if progress: progress(line.rstrip())
    code=process.wait()
    if code: raise RuntimeError(f'RAR decoder exited with code {code}')


def aggressive_supported(metadata, password=None):
    """Return whether metadata contains enough native range information."""
    if metadata.get('encrypted') and password is None:
        return False, 'encrypted RAR requires a password'
    if not metadata.get('entries'):
        return False, 'RAR contains no extractable entries'
    if not all('Offset' in e and 'PackSize' in e for e in metadata['entries']):
        return False, 'native RAR data offsets are unavailable from this decoder'
    return True, 'native compressed ranges available; extraction lifecycle validation required'
