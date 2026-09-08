import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from empirical.backbone import run_empirical_reference
from tenx.engine import run_decision
def test_empirical_backbone_contract():
 e=run_empirical_reference(); assert e['local_profile']['rows']>=1; assert len(e['analysis_stack'])>=10; assert all(k in e['case_study'] for k in ['facts','inferences','recommendation','limitations'])
def test_ai_dossier_contains_empirical_provenance():
 d=run_decision(); assert d.get('empirical_backbone'); assert any('provenance' in str(x).lower() for x in d.get('tool_trace',[]))
def test_ui_has_live_empirical_workflow():
 h=(ROOT/'tenx_ui/index.html').read_text(encoding='utf-8'); wc=__import__('json').loads((ROOT/'tenx_ui/workspace_contract.json').read_text(encoding='utf-8')); assert '/api/airlines15x/empirical' in h and len(wc['workspaces'])>=26 and 'dataset.workspace' in h
