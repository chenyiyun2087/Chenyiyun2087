import os
import argparse
import pandas as pd
import numpy as np
from pathlib import Path


def read_prompt_template(prompt_path: str) -> str:
    """读取提示词模板文件"""
    with open(prompt_path, 'r', encoding='utf-8') as f:
        return f.read()


def detect_and_read_excel(excel_path: str) -> pd.DataFrame:
    """自动检测并读取Excel文件（支持.xls和.xlsx）"""
    try:
        # 先尝试 openpyxl (for .xlsx)
        return pd.read_excel(excel_path, engine='openpyxl')
    except Exception:
        try:
            # 再尝试 xlrd (for .xls)
            return pd.read_excel(excel_path, engine='xlrd')
        except Exception as e:
            raise RuntimeError(f"无法读取Excel文件: {excel_path}. 错误: {e}")


def normalize_stock_code(code) -> str:
    """规范化股票代码为6位数字，不足补0"""
    if pd.isna(code):
        return ''
    code_str = str(code).strip()
    # 提取数字部分
    digits = ''.join(ch for ch in code_str if ch.isdigit())
    # 左侧补0到6位
    return digits.zfill(6) if digits else ''


def clean_industry(industry) -> str:
    """清理行业字段，去除#RESTRICTED!等无效值"""
    if pd.isna(industry):
        return ''
    industry_str = str(industry).strip()
    # 去除无效标记
    invalid_markers = ['#RESTRICTED!', '#REF!', '#N/A', '#VALUE!', '#NAME?', 'nan']
    if industry_str in invalid_markers or industry_str.upper() in invalid_markers:
        return ''
    return industry_str


def extract_stock_data(df: pd.DataFrame) -> list:
    """从DataFrame中提取股票数据"""
    stock_list = []

    for idx, row in df.iterrows():
        # 提取关键字段
        stock_code = normalize_stock_code(row.get('stock_code', ''))
        name = str(row.get('名称', '')).strip()
        price = row.get('现 价', np.nan)
        pe = row.get('市盈率(经调整)', np.nan)
        industry = clean_industry(row.get('行业', ''))

        # 跳过未匹配的记录
        if name == '未匹配' or not stock_code or name == 'nan':
            continue

        # 构建股票信息字典
        stock_info = {
            'code': stock_code,
            'name': name,
            'price': price if pd.notna(price) else '',
            'pe': pe if pd.notna(pe) else '',
            'industry': industry
        }

        stock_list.append(stock_info)

    return stock_list


def format_stock_list(stock_list: list, format_type: str = 'full') -> str:
    """
    格式化股票列表
    format_type: 'simple' (仅代码) 或 'full' (代码+价格+PE+行业)
    """
    if format_type == 'simple':
        # 格式1: 600089, 000858, 002594
        codes = [s['code'] for s in stock_list]
        return ', '.join(codes)
    else:
        # 格式2: 600089,20.35,15.2,变压器/新能源
        lines = []
        for s in stock_list:
            parts = [s['code']]
            if s['price']:
                parts.append(str(s['price']))
            if s['pe']:
                parts.append(str(s['pe']))
            if s['industry']:
                parts.append(s['industry'])
            lines.append(','.join(parts))
        return '\n'.join(lines)


def create_analysis_prompt(prompt_template: str, stock_data_str: str,
                           stock_count: int) -> str:
    """创建完整的分析提示词"""
    # 在模板末尾添加股票数据
    separator = "\n" + "=" * 60 + "\n"

    prompt = prompt_template + separator
    prompt += f"【待分析股票列表】({stock_count}只)\n\n"
    prompt += stock_data_str + "\n\n"
    prompt += "请按照上述协议对以上股票进行批量筛查分析。"

    return prompt


def main():
    parser = argparse.ArgumentParser(
        description='将Excel股票数据自动映射到分析提示词中'
    )
    parser.add_argument(
        '--excel',
        help='输入Excel文件路径（PrepareInputFile.py生成的文件）'
    )
    parser.add_argument(
        '--prompt',
        default='prompt.txt',
        help='提示词模板文件路径，默认: prompt.txt'
    )
    parser.add_argument(
        '--output',
        help='输出文件路径，默认在Excel同目录下生成 -analysis-prompt.txt'
    )
    parser.add_argument(
        '--format',
        choices=['simple', 'full'],
        default='full',
        help='股票列表格式: simple(仅代码) 或 full(完整信息)，默认: full'
    )

    args = parser.parse_args()

    # 查找Excel文件
    if args.excel:
        excel_path = args.excel
    else:
        # 自动查找当前目录及子目录中最新的subset文件
        patterns = ['*-subset.xls', '*-subset.xlsx']
        candidates = []
        for pattern in patterns:
            candidates.extend(Path('.').rglob(pattern))

        if candidates:
            # 按修改时间排序，取最新的
            excel_path = str(max(candidates, key=lambda p: p.stat().st_mtime))
            print(f"自动找到Excel文件: {excel_path}")
        else:
            raise FileNotFoundError(
                "未找到Excel文件。请使用 --excel 参数指定文件路径"
            )

    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"Excel文件不存在: {excel_path}")

    # 检查提示词文件
    if not os.path.exists(args.prompt):
        raise FileNotFoundError(f"提示词文件不存在: {args.prompt}")

    # 读取数据
    print(f"正在读取Excel文件: {excel_path}")
    df = detect_and_read_excel(excel_path)
    print(f"成功读取 {len(df)} 行数据")

    # 提取股票数据
    stock_list = extract_stock_data(df)
    print(f"有效股票数量: {len(stock_list)}")

    if not stock_list:
        raise ValueError("未找到有效的股票数据")

    # 读取提示词模板
    print(f"正在读取提示词模板: {args.prompt}")
    prompt_template = read_prompt_template(args.prompt)

    # 格式化股票列表
    stock_data_str = format_stock_list(stock_list, args.format)

    # 创建完整提示词
    final_prompt = create_analysis_prompt(
        prompt_template,
        stock_data_str,
        len(stock_list)
    )

    # 确定输出路径
    if args.output:
        output_path = args.output
    else:
        excel_base = os.path.splitext(os.path.basename(excel_path))[0]
        excel_dir = os.path.dirname(excel_path)
        output_path = os.path.join(
            excel_dir,
            f"{excel_base}-analysis-prompt.txt"
        )

    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_prompt)

    print(f"\n✅ 成功生成分析提示词文件: {output_path}")
    print(f"📊 包含 {len(stock_list)} 只股票")
    print(f"📝 格式: {args.format}")
    print(f"\n前3只股票预览:")
    for i, stock in enumerate(stock_list[:3], 1):
        print(f"  {i}. {stock['code']} {stock['name']} - "
              f"¥{stock['price']} PE:{stock['pe']}")

    if len(stock_list) > 3:
        print(f"  ... 还有 {len(stock_list) - 3} 只股票")

    print(f"\n💡 使用建议:")
    print(f"  - 将 {output_path} 的内容复制到 DeepSeek/Gemini/Grok")
    print(f"  - 或直接使用命令: cat '{output_path}' | pbcopy (Mac)")


if __name__ == '__main__':
    '''python .\StockPrompt.py --excel "D:\交易\investingPro\cyy - cyy - 2026-01-09-filteredubset.xlsx"
    '''
    main()