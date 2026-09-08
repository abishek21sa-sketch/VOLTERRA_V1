from pathlib import Path
import json,re
ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'tenx_ui/index.html').read_text(encoding='utf-8')
wc=json.loads((ROOT/'tenx_ui/workspace_contract.json').read_text())
ident=json.loads((ROOT/'tenx_ui/ui_identity.json').read_text())
classes=set()
for m in re.finditer(r'class="([^"]+)"',html): classes.update(m.group(1).split())
checks={
  'signature_marker': ident['signature_marker'] in html,
  'namespace_depth': sum(c.startswith(ident['ui_namespace']) for c in classes)>=12,
  'workspace_depth': len(wc['workspaces'])>=26 and 'dataset.workspace' in html,
  'workspace_count_matches': len(wc['workspaces'])==ident['workspace_count'],
  'ml_lifecycle_visible': '/api/ml/lifecycle' in html,
  'agent_planning_visible': '/api/agent/run' in html,
  'empirical_provenance_visible': '/api/airlines15x/empirical' in html,
}
status='PASS' if all(checks.values()) else 'FAIL'
out={'status':status,'project':ident['project'],'ui_identity':ident['product_identity'],'ui_namespace':ident['ui_namespace'],'workspaces':len(wc['workspaces']),'checks':checks}
(ROOT/'artifacts').mkdir(exist_ok=True); (ROOT/'artifacts/ui_identity_validation.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out,indent=2)); print('UI_IDENTITY_VALIDATION='+status); raise SystemExit(0 if status=='PASS' else 1)
