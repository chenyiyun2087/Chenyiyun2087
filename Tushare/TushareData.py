import tushare as ts
import pandas as pd

# 1. 初始化接口（替换为你的Token）
pro = ts.pro_api(token='9e42dcf3815d83da1d5007c7e5938803cbc82a8f0221d7c36c71f4be')
print(pro)
# 2. 定义参数（沪深300指数代码为000300.SH）
index_code = '000300.SH'  # 固定代码，不可修改
start_date = '20050101'   # 起始日期，格式：YYYYMMDD
end_date = '20241230'     # 结束日期，格式：YYYYMMDD

# 3. 调用接口获取数据
df = pro.index_daily(
    ts_code=index_code,
    start_date=start_date,
    end_date=end_date
)

# 4. 数据处理（按日期升序排列，方便后续分析）
df['trade_date'] = pd.to_datetime(df['trade_date'])  # 将字符串日期转为日期格式
df = df.sort_values('trade_date').reset_index(drop=True)  # 按日期排序

# 5. 保存数据（可选，支持CSV/Excel格式）
df.to_csv('CSI300_2005-2024.csv', index=False)  # 保存为CSV
# df.to_excel('CSI300_2005-2024.xlsx', index=False)  # 如需Excel，取消注释

# 6. 打印数据预览（确认是否正确）
print("数据获取成功！前5行预览：")
print(df.head())
print(f"\n数据时间范围：{df['trade_date'].min()} 至 {df['trade_date'].max()}")
print(f"共 {len(df)} 条记录（交易日数据）")