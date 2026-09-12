import argparse, subprocess
from pathlib import Path
def run(source,destination,decoder=None):
 source=Path(source).absolute(); destination=Path(destination).absolute(); destination.mkdir(parents=True,exist_ok=True)
 decoder=Path(decoder or Path(__file__).resolve().parent/"tools"/"7zz.exe")
 r=subprocess.run([str(decoder),"x","-y",f"-o{destination}","--",str(source)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace")
 if r.returncode: raise RuntimeError(r.stdout[-1000:])
 return {"source":str(source),"destination":str(destination)}
if __name__=="__main__":
 a=argparse.ArgumentParser();a.add_argument("source");a.add_argument("destination");a= a.parse_args();print(run(a.source,a.destination))
