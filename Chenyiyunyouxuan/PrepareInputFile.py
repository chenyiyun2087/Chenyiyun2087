import os
import re
import glob
import argparse
from datetime import datetime
import pandas as pd
import numpy as np


def detect_and_read_csv(csv_path: str) -> pd.DataFrame:
	encodings = ['utf-8-sig', 'utf-8', 'gbk', 'gb2312', 'latin1']
	last_err = None
	for enc in encodings:
		try:
			return pd.read_csv(csv_path, encoding=enc)
		except Exception as e:
			last_err = e
			continue
	raise RuntimeError(f"无法读取CSV文件: {csv_path}. 最后错误: {last_err}")


def normalize_code(code: str) -> str:
	if code is None:
		return ''
	code = str(code).strip()
	# 仅保留数字部分并左侧补零到6位（常见A股代码格式）
	digits = ''.join(ch for ch in code if ch.isdigit())
	return digits.zfill(6) if digits else code


def build_full_ticker_for_szse(code: str) -> str:
	code = normalize_code(code)
	# 目标匹配样式：'SZSE:' + 6位代码；若原数据有其他变体，也可在此扩展
	return f"SZSE:{code}"


def select_and_rename_columns(df: pd.DataFrame) -> pd.DataFrame:
	# 目标列及其来源映射（右侧为源数据列名）
	column_mapping = {
		'名称': '名称',
		'行业': '行业',
		'stock_code': 'stock_code',  # 由 Full Ticker 或输入代码派生
		'现 价': '现价',
		'5日移动均线': '5日移动均线',
		'10日移动均线': '10日移动均线',
		'20日移动均线': '20日移动均线',
		'市盈增长比率': '市盈增长比率',
		'公允价值': '公允价值',
		'公允价 值上行边际': '公允价值上行边际',
		'公允价值不确定性': '公允价值不确定性',
		'贝塔(5年)': '贝塔(5年)',
		'价格动能评分': '价格动能评分',
		'现金流评分': '现金流评分',
		'财务成长稳 健度评分': '财务成长稳健度评分',
		'盈利评分': '盈利评分',
		'atr_14d': 'atr_14d',
		'交易量(仅交易日)': '交易量(仅交易日)',
		'流通股份': '流通股份',
		'市值(经调整)': '市值(经调整)',
		'预期 净利润增长率': '预期净利润增长率',
		'毛利率': '毛利率',
		'税前利润率': '税前利润率',
		'1周价格总回报': '1周价格总回报',
		'年初至今的价格总回报': '年初至今的价格总回报',
		'市盈率(经调整)': '市盈率(经调整)',
		'technical_signal_1d': 'technical_signal_1d',
		'technical_signal_1w': 'technical_signal_1w',
	}

	# 先复制一份，缺失列补空
	for target_col, src_col in column_mapping.items():
		if src_col not in df.columns:
			df[src_col] = np.nan

	# 重命名为目标列名顺序
	ordered = pd.DataFrame()
	for target_col, src_col in column_mapping.items():
		ordered[target_col] = df[src_col]
	return ordered


def main():
	parser = argparse.ArgumentParser(description='按股票代码匹配 CSV 的 Full Ticker 并导出指定列为 XLS')
	parser.add_argument('--csv', default='/Users/chenyiyun/Trade/investingPro/cyy - cyy - YYYY-MM-DD-filtered.csv', help='输入CSV路径，可含占位符 YYYY-MM-DD；默认: D:/交易/investingPro/cyy - cyy - YYYY-MM-DD-filtered.csv（自动取最新日期）')
	parser.add_argument('--date', help='用于替换占位符 YYYY-MM-DD 的日期，如 2025-08-18；未提供则自动匹配该目录下最新日期文件')
	parser.add_argument('--codes', help='逗号分隔的一串股票代码，如 000001,000002,000063')
	parser.add_argument('--codes-file', help='包含股票代码的文本文件路径，每行一个代码')
	parser.add_argument('--output', help='输出XLS路径（可选），默认与CSV同目录，文件名加 -subset.xls')
	args = parser.parse_args()

	def resolve_csv_path(path_in: str, date_str: str | None) -> str:
		# 直接存在则返回
		if os.path.isfile(path_in):
			return path_in
		# 占位符替换
		if 'YYYY-MM-DD' in path_in:
			if date_str:
				cand = path_in.replace('YYYY-MM-DD', date_str)
				if os.path.isfile(cand):
					return cand
			# 自动匹配目录下最新日期
			pattern = path_in.replace('YYYY-MM-DD', '*')
			cands = glob.glob(pattern)
			best = None
			best_dt = None
			for p in cands:
				m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(p))
				if m:
					try:
						dt = datetime.strptime(m.group(1), '%Y-%m-%d').date()
						if best_dt is None or dt > best_dt:
							best_dt = dt
							best = p
					except Exception:
						pass
			if best and os.path.isfile(best):
				print(f"使用匹配到的最新文件: {best}")
				return best
		# 通配符路径
		cands = glob.glob(path_in)
		if cands:
			# 返回按修改时间最新的
			cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
			print(f"使用通配符匹配到的文件: {cands[0]}")
			return cands[0]
		raise FileNotFoundError(f'未找到CSV文件: {path_in}')

	args.csv = resolve_csv_path(args.csv, args.date)

	# 收集股票代码
	codes = []
	if args.codes:
		codes += [normalize_code(x) for x in args.codes.split(',') if str(x).strip()]
	if args.codes_file:
		if not os.path.exists(args.codes_file):
			raise FileNotFoundError(f'代码文件不存在: {args.codes_file}')
		with open(args.codes_file, 'r', encoding='utf-8') as f:
			for line in f:
				line = line.strip()
				if line:
					codes.append(normalize_code(line))
	if not codes:
		try:
			user_in = input('请输入股票代码（逗号/空格/中文逗号分隔，如 000001,000002  或  000001 000002）：').strip()
		except EOFError:
			user_in = ''
		if user_in:
			# 支持逗号、中文逗号、分号、空格等分隔
			seps = [',', '，', ';', '；', ' ']
			for sep in seps:
				user_in = user_in.replace(sep, ',')
			parts = [p for p in user_in.split(',') if p.strip()]
			codes += [normalize_code(p) for p in parts]
		if not codes:
			raise ValueError('未提供任何股票代码。请使用 --codes/--codes-file 或在提示中手动输入。')

	# 读取CSV
	df = detect_and_read_csv(args.csv)

	# 定位 Full Ticker 列
	full_ticker_col = None
	for cand in ['Full Ticker', 'FullTicker', 'full_ticker', 'Full_Ticker']:
		if cand in df.columns:
			full_ticker_col = cand
			break
	if full_ticker_col is None:
		# 退回到第2列
		if df.shape[1] >= 2:
			full_ticker_col = df.columns[1]
		else:
			raise KeyError('未找到 Full Ticker 列，且文件列数不足2列')

	# 基于 Full Ticker 提取规范6位代码并据此匹配（更稳健，忽略前缀差异如 SZSE/SSE）
	norm_code_series = df[full_ticker_col].astype(str).str.extract(r'(\d{6})', expand=False).fillna('')
	norm_code_series = norm_code_series.apply(normalize_code)
	keep_mask = norm_code_series.isin(set(codes))
	filtered = df.loc[keep_mask].copy()

	# 生成 stock_code 列（反向从 Full Ticker 提取，如果可能）
	def extract_code_from_full(full: str) -> str:
		if not isinstance(full, str):
			return ''
		m = re.findall(r'\d{6}', full)
		if m:
			return normalize_code(m[-1])
		return normalize_code(full)

	filtered['stock_code'] = filtered[full_ticker_col].map(extract_code_from_full)

	# 选择并重命名列
	ordered = select_and_rename_columns(filtered)

	# 未匹配到的代码：在输出中追加占位行并标记
	matched_codes_set = set(ordered['stock_code'].astype(str)) if 'stock_code' in ordered.columns else set()
	unmatched = sorted(set(codes) - matched_codes_set)
	if unmatched:
		print(f"未匹配到的代码（可能因前缀/市场不同或CSV无该标的）: {', '.join(unmatched)}")
		placeholder_rows = []
		for c in unmatched:
			row = {col: (np.nan) for col in ordered.columns}
			# 仅填充可识别字段进行标记
			if 'stock_code' in ordered.columns:
				row['stock_code'] = c
			if '名称' in ordered.columns:
				row['名称'] = '未匹配'
			placeholder_rows.append(row)
		if placeholder_rows:
			ordered = pd.concat([ordered, pd.DataFrame(placeholder_rows)], ignore_index=True)

	# 输出路径
	if args.output:
		out_path = args.output
	else:
		base, _ = os.path.splitext(os.path.basename(args.csv))
		dir_name = os.path.dirname(args.csv)
		out_path = os.path.join(dir_name, f"{base}-subset.xls")

	# 写出为 XLS（xlwt），若缺失则降级为 XLSX
	try:
		ordered.to_excel(out_path, index=False, engine='xlwt')
		print(f"已导出: {out_path}")
	except Exception as e:
		print(f"写XLS失败({e})，尝试写为XLSX...")
		out_path_xlsx = out_path[:-4] + '.xlsx' if out_path.lower().endswith('.xls') else out_path + '.xlsx'
		ordered.to_excel(out_path_xlsx, index=False)
		print(f"已导出: {out_path_xlsx}")


if __name__ == '__main__':
	main()


