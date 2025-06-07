import cv2
import numpy as np
import os
import sys
import glob
from datetime import datetime
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import traceback


def get_contour_center(contour):
    """计算轮廓的中心点"""
    M = cv2.moments(contour)
    if M["m00"] == 0:
        x, y, w, h = cv2.boundingRect(contour)
        return (x + w // 2, y + h // 2)
    cX = int(M["m10"] / M["m00"])
    cY = int(M["m01"] / M["m00"])
    return (cX, cY)


def detect_buy_sell_signals(image_path, debug_dir_path):
    """
    检测图像中最右侧K线柱区域是否有特定标记。
    S点: 蓝色方块 + 其下方紧邻的蓝色三角形 (尝试检测)
    B点: 红色方块 (参数已进一步优化以减少误判K线实体)
    注意: 不检测方块内部的白色字母。
    """
    try:
        img = cv2.imread(image_path)
        if img is None:
            print(f"警告: 无法读取图像: {image_path}，跳过此文件。")
            return (False, False)

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        height, width = hsv.shape[:2]

        right_region_start_x = int(width * 0.94)
        if right_region_start_x >= width or width == 0:
            return (False, False)

        right_region_img = img[:, right_region_start_x:]
        right_region_hsv = hsv[:, right_region_start_x:]

        if right_region_hsv.shape[1] == 0 or right_region_hsv.shape[0] == 0:
            return (False, False)

        has_s_sign = False
        has_b_sign = False
        debug_img = right_region_img.copy()

        # --- S点检测参数 (保持不变) ---
        kernel = np.ones((2, 2), np.uint8)  # 通用kernel
        min_area_square_S = 15
        max_area_square_S = 250
        min_aspect_ratio_square_S = 0.7
        max_aspect_ratio_square_S = 1.3
        min_dim_square_S = 4
        min_area_triangle_S = 10
        max_area_triangle_S = 200

        # --- B点检测参数 (进一步严格化) ---
        min_area_square_B = 8  # B点标记的最小面积 (允许更小标记)
        max_area_square_B = 90  # B点标记的最大面积 (显著缩小以避免K线实体)
        min_aspect_ratio_square_B = 0.8  # B点标记的最小宽高比 (更接近正方形)
        max_aspect_ratio_square_B = 1.2  # B点标记的最大宽高比 (更接近正方形)
        min_dim_square_B = 3  # B点标记的最小边长 (允许更小标记)

        # --- S点检测 (蓝色方块 + 蓝色三角形下方) ---
        lower_blue = np.array([95, 100, 100])
        upper_blue = np.array([125, 255, 255])
        blue_mask = cv2.inRange(right_region_hsv, lower_blue, upper_blue)
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        blue_mask = cv2.morphologyEx(blue_mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        all_blue_contours, _ = cv2.findContours(blue_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        candidate_blue_squares = []
        for contour in all_blue_contours:
            area = cv2.contourArea(contour)
            x, y, w, h = cv2.boundingRect(contour)
            if not (min_area_square_S < area < max_area_square_S): continue
            if h == 0: continue
            aspect_ratio = float(w) / h
            if not (min_aspect_ratio_square_S < aspect_ratio < max_aspect_ratio_square_S): continue
            if w < min_dim_square_S or h < min_dim_square_S: continue
            candidate_blue_squares.append({'contour': contour, 'x': x, 'y': y, 'w': w, 'h': h})

        for sq_info in candidate_blue_squares:
            sq_x, sq_y, sq_w, sq_h = sq_info['x'], sq_info['y'], sq_info['w'], sq_info['h']
            for tri_contour in all_blue_contours:
                if tri_contour is sq_info['contour']: continue
                tri_area = cv2.contourArea(tri_contour)
                tri_x, tri_y, tri_w, tri_h = cv2.boundingRect(tri_contour)
                if not (min_area_triangle_S < tri_area < max_area_triangle_S): continue
                is_below = tri_y > (sq_y + sq_h - 5)
                is_aligned_horizontally = max(sq_x, tri_x) < min(sq_x + sq_w, tri_x + tri_w)
                if is_below and is_aligned_horizontally:
                    peri = cv2.arcLength(tri_contour, True)
                    approx = cv2.approxPolyDP(tri_contour, 0.04 * peri, True)
                    if len(approx) == 3:
                        has_s_sign = True
                        cv2.rectangle(debug_img, (sq_x, sq_y), (sq_x + sq_w, sq_y + sq_h), (255, 0, 0), 2)
                        cv2.drawContours(debug_img, [approx], -1, (255, 100, 0), 2)
                        cv2.putText(debug_img, "S", (sq_x, sq_y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)
                        break
            if has_s_sign: break

        # --- B点检测 (红色方块) ---
        # 红色HSV范围，尝试更针对鲜艳的标记红
        lower_red1_B = np.array([0, 130, 130])
        upper_red1_B = np.array([10, 255, 255])
        lower_red2_B = np.array([160, 130, 130])
        upper_red2_B = np.array([180, 255, 255])  # 之前是165，改回160以覆盖更广的红色标记色调

        red_mask1 = cv2.inRange(right_region_hsv, lower_red1_B, upper_red1_B)
        red_mask2 = cv2.inRange(right_region_hsv, lower_red2_B, upper_red2_B)
        red_mask = cv2.bitwise_or(red_mask1, red_mask2)
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        all_red_contours, _ = cv2.findContours(red_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in all_red_contours:
            area = cv2.contourArea(contour)
            x, y, w, h = cv2.boundingRect(contour)

            if not (min_area_square_B < area < max_area_square_B): continue
            if h == 0: continue
            aspect_ratio = float(w) / h
            if not (min_aspect_ratio_square_B < aspect_ratio < max_aspect_ratio_square_B): continue
            if w < min_dim_square_B or h < min_dim_square_B: continue

            has_b_sign = True
            cv2.rectangle(debug_img, (x, y), (x + w, y + h), (0, 0, 255), 2)
            cv2.putText(debug_img, "B", (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
            break

        base_name = os.path.basename(image_path)
        if not os.path.exists(debug_dir_path) and debug_dir_path:
            try:
                os.makedirs(debug_dir_path)
            except OSError as e:
                print(f"错误: 创建调试子目录 {debug_dir_path} 失败: {e}")

        if os.path.isdir(debug_dir_path):
            debug_file_path = os.path.join(debug_dir_path, f"debug_BS_refined_{base_name}")  # changed suffix
            try:
                cv2.imwrite(debug_file_path, debug_img)
            except Exception as e_write:
                print(f"错误: 写入调试文件到 {debug_dir_path} 失败对于 {base_name}: {e_write}")

        return (has_b_sign, has_s_sign)

    except Exception as e:
        print(f"处理图像 {image_path} 时在 detect_buy_sell_signals 中出现严重异常: {e}")
        traceback.print_exc()
        return (False, False)


def process_image_worker(image_file_path, current_debug_dir):
    has_b, has_s = detect_buy_sell_signals(image_file_path, current_debug_dir)
    result = {
        '图片文件名': os.path.basename(image_file_path),
        '检测到B点': has_b,
        '检测到S点': has_s,
        '完整路径': image_file_path
    }
    return result


if __name__ == "__main__":
    today = datetime.now()
    date_as_string = today.strftime("%Y%m%d")

    input_images_directory = './SinaAppBS/'+date_as_string
    debug_files_directory = f"{date_as_string}_debug"
    excel_output_filename = f"{date_as_string}_detection_results_BS_refined.xlsx"  # changed suffix

    if not os.path.isdir(input_images_directory):
        print(f"错误: 输入文件夹 '{input_images_directory}' 未找到或不是一个有效的目录。")
        print(f"请确保在脚本所在目录下，存在一个以当前日期命名的文件夹 (例如 '{date_as_string}')，")
        print("并且该文件夹中包含需要处理的图片文件 (如 .png, .jpg)。")
        sys.exit(1)

    if not os.path.exists(debug_files_directory):
        try:
            os.makedirs(debug_files_directory)
            print(f"调试文件夹已创建: {debug_files_directory}")
        except OSError as e:
            print(f"错误: 创建调试文件夹 '{debug_files_directory}' 失败: {e}。调试文件可能无法保存。")

    supported_extensions = ('*.png', '*.jpg', '*.jpeg', '*.bmp', '*.gif')
    image_file_paths_list = []
    for extension in supported_extensions:
        image_file_paths_list.extend(glob.glob(os.path.join(input_images_directory, extension)))

    if not image_file_paths_list:
        print(f"在文件夹 '{input_images_directory}' 中未找到任何支持的图片文件。")
        sys.exit(0)

    print(f"在 '{input_images_directory}' 中找到 {len(image_file_paths_list)} 张图片待处理。")

    collected_processing_results = []
    cpu_cores = os.cpu_count()
    num_max_workers = min(8, (cpu_cores if cpu_cores is not None else 0) + 4) if cpu_cores is not None else 8

    print(f"将使用最多 {num_max_workers} 个线程进行处理...")
    with ThreadPoolExecutor(max_workers=num_max_workers) as executor:
        future_to_image_map = {executor.submit(process_image_worker, img_path, debug_files_directory): img_path for
                               img_path in image_file_paths_list}

        processed_count = 0
        for future in as_completed(future_to_image_map):
            image_path_for_future = future_to_image_map[future]
            try:
                single_image_result = future.result()
                if single_image_result:
                    collected_processing_results.append(single_image_result)
            except Exception as exc:
                print(f"处理图片 '{image_path_for_future}' 时线程内发生异常: {exc}")
                traceback.print_exc()

            processed_count += 1
            if processed_count % max(1, len(image_file_paths_list) // 20) == 0 or processed_count == len(
                    image_file_paths_list):
                print(
                    f"进度: {processed_count}/{len(image_file_paths_list)} ({os.path.basename(image_path_for_future)} 处理完毕)")

    print(f"\n所有 {len(image_file_paths_list)} 张图片处理完成。")

    if collected_processing_results:
        results_dataframe = pd.DataFrame(collected_processing_results)
        if not results_dataframe.empty:
            results_dataframe = results_dataframe[['图片文件名', '检测到B点', '检测到S点', '完整路径']]

        try:
            results_dataframe.to_excel(excel_output_filename, index=False, engine='openpyxl')
            print(f"识别结果已成功保存到Excel文件: {excel_output_filename}")
        except Exception as e:
            print(f"错误: 保存Excel文件 '{excel_output_filename}' 失败: {e}")
            print("请确保已安装 'pandas' 和 'openpyxl' 库 (例如: pip install pandas openpyxl)")
    else:
        print("没有收集到任何有效的识别结果，未生成Excel文件。")

