
import pymysql
from Eastmoney.EastmoneyController import DEFAULT_MYSQL_CONFIG

def check_schema():
    conf = DEFAULT_MYSQL_CONFIG
    print(f"Connecting to {conf['database']}...")
    conn = pymysql.connect(**conf)
    try:
        with conn.cursor() as cursor:
            cursor.execute("DESCRIBE em_individual_margin_trading")
            rows = cursor.fetchall()
            print("Current columns:")
            for row in rows:
                print(row)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_schema()
