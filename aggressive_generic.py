"""No incremental generic backend is implemented yet."""
def run(*args, **kwargs):
    raise RuntimeError('Incremental reclamation is not implemented for this format. Use generic_extract.py for ordinary extraction; it requires full output space.')

if __name__ == '__main__':
    import sys
    print('Stopped: generic aggressive extraction is unavailable; no source bytes modified.', file=sys.stderr)
    raise SystemExit(1)
