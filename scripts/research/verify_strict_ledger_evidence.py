from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
def verify(path: Path) -> dict:
    m=json.loads((path/"manifest.json").read_text()); bad=[]
    for name,digest in m["files"].items():
        file=path/name
        if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest: bad.append(name)
    if bad: raise RuntimeError(f"evidence verification failed: {bad}")
    return {"verified": True, "files": len(m["files"]), "commit": m["commit"]}
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--evidence-dir",type=Path,required=True);print(json.dumps(verify(p.parse_args().evidence_dir)))
