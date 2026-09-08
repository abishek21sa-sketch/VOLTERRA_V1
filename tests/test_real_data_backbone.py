from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT));
if (ROOT/'src').exists(): sys.path.insert(0,str(ROOT/'src'))
from empirical.public_data_backbone import data_backbone_status,run_public_case
from tenx.engine import run_decision

def test_data_backbone_contract_and_claim_boundary():
    s=data_backbone_status(); assert s['project']; assert s['dataset']; assert s['claim_boundary']; assert s['human_authority']; assert s['autonomous_execution'] is False
    assert set(['raw','processed','contracts','dictionaries','provenance','snapshots']).issubset(s['layers'])
    assert s['case_study']['name']

def test_decision_dossier_contains_public_data_gate():
    d=run_decision(); assert d.get('public_data_backbone'); assert d.get('real_data_case'); assert d.get('public_evidence_gate')
    assert any('claim ledger' in str(x).lower() for x in d.get('tool_trace',[]))
