from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tenx.engine import run_decision

def test_tenx_predict_decide_challenge_act_contract():
    d=run_decision(7)
    assert d['autonomous_execution'] is False
    assert d['counterfactual']['disagrees'] is True
    assert len(d['tool_trace']) >= 5
    assert len(d['abstention_conditions']) >= 2
    assert len(d['user_aid']) >= 3
    assert d['original_algorithm'] and d['ml_family'] and d['or_escalation']
    assert d['model_validation']['metric']
    ui=(ROOT/'tenx_ui'/'index.html').read_text(encoding='utf-8')
    wc=__import__('json').loads((ROOT/'tenx_ui'/'workspace_contract.json').read_text()); assert len(wc.get('workspaces',[])) >= 20 and 'dataset.workspace' in ui
