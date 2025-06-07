import cv2
import numpy as np
import os
import sys


def detect_specific_blue_S_signs(image_path):
    """
    检测图像中最右侧K线柱区域是否有特定的蓝色小方块标记（假定为S点）。

    参数:
    image_path (str): 图像文件路径

    返回:
    tuple: (是否有B点 (始终为False), 是否有S点)
    """
    try:
        # 读取图像
        img = cv2.imread(image_path)
        if img is None:
            print(f"无法读取图像: {image_path}")
            return (False, False)

        # 转换为HSV颜色空间
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        # 获取图像尺寸
        height, width = hsv.shape[:2]

        # 定义最右侧K线柱的区域（例如，图像最右侧的6%，可以根据实际情况调整）
        # 这个区域应该足够窄，以聚焦于最后一个或两个K线的位置
        right_region_start_x = int(width * 0.94)  # 分析最右侧6%的区域

        # 确保裁剪区域有效
        if right_region_start_x >= width:
            print(f"计算的右侧区域起始点 {right_region_start_x} 超出或等于图像宽度 {width}，无法裁剪。")
            # 可以选择分析整个图像，或者返回未检测到
            # 为安全起见，这里返回未检测到，因为逻辑是针对“最右侧”
            return (False, False)

        right_region_img = img[:, right_region_start_x:]
        right_region_hsv = hsv[:, right_region_start_x:]

        if right_region_hsv.shape[1] == 0 or right_region_hsv.shape[0] == 0:
            print("右侧区域尺寸为0，无法检测。")
            return (False, False)

        # --- 针对特定蓝色小方块的HSV范围 ---
        # 这个范围需要根据图片中实际蓝色小方块的颜色精确调整
        # H (色调): 大致在 95-125 (青色到蓝色)
        # S (饱和度): 较高，例如 100-255
        # V (亮度): 较高，例如 100-255
        # 这些值需要实验确定，以下为示例值：
        lower_blue = np.array([95, 100, 100])
        upper_blue = np.array([125, 255, 255])

        # 创建蓝色掩码
        color_mask = cv2.inRange(right_region_hsv, lower_blue, upper_blue)

        # 应用形态学操作
        # 使用较小的kernel，因为标记本身较小
        kernel = np.ones((2, 2), np.uint8)
        # MORPH_OPEN: 去除小的噪点 (先腐蚀后膨胀)
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, kernel, iterations=1)
        # MORPH_CLOSE: 填充标记内部的小洞，连接邻近区域 (先膨胀后腐蚀)
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, kernel, iterations=2)

        # 寻找轮廓
        contours, _ = cv2.findContours(color_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        has_s_sign = False  # B点在此逻辑中始终为False

        # 创建调试图像
        debug_img = right_region_img.copy()
        right_region_height = right_region_hsv.shape[0]

        for contour in contours:
            area = cv2.contourArea(contour)
            x, y, w, h = cv2.boundingRect(contour)

            # 过滤条件：
            # 1. 面积：不能太小（噪点）也不能太大（不太可能是小图标）
            #    蓝色小方块面积大约在15到150像素之间 (根据图片分辨率和图标大小调整)
            if not (15 < area < 200):  # 可调整
                continue

            # 2. 宽高比：小方块的宽高比接近1
            if h == 0: continue  # 防止除以零
            aspect_ratio = float(w) / h
            if not (0.7 < aspect_ratio < 1.3):  # 可调整
                continue

            # 3. 尺寸：宽度和高度不能过小
            if w < 4 or h < 4:  # 可调整
                continue

            # 如果通过所有过滤，我们认为可能找到了一个蓝色小方块标记
            # 假定这种蓝色小方块是S点
            has_s_sign = True
            cv2.rectangle(debug_img, (x, y), (x + w, y + h), (0, 255, 0), 2)  # 绿色框标记
            cv2.putText(debug_img, "S (blue_sq)", (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 0), 1)
            print(
                f"检测到特定蓝色S点标记: x={x}, y={y}, w={w}, h={h}, area={area:.0f}, aspect_ratio={aspect_ratio:.2f}")
            # 由于通常S点标记只有一个，找到后可以break，除非可能有多个
            # break

        # 保存调试图像
        base_name = os.path.basename(image_path)
        dir_name = os.path.dirname(image_path)
        if not dir_name:
            dir_name = "."

        debug_path = os.path.join(dir_name, f"debug_specific_S_{base_name}")
        cv2.imwrite(debug_path, debug_img)
        print(f"特定S点调试图像已保存为: {debug_path}")

        mask_path = os.path.join(dir_name, f"mask_specific_S_{base_name}")
        cv2.imwrite(mask_path, color_mask)
        print(f"特定S点掩码图像已保存为: {mask_path}")

        return (False, has_s_sign)  # B点始终为False

    except Exception as e:
        print(f"检测特定蓝色S点标记时出现异常: {e}")
        import traceback
        traceback.print_exc()
        return (False, False)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python your_script_name.py <图像路径>")
        print("例如: python your_script_name.py 600519_20250604.png")
        sys.exit(1)

    image_file_path = sys.argv[1]

    if not os.path.exists(image_file_path):
        print(f"错误: 图像文件未找到 - {image_file_path}")
        sys.exit(1)

    # 检测B/S点标记
    has_b, has_s = detect_specific_blue_S_signs(image_file_path)

    print(f"最终检测结果: B点={has_b}, S点={has_s}")
