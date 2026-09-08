from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from empirical.backbone import run_empirical_reference
e=run_empirical_reference(); promoted=e['data_mode'] in {'refreshed_external','published_external_snapshot'}
out={'status':'PASS' if promoted else 'PENDING_REFRESH','data_mode':e['data_mode'],'source':e['source'].get('source_name'),'profile':e['local_profile'],'claim_boundary':e['claim_boundary']}
(ROOT/'artifacts').mkdir(exist_ok=True);(ROOT/'artifacts/external_data_promotion.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2));print('EXTERNAL_DATA_PROMOTION='+out['status']);raise SystemExit(0 if promoted else 2)
