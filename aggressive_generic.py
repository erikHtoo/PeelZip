"""Generic 7-Zip extractor for archive formats without independent ranges."""
import argparse
from pathlib import Path
import subprocess
import ntfs_reclaim

def run(source, destination, decoder=None, progress=print):
    source=Path(source).absolute(); destination=Path(destination).absolute(); destination.mkdir(parents=True,exist_ok=True)
    decoder=Path(decoder or Path(__file__).resolve().parent / "tools" / "7zz.exe")
    command=[str(decoder),"x","-y",f"-o{destination}","--",str(source)]
    p=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace")
    if p.returncode: raise RuntimeError(f"7zz failed: {p.stdout[-1000:]}")
    size=source.stat().st_size
    if size: ntfs_reclaim.reclaim_range(source,0,size)
    return {"source":str(source),"destination":str(destination),"reclaimed_bytes":size}

if __name__=="__main__":
    a=argparse.ArgumentParser();a.add_argument("source");a.add_argument("destination");a.add_argument("--decoder");args=a.parse_args(); print(run(args.source,args.destination,args.decoder))
