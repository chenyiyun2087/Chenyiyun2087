import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text


COLUMN_MAP = {
    "日期": "trade_date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "振幅": "amplitude",
    "涨跌幅": "pct_change",
    "涨跌额": "change_amount",
    "换手率": "turnover",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将ScoreRank目录下的A股K线CSV文件导入本地MySQL数据库",
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).resolve().parent),
        help="CSV文件所在目录（默认：ScoreRank目录）",
    )
    parser.add_argument(
        "--db-host",
        default="localhost",
        help="MySQL host",
    )
    parser.add_argument(
        "--db-port",
        default=3306,
        type=int,
        help="MySQL port",
    )
    parser.add_argument(
        "--db-user",
        default="root",
        help="MySQL user",
    )
    parser.add_argument(
        "--db-password",
        default="19871019",
        help="MySQL password",
    )
    parser.add_argument(
        "--db-name",
        default="stock_kline",
        help="MySQL database name",
    )
    parser.add_argument(
        "--table",
        default="daily_kline",
        help="目标表名",
    )
    parser.add_argument(
        "--chunksize",
        default=20000,
        type=int,
        help="每批导入行数",
    )
    return parser.parse_args()


def get_adj_type(file_path: Path) -> str:
    filename = file_path.name.lower()
    if "_daily_qfq" in filename:
        return "qfq"
    if "_daily_hfq" in filename:
        return "hfq"
    return "raw"


def get_mysql_engine(host: str, port: int, user: str, password: str, db_name: str):
    return create_engine(
        f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4",
        future=True,
    )


def ensure_database_and_table(
    host: str,
    port: int,
    user: str,
    password: str,
    db_name: str,
    table: str,
) -> None:
    root_engine = create_engine(
        f"mysql+pymysql://{user}:{password}@{host}:{port}/?charset=utf8mb4",
        future=True,
    )
    with root_engine.begin() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{db_name}`"))

    engine = get_mysql_engine(host, port, user, password, db_name)
    ddl = f"""
    CREATE TABLE IF NOT EXISTS `{table}` (
        trade_date DATE NOT NULL,
        symbol VARCHAR(6) NOT NULL,
        adj_type VARCHAR(8) NOT NULL,
        open DECIMAL(18, 6),
        close DECIMAL(18, 6),
        high DECIMAL(18, 6),
        low DECIMAL(18, 6),
        volume BIGINT,
        amount BIGINT,
        amplitude DECIMAL(18, 6),
        pct_change DECIMAL(18, 6),
        change_amount DECIMAL(18, 6),
        turnover DECIMAL(18, 6),
        PRIMARY KEY (symbol, trade_date, adj_type),
        KEY idx_trade_date (trade_date),
        KEY idx_adj_type (adj_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))


def read_csv_with_fallback(file_path: Path, chunksize: int):
    encodings = ["utf-8-sig", "utf-8", "gbk", "gb2312"]
    last_error = None
    for encoding in encodings:
        try:
            return pd.read_csv(
                file_path,
                sep="\t",
                encoding=encoding,
                chunksize=chunksize,
            )
        except UnicodeDecodeError as exc:
            last_error = exc
    raise last_error


def normalize_chunk(df: pd.DataFrame, adj_type: str) -> pd.DataFrame:
    df = df.rename(columns=COLUMN_MAP)
    missing = set(COLUMN_MAP.values()) - set(df.columns)
    if missing:
        raise ValueError(f"缺少字段: {sorted(missing)}")

    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df["symbol"] = df["symbol"].astype(str).str.zfill(6)
    for col in [
        "open",
        "close",
        "high",
        "low",
        "volume",
        "amount",
        "amplitude",
        "pct_change",
        "change_amount",
        "turnover",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["trade_date", "symbol"])
    df["adj_type"] = adj_type
    return df[
        [
            "trade_date",
            "symbol",
            "adj_type",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "amplitude",
            "pct_change",
            "change_amount",
            "turnover",
        ]
    ]


def import_file(
    engine,
    file_path: Path,
    table: str,
    chunksize: int,
) -> int:
    adj_type = get_adj_type(file_path)
    total_rows = 0
    for chunk in read_csv_with_fallback(file_path, chunksize):
        normalized = normalize_chunk(chunk, adj_type)
        normalized.to_sql(
            table,
            engine,
            if_exists="append",
            index=False,
            chunksize=chunksize,
            method="multi",
        )
        total_rows += len(normalized)
    return total_rows


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        raise SystemExit(f"目录不存在: {data_dir}")

    ensure_database_and_table(
        args.db_host,
        args.db_port,
        args.db_user,
        args.db_password,
        args.db_name,
        args.table,
    )
    engine = get_mysql_engine(
        args.db_host,
        args.db_port,
        args.db_user,
        args.db_password,
        args.db_name,
    )

    csv_files = sorted(data_dir.glob("*_daily*.csv"))
    if not csv_files:
        raise SystemExit(f"未找到CSV文件: {data_dir}")

    total_files = 0
    total_rows = 0
    for file_path in csv_files:
        rows = import_file(engine, file_path, args.table, args.chunksize)
        total_files += 1
        total_rows += rows
        print(f"导入 {file_path.name}: {rows} 行")

    print(f"完成导入: {total_files} 个文件, {total_rows} 行")


if __name__ == "__main__":
    main()
