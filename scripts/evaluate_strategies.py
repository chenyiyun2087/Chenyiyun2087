import sys
import os
from pathlib import Path
import pymysql
import json
from datetime import datetime

# Add project root to path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from web.strategy_playbook import (
    evaluate_m2_presets,
    evaluate_m3_optimizer,
    evaluate_m5_rolling,
    evaluate_m6_nav
)
from sina.live_tracker.live_tracker_config import LIVE_CONFIG

def get_db_connection():
    db_url = LIVE_CONFIG["db_url"]
    parts = db_url.replace("mysql+pymysql://", "").split("@")
    user_pass = parts[0].split(":")
    host_db = parts[1].split("/")
    host_port = host_db[0].split(":")
    db_name = host_db[1].split("?")[0]

    return pymysql.connect(
        host=host_port[0],
        port=int(host_port[1]) if len(host_port) > 1 else 3306,
        user=user_pass[0],
        password=user_pass[1] if len(user_pass) > 1 else "",
        db=db_name,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def fetch_m1_rows(conn, lookback=60):
    """Fetch M1 event attributes and KPI for recent history."""
    with conn.cursor() as cursor:
        # Check tables existence first
        cursor.execute("SHOW TABLES LIKE 'b_event_fact'")
        if not cursor.fetchone():
            print("Table b_event_fact not found.")
            return []
            
        # 1. Fetch distinct recent dates
        cursor.execute("SELECT DISTINCT event_date FROM b_event_fact ORDER BY event_date DESC LIMIT %s", (lookback,))
        dates = [row['event_date'] for row in cursor.fetchall()]
        if not dates:
            return []
        
        min_date = dates[-1]
        
        # 2. Fetch joined data
        sql = """
            SELECT 
                f.event_date,
                f.symbol,
                f.name,
                f.score,
                COALESCE(f.opt_score, 0) as opt_score,
                COALESCE(f.claude_score, 0) as claude_score,
                COALESCE(f.is_eligible, 0) as is_eligible,
                k.ret_3,
                k.ret_5,
                k.ret_10,
                k.hit_3_10pct,
                k.hit_5_10pct,
                k.hit_10_10pct
            FROM b_event_fact f
            LEFT JOIN b_event_kpi k 
              ON f.event_date = k.event_date AND f.symbol = k.symbol
            WHERE f.event_date >= %s
            ORDER BY f.event_date ASC
        """
        cursor.execute(sql, (min_date,))
        rows = cursor.fetchall()
        return rows

def run_evaluation():
    conn = get_db_connection()
    output_lines = []

    def log(msg):
        print(msg)
        output_lines.append(str(msg))

    try:
        log(f"Evaluation Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log("=" * 50)
        
        log("Fetching M1 Data...")
        rows = fetch_m1_rows(conn, lookback=60)
        log(f"Loaded {len(rows)} event rows.")
        
        if not rows:
            log("No data available for evaluation.")
            return

        log("\n=== M2: Strategy Presets (Recent 60 Days) ===")
        m2_res = evaluate_m2_presets(rows)
        for res in m2_res.get("results", []):
            log(f"Strategy: {res['strategy']}")
            log(f"  Desc: {res['description']}")
            log(f"  Avg Ret (10d): {res.get('avg_ret_10')}%")
            log(f"  Hit Rate (10d): {res.get('hit_10')}%")
            log("-" * 30)

        log("\n=== M3: Parameter Optimization ===")
        m3_res = evaluate_m3_optimizer(rows)
        log(f"Tested {m3_res['searched_total']} combinations.")
        log("Top Winners:")
        for w in m3_res.get("winners", [])[:3]:
            log(f"  Family: {w['family']}")
            log(f"  Params: {w['params']}")
            log(f"  Avg Ret (10d): {w.get('avg_ret_10')}% | Hit (10d): {w.get('hit_10')}%")

        log("\n=== M5: Rolling Validation (Window=5) ===")
        m5_res = evaluate_m5_rolling(rows, window_size=5)
        summary_ret = m5_res.get("summary_ret_10", {})
        summary_hit = m5_res.get("summary_hit_10", {})
        log(f"Windows Tested: {m5_res['windows_total']}")
        log(f"Avg Return Distribution: Mean={summary_ret.get('mean')}% [Min={summary_ret.get('min')}%, Max={summary_ret.get('max')}%]")
        log(f"Hit Rate Distribution: Mean={summary_hit.get('mean')}% [Min={summary_hit.get('min')}%, Max={summary_hit.get('max')}%]")

        log("\n=== M6: Net Asset Value (NAV) ===")
        # Using M3 best parameters if we could, but M6 uses M4 logic internally which usually uses fixed or provided params
        # evaluate_m6_nav internally uses evaluate_m4_allocation which uses hardcoded weights in strategy_playbook unless modified.
        # Let's run it with default settings.
        m6_res = evaluate_m6_nav(rows)
        log(f"Total Trading Days: {m6_res['dates_total']}")
        log(f"Gross Return: {m6_res['gross_final_ret_pct']}%")
        log(f"Net Return (after cost/slip): {m6_res['net_final_ret_pct']}%")
        log(f"Max Drawdown: {m6_res['max_drawdown_pct']}%")
        
        # === Generate Conclusion ===
        log("\n=== 综合投资建议 (Investment Conclusion) ===")
        
        # Logic for conclusion
        signals = []
        suggested_pos = 0.0
        
        # 1. Check M6 (Net Asset Value) Trend
        net_ret = m6_res.get('net_final_ret_pct') or 0
        if net_ret > 0:
            signals.append("✅ M6 净值长期向上 (Positive NAV)")
            suggested_pos += 20
        else:
            signals.append(f"❌ M6 净值回撤 (Negative NAV: {net_ret}%)")
            
        # 2. Check M5 (Rolling Stability)
        m5_mean = m5_res.get("summary_ret_10", {}).get('mean') or 0
        if m5_mean > 0:
            signals.append(f"✅ M5 滚动窗口盈利 (Avg Ret: {m5_mean}%)")
            suggested_pos += 30
        else:
            signals.append(f"❌ M5 滚动窗口亏损 (Avg Ret: {m5_mean}%)")

        # 3. Check M3 (Best Parameter)
        winners = m3_res.get("winners", [])
        if winners:
            best_ret = winners[0].get('avg_ret_10')
            if best_ret and best_ret > 0:
                 signals.append(f"✅ M3 存在正收益参数 (Best: {best_ret}%)")
                 suggested_pos += 30
            else:
                 signals.append(f"❌ M3 最优参数仍亏损 (Best: {best_ret}%)")
        else:
             signals.append("❌ M3 无有效参数组合")

        # 4. Check Data Sufficiency
        if len(rows) < 100:
            signals.append("⚠️ 数据样本不足 (Low Data Sample)")
            suggested_pos = min(suggested_pos, 20) # Cap position if low data

        log("信号分析:")
        for s in signals:
            log(f"  {s}")
            
        log("\n最终结论:")
        if suggested_pos <= 20:
            decision = "空仓 (Empty Position)"
            action = "市场环境极差，建议完全空仓观望，停止所有买入操作。"
        elif suggested_pos <= 50:
            decision = "轻仓 (Underweight)"
            action = "市场震荡或数据不足，建议轻仓试错，严格止损。"
        else:
            decision = "满仓/重仓 (Full Position)"
            action = "市场环境向好，策略有效性高，建议积极参与。"
            
        log(f"  建议仓位: {suggested_pos}%")
        log(f"  操作指令: 【{decision}】")
        log(f"  执行建议: {action}")

        # Save to file
        today_str = datetime.now().strftime('%Y-%m-%d')
        filename = f"evaluation_{today_str}.txt"
        output_dir = REPO_ROOT / "result" / "evaluation_reports"
        os.makedirs(output_dir, exist_ok=True)
        filepath = output_dir / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(output_lines))
        
        print(f"\nReport saved to: {filepath}")
        
    finally:
        conn.close()

if __name__ == "__main__":
    run_evaluation()
