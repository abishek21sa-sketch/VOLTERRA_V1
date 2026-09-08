from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT));
if (ROOT/'src').exists(): sys.path.insert(0,str(ROOT/'src'))
from empirical.public_data_backbone import data_backbone_status
out=data_backbone_status(); (ROOT/'artifacts').mkdir(exist_ok=True); (ROOT/'artifacts'/'data_backbone_status.json').write_text(json.dumps(out,indent=2,sort_keys=True),encoding='utf-8')
print(json.dumps({'project':out['project'],'dataset_state':out['dataset_state'],'case_status':out['case_study'].get('status'),'schema_validation':out['schema_validation']},indent=2))
