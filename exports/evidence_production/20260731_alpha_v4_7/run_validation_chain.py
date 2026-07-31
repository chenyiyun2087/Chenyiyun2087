#!/usr/bin/env python3
"""Run v4.3–v4.5 validation chain on the E3 PIT panel via CLI."""
import json, os, subprocess, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2] if '__file__' in dir() else Path('.').resolve()
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(str(PROJECT_ROOT))

import pandas as pd
PYTHON = str(Path(sys.executable))  # use same venv python

E3_PANEL = Path('exports/evidence_production/20260731_alpha_v4_7/pit_builder/factor_panel_daily.parquet')
MARKET = Path('exports/evidence_production/20260731_alpha_v4_7/adapter/snapshots/market.parquet')
OUTPUT_BASE = Path('exports/evidence_production/20260731_alpha_v4_7')
BENCHMARK = Path('exports/evidence_production/20260730_alpha_v4_2/benchmark/benchmark_nav_daily.csv')

# ── Step 1: Enrich panel ──
print("=" * 60)
print("STEP 1: Enriching E3 panel with close prices...")
panel = pd.read_parquet(E3_PANEL)
market = pd.read_parquet(MARKET)
market['symbol'] = market['symbol'].astype(str).str.zfill(6)
panel['symbol'] = panel['symbol'].astype(str).str.zfill(6)

panel = panel.merge(
    market[['trade_date', 'symbol', 'close', 'open', 'pre_close', 'amount', 'circ_mv']],
    on=['trade_date', 'symbol'], how='left'
)
panel['close_qfq'] = panel['close'].fillna(0.0)
if 'pb' not in panel.columns:
    panel['pb'] = 0.0

ENRICHED_CSV = OUTPUT_BASE / 'pit_builder' / 'factor_panel_enriched.csv'
panel.to_csv(ENRICHED_CSV, index=False)
print(f"Enriched panel CSV: {ENRICHED_CSV} ({len(panel)} rows)")

# ── Step 2: factor_evidence (v4.3) ──
print("=" * 60)
print("STEP 2: factor_evidence (v4.3)...")
FACTOR_DIR = OUTPUT_BASE / 'factors_v2'
FACTOR_DIR.mkdir(parents=True, exist_ok=True)

cmd = [
    PYTHON, '-m', 'scripts.research.factor_evidence',
    '--source', str(ENRICHED_CSV),
    '--output-dir', str(FACTOR_DIR),
    '--profile', 'alpha_v4_7',
]
if BENCHMARK.exists():
    cmd.extend(['--benchmark', str(BENCHMARK)])
else:
    cmd.extend(['--benchmark', str(ENRICHED_CSV)])  # fallback

result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
print("STDOUT:", result.stdout[-2000:] if result.stdout else "(empty)")
if result.stderr:
    print("STDERR:", result.stderr[-1000:])
print(f"Exit: {result.returncode}")

# ── Step 3: factor_challenger_lab (v4.4) ──
print("=" * 60)
print("STEP 3: factor_challenger_lab (v4.4)...")
CHALLENGER_DIR = OUTPUT_BASE / 'challenger_v2'
CHALLENGER_DIR.mkdir(parents=True, exist_ok=True)

cmd = [
    PYTHON, '-m', 'scripts.research.factor_challenger_lab',
    '--factor-dir', str(FACTOR_DIR),
    '--output-dir', str(CHALLENGER_DIR),
    '--profile', 'alpha_v4_7',
]
result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
print("STDOUT:", result.stdout[-2000:] if result.stdout else "(empty)")
if result.stderr:
    print("STDERR:", result.stderr[-1000:])
print(f"Exit: {result.returncode}")

# ── Step 4: factor_net_ledger (v4.5) ──
print("=" * 60)
print("STEP 4: factor_net_ledger (v4.5)...")
NET_LEDGER_DIR = OUTPUT_BASE / 'net_ledger_v2'
NET_LEDGER_DIR.mkdir(parents=True, exist_ok=True)

cmd = [
    PYTHON, '-m', 'scripts.research.factor_net_ledger',
    '--factor-dir', str(FACTOR_DIR),
    '--source', str(ENRICHED_CSV),
    '--output-dir', str(NET_LEDGER_DIR),
    '--profile', 'alpha_v4_7',
]
result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
print("STDOUT:", result.stdout[-2000:] if result.stdout else "(empty)")
if result.stderr:
    print("STDERR:", result.stderr[-1000:])
print(f"Exit: {result.returncode}")

print("=" * 60)
print("ALL DONE")
