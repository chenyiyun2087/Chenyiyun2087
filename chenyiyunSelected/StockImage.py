import matplotlib.pyplot as plt
import pandas as pd

# 数据准备
data = [
    {'code': '300705', 'date': '2025-08-13', 'type': 'buy'},
    {'code': '300705', 'date': '2025-08-13', 'type': 'sell'},
    {'code': '300705', 'date': '2025-08-18', 'type': 'buy'},
    {'code': '300705', 'date': '2025-08-29', 'type': 'sell'},
]

# 转换为DataFrame
df = pd.DataFrame(data)
df['date'] = pd.to_datetime(df['date'])

# 为股票代码分配y轴位置
codes = df['code'].unique()
code_to_y = {code: idx for idx, code in enumerate(codes)}
df['y'] = df['code'].map(code_to_y)

# 绘图
plt.figure(figsize=(10, 3))
for _, row in df.iterrows():
    color = 'red' if row['type'] == 'buy' else 'green'
    plt.scatter(row['date'], row['y'], color=color, s=100, alpha=0.8)

# 设置坐标轴
plt.yticks(range(len(codes)), codes)
plt.xlabel('日期')
plt.ylabel('股票代码')
plt.title('股票买卖点分布图')
plt.grid(True, axis='x', linestyle='--', alpha=0.5)
plt.tight_layout()
plt.show()