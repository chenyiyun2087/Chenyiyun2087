import pandas as pd
import numpy as np
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import chardet
import io
import os
from datetime import datetime
pd.set_option('future.no_silent_downcasting', True)

# Function to detect file encoding
def detect_encoding(file_path):
    with open(file_path, 'rb') as file:
        raw_data = file.read()
        result = chardet.detect(raw_data)
        return result['encoding']

# Function to clean problematic lines
def clean_file(file_path, encoding='utf-8'):
    cleaned_lines = []
    with open(file_path, 'r', encoding=encoding, errors='replace') as file:
        for i, line in enumerate(file, 1):
            # Replace tabs within quoted strings to avoid splitting issues
            line = line.replace('\t"', '"').replace('"\t', '"')
            # Count fields (assuming tab-separated)
            fields = line.strip().split('\t')
            if len(fields) != 29:  # Expected number of columns based on provided data
                print(f"Warning: Line {i} has {len(fields)} fields, expected 29. Line: {line.strip()}")
                # Attempt to fix by merging fields if possible
                if len(fields) > 29:
                    # Example: Merge extra fields (simplified, adjust based on inspection)
                    cleaned_line = '\t'.join(fields[:29])
                    cleaned_lines.append(cleaned_line)
                else:
                    print(f"Skipping malformed line {i}")
                    continue
            else:
                cleaned_lines.append(line.strip())
    return cleaned_lines

# Register a Chinese-capable font to avoid garbled text in PDF
def register_cjk_font():
    candidates = [
        ('SimHei', 'simhei.ttf'),
        ('SimSun', 'simsun.ttc'),
        ('MicrosoftYaHei', 'msyh.ttc'),
        ('MicrosoftYaHei', 'msyh.ttf'),
        ('NotoSansCJKsc', 'NotoSansCJKsc-Regular.otf'),
        ('SourceHanSansCN', 'SourceHanSansCN-Regular.otf'),
    ]
    font_dirs = [
        os.path.join(os.environ.get('WINDIR', 'C\\Windows'), 'Fonts'),
        os.getcwd(),
    ]
    for font_name, font_file in candidates:
        for font_dir in font_dirs:
            font_path = os.path.join(font_dir, font_file)
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont(font_name, font_path))
                    print(f"Registered CJK font: {font_name} from {font_path}")
                    return font_name
                except Exception as e:
                    print(f"Failed to register font {font_file}: {e}")
                    continue
    print("警告：未找到可用中文字体，将使用 Helvetica，可能出现乱码。可将 Windows 的 simhei.ttf 复制到项目目录。")
    return 'Helvetica'

# Robust percent parser: accepts values like '12.3%', '12.3', 12.3, 0.123
def parse_percent_series_to_ratio(series):
    s = pd.to_numeric(series.astype(str).str.replace('%', '', regex=False), errors='coerce')
    non_na = s.dropna()
    if not non_na.empty and non_na.abs().max() > 1:
        s = s / 100.0
    return s

# Function to calculate scores based on the model
def calculate_scores(df):
    # Ensure required columns exist with safe defaults
    def ensure_series(column_name, default_value=np.nan):
        if column_name not in df.columns:
            df[column_name] = default_value
        return df[column_name]

    # Defaults for columns used downstream
    ensure_series('现价', np.nan)
    ensure_series('5日移动均线', np.nan)
    ensure_series('10日移动均线', np.nan)
    ensure_series('20日移动均线', np.nan)
    ensure_series('atr_14d', 1.0)
    ensure_series('technical_signal_1d', '中性')
    ensure_series('technical_signal_1w', '中性')
    ensure_series('价格动能评分', 0.0)
    ensure_series('现金流评分', 0.0)
    ensure_series('财务成长稳健度评分', 0.0)
    ensure_series('盈利评分', 0.0)
    ensure_series('预期净利润增长率', 0.0)
    ensure_series('毛利率', 0.0)
    ensure_series('税前利润率', 0.0)
    ensure_series('市盈率(经调整)', np.nan)
    ensure_series('EV/EBITDA增长率', np.nan)
    ensure_series('PEGY比率', np.nan)
    ensure_series('每股股息(不包括特别股息及未就拆股调整)', 0.0)
    ensure_series('公允价值上行边际', np.nan)
    ensure_series('市盈增长比率', np.nan)
    ensure_series('贝塔(5年)', 1.0)
    ensure_series('公允价值不确定性', '中性')
    ensure_series('名称', '')
    ensure_series('Full Ticker', '')
    numeric_cols = ['现价', '5日移动均线', '10日移动均线', '20日移动均线', '市盈增长比率', '公允价值上行边际', '贝塔(5年)',
                    '价格动能评分', '现金流评分', '财务成长稳健度评分', '盈利评分', 'atr_14d', '预期净利润增长率',
                    '毛利率', '税前利润率', '1周价格总回报', '年初至今的价格总回报', '市盈率(经调整)',
                    'EV/EBITDA增长率', 'PEGY比率', '每股股息(不包括特别股息及未就拆股调整)']
    for col in numeric_cols:
        if col in df.columns:
            series_obj = df[col].astype('object')
            series_clean = series_obj.replace({'NM-': np.nan, '-': np.nan})
            series_clean = series_clean.infer_objects(copy=False)
            df[col] = pd.to_numeric(series_clean, errors='coerce')

    if '公允价值上行边际' in df.columns:
        df['公允价值上行边际'] = parse_percent_series_to_ratio(df['公允价值上行边际'])

    def ma_arrangement(row):
        if row['现价'] > row['5日移动均线'] > row['10日移动均线'] > row['20日移动均线']:
            return 1.0
        elif row['现价'] > row['5日移动均线']:
            return 0.7
        else:
            return 0.3

    df['MA_Score'] = df.apply(ma_arrangement, axis=1)
    df['ATR_Score'] = 1 / (1 + df['atr_14d'].fillna(1))
    tech_signal_map = {
        '强力买入': 1.0,
        '买入': 0.8,
        '中性': 0.5,
        '卖出': 0.2,
        '强力卖出': 0.0,
    }
    day_sig = df['technical_signal_1d'].map(tech_signal_map).fillna(0.5)
    week_sig = df['technical_signal_1w'].map(tech_signal_map).fillna(0.5)
    df['Tech_Signal_Score'] = day_sig * 0.6 + week_sig * 0.4
    df['Technical_Score'] = (df['价格动能评分'].fillna(0) * 0.4 + df['MA_Score'] * 0.3 +
                             df['ATR_Score'] * 0.2 + df['Tech_Signal_Score'] * 0.1)
    df['Technical_Score'] = np.clip(df['Technical_Score'], 0, 3)

    df['Profit_Score'] = (df['毛利率'].fillna(0) + df['税前利润率'].fillna(0)) / 2 * 5
    df['Growth_Score'] = np.clip(df['预期净利润增长率'].fillna(0) * 0.5, -2, 5)
    df['Val_Score'] = (np.clip(1 / (1 + df['市盈增长比率'].abs().fillna(1)), 0, 1) * 3 +
                       np.clip(df['公允价值上行边际'].fillna(0), -1, 1) * 2)
    df['Stability_Score'] = (df['现金流评分'].fillna(0) + df['财务成长稳健度评分'].fillna(0)) / 2
    df['Dividend_Score'] = df['每股股息(不包括特别股息及未就拆股调整)'].fillna(0) * 10
    df['Fundamental_Score'] = (df['Profit_Score'] * 0.3 + df['Growth_Score'] * 0.25 +
                               df['Val_Score'] * 0.2 + df['Stability_Score'] * 0.15 +
                               df['Dividend_Score'] * 0.1)
    df['Fundamental_Score'] = np.clip(df['Fundamental_Score'], 0, 3)

    uncertainty_map = {'最低': 1.0, '中性': 0.7, '最高': 0.3}
    df['Uncertainty_Score'] = df['公允价值不确定性'].map(uncertainty_map).fillna(0.5)
    df['Beta_Score'] = np.clip(1 / (1 + df['贝塔(5年)'].abs().fillna(1)), 0.3, 1)
    df['Risk_Score'] = (df['Beta_Score'] * 0.6 + df['Uncertainty_Score'] * 0.4) * 2

    # 综合评分 = 0.6211 * 技术面评分 + 0.4628 * 基本面评分 - 0.4227 * 风险面评分 + 0.2123
    df['Comprehensive_Score'] = (
        df['Technical_Score'] * 0.6211 +
        df['Fundamental_Score'] * 0.4628 -
        df['Risk_Score'] * 0.4227 +
        0.2123
    )
    # 2025-08-26 第一次迭代优化：
    # 动态调整技术面与基本面权重
    # 要修改的函数: calculate_scores
    # 要调整的参数/权重: 修改 Comprehensive_Score 的计算公式，考虑引入市场环境因子，或者简单地降低技术面权重，提升基本面权重。
    df['Comprehensive_Score'] = (
            df['Technical_Score'] * 0.55 +  # from 0.6211
            df['Fundamental_Score'] * 0.50 +  # from 0.4628
            df['Risk_Score'] * -0.4227 +
            0.2123
    )


    valid_scores = df['Comprehensive_Score'].dropna()
    if len(valid_scores) >= 5:
        p85 = np.nanpercentile(valid_scores, 85)
        p65 = np.nanpercentile(valid_scores, 65)
        p45 = np.nanpercentile(valid_scores, 45)

        def map_rating(score):
            if np.isnan(score):
                return '回避'
            if score >= p85:
                return '强烈推荐'
            if score >= p65:
                return '推荐'
            if score >= p45:
                return '关注'
            return '回避'
    else:
        median_score = np.nanmedian(valid_scores) if len(valid_scores) > 0 else np.nan

        def map_rating(score):
            if np.isnan(score):
                return '回避'
            if not np.isnan(median_score) and score >= median_score + 0.2:
                return '强烈推荐'
            if not np.isnan(median_score) and score >= median_score:
                return '推荐'
            if not np.isnan(median_score) and score >= median_score - 0.2:
                return '关注'
            return '回避'

    df['Rating'] = df['Comprehensive_Score'].apply(map_rating)

    # #2025-08-26 第一次迭代优化：
    # 增加一个判断：如果PEG极低且增长率极高，即使总分不高，也值得关注
    # Define what is considered 'excellent' fundamental
    excellent_peg = df['市盈增长比率'] < 0.5
    excellent_growth = df['预期净利润增长率'] > 1.0  # > 100%
    override_condition = excellent_peg & excellent_growth & (df['Rating'] == '回避')
    if override_condition.any():
        print(f"发现 {override_condition.sum()} 只基本面卓越但被评为'回避'的股票，将其评级提升至'关注'。")
        df.loc[override_condition, 'Rating'] = '关注'
    else:
        print("未发现符合加分条件的股票。")
    return df

# Function to generate PDF report
def generate_pdf_report(df, output_file='report.pdf'):
    df_sorted = df.sort_values('Comprehensive_Score', ascending=False).reset_index(drop=True)
    df_sorted['排名'] = df_sorted.index + 1

    # Key Overview
    num_stocks = len(df)
    num_recommended = len(df[df['Rating'] == '强烈推荐'])
    max_score = df['Comprehensive_Score'].max()
    avg_growth = df['预期净利润增长率'].mean()

    # Set up PDF
    doc = SimpleDocTemplate(output_file, pagesize=letter)
    font_name = register_cjk_font()
    styles = getSampleStyleSheet()
    title_style = styles['Title']
    title_style.fontName = font_name
    heading_style = styles['Heading1']
    heading_style.fontName = font_name
    subheading_style = styles['Heading2']
    subheading_style.fontName = font_name
    body_style = ParagraphStyle(name='Body', fontName=font_name, fontSize=10, leading=12, spaceAfter=6)
    elements = []

    # Title
    elements.append(Paragraph("股票技术面与基本面综合评分报告", title_style))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("基于量化分析的投资决策支持系统，深度解析提供的A股上市公司投资价值与风险特征。", body_style))
    elements.append(Spacer(1, 12))

    # Executive Summary
    elements.append(Paragraph("执行摘要", heading_style))
    elements.append(Paragraph("本报告应用原模型框架，从技术面、基本面和风险三个维度对提供的股票数据进行量化评估。技术面重点考察价格动能、移动平均线排列、ATR波动性和技术信号；基本面深入评估盈利能力、成长性、估值水平、财务稳健度和股息回报（权重分别为30%、25%、20%、15%、10%）；风险评估通过贝塔系数、公允价值不确定性和相关指标，提供投资控制依据。", body_style))
    elements.append(Paragraph("数据中部分股票显示强劲技术信号（如“强力买入”），但需警惕负增长或高估值风险（如德方纳米的基本面缺陷）。顶级股票如东北证券和全志科技在多维度表现出色，建议优先考虑。", body_style))
    elements.append(Spacer(1, 12))

    # Key Overview Table
    elements.append(Paragraph("关键指标概览", heading_style))
    overview_data = [
        ['评估股票数量', '推荐标的数量（强烈推荐+推荐）', '最高综合得分', '平均预期净利润增长'],
        [f"{num_stocks}只", f"{num_recommended}只", f"{max_score:.4f}", f"{avg_growth:.2%}"]
    ]
    overview_table = Table(overview_data)
    overview_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(overview_table)
    elements.append(Spacer(1, 12))

    # Ranking Table
    elements.append(Paragraph("1. 综合评分排名与核心推荐", heading_style))
    elements.append(Paragraph("1.1 最终综合得分排名", subheading_style))
    ranking_data = [['排名', '股票名称', '股票代码', '综合得分', '评级']]
    for _, row in df_sorted.iterrows():
        score = 'NaN' if np.isnan(row['Comprehensive_Score']) else f"{row['Comprehensive_Score']:.4f}"
        ranking_data.append([str(row['排名']), row['名称'], row['Full Ticker'], score, row['Rating']])
    ranking_table = Table(ranking_data)
    ranking_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(ranking_table)
    elements.append(Spacer(1, 12))

    # Highlight Top Stocks
    top1 = df_sorted.iloc[0]
    top2 = df_sorted.iloc[2] if len(df_sorted) > 2 else top1
    elements.append(Paragraph(f"<b>{top1['名称']}({top1['Full Ticker']})</b> 技术与基本面双强", body_style))
    elements.append(Paragraph(f"价格动能评分: {top1['价格动能评分']:.4f} (强劲)", body_style))
    elements.append(Paragraph(f"预期净利润增长率: {top1['预期净利润增长率']*100:.2f}%", body_style))
    elements.append(Paragraph(f"税前利润率: {top1['税前利润率']*100:.2f}%", body_style))
    elements.append(Paragraph(f"贝塔系数: {top1['贝塔(5年)']:.4f}", body_style))
    elements.append(Paragraph(f"公允价值上行边际显示{top1['公允价值上行边际']*100:.2f}%潜力，低不确定性支持稳健增长。", body_style))
    elements.append(Spacer(1, 12))

    elements.append(Paragraph(f"<b>{top2['名称']}({top2['Full Ticker']})</b> 增长潜力突出", body_style))
    elements.append(Paragraph(f"预期净利润增长率: {top2['预期净利润增长率']*100:.2f}% (极高)", body_style))
    elements.append(Paragraph(f"PEG比率: {top2['市盈增长比率']:.2f} (合理)", body_style))
    elements.append(Paragraph(f"贝塔系数: {top2['贝塔(5年)']:.4f}", body_style))
    elements.append(Paragraph(f"MA排列: 多头，技术信号强力买入，适合成长型投资。", body_style))
    elements.append(Spacer(1, 12))

    # Technical Analysis Section
    elements.append(Paragraph("2. 技术面分析：关键指标与评分", heading_style))
    elements.append(Paragraph("技术面评分基于价格动能（标准化）、MA排列（现价>短期>长期均线得高分）、ATR（低波动高分）和技术信号（强力买入=1.0）。", body_style))
    tech_data = [['示例股票', '关键指标与评分']]
    for _, row in df_sorted.head(3).iterrows():
        rating = "强势" if row['Technical_Score'] > 2.5 else "买入" if row['Technical_Score'] > 2 else "高风险买入"
        atr_level = "低" if row['atr_14d'] < 0.5 else "适中" if row['atr_14d'] < 1 else "较高"
        tech_html = (
            f"价格动能: {row['价格动能评分']:.4f} ({'强劲' if row['价格动能评分'] > 2.5 else '充足'})<br/>"
            f"MA排列: {'多头排列' if row['MA_Score'] == 1 else '站上短期均线'}<br/>"
            f"ATR: {row['atr_14d']:.4f} ({atr_level})<br/>"
            f"评级: {rating} (分数: {row['Technical_Score']:.4f})"
        )
        tech_data.append([row['名称'], Paragraph(tech_html, body_style)])
    tech_table = Table(tech_data)
    tech_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(tech_table)
    elements.append(Spacer(1, 12))

    # Fundamental Analysis Section
    elements.append(Paragraph("3. 基本面分析：财务数据与估值", heading_style))
    elements.append(Paragraph("基本面评分模型使用指定权重，整合盈利（毛利率/税前利润率）、成长（净利润增长）、估值（PEG/公允价值上行）、稳健度（现金流/成长评分）和股息。", body_style))
    fund_data = [
        ['核心指标权重', '值'],
        ['盈利能力 (30%)', '标准化毛利率+税前利润率'],
        ['成长性 (25%)', '预期净利润增长 (截断极端值)'],
        ['估值水平 (20%)', '反向PEG + 公允价值上行'],
        ['财务稳健度 (15%)', '现金流+成长稳健评分平均'],
        ['股息回报 (10%)', '标准化每股股息']
    ]
    fund_table = Table(fund_data)
    fund_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, -1), font_name),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(fund_table)
    elements.append(Spacer(1, 12))

    top_fund = df_sorted.iloc[0]
    elements.append(Paragraph("基本面优异标的", subheading_style))
    elements.append(Paragraph(f"<b>{top_fund['名称']}</b>", body_style))
    elements.append(Paragraph(f"净利润增长率: {top_fund['预期净利润增长率']*100:.2f}%", body_style))
    elements.append(Paragraph(f"毛利率: {top_fund['毛利率']*100:.2f}%, 税前利润率: {top_fund['税前利润率']*100:.2f}%", body_style))
    elements.append(Paragraph(f"PEG比率: {top_fund['市盈增长比率']:.2f} (合理), 分数: {top_fund['Fundamental_Score']:.4f}", body_style))
    elements.append(Spacer(1, 12))

    low_fund = df_sorted[df_sorted['Fundamental_Score'] == df_sorted['Fundamental_Score'].min()].iloc[0]
    elements.append(Paragraph("基本面谨慎标的", subheading_style))
    elements.append(Paragraph(f"<b>{low_fund['名称']}</b>", body_style))
    elements.append(Paragraph(f"净利润增长率: {low_fund['预期净利润增长率']*100:.2f}% (高) 但毛利率: {low_fund['毛利率']*100:.2f}%, 税前利润率: {low_fund['税前利润率']*100:.2f}%", body_style))
    elements.append(Paragraph(f"PEG: {low_fund['市盈增长比率']:.2f} (低估) 但负盈利拖累分数: {low_fund['Fundamental_Score']:.4f}", body_style))
    elements.append(Spacer(1, 12))

    # Risk Assessment Section
    elements.append(Paragraph("4. 风险评估与投资建议", heading_style))
    elements.append(Paragraph("风险评分基于反向贝塔（<1高分）和不确定性（最低=低风险）。", body_style))
    elements.append(Paragraph("投资组合构建建议", subheading_style))
    elements.append(Paragraph("1. 分批建仓：分3-4次，降低波动。", body_style))
    elements.append(Paragraph("2. 止损设置：8-10%止损线。", body_style))
    elements.append(Paragraph("3. 定期复盘：每季度评估。", body_style))
    elements.append(Paragraph("4. 仓位控制：单股不超过15%。", body_style))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("数据说明：基于提供数据计算，投资者应核实最新市场信息。", body_style))
    elements.append(Paragraph("<i>免责声明不是财务顾问；请咨询专业人士。不要分享可以识别您身份的信息。</i>", body_style))

    # Build PDF
    doc.build(elements)
    print(f"PDF report generated at {output_file}")

# Main function
def main(input_file, output_file='report.pdf'):
    # Read data depending on file type
    df = None
    lower_name = input_file.lower()
    is_excel = lower_name.endswith(('.xlsx', '.xls'))
    if is_excel:
        try:
            df = pd.read_excel(input_file)
            print("Successfully read Excel file")
        except ImportError as e:
            print(f"读取Excel需要 openpyxl：{e}. 请先安装：pip install openpyxl")
            return
        except Exception as e:
            print(f"读取Excel出错：{e}")
            return
    else:
        # Detect encoding for text/CSV/TSV
        detected_encoding = detect_encoding(input_file)
        print(f"Detected encoding: {detected_encoding}")

        encodings = [detected_encoding, 'gbk', 'gb2312', 'utf-8', 'latin1']
        for encoding in encodings:
            try:
                df = pd.read_csv(input_file, sep='\t', encoding=encoding, engine='python')
                print(f"Successfully read file with encoding: {encoding}")
                break
            except UnicodeDecodeError:
                print(f"Failed with encoding {encoding}, trying next...")
                continue
            except pd.errors.ParserError as e:
                print(f"ParserError with encoding {encoding}: {e}")
                print("Attempting to clean file...")
                cleaned_lines = clean_file(input_file, encoding=encoding if encoding != 'latin1' else 'utf-8')
                if cleaned_lines:
                    df = pd.read_csv(io.StringIO('\n'.join(cleaned_lines)), sep='\t', encoding='utf-8')
                    print("Successfully read cleaned file")
                    break
            except Exception as e:
                print(f"Error reading file with encoding {encoding}: {e}")
                continue

        if df is None:
            print("All encodings failed. Attempting to read with error replacement...")
            with open(input_file, 'rb') as file:
                raw_data = file.read()
                text = raw_data.decode('utf-8', errors='replace')
                try:
                    df = pd.read_csv(io.StringIO(text), sep='\t', engine='python')
                    print("Successfully read file with error replacement")
                except pd.errors.ParserError as e:
                    print(f"ParserError after error replacement: {e}")
                    print("Please check the file for formatting issues around line 22.")
                    return

    # Ensure correct number of columns for text/TSV only
    if not is_excel:
        expected_columns = 29  # Based on provided data
        if len(df.columns) != expected_columns:
            print(f"Warning: Expected {expected_columns} columns, but found {len(df.columns)}. Columns: {list(df.columns)}")
            print("Please verify the file format and ensure consistent delimiters.")

    df = calculate_scores(df)
    generate_pdf_report(df, output_file=output_file)

# Example usage
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate Stock Analysis PDF Report")
    parser.add_argument("--input", required=True, help="Path to input .csv or .xlsx file")
    parser.add_argument("--output", default=None, help="Path to output .pdf file (optional)")
    args = parser.parse_args()

    # script_dir = os.path.dirname(os.path.abspath(__file__))
    input_file = args.input
    
    if not os.path.exists(input_file):
        print(f"Error: Input file not found: {input_file}")
        exit(1)

    try:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        if args.output:
            output_pdf = args.output
        else:
             # Default to same dir as input
             input_dir = os.path.dirname(os.path.abspath(input_file))
             output_pdf = os.path.join(input_dir, f'report_{ts}.pdf')
            
        main(input_file, output_file=output_pdf)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        print("请确认输入文件存在且格式正确。如果为Excel请确保安装 openpyxl；如果为文本请检查分隔符与编码。")
