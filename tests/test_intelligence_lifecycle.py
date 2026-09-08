
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if (ROOT/'src').exists(): sys.path.insert(0,str(ROOT/'src'))
from intelligence.engine import lifecycle_report, run_agent

def test_ml_lifecycle_and_agent_are_operational():
    life=lifecycle_report(); agent=run_agent()
    assert life['model_family'] and life['target'] and life['validation']
    assert life['retrain_trigger'] and len(life['monitoring']) >= 4
    assert agent['agent'] and agent['decision'] and agent['prediction'] is not None
    assert len(agent['chosen_tool_sequence']) >= 5
    assert len(agent['operator_actions']) >= 3
    assert agent['autonomous_execution'] is False
