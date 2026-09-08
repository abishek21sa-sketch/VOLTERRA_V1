from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
failed=[]
manifest=json.loads((ROOT/'RELEASE_MANIFEST.json').read_text(encoding='utf-8'))
for item in manifest.get('evidence_inventory',[]):
 p=ROOT/item['path']
 if not p.is_file(): failed.append('missing evidence: '+item['path'])
 elif hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']: failed.append('evidence hash mismatch: '+item['path'])
inv=json.loads((ROOT/'SOURCE_SHA256SUMS.json').read_text(encoding='utf-8'))
for rel,expected in inv.get('files',{}).items():
 p=ROOT/rel
 if not p.is_file(): failed.append('source missing: '+rel)
 elif hashlib.sha256(p.read_bytes()).hexdigest()!=expected: failed.append('source hash mismatch: '+rel)
if failed:
 print('RELEASE_INTEGRITY=FAIL'); [print(x) for x in failed]; raise SystemExit(1)
print('RELEASE_INTEGRITY=PASS')
