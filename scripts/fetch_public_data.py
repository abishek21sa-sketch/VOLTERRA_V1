from pathlib import Path
import argparse, json, shutil, urllib.request
ROOT=Path(__file__).resolve().parents[1]
CFG=json.loads((ROOT/'empirical'/'public_data_config.json').read_text(encoding='utf-8'))

def fetch_uci(dataset_id:int, dest:Path):
    try:
        from ucimlrepo import fetch_ucirepo
    except ImportError:
        raise SystemExit('Install acquisition dependency: python -m pip install ucimlrepo pandas')
    ds=fetch_ucirepo(id=dataset_id); dest.mkdir(parents=True,exist_ok=True)
    ds.data.features.to_csv(dest/'features.csv',index=False)
    ds.data.targets.to_csv(dest/'targets.csv',index=False)
    (dest/'metadata.txt').write_text(str(ds.metadata),encoding='utf-8')

def fetch_afdc(dest:Path,state='IL',api_key='DEMO_KEY'):
    dest.mkdir(parents=True,exist_ok=True)
    url=f'https://developer.nlr.gov/api/alt-fuel-stations/v1.json?fuel_type=ELEC&state={state}&limit=all&api_key={api_key}'
    req=urllib.request.Request(url,headers={'User-Agent':'VOLTERRA-public-data-acquisition/1.0'})
    with urllib.request.urlopen(req,timeout=90) as r: (dest/'afdc_il_ev_stations.json').write_bytes(r.read())

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--state',default='IL'); ap.add_argument('--api-key',default='DEMO_KEY'); a=ap.parse_args()
    d=ROOT/'data'/'raw'/CFG['raw_dir']
    if CFG.get('uci_id'): fetch_uci(int(CFG['uci_id']),d)
    elif CFG['ptype']=='volterra': fetch_afdc(d,a.state,a.api_key)
    else: print('No network acquisition required; published snapshot is bundled.')
    print('PUBLIC_DATA_ACQUISITION=PASS')
