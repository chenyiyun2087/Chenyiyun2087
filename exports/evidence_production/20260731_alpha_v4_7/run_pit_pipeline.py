#!/usr/bin/env python3
"""Run the full PIT adapter → enrichment → builder pipeline in one shot."""
import json, os, sys
from pathlib import Path

os.environ['CHENYIYUN_DB_URL'] = 'mysql+pymysql://root:19871019@localhost:3306/chenyiyun?charset=utf8mb4'

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

ADAPTER_DIR = Path('exports/evidence_production/20260731_alpha_v4_7/adapter')
SNAPSHOTS = ADAPTER_DIR / 'snapshots'
CONFIG = ADAPTER_DIR / 'pit_adapter_config.json'
OUTPUT = Path('exports/evidence_production/20260731_alpha_v4_7/pit_builder')

# ── Step 1: Adapter ──
print("=" * 60)
print("STEP 1: Running PIT adapter...")
from scripts.research.pit_data_adapter import build_pit_adapter_manifest
adapter_result = build_pit_adapter_manifest(CONFIG, ADAPTER_DIR)
print(f"Adapter: {adapter_result['status']}, level: {adapter_result['historical_evidence_level']}")
if adapter_result['status'] != 'PASS':
    print(f"BLOCKERS: {adapter_result.get('blockers', [])}")
    sys.exit(1)

# ── Step 2: Enrich market/financial with real circ_mv/pb ──
print("=" * 60)
print("STEP 2: Enriching with circ_mv and pb from MySQL...")
import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine(os.environ['CHENYIYUN_DB_URL'])

# Enrich market with circ_mv
market = pd.read_parquet(SNAPSHOTS / 'market.parquet')
basic = pd.read_sql(
    text('SELECT trade_date, ts_code, circ_mv FROM tushare_stock.dwd_daily_basic WHERE trade_date >= 20200101 AND trade_date <= 20260730'),
    engine
)
basic['symbol'] = basic['ts_code'].str.extract(r'(\d+)', expand=False).str.zfill(6).str[-6:]
market['symbol'] = market['symbol'].astype(str).str.zfill(6)
market = market.merge(basic[['trade_date', 'symbol', 'circ_mv']], on=['trade_date', 'symbol'], how='left')
market['circ_mv'] = market['circ_mv_y'].fillna(0.0)
market = market.drop(columns=['circ_mv_x', 'circ_mv_y'], errors='ignore')
market.to_parquet(SNAPSHOTS / 'market.parquet', index=False)
print(f"Market enriched: {len(market)} rows, circ_mv non-zero: {(market['circ_mv'] > 0).sum()}")

# Enrich financial with pb
fin = pd.read_parquet(SNAPSHOTS / 'financial.parquet')
fin['symbol'] = fin['symbol'].astype(str).str.zfill(6)
fin = fin.merge(basic[['trade_date', 'symbol', 'pb']].rename(columns={'pb': 'pb_real'}), on=['trade_date', 'symbol'], how='left')
fin['pb'] = fin['pb_real'].fillna(0.0)
fin = fin.drop(columns=['pb_real'], errors='ignore')
fin.to_parquet(SNAPSHOTS / 'financial.parquet', index=False)
print(f"Financial enriched: {len(fin)} rows, pb non-zero: {(fin['pb'] > 0).sum()}")

engine.dispose()

# Update manifest SHAs
import hashlib
def file_sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()

MANIFEST_PATH = ADAPTER_DIR / 'pit_source_manifest.json'
manifest = json.loads(MANIFEST_PATH.read_text())
from runtime.acceptance_config import canonical_sha
for name in ['market', 'financial']:
    manifest['sources'][name]['sha256'] = file_sha(SNAPSHOTS / f'{name}.parquet')
manifest['content_sha256'] = canonical_sha({k: v for k, v in manifest.items() if k != 'content_sha256'})
MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
print("Manifest SHAs updated")

# ── Step 3: Builder ──
print("=" * 60)
print("STEP 3: Running PIT builder...")
from scripts.research.pit_factor_panel_builder import build_pit_factor_panel

builder_result = build_pit_factor_panel(
    market_path=SNAPSHOTS / 'market.parquet',
    universe_path=SNAPSHOTS / 'universe.parquet',
    financial_path=SNAPSHOTS / 'financial.parquet',
    industry_path=SNAPSHOTS / 'industry.parquet',
    adjustment_path=SNAPSHOTS / 'adjustment.parquet',
    source_manifest_path=MANIFEST_PATH,
    output_dir=OUTPUT,
    profile_name='alpha_v4_7',
)
print(json.dumps(builder_result, ensure_ascii=False, indent=2, sort_keys=True))
