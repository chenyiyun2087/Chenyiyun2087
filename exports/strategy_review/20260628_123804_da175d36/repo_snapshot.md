# Repository and Evidence Snapshot

- Run ID: `20260628_123804_da175d36`
- Git commit: `da175d36e5e28e06a300eed6873e544d12cca464`
- Git branch: `main`
- Git status: `DIRTY`
- Existing user change preserved: `M scripts/ops/feishu_notifier.py
?? exports/strategy_review/`
- Python: `3.14.3 (main, Feb  3 2026, 15:32:20) [Clang 17.0.0 (clang-1700.6.3.2)]`
- Python executable: `/Volumes/extension/projects/Chenyiyun2087/.venv/bin/python`
- Platform: `macOS-26.4.1-arm64-arm-64bit-Mach-O`
- Tests: `33 passed in 1.17s` (strict ledger, sizing, PIT market rules, replay, governance chain)
- Requested mode: full fresh replay
- Fresh replay result: `NOT_VERIFIABLE`
- Failure: `raw-ledger research requires verified corporate-action and lifecycle snapshots`
- Authoritative data snapshot date: `NOT_VERIFIABLE` (no immutable source snapshot/manifest found)
- Historical saved-ledger date range: `2023-01-04` to `2026-06-04`
- Authoritative trading-calendar range: `NOT_VERIFIABLE`; dates above are inferred from saved NAV rows and are not a calendar snapshot.
- Database writes/orders/messages: none

## Commands and outcomes

1. Relevant pytest suite: PASS (33/33).
2. Strict smoke replay for six target strategies: FAIL-CLOSED before data execution because required immutable snapshots were absent.
3. No full replay was attempted after the prerequisite failure; no empty snapshot was manufactured.

## Actual input files and SHA-256

| File | SHA-256 |
|---|---|
| `AGENTS.md` | `15b1af4c8a197ff3b5c0581c5c246d794ee39b2db4d2bafc5bbfdab435a5b0e0` |
| `README.md` | `0e036781623011f2b0a9d1a8dd0bc2784ecf62942b57b43082ab111d19c83a18` |
| `docs/00_project_overview/RUNBOOK.md` | `de197dbf7e022db37261e07ed23336c03bc1ed20d9a2232eaea600c320011b1d` |
| `docs/00_project_overview/INCIDENT_RUNBOOK.md` | `4862f5505e69ab5532c374a91df78a4bdd48d4d8c7c05eea5117c991d576cfdb` |
| `docs/01_strategy_research/2026-06-20_strict_公司行为数据契约.md` | `cf7914e68d33c2cdef9be08c047998ccade287835163bcd64c85730bfc8b6824` |
| `config/production_strategy.yaml` | `c2ecad6f68c6286295212bfabd5ee0d0df694bdbd69be5a87ec4252caa7c33de` |
| `runtime/ledger_runtime.py` | `7189f707f44bcdf3c4737b19fa19a6fa49ae9a8b35cfd4582c4b337e9aa50d8d` |
| `scripts/research/strict_execution_ledger.py` | `8996074c27a9b46acfc89d021ddc7146b8873a673ad501b9ae953088ce6583dd` |
| `scripts/research/strict_ledger_runtime_adapter.py` | `b3fff1cf75e08e58d2b5f7ce2d8ed25df29eda8c0ea849950692c5c1b692f037` |
| `scripts/research_trusted_strategy_account_backtest.py` | `de366b18d0b3ea9f081b934c52f6930192d1f419b242a62b7e066088fc2f9ddf` |
| `scripts/research_full_pool_liquidity_strategies.py` | `5920a9f1f37cfe56cb8a8702c87c4f899bbf91655183af5109c8ede6e4941778` |
| `scripts/research/build_strict_corporate_action_snapshot.py` | `a06ed2058b297491d3e6f07f942315d6e4f686084a403afa9268578aa31d8e33` |
| `exports/signal_research/20260628_082814_builtin_strategy_full_review/strategy_summary.csv` | `a02049de541f65a4bc50b095a9ca449df89fd765066ee75dc438f7a39d0c5bed` |
| `exports/signal_research/20260628_082814_builtin_strategy_full_review/data_quality_checks.csv` | `4018ea48d206e4b632e22a2702f9fcee3b270c41b67658aeeb34e9e07589be3d` |
| `strategy_cards/adaptive_market_style_shadow.yaml` | `422d8d638f244fd3f7acc7e32d9b32c6a3a6e20c4c3ebb33626bb54ce3a714b4` |
| `strategy_cards/ashare_auto_shadow.yaml` | `30e9b87e0617b3bcad147bfbe95563d87a751f3d90b6efdd403cf9cedea4b03c` |
| `strategy_cards/ashare_hybrid_conservative_shadow.yaml` | `7059b4883c0f7f4001ac45d8699d231bfc73bd0ea896093e92e3477a14c0b97b` |
| `strategy_cards/ashare_trend_breakout_shadow.yaml` | `d0897c55f03f52c6c405f85b63d73b47131195dba9fc1dcb356a0341d074ef23` |
| `strategy_cards/baseline_full_liquidity_detail_vol_position.yaml` | `48ee3bd2ebd1f43e862154282766c7b208e35a9a59b998e1da360357ab6b5452` |
| `strategy_cards/chenyiyun_selected_legacy.yaml` | `5ce1fe8619134a0e44e8e9c345968daf1a5e132b1cba5f5e93cbbbb6cbce2b51` |
| `strategy_cards/dual_system_adaptive_route.yaml` | `7e056941606ce54458eab8cf7133583b9c65466082c2acc1b2d5fcaf6d3607e4` |
| `strategy_cards/repair_reversal_shadow.yaml` | `031ffa2b20f8bc075e16bc909cb47edb8880e7f9f9061be7d0513f9b6cccf67b` |
| `strategy_cards/tiered_liquidity_then_bs_v2.yaml` | `43063bf386780aeaa49ba9330eb79f4c2fc07178b7a262e9c77702e3520914b7` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv` | `74be39223330e3a27b05b6190d327d01a9b6c93196d417e24c91ee3b173facfb` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv` | `98cbe82b73245cf75806f48998c638dbc839b7b08b7e7670ee72f990352efebf` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_candidates.csv` | `b9beaca4c4702896089f17f4f9a01c6554a8ed3bf548cb774375a612d1f7f6bc` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv` | `15bbc4f2395f07951f1640985aa91544fcd1b807f809e819a66df31a74b62590` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_market_environment.csv` | `7bd705d9801e6529d78c1e1ef64a717e6a53650e30df24adb258a39c059ea279` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_nav.csv` | `9517409b96606ae9b3b52481fc4d35ae2c54d8d45b11b2a0136292eade1bf001` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_positions.csv` | `878f73405e1dee0848f09f29bb23b92daecce57ecdbfcb23fe7ffb282628b944` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_summary.csv` | `d553b32725ec9d010eb67b68964d584bd74b22aeba075f3e8d5f23cc95c33bfd` |
| `exports/signal_research/20260603_202728_444675_trusted_account_backtest/trusted_account_backtest_trades.csv` | `a415694bb95e207db2d1e787de2b6b462dcd619baea5dff8fa07d037c4f40591` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv` | `c9cd8a356f839086ccdc3c4425792328cd9a051a5f7df3a23866ecb80f7889ae` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv` | `a81b35bfdebd10dbc719f838f1b5d631902c8a159e7f3b5a532b5d0002cd5cda` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_candidates.csv` | `f801009fc3879b964d256068914b04c37e28cd6c5af275fbdfd5a420dee86f64` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv` | `01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_market_environment.csv` | `887ccab7dda1648d45aab606aea5792dfd9ee158da3fa81bd0ad4cf5105c0593` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_nav.csv` | `9f73ae08c89182d3651668f3557dd942a169616ee01a7f462b62d1766a55e46b` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_positions.csv` | `9df542fb267c612547d92df8cfbc0538472fcf790468d1f8f66ec8c69330a6ac` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_summary.csv` | `be405196e49d18debaa646dcac45693d541427f40f9b0c6374b004c1c45276b4` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_trades.csv` | `6c9dd59cb9c29fbe21c15c2fc538b4d0f74729c012a5a43e5fabc24fc3a5378a` |
| `exports/signal_research/20260604_152142_206060_trusted_account_backtest/trusted_account_backtest_window_summary.csv` | `d570da5ab5cfad7a71d39a37f95680842a3929bf1f6fae526e07a888d9d76287` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv` | `4189e4afdf5cd25c323d38caf6a661905427880752d8dcb34ca40e56f10c275f` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv` | `496bcbefeafcdc61b127b7527c789f6e68b39cf23a16231acd10fa2ea7cdf2ae` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_candidates.csv` | `e05cd749a60edd1f4186f131d194f15a9b697234f6e80c9e4ee3d256c71f808c` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv` | `01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_market_environment.csv` | `e9500495cd377418833413bd28db23b55814de38a2d08a85d3f5345c017bf9f8` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_nav.csv` | `5b7ef16f870c86148d012efbbb7182a2fc43e365b4b22267d197500086770a85` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_positions.csv` | `a5bd28dc499692b80154b761b2695ab22eb9db27bbdcd9a9af4e0cad0c0d9339` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_summary.csv` | `11d2c18103dc112f5888ab5ba27f307c4e3925d6d4b915c820b8262242ca6c16` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_trades.csv` | `f317f7ca3e0b5db15e9a51deb7d100fd6a639f04b6e9b60ee668423c60ce997b` |
| `exports/signal_research/20260604_163941_308980_trusted_account_backtest/trusted_account_backtest_window_summary.csv` | `9784c06506e8d245012c63924fedc8045d8808080e2489a9eb404fefffa2b821` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_adaptive_decisions.csv` | `60ca670b4b9f86887ce3214c5e4e69967e36ce355bb9d7536b60c4b0b9706f7e` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_adaptive_perf.csv` | `629b4453240b08d16fc81f9bd0cd02d198458bceb0e53bae29fa22e5bcab926a` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_candidates.csv` | `2e971c2d3ad33a8c06d98251db42470317a5b73d7028108505bd4aaba6a14ad4` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_dynamic_weights.csv` | `01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_market_environment.csv` | `5c43acd631c7f5bda94f034887d50f970f2b7c5c74b17129c31ba885150da8b8` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_nav.csv` | `5e976f6e938546931a4cf52af89b5223a4fab69566279f9360d0ffb5b92d5f39` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_positions.csv` | `66f38f7480dcd4a5280d7b5c7dd44c52c4b08b6881783fe3a1438ed92b8812a2` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_summary.csv` | `a4bdac54dc508066b95882e24185d1ed07fe00f2fff808871931ba12d97d86b4` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_trades.csv` | `657f1113e4fb39c5824665af511e42d247ddc81bd0260eacb01b57554cd4de91` |
| `exports/signal_research/20260605_004258_229723_trusted_account_backtest/trusted_account_backtest_window_summary.csv` | `8934b7136f10a70eb398ae822eec7a627d88da08a6750d11ee66824b2200fc88` |
