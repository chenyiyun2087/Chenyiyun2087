"""Release rotation must preserve report/backtest discovery and writes."""
import os
import sys
import json
import subprocess
from pathlib import Path


def test_all_review_producers_and_scorer_use_persistent_root(tmp_path):
    latest = tmp_path/'exports/signal_research/20261016_220000_trusted_account_backtest'
    latest.mkdir(parents=True)
    code = '''
import json
from scripts import research_trusted_strategy_account_backtest as backtest
from scripts.ops import run_strategy_performance_review as review
from scripts.ops import run_rolling_strategy_scorer as scorer
print(json.dumps([str(backtest.OUT_ROOT),str(review.DEFAULT_OUTPUT_ROOT),str(review.DEFAULT_SIGNAL_RESEARCH_DIR),scorer.find_latest_review_dir()]))
'''
    env = dict(os.environ, CHENYIYUN_SOURCE_REPO=str(tmp_path),DISABLE_APP_SCHEDULER_LOOP='1')
    output = subprocess.check_output([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env=env,text=True)
    result = json.loads(output.splitlines()[-1])
    assert result == [str(latest.parent),str(tmp_path/'exports/production_strategy_reviews'),str(latest.parent),str(latest)]
