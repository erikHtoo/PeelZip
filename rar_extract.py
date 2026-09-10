import argparse
import sys
from rar_backend import extract, inspect

def main():
    p=argparse.ArgumentParser(); p.add_argument('rar'); p.add_argument('destination'); p.add_argument('--inspect',action='store_true'); a=p.parse_args()
    try:
        meta=inspect(a.rar)
        if a.inspect:
            print(meta); return 0
        print(f"RAR entries: {len(meta['entries'])}; solid={meta['solid']}; multipart={meta['multipart']}; encrypted={meta['encrypted']}")
        if meta['solid']: print('Solid archive: aggressive reclamation is unavailable; using normal extraction.')
        extract(a.rar,a.destination,progress=print); print('Complete'); return 0
    except Exception as e: print(f'Stopped: {e}',file=sys.stderr); return 1
if __name__=='__main__':sys.exit(main())
