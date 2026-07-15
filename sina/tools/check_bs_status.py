#!/usr/bin/env python3
"""
检查 sina B/S 检测任务状态脚本
"""
import argparse
import json
import logging
from datetime import datetime

import pymysql
from scoreRank.core.db_config import require_pymysql_config

def check_status(target_date: str, db_config: dict = None):
    """
    检查指定日期的 B/S 检测状态
    :param target_date: YYYYMMDD
    :return: dict 状态信息
    """
    if db_config is None:
        db_config = require_pymysql_config(dict_cursor=True)

    try:
        conn = pymysql.connect(**db_config)
        with conn.cursor() as cursor:
            # 1. 查询当日记录总数
            sql_count = "SELECT COUNT(*) as cnt FROM bs_detection_results WHERE batch_date = %s"
            cursor.execute(sql_count, (target_date,))
            count = cursor.fetchone()['cnt']
            
            # 2. 查询最近更新时间
            sql_last = "SELECT created_at FROM bs_detection_results WHERE batch_date = %s ORDER BY created_at DESC LIMIT 1"
            cursor.execute(sql_last, (target_date,))
            last_record = cursor.fetchone()
            last_update = last_record['created_at'].strftime('%Y-%m-%d %H:%M:%S') if last_record else None
            
            # 3. 统计B/S信号数量
            sql_stats = """
                SELECT 
                    SUM(has_buy_signal) as buy_count,
                    SUM(has_sell_signal) as sell_count
                FROM bs_detection_results 
                WHERE batch_date = %s
            """
            cursor.execute(sql_stats, (target_date,))
            stats = cursor.fetchone()
            
            # 简单判断逻辑：如果有数据，认为"部分完成"或"完成"
            # 实际生产中可能需要对比总股票数，但这里以是否有数据为准
            status = "Completed" if count > 0 else "Pending"
            
            return {
                "date": target_date,
                "status": status,
                "count": count,
                "buy_count": int(stats['buy_count'] or 0),
                "sell_count": int(stats['sell_count'] or 0),
                "last_update": last_update
            }
            
    except Exception as e:
        return {
            "date": target_date,
            "status": "Error",
            "message": str(e)
        }
    finally:
        if 'conn' in locals() and conn.open:
            conn.close()

def main():
    parser = argparse.ArgumentParser(description="Check sina B/S Detection Status")
    parser.add_argument("--date", help="Date to check (YYYYMMDD)", default=datetime.now().strftime('%Y%m%d'))
    parser.add_argument("--json", action="store_true", help="Output in JSON format")
    args = parser.parse_args()
    
    result = check_status(args.date)
    
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"=== sina B/S Status Check ({args.date}) ===")
        print(f"Status: {result['status']}")
        print(f"Total Records: {result.get('count', 0)}")
        print(f"Buy Signals: {result.get('buy_count', 0)}")
        print(f"Sell Signals: {result.get('sell_count', 0)}")
        print(f"Last Update: {result.get('last_update', 'N/A')}")

if __name__ == "__main__":
    main()
