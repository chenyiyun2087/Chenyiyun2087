import cv2
import numpy as np
import matplotlib.pyplot as plt
import argparse
from pathlib import Path


def create_letter_templates():
    """
    创建B和S字母的模板
    返回:
        tuple: (b_template, s_template) 两个模板数组
    """
    # 创建B字母模板 (23x27像素，与实际S点标记大小一致)
    b_template = np.zeros((27, 23), dtype=np.uint8)
    # 绘制B字母的轮廓
    cv2.line(b_template, (5, 5), (5, 22), 255, 2)  # 左边竖线
    cv2.line(b_template, (5, 5), (18, 5), 255, 2)  # 上横线
    cv2.line(b_template, (5, 13), (18, 13), 255, 2)  # 中间横线
    cv2.line(b_template, (5, 22), (18, 22), 255, 2)  # 下横线
    cv2.line(b_template, (18, 5), (18, 13), 255, 2)  # 右上竖线
    cv2.line(b_template, (18, 13), (18, 22), 255, 2)  # 右下竖线
    cv2.ellipse(b_template, (11, 9), (7, 4), 0, 0, 180, 255, 2)  # 上半圆
    cv2.ellipse(b_template, (11, 18), (7, 4), 0, 0, 180, 255, 2)  # 下半圆

    # 创建S字母模板 (23x27像素，与实际S点标记大小一致)
    s_template = np.zeros((27, 23), dtype=np.uint8)
    # 绘制S字母的轮廓
    cv2.ellipse(s_template, (11, 9), (10, 8), 0, 0, 180, 255, 2)  # 上半圆
    cv2.ellipse(s_template, (11, 18), (10, 8), 0, 180, 360, 255, 2)  # 下半圆
    cv2.line(s_template, (1, 13), (21, 13), 255, 2)  # 中间横线

    return b_template, s_template


def preprocess_image(image):
    """
    图像预处理函数
    """
    # 转换为灰度图
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    
    # 使用CLAHE（对比度受限的自适应直方图均衡化）
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
    gray = clahe.apply(gray)
    
    # 高斯模糊去噪
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    
    # 自适应二值化
    binary = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 11, 2
    )
    
    # 形态学操作
    kernel = np.ones((2, 2), np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    
    return binary


def detect_bs_points(image_path):
    """
    检测K线图中最新K线柱上的B点或S点标记

    参数:
        image_path: K线图片的路径

    返回:
        dict: 包含检测结果的字典
    """
    # 读取图像
    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"无法读取图像: {image_path}")

    # 转换为RGB格式（OpenCV默认是BGR）
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # 获取图像尺寸
    height, width = image.shape[:2]

    # 定位最右侧K线柱区域（假设最右侧5%的区域包含最新K线柱）
    right_region_width = int(width * 0.05)
    right_region = image_rgb[:, width - right_region_width:width, :]

    # 图像预处理
    binary = preprocess_image(right_region)

    # 查找轮廓
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 初始化结果
    result = {
        "has_B_point": False,
        "has_S_point": False,
        "detected_letter": None,
        "confidence": 0.0
    }

    # 创建字母模板
    b_template, s_template = create_letter_templates()

    # 对每个轮廓进行分析
    for contour in contours:
        # 获取轮廓的边界框
        x, y, w, h = cv2.boundingRect(contour)
        
        # 根据实际S点标记大小（23*27）设置筛选条件
        if w < 20 or h < 24 or w > 26 or h > 30:  # 允许±3像素的误差
            continue
            
        # 计算轮廓的宽高比
        aspect_ratio = w / float(h)
        if aspect_ratio < 0.7 or aspect_ratio > 1.0:  # 23/27 ≈ 0.85
            continue

        # 提取潜在的字母区域
        letter_region = binary[y:y + h, x:x + w]
        
        # 调整大小以匹配模板
        letter_resized = cv2.resize(letter_region, (23, 27))
        
        # 计算与B和S模板的匹配度
        b_match = cv2.matchTemplate(letter_resized, b_template, cv2.TM_CCOEFF_NORMED)
        s_match = cv2.matchTemplate(letter_resized, s_template, cv2.TM_CCOEFF_NORMED)
        
        b_score = np.max(b_match)
        s_score = np.max(s_match)
        
        # 降低匹配阈值
        if b_score > 0.5 and b_score > s_score:
            result["has_B_point"] = True
            result["detected_letter"] = "B"
            result["confidence"] = float(b_score)
            break
        elif s_score > 0.5 and s_score > b_score:
            result["has_S_point"] = True
            result["detected_letter"] = "S"
            result["confidence"] = float(s_score)
            break

    return result


def visualize_result(image_path, result):
    """
    可视化检测结果

    参数:
        image_path: 原始图像路径
        result: 检测结果字典
    """
    # 读取图像
    image = cv2.imread(str(image_path))
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # 创建图形
    plt.figure(figsize=(12, 8))
    plt.imshow(image_rgb)

    # 添加检测结果文本
    if result["detected_letter"]:
        plt.title(f"检测到{result['detected_letter']}点标记，置信度: {result['confidence']:.2f}", fontsize=16)
    else:
        plt.title("未检测到B点或S点标记", fontsize=16)

    # 在右上角添加结果说明
    info_text = f"最新K线柱:\n"
    info_text += f"B点标记: {'是' if result['has_B_point'] else '否'}\n"
    info_text += f"S点标记: {'是' if result['has_S_point'] else '否'}"

    plt.text(0.98, 0.05, info_text, transform=plt.gca().transAxes,
             horizontalalignment='right', verticalalignment='bottom',
             bbox=dict(facecolor='white', alpha=0.8), fontsize=12)

    plt.axis('off')

    # 保存结果图像
    output_path = Path(image_path).parent / f"{Path(image_path).stem}_result.png"
    plt.savefig(output_path)
    plt.close()

    return output_path


def main():
    parser = argparse.ArgumentParser(description='检测K线图中的B点和S点标记')
    parser.add_argument('image_path', type=str, help='K线图片的路径')
    args = parser.parse_args()

    try:
        # 执行检测
        result = detect_bs_points(args.image_path)

        # 输出检测结果
        print("\n===== 检测结果 =====")
        print(f"最新K线柱:")
        print(f"B点标记: {'是' if result['has_B_point'] else '否'}")
        print(f"S点标记: {'是' if result['has_S_point'] else '否'}")
        if result["detected_letter"]:
            print(f"检测到的字母: {result['detected_letter']}")
            print(f"置信度: {result['confidence']:.2f}")
        else:
            print("未检测到B点或S点标记")

        # 可视化结果
        output_path = visualize_result(args.image_path, result)
        print(f"\n结果图像已保存至: {output_path}")

    except Exception as e:
        print(f"错误: {e}")


if __name__ == "__main__":
    main()
