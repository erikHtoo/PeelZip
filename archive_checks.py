"""Preflight checks that do not copy archive data."""
from pathlib import Path
import re


def targets(destination, names):
    root = Path(destination).resolve()
    seen = set()
    files = set()
    result = []
    for name in names:
        parts = name.replace('\\', '/').split('/')
        if parts and not parts[-1]:
            parts.pop()
        if not parts or any(not p or p in ('.', '..') or
                re.search(r'[\x00-\x1f:<>"|?*]', p) or p.endswith((' ', '.')) or
                p.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}
                for p in parts):
            raise ValueError(f'Unsupported output path: {name!r}')
        key = '/'.join(parts).casefold()
        if key in seen:
            raise ValueError(f'Conflicting output path: {name}')
        seen.add(key)
        if not name.endswith(('/', '\\')):
            files.add(key)
        target = root.joinpath(*parts)
        current = target
        while current != current.parent:
            if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
                raise ValueError(f'Output uses a link/junction: {current}')
            current = current.parent
        result.append(target)
    for key in seen:
        parts = key.split('/')
        if any('/'.join(parts[:i]) in files for i in range(1, len(parts))):
            raise ValueError(f'File/directory conflict: {key}')
    return result


def ranges(items, length):
    previous = 0
    for start, end in sorted(items):
        if not 0 <= start <= end <= length or start < previous:
            raise ValueError('Invalid or overlapping archive ranges')
        previous = end
