from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path


def _sha256sums(path: Path) -> dict[str, str]:
    sums = path / "SHA256SUMS"
    if not sums.exists():
        raise RuntimeError("evidence verification failed: missing SHA256SUMS")
    parsed = {}
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or len(digest) != 64 or not name:
            raise RuntimeError("evidence verification failed: invalid SHA256SUMS")
        parsed[name] = digest
    return parsed


def verify(path: Path) -> dict:
    m=json.loads((path/"manifest.json").read_text()); bad=[]
    sums = _sha256sums(path)
    if sums != m["files"]:
        raise RuntimeError("evidence verification failed: manifest/SHA256SUMS mismatch")
    for name,digest in m["files"].items():
        file=path/name
        if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest: bad.append(name)
    if bad: raise RuntimeError(f"evidence verification failed: {bad}")
    return {"verified": True, "files": len(m["files"]), "commit": m["commit"]}
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--evidence-dir",type=Path,required=True);print(json.dumps(verify(p.parse_args().evidence_dir)))
