import pandas as pd

def xlsx_to_csv(xlsx_file_path, csv_file_path):
    # 读取xlsx文件
    df = pd.read_excel(xlsx_file_path)
    # 将数据保存为csv文件
    df.to_csv(csv_file_path, index=False)

# 调用函数进行转换
if __name__ == '__main__':
    xlsx_to_csv('stock_codes.xlsx', 'output.csv')