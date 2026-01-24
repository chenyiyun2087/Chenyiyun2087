import cv2
import glob
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd
import pytesseract

from SinaLatestBSShow import print_latest_buy_signals

_tesseract_cmd = shutil.which("tesseract")
if _tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd

# 全局配置
THREAD_LOCK = threading.Lock()
DETECTION_RESULTS = []  # 存储所有检测结果
COORDINATE_THRESHOLD = 1810  # 横坐标阈值
MYSQL_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bs_detection_results (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    batch_name VARCHAR(128) NOT NULL,
    batch_date VARCHAR(8) NOT NULL,
    stock_code VARCHAR(16) NOT NULL,
    has_buy_signal TINYINT NOT NULL,
    has_sell_signal TINYINT NOT NULL,
    buy_signal_description TEXT,
    sell_signal_description TEXT,
    total_b_points INT,
    total_s_points INT,
    buy_points_count INT,
    sell_points_count INT,
    process_time VARCHAR(32),
    image_path TEXT,
    created_at DATETIME NOT NULL,
    UNIQUE KEY uniq_bs_detection (batch_name, batch_date, stock_code)
);
"""
MYSQL_INSERT_SQL = """
INSERT INTO bs_detection_results (
    batch_name,
    batch_date,
    stock_code,
    has_buy_signal,
    has_sell_signal,
    buy_signal_description,
    sell_signal_description,
    total_b_points,
    total_s_points,
    buy_points_count,
    sell_points_count,
    process_time,
    image_path,
    created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    has_buy_signal = VALUES(has_buy_signal),
    has_sell_signal = VALUES(has_sell_signal),
    buy_signal_description = VALUES(buy_signal_description),
    sell_signal_description = VALUES(sell_signal_description),
    total_b_points = VALUES(total_b_points),
    total_s_points = VALUES(total_s_points),
    buy_points_count = VALUES(buy_points_count),
    sell_points_count = VALUES(sell_points_count),
    process_time = VALUES(process_time),
    image_path = VALUES(image_path),
    created_at = VALUES(created_at);
"""


def get_base_dir():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "SinaAppBS"))


def init_mysql_db(mysql_config):
    import pymysql

    base_config = {k: v for k, v in mysql_config.items() if k != "database"}
    database = mysql_config.get("database")
    if not database:
        raise ValueError("mysql_config 必须包含 database")

    with pymysql.connect(**base_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` DEFAULT CHARSET utf8mb4")
        conn.commit()

    with pymysql.connect(**mysql_config) as conn:
        with conn.cursor() as cursor:
            cursor.execute(MYSQL_CREATE_TABLE_SQL)


def deduplicate_results(results):
    if not results:
        return []

    deduped = {}
    for result in results:
        if not result:
            continue
        stock_code = result.get("stock_code")
        if not stock_code:
            continue
        deduped[stock_code] = result

    if len(deduped) < len(results):
        print(f"检测结果去重: {len(results)} -> {len(deduped)}")

    return list(deduped.values())


def save_results_to_mysql(results, mysql_config, batch_date, batch_name):
    results = deduplicate_results(results)
    if not results:
        print("没有检测结果可写入MySQL数据库")
        return

    try:
        init_mysql_db(mysql_config)
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        rows = [
            (
                batch_name,
                batch_date,
                result.get('stock_code'),
                int(bool(result.get('has_buy_signal'))),
                int(bool(result.get('has_sell_signal'))),
                result.get('buy_signal_description'),
                result.get('sell_signal_description'),
                result.get('total_b_points'),
                result.get('total_s_points'),
                result.get('buy_points_count'),
                result.get('sell_points_count'),
                result.get('process_time'),
                result.get('image_path'),
                now_str,
            )
            for result in results
        ]

        import pymysql

        with pymysql.connect(**mysql_config) as conn:
            with conn.cursor() as cursor:
                cursor.executemany(MYSQL_INSERT_SQL, rows)
            conn.commit()
        print("检测结果已写入MySQL数据库")
    except Exception as exc:
        print(f"MySQL保存失败，已跳过: {exc}")


# --- OCR预处理函数 ---
def preprocess_for_ocr(roi):
    """
    对ROI进行专门为OCR设计的预处理。
    :param roi: 从原图截取的候选区域（彩色图像）
    :return: 预处理后的二值化图像
    """
    # 1. 放大图像以提高识别率，特别是对于小字符
    scale_factor = 6  # 增加缩放因子
    height, width = roi.shape[:2]
    new_width = int(width * scale_factor)
    new_height = int(height * scale_factor)
    dim = (new_width, new_height)
    resized_roi = cv2.resize(roi, dim, interpolation=cv2.INTER_CUBIC)

    # 2. 转换为灰度图
    gray_roi = cv2.cvtColor(resized_roi, cv2.COLOR_BGR2GRAY)

    # 3. 应用高斯模糊减少噪声
    blurred = cv2.GaussianBlur(gray_roi, (3, 3), 0)

    # 4. 多种二值化方法尝试
    # 方法1: Otsu's 阈值
    _, binary1 = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 方法2: 自适应阈值
    binary2 = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 11, 2)

    # 方法3: 固定阈值
    _, binary3 = cv2.threshold(blurred, 127, 255, cv2.THRESH_BINARY_INV)

    # 选择最佳的二值化结果（这里使用Otsu方法）
    binary_roi = binary1

    # 5. 形态学操作清理图像
    kernel = np.ones((2, 2), np.uint8)
    binary_roi = cv2.morphologyEx(binary_roi, cv2.MORPH_CLOSE, kernel, iterations=1)
    binary_roi = cv2.morphologyEx(binary_roi, cv2.MORPH_OPEN, kernel, iterations=1)

    return binary_roi


# --- 标记检测核心函数 ---
def detect_markers(
    image,
    hsv_color_ranges,
    expected_char,
    config,
    extra_ocr_configs=None,
    enable_lenient_match=False,
):
    """
    在图像中检测特定颜色和字符的标记。
    :param image: 原始输入图像 (BGR)
    :param hsv_color_ranges: 一个包含HSV颜色范围元组的列表 [(lower1, upper1), (lower2, upper2),...]
    :param expected_char: 期望识别到的字符 ('B' 或 'S')
    :param config: Pytesseract的配置字符串
    :return: 一个包含检测到的标记边界框 (x, y, w, h) 的列表
    """
    # 转换为HSV色彩空间
    hsv_image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # 根据提供的颜色范围创建组合掩码
    combined_mask = None
    for lower_bound, upper_bound in hsv_color_ranges:
        mask = cv2.inRange(hsv_image, lower_bound, upper_bound)
        if combined_mask is None:
            combined_mask = mask
        else:
            combined_mask = cv2.bitwise_or(combined_mask, mask)

    if combined_mask is None:
        return [], []

    # 形态学开运算去噪
    kernel = np.ones((3, 3), np.uint8)
    cleaned_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel, iterations=1)

    # 寻找轮廓
    contours, _ = cv2.findContours(cleaned_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 初始化检测结果列表
    detected_locations = []

    # 用于存储所有扫描区域的列表（用于后续绘制）
    all_scan_areas = []

    # 遍历所有找到的轮廓
    for cnt in contours:
        # 获取边界框
        x, y, w, h = cv2.boundingRect(cnt)

        # 将所有扫描区域添加到列表中（包括被筛选掉的）
        all_scan_areas.append((x, y, w, h))

        # 面积筛选（放宽范围）
        area = cv2.contourArea(cnt)
        if not (30 < area < 1000):  # 扩大面积范围
            continue

        # 长宽比筛选（放宽限制）
        aspect_ratio = float(w) / h
        if not (0.5 < aspect_ratio < 2.0):  # 放宽长宽比限制
            continue

        # 提取ROI并进行OCR
        roi = image[y:y + h, x:x + w]

        # 检查ROI是否为空
        if roi.size == 0:
            continue

        preprocessed_roi = preprocess_for_ocr(roi)

        # 使用Pytesseract进行字符识别
        try:
            configs = [config] if config else []
            if extra_ocr_configs:
                configs.extend(extra_ocr_configs)

            text_found = False
            for cfg in configs:
                text = pytesseract.image_to_string(preprocessed_roi, config=cfg).strip()
                if expected_char in text:
                    detected_locations.append((x, y, w, h))
                    text_found = True
                    break

            # 如果标准OCR失败，按需尝试更宽松的匹配
            if not text_found and enable_lenient_match:
                # 尝试识别任何文本并检查是否包含类似字符
                text = pytesseract.image_to_string(preprocessed_roi, config=r'--oem 3 --psm 10').strip()
                # 检查是否包含B/S或类似字符（如8、5等可能被误识别的字符）
                if expected_char == 'B' and any(char in text for char in ['B', '8', 'R', 'P']):
                    detected_locations.append((x, y, w, h))
                elif expected_char == 'S' and any(char in text for char in ['S', '5', 'G']):
                    detected_locations.append((x, y, w, h))

        except pytesseract.TesseractNotFoundError:
            print("错误：Tesseract未安装或未在系统PATH中。请确保Tesseract OCR引擎已正确安装。")
            return [], []  # 返回空列表
        except Exception as e:
            # 捕获其他可能的OCR错误
            print(f"OCR处理时发生错误: {e}")

    return detected_locations, all_scan_areas


# --- 分析买卖点 ---
def analyze_bs_points(b_points, s_points, stock_code):
    """
    根据横坐标判断是否出现买卖点信号
    :param b_points: B点列表
    :param s_points: S点列表
    :param stock_code: 股票代码
    :return: dict 包含分析结果
    """
    has_buy_signal = False
    has_sell_signal = False

    buy_points_over_threshold = []
    sell_points_over_threshold = []

    # 检查B点（买点）
    for x, y, w, h in b_points:
        if x > COORDINATE_THRESHOLD:
            has_buy_signal = True
            buy_points_over_threshold.append((x, y, w, h))

    # 检查S点（卖点）
    for x, y, w, h in s_points:
        if x > COORDINATE_THRESHOLD:
            has_sell_signal = True
            sell_points_over_threshold.append((x, y, w, h))

    return {
        'stock_code': stock_code,
        'total_b_points': len(b_points),
        'total_s_points': len(s_points),
        'has_buy_signal': has_buy_signal,
        'has_sell_signal': has_sell_signal,
        'buy_points_count': len(buy_points_over_threshold),
        'sell_points_count': len(sell_points_over_threshold),
        'buy_signal_description': '当天出现买点' if has_buy_signal else '当天无买点',
        'sell_signal_description': '当天出现卖点' if has_sell_signal else '当天无卖点'
    }


# --- 单个图片处理函数 ---
def process_single_image(image_path, debug_mode=False):
    """
    处理单个图片的函数，用于多线程调用
    :param image_path: 图片路径
    :param debug_mode: 是否启用调试模式
    :return: 检测结果字典
    """
    try:
        # 从文件名提取股票代码
        filename = os.path.basename(image_path)
        stock_code = filename.split('_')[0] if '_' in filename else filename.split('.')[0]

        # 加载图像
        original_image = cv2.imread(image_path)
        if original_image is None:
            print(f"错误：无法加载图像，请检查路径：{image_path}")
            return None

        # --- 颜色范围定义 ---
        # 红色HSV范围（扩大范围以提高检测率）
        lower_red1 = np.array([0, 30, 30])
        upper_red1 = np.array([15, 255, 255])
        lower_red2 = np.array([165, 30, 30])
        upper_red2 = np.array([180, 255, 255])

        # 蓝色HSV范围（扩大范围）
        lower_blue = np.array([90, 30, 30])
        upper_blue = np.array([140, 255, 255])

        # Pytesseract配置
        ocr_config = r'--oem 3 --psm 10 -c tessedit_char_whitelist=BS'

        # 执行检测
        b_points, b_scan_areas = detect_markers(
            original_image,
            [(lower_red1, upper_red1), (lower_red2, upper_red2)],
            'B',
            ocr_config,
        )

        s_points, s_scan_areas = detect_markers(
            original_image,
            [(lower_blue, upper_blue)],
            'S',
            ocr_config,
        )

        # 分析结果
        result = analyze_bs_points(b_points, s_points, stock_code)
        result['image_path'] = image_path
        result['process_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if debug_mode:
            print(f"处理完成: {stock_code} - B点:{len(b_points)}, S点:{len(s_points)}")

        return result

    except Exception as e:
        print(f"处理图片 {image_path} 时发生错误: {e}")
        return None


# --- 批量处理主函数 ---
def batch_process_images(
    config_name=None,
    date_str=None,
    date_folder=None,
    max_workers=4,
    debug_mode=False,
    base_dir=None,
    db_path=None,
    mysql_config=None,
    save_excel=True,
):
    """
    批量多线程处理图片
    :param date_folder: 日期文件夹，如果为None则使用当前日期
    :param max_workers: 最大线程数
    :param debug_mode: 是否启用调试模式
    """
    global DETECTION_RESULTS

    # 确定处理的日期文件夹
    if base_dir is None:
        base_dir = get_base_dir()

    if date_folder is None:
        current_date = date_str or datetime.now().strftime('%Y%m%d')
        if not config_name:
            raise ValueError("必须提供 config_name 才能定位日期文件夹")
        folder_path = os.path.join(base_dir, config_name, current_date)
    else:
        folder_path = os.path.join(base_dir, date_folder)
        current_date = os.path.basename(date_folder)

    if config_name:
        batch_name = config_name
    else:
        relative_path = os.path.relpath(folder_path, base_dir)
        parts = os.path.normpath(relative_path).split(os.sep)
        batch_name = parts[-2] if len(parts) >= 2 else "default"

    # 检查文件夹是否存在
    if not os.path.exists(folder_path):
        print(f"错误：文件夹不存在 - {folder_path}")
        return []

    # 获取所有图片文件
    image_extensions = ['*.png', '*.jpg', '*.jpeg', '*.bmp', '*.tiff']
    image_files = []
    for extension in image_extensions:
        image_files.extend(glob.glob(os.path.join(folder_path, extension)))

    if not image_files:
        print(f"在文件夹 {folder_path} 中未找到图片文件")
        return []

    print(f"找到 {len(image_files)} 个图片文件，开始处理...")
    print(f"使用 {max_workers} 个线程进行并行处理")

    # 重置结果列表
    DETECTION_RESULTS = []

    # 使用线程池进行并行处理
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        future_to_image = {
            executor.submit(process_single_image, image_path, debug_mode): image_path
            for image_path in image_files
        }

        # 收集结果
        completed = 0
        for future in future_to_image:
            try:
                result = future.result()
                if result is not None:
                    with THREAD_LOCK:
                        DETECTION_RESULTS.append(result)
                completed += 1

                if debug_mode:
                    print(f"进度: {completed}/{len(image_files)} ({completed / len(image_files) * 100:.1f}%)")

            except Exception as e:
                image_path = future_to_image[future]
                print(f"处理 {image_path} 时发生异常: {e}")

    end_time = time.time()
    processing_time = end_time - start_time

    print(f"批量处理完成！")
    print(f"总共处理: {len(image_files)} 个文件")
    print(f"成功处理: {len(DETECTION_RESULTS)} 个文件")
    print(f"处理时间: {processing_time:.2f} 秒")
    print(f"平均每个文件: {processing_time / len(image_files):.2f} 秒")

    if mysql_config is None:
        mysql_config = {
            "host": "localhost",
            "user": "root",
            "password": "19871019",
            "database": "chenyiyun",
            "charset": "utf8mb4",
        }

    # 保存结果到Excel
    deduped_results = deduplicate_results(DETECTION_RESULTS)
    if deduped_results:
        if save_excel:
            save_results_to_excel(current_date, deduped_results, base_dir, batch_name)
    else:
        print("没有有效的检测结果，不生成Excel文件")

    try:
        save_results_to_mysql(deduped_results, mysql_config, current_date, batch_name)
        print_latest_buy_signals(mysql_config)
    except Exception as exc:
        print(f"MySQL保存失败，已跳过: {exc}")

    return deduped_results


# --- 保存结果到Excel ---
def save_results_to_excel(date_str, results, base_dir, batch_name):
    """
    将检测结果保存到Excel文件
    :param date_str: 日期字符串
    """
    try:
        # 创建DataFrame
        df = pd.DataFrame(results)

        # 重新排列列的顺序
        column_order = [
            'stock_code', 'has_buy_signal', 'has_sell_signal',
            'buy_signal_description', 'sell_signal_description',
            'total_b_points', 'total_s_points',
            'buy_points_count', 'sell_points_count',
            'process_time', 'image_path'
        ]

        df = df[column_order]

        # 重命名列名为中文
        df.columns = [
            '股票代码', '有买点信号', '有卖点信号',
            '买点信号描述', '卖点信号描述',
            '总B点数量', '总S点数量',
            '买点信号数量', '卖点信号数量',
            '处理时间', '图片路径'
        ]

        result_dir = os.path.join(base_dir, "result")
        os.makedirs(result_dir, exist_ok=True)

        # 保存到Excel文件
        excel_filename = f'{batch_name}_{date_str}.xlsx'
        excel_path = os.path.join(result_dir, excel_filename)
        df.to_excel(excel_path, index=False, engine='openpyxl')

        print(f"结果已保存到Excel文件: {excel_path}")

        # 输出统计信息
        total_stocks = len(df)
        buy_signals = df['有买点信号'].sum()
        sell_signals = df['有卖点信号'].sum()

        print(f"\n=== 检测结果统计 ===")
        print(f"总股票数: {total_stocks}")
        print(f"有买点信号的股票数: {buy_signals} ({buy_signals / total_stocks * 100:.1f}%)")
        print(f"有卖点信号的股票数: {sell_signals} ({sell_signals / total_stocks * 100:.1f}%)")

        # 显示有信号的股票列表
        if buy_signals > 0:
            print(f"\n=== 有买点信号的股票 ===")
            buy_stocks = df[df['有买点信号'] == True]['股票代码'].tolist()
            print(", ".join(buy_stocks))

        if sell_signals > 0:
            print(f"\n=== 有卖点信号的股票 ===")
            sell_stocks = df[df['有卖点信号'] == True]['股票代码'].tolist()
            print(", ".join(sell_stocks))

    except Exception as e:
        print(f"保存Excel文件时发生错误: {e}")


# --- 单个图片处理函数（用于测试） ---
def process_single_chart(image_path):
    """
    处理单个图片并显示结果（原有功能，用于测试）
    :param image_path: 待处理的K线图图片路径
    """
    # 加载图像
    original_image = cv2.imread(image_path)
    if original_image is None:
        print(f"错误：无法加载图像，请检查路径：{image_path}")
        return

    # --- 颜色范围定义 ---
    lower_red1 = np.array([0, 30, 30])
    upper_red1 = np.array([15, 255, 255])
    lower_red2 = np.array([165, 30, 30])
    upper_red2 = np.array([180, 255, 255])
    lower_blue = np.array([90, 30, 30])
    upper_blue = np.array([140, 255, 255])

    ocr_config = r'--oem 3 --psm 10 -c tessedit_char_whitelist=BS'
    debug_mode = True

    # 执行检测
    print("正在检测 'B' 点 (红色)...")
    b_points, b_scan_areas = detect_markers(
        original_image,
        [(lower_red1, upper_red1), (lower_red2, upper_red2)],
        'B',
        ocr_config,
        extra_ocr_configs=[
            r'--oem 3 --psm 8 -c tessedit_char_whitelist=BS',
            r'--oem 3 --psm 7 -c tessedit_char_whitelist=BS',
            r'--oem 3 --psm 13 -c tessedit_char_whitelist=BS',
        ],
        enable_lenient_match=True,
    )

    print("正在检测 'S' 点 (蓝色)...")
    s_points, s_scan_areas = detect_markers(
        original_image,
        [(lower_blue, upper_blue)],
        'S',
        ocr_config,
        extra_ocr_configs=[
            r'--oem 3 --psm 8 -c tessedit_char_whitelist=BS',
            r'--oem 3 --psm 7 -c tessedit_char_whitelist=BS',
            r'--oem 3 --psm 13 -c tessedit_char_whitelist=BS',
        ],
        enable_lenient_match=True,
    )

    # 提取股票代码
    filename = os.path.basename(image_path)
    stock_code = filename.split('_')[0] if '_' in filename else filename.split('.')[0]

    # 分析结果
    analysis_result = analyze_bs_points(b_points, s_points, stock_code)

    # 输出详细结果
    print(f"\n=== {stock_code} 检测结果 ===")
    print(f"总B点数: {analysis_result['total_b_points']}")
    print(f"总S点数: {analysis_result['total_s_points']}")
    print(f"买点信号: {analysis_result['buy_signal_description']}")
    print(f"卖点信号: {analysis_result['sell_signal_description']}")
    print(f"符合条件的买点数量: {analysis_result['buy_points_count']}")
    print(f"符合条件的卖点数量: {analysis_result['sell_points_count']}")

    if debug_mode:
        print(f"\n详细坐标信息:")
        print(f"所有B点坐标:")
        for i, (x, y, w, h) in enumerate(b_points):
            signal = "✓ 买点信号" if x > COORDINATE_THRESHOLD else ""
            print(f"  B点 {i + 1}: 位置({x}, {y}), 大小({w}x{h}) {signal}")

        print(f"所有S点坐标:")
        for i, (x, y, w, h) in enumerate(s_points):
            signal = "✓ 卖点信号" if x > COORDINATE_THRESHOLD else ""
            print(f"  S点 {i + 1}: 位置({x}, {y}), 大小({w}x{h}) {signal}")

    # 可视化结果
    output_image = original_image.copy()

    # 绘制阈值线
    cv2.line(output_image, (COORDINATE_THRESHOLD, 0), (COORDINATE_THRESHOLD, original_image.shape[0]), (255, 255, 0), 2)
    cv2.putText(output_image, f'Threshold: {COORDINATE_THRESHOLD}', (COORDINATE_THRESHOLD + 10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

    # 绘制扫描区域
    for (x, y, w, h) in b_scan_areas + s_scan_areas:
        cv2.rectangle(output_image, (x, y), (x + w, y + h), (0, 255, 128), 1)

    # 绘制B点
    for (x, y, w, h) in b_points:
        color = (0, 255, 0) if x > COORDINATE_THRESHOLD else (0, 128, 0)
        thickness = 3 if x > COORDINATE_THRESHOLD else 2
        cv2.rectangle(output_image, (x, y), (x + w, y + h), color, thickness)
        cv2.putText(output_image, 'B', (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    # 绘制S点
    for (x, y, w, h) in s_points:
        color = (0, 0, 255) if x > COORDINATE_THRESHOLD else (0, 0, 128)
        thickness = 3 if x > COORDINATE_THRESHOLD else 2
        cv2.rectangle(output_image, (x, y), (x + w, y + h), color, thickness)
        cv2.putText(output_image, 'S', (x, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

    # 显示结果
    cv2.imshow('Detection Result', output_image)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


# --- 程序入口 ---
if __name__ == '__main__':
    print("=== 股票买卖点检测系统 ===")
    print("1. 单个图片处理（测试模式）")
    print("2. 批量处理当前日期文件夹")
    print("3. 批量处理指定日期文件夹")

    try:
        choice = input("\n请选择模式 (1/2/3): ").strip()

        if choice == '1':
            # 单个图片处理模式
            image_path = input("请输入图片路径: ").strip()
            if not image_path:
                image_path = './SinaAppBS/20250702/000009_20250702.png'  # 默认路径
            process_single_chart(image_path)

        elif choice == '2':
            # 批量处理当前日期
            max_workers = int(input("请输入线程数 (默认4): ").strip() or "4")
            debug_mode = input("是否启用调试模式? (y/n, 默认n): ").strip().lower() == 'y'
            batch_process_images(max_workers=max_workers, debug_mode=debug_mode)

        elif choice == '3':
            # 批量处理指定日期
            date_folder = input("请输入日期文件夹名称 (格式: YYYYMMDD): ").strip()
            if not date_folder:
                print("错误：日期不能为空")
                exit(1)
            max_workers = int(input("请输入线程数 (默认4): ").strip() or "4")
            debug_mode = input("是否启用调试模式? (y/n, 默认n): ").strip().lower() == 'y'
            batch_process_images(date_folder=date_folder, max_workers=max_workers, debug_mode=debug_mode)

        else:
            print("无效的选择")

    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
    except Exception as e:
        print(f"\n程序执行时发生错误: {e}")
