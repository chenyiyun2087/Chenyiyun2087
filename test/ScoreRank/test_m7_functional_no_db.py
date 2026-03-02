import unittest
from datetime import date

from web.app import app
from web.strategy_playbook import evaluate_m7_rebalance


class TestM7FunctionalNoDB(unittest.TestCase):
    def setUp(self):
        self.target = [
            {'symbol': '000001', 'name': 'A', 'weight_pct': 45, 'm4_score': 88},
            {'symbol': '000002', 'name': 'B', 'weight_pct': 35, 'm4_score': 79},
            {'symbol': '000003', 'name': 'C', 'weight_pct': 20, 'm4_score': 71},
        ]
        self.current = [
            {'symbol': '000001', 'name': 'A', 'weight_pct': 20},
            {'symbol': '000004', 'name': 'D', 'weight_pct': 30},
        ]

    def test_evaluate_m7_rebalance(self):
        out = evaluate_m7_rebalance(self.target, self.current, total_capital=100000, min_trade_weight=1)
        self.assertEqual(out['target_count'], 3)
        self.assertEqual(out['current_count'], 2)
        self.assertGreater(out['orders_total'], 0)
        self.assertIn('orders', out)
        self.assertTrue(all(o['status'] == 'SIMULATED' for o in out['orders']))

    def test_m7_page_without_db(self):
        client = app.test_client()
        resp = client.get('/sina/strategy/m7?max_positions=5&capital=100000&min_trade_weight=1.5')
        self.assertEqual(resp.status_code, 200)

    @staticmethod
    def _mk_trade_day_index(days, asof):
        ordered = sorted(days)
        return {
            "ordered": ordered,
            "index": {d: i for i, d in enumerate(ordered)},
            "asof_trade_day": asof,
        }

    def test_bs_reversal_stale_should_not_trigger(self):
        target = [{'symbol': '000001', 'name': 'A', 'weight_pct': 20, 'm4_score': 70}]
        current = [{'symbol': '000001', 'name': 'A', 'weight_pct': 20, 'shares': 1000, 'avg_cost': 10, 'current_price': 10}]
        td_idx = self._mk_trade_day_index(
            ['20260224', '20260225', '20260226', '20260227', '20260302'],
            '20260302',
        )
        out = evaluate_m7_rebalance(
            target_allocations=target,
            current_positions=current,
            total_capital=100000,
            rule_version='m7_sell_v2.1',
            asof_date=date(2026, 3, 2),
            bs_fresh_trade_days=3,
            trade_day_index_override=td_idx,
            bs_state_override={
                '000001': {
                    'latest_buy_date': date(2026, 2, 20),
                    'latest_sell_date': date(2026, 2, 24),
                    'has_exit_signal': True,
                }
            },
            market_state_override={'000001': {'close': 10.0}},
        )
        sells = [x for x in out['orders'] if x['action'] == 'SELL']
        self.assertEqual(len(sells), 0)

    def test_hard_stop_over_rebalance(self):
        target = [{'symbol': '000001', 'name': 'A', 'weight_pct': 10, 'm4_score': 70}]
        current = [{'symbol': '000001', 'name': 'A', 'weight_pct': 30, 'shares': 1000, 'avg_cost': 10, 'current_price': 9.3}]
        out = evaluate_m7_rebalance(
            target_allocations=target,
            current_positions=current,
            total_capital=100000,
            rule_version='m7_sell_v2.1',
            stop_loss_pct=6.0,
            market_state_override={'000001': {'close': 9.3}},
        )
        sells = [x for x in out['orders'] if x['action'] == 'SELL']
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]['reason_code'], 'HARD_STOP')
        self.assertEqual(int(sells[0]['shares']), 1000)

    def test_forced_sell_not_rounded_but_rebalance_sell_rounded(self):
        target = [
            {'symbol': '000001', 'name': 'A', 'weight_pct': 5, 'm4_score': 70},
            {'symbol': '000002', 'name': 'B', 'weight_pct': 27.5, 'm4_score': 68},
        ]
        current = [
            {'symbol': '000001', 'name': 'A', 'weight_pct': 20, 'shares': 350, 'avg_cost': 10, 'current_price': 9.3},
            {'symbol': '000002', 'name': 'B', 'weight_pct': 30, 'shares': 430, 'avg_cost': 8, 'current_price': 10},
        ]
        out = evaluate_m7_rebalance(
            target_allocations=target,
            current_positions=current,
            total_capital=100000,
            rule_version='m7_sell_v2.1',
            min_trade_weight=1.0,
            min_trade_notional=1000,
            stop_loss_pct=6.0,
            market_state_override={
                '000001': {'close': 9.3},
                '000002': {'close': 10.0},
            },
        )
        sell_map = {x['symbol']: x for x in out['orders'] if x['action'] == 'SELL'}
        self.assertEqual(sell_map['000001']['reason_code'], 'HARD_STOP')
        self.assertEqual(int(sell_map['000001']['shares']), 350)
        self.assertEqual(sell_map['000002']['reason_code'], 'REBALANCE_SELL')
        self.assertEqual(int(sell_map['000002']['shares']), 300)

    def test_limit_down_should_be_pending(self):
        target = [{'symbol': '000001', 'name': 'A', 'weight_pct': 20, 'm4_score': 70}]
        current = [{'symbol': '000001', 'name': 'A', 'weight_pct': 30, 'shares': 1000, 'avg_cost': 8, 'current_price': 9.0}]
        out = evaluate_m7_rebalance(
            target_allocations=target,
            current_positions=current,
            total_capital=100000,
            rule_version='m7_sell_v2.1',
            market_state_override={
                '000001': {
                    'close': 9.0,
                    'pre_close': 10.0,
                    'low': 9.0,
                    'is_limit_down': True,
                    'is_suspended': False,
                    'tradable': False,
                    'trade_date': '20260302',
                }
            },
        )
        sells = [x for x in out['orders'] if x['action'] == 'SELL']
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]['reason_code'], 'LIMIT_DOWN_EXIT')
        self.assertEqual(int(sells[0]['pending_flag']), 1)
        self.assertEqual(sells[0]['pending_reason'], 'LIMIT_DOWN')
        self.assertEqual(sells[0]['exec_status'], 'PENDING')

    def test_score_exit_requires_and_two_days(self):
        target = [{'symbol': '000001', 'name': 'A', 'weight_pct': 20, 'm4_score': 70}]
        current = [{'symbol': '000001', 'name': 'A', 'weight_pct': 20, 'shares': 1000, 'avg_cost': 10, 'current_price': 10}]
        td_idx = self._mk_trade_day_index(['20260227', '20260302'], '20260302')
        score_good = {
            '000001': {
                'score_date': '20260302',
                'rows_desc': [
                    {'trade_date': '20260302', 'claude_score': 40, 'm4_score': 55},
                    {'trade_date': '20260227', 'claude_score': 44, 'm4_score': 58},
                ],
                'by_trade_day': {
                    '20260302': {'trade_date': '20260302', 'claude_score': 40, 'm4_score': 55},
                    '20260227': {'trade_date': '20260227', 'claude_score': 44, 'm4_score': 58},
                },
            }
        }
        out_good = evaluate_m7_rebalance(
            target_allocations=target,
            current_positions=current,
            total_capital=100000,
            rule_version='m7_sell_v2.1',
            asof_date=date(2026, 3, 2),
            trade_day_index_override=td_idx,
            score_state_override=score_good,
            score_confirm_days=2,
            claude_floor=45,
            score_floor=60,
            market_state_override={'000001': {'close': 10.0}},
        )
        sells_good = [x for x in out_good['orders'] if x['action'] == 'SELL']
        self.assertEqual(len(sells_good), 1)
        self.assertEqual(sells_good[0]['reason_code'], 'SCORE_EXIT')

        score_bad = {
            '000001': {
                'score_date': '20260302',
                'rows_desc': [
                    {'trade_date': '20260302', 'claude_score': 40, 'm4_score': 55},
                    {'trade_date': '20260227', 'claude_score': 50, 'm4_score': 58},
                ],
                'by_trade_day': {
                    '20260302': {'trade_date': '20260302', 'claude_score': 40, 'm4_score': 55},
                    '20260227': {'trade_date': '20260227', 'claude_score': 50, 'm4_score': 58},
                },
            }
        }
        out_bad = evaluate_m7_rebalance(
            target_allocations=target,
            current_positions=current,
            total_capital=100000,
            rule_version='m7_sell_v2.1',
            asof_date=date(2026, 3, 2),
            trade_day_index_override=td_idx,
            score_state_override=score_bad,
            score_confirm_days=2,
            claude_floor=45,
            score_floor=60,
            market_state_override={'000001': {'close': 10.0}},
        )
        sells_bad = [x for x in out_bad['orders'] if x['action'] == 'SELL']
        self.assertEqual(len(sells_bad), 0)


if __name__ == '__main__':
    unittest.main()
