import cv2
import numpy as np
import matplotlib.pyplot as plt
import argparse
import os
from pathlib import Path
import tensorflow as tf
from tensorflow.keras import layers, models, optimizers
from tensorflow.keras.preprocessing.image import ImageDataGenerator
from sklearn.model_selection import train_test_split
import random


class CNNBSPointDetector:
    """使用CNN识别K线图中的B点和S点标记"""

    def __init__(self, model_path=None):
        """
        初始化CNN检测器

        参数:
            model_path: 预训练模型路径，如果为None则创建新模型
        """
        self.model = None
        self.model_path = model_path

        # 如果提供了模型路径且模型存在，则加载模型
        if model_path and os.path.exists(model_path):
            self.load_model(model_path)
        else:
            self.build_model()

    def build_model(self):
        """构建CNN模型"""
        model = models.Sequential([
            # 第一个卷积块
            layers.Conv2D(32, (3, 3), activation='relu', padding='same', input_shape=(32, 32, 1)),
            layers.BatchNormalization(),
            layers.MaxPooling2D((2, 2)),

            # 第二个卷积块
            layers.Conv2D(64, (3, 3), activation='relu', padding='same'),
            layers.BatchNormalization(),
            layers.MaxPooling2D((2, 2)),

            # 第三个卷积块
            layers.Conv2D(128, (3, 3), activation='relu', padding='same'),
            layers.BatchNormalization(),
            layers.MaxPooling2D((2, 2)),

            # 全连接层
            layers.Flatten(),
            layers.Dropout(0.5),
            layers.Dense(128, activation='relu'),
            layers.BatchNormalization(),
            layers.Dropout(0.5),
            layers.Dense(3, activation='softmax')  # 3类：背景、B点、S点
        ])

        # 编译模型
        model.compile(
            optimizer=optimizers.Adam(learning_rate=0.001),
            loss='categorical_crossentropy',
            metrics=['accuracy']
        )

        self.model = model
        return model

    def load_model(self, model_path):
        """加载预训练模型"""
        self.model = models.load_model(model_path)
        print(f"已加载模型: {model_path}")
        return self.model

    def save_model(self, model_path):
        """保存模型"""
        if self.model:
            self.model.save(model_path)
            print(f"模型已保存至: {model_path}")

    def generate_synthetic_data(self, num_samples=1000, output_dir=None):
        """
        生成合成训练数据

        参数:
            num_samples: 生成样本数量
            output_dir: 输出目录，如果提供则保存样本图像

        返回:
            X: 图像数据，形状为(num_samples, 32, 32, 1)
            y: 标签，形状为(num_samples, 3)
        """
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(os.path.join(output_dir, 'B'), exist_ok=True)
            os.makedirs(os.path.join(output_dir, 'S'), exist_ok=True)
            os.makedirs(os.path.join(output_dir, 'background'), exist_ok=True)

        X = []
        y_labels = []  # 修改变量名，避免与循环内的y变量冲突

        # 生成B点、S点和背景样本
        for i in range(num_samples):
            # 随机决定生成哪种类型的样本
            sample_type = random.choice(['B', 'S', 'background'])

            # 创建空白图像
            img = np.zeros((32, 32), dtype=np.uint8)

            if sample_type == 'B':
                # 生成B点样本
                # 随机调整字体大小、位置、粗细和背景
                font_scale = random.uniform(0.6, 1.0)
                thickness = random.randint(1, 2)
                x = random.randint(8, 16)
                y = random.randint(18, 24)

                # 添加背景噪声
                if random.random() > 0.5:
                    img = np.random.randint(0, 30, (32, 32), dtype=np.uint8)

                # 绘制B字母
                cv2.putText(img, 'B', (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                            font_scale, 255, thickness)

                # 添加随机旋转和缩放
                if random.random() > 0.7:
                    M = cv2.getRotationMatrix2D((16, 16), random.uniform(-15, 15), 1)
                    img = cv2.warpAffine(img, M, (32, 32))

                label = [0, 1, 0]  # B点标签

            elif sample_type == 'S':
                # 生成S点样本
                font_scale = random.uniform(0.6, 1.0)
                thickness = random.randint(1, 2)
                x = random.randint(8, 16)
                y = random.randint(18, 24)

                # 添加背景噪声
                if random.random() > 0.5:
                    img = np.random.randint(0, 30, (32, 32), dtype=np.uint8)

                # 绘制S字母
                cv2.putText(img, 'S', (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                            font_scale, 255, thickness)

                # 添加随机旋转和缩放
                if random.random() > 0.7:
                    M = cv2.getRotationMatrix2D((16, 16), random.uniform(-15, 15), 1)
                    img = cv2.warpAffine(img, M, (32, 32))

                label = [0, 0, 1]  # S点标签

            else:
                # 生成背景样本（随机噪声、线条等）
                if random.random() > 0.5:
                    # 随机噪声
                    img = np.random.randint(0, 100, (32, 32), dtype=np.uint8)
                else:
                    # 随机线条
                    for _ in range(random.randint(1, 5)):
                        pt1 = (random.randint(0, 31), random.randint(0, 31))
                        pt2 = (random.randint(0, 31), random.randint(0, 31))
                        cv2.line(img, pt1, pt2, 255, 1)

                label = [1, 0, 0]  # 背景标签

            # 保存样本图像
            if output_dir:
                img_path = os.path.join(output_dir, sample_type, f"{sample_type}_{i}.png")
                cv2.imwrite(img_path, img)

            # 归一化并添加到数据集
            img = img.astype(np.float32) / 255.0
            X.append(img.reshape(32, 32, 1))
            y_labels.append(label)  # 使用修改后的变量名

        return np.array(X), np.array(y_labels)  # 返回修改后的变量名

    def train(self, epochs=20, batch_size=32, validation_split=0.2, data_augmentation=True,
              generate_samples=1000, save_samples_dir=None):
        """
        训练模型

        参数:
            epochs: 训练轮数
            batch_size: 批次大小
            validation_split: 验证集比例
            data_augmentation: 是否使用数据增强
            generate_samples: 生成的合成样本数量
            save_samples_dir: 保存合成样本的目录，如果为None则不保存

        返回:
            训练历史
        """
        # 生成合成训练数据
        X, y = self.generate_synthetic_data(generate_samples, save_samples_dir)

        # 划分训练集和验证集
        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=validation_split, random_state=42
        )

        # 数据增强
        if data_augmentation:
            datagen = ImageDataGenerator(
                rotation_range=15,
                width_shift_range=0.1,
                height_shift_range=0.1,
                zoom_range=0.1,
                horizontal_flip=False,
                vertical_flip=False,
                fill_mode='nearest'
            )
            datagen.fit(X_train)

            # 训练模型
            history = self.model.fit(
                datagen.flow(X_train, y_train, batch_size=batch_size),
                epochs=epochs,
                validation_data=(X_val, y_val),
                steps_per_epoch=len(X_train) // batch_size
            )
        else:
            # 不使用数据增强
            history = self.model.fit(
                X_train, y_train,
                epochs=epochs,
                batch_size=batch_size,
                validation_data=(X_val, y_val)
            )

        return history

    def detect_bs_points(self, image_path, debug=False):
        """
        检测K线图中最新K线柱上的B点或S点标记

        参数:
            image_path: K线图片的路径
            debug: 是否输出调试图像

        返回:
            dict: 包含检测结果的字典
        """
        if self.model is None:
            raise ValueError("模型未加载，请先加载或训练模型")

        # 读取图像
        image = cv2.imread(str(image_path))
        if image is None:
            raise FileNotFoundError(f"无法读取图像: {image_path}")

        # 转换为RGB格式（OpenCV默认是BGR）
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

        # 获取图像尺寸
        height, width = image.shape[:2]

        # 定位最右侧K线柱区域（扩大到右侧20%的区域，确保包含完整K线柱及标记）
        right_region_width = int(width * 0.2)
        right_region = image_rgb[:, width - right_region_width:width, :]

        # 创建调试目录
        if debug:
            debug_dir = os.path.join(os.path.dirname(image_path), "cnn_debug")
            os.makedirs(debug_dir, exist_ok=True)

            # 保存右侧区域图像
            plt.figure(figsize=(8, 6))
            plt.imshow(right_region)
            plt.title("右侧区域")
            plt.axis('off')
            plt.savefig(os.path.join(debug_dir, "01_right_region.png"))
            plt.close()

        # 转换为灰度图像
        gray = cv2.cvtColor(right_region, cv2.COLOR_RGB2GRAY)

        # 使用自适应阈值处理
        binary_adaptive = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 11, 2
        )

        if debug:
            # 保存灰度和二值化图像
            plt.figure(figsize=(8, 4))
            plt.subplot(121)
            plt.imshow(gray, cmap='gray')
            plt.title("灰度图像")
            plt.axis('off')

            plt.subplot(122)
            plt.imshow(binary_adaptive, cmap='gray')
            plt.title("二值化图像")
            plt.axis('off')

            plt.tight_layout()
            plt.savefig(os.path.join(debug_dir, "02_binary.png"))
            plt.close()

        # 查找轮廓
        contours, _ = cv2.findContours(binary_adaptive, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        # 初始化结果
        result = {
            "has_B_point": False,
            "has_S_point": False,
            "detected_letter": None,
            "confidence": 0.0,
            "position": None
        }

        # 创建调试图像
        if debug:
            debug_image = right_region.copy()
            cv2.drawContours(debug_image, contours, -1, (0, 255, 0), 1)

            plt.figure(figsize=(8, 6))
            plt.imshow(debug_image)
            plt.title(f"检测到 {len(contours)} 个轮廓")
            plt.axis('off')
            plt.savefig(os.path.join(debug_dir, "03_contours.png"))
            plt.close()

        # 对每个轮廓进行分析
        valid_contours = []
        for contour in contours:
            # 过滤掉太小的轮廓
            if cv2.contourArea(contour) < 10:
                continue

            # 获取轮廓的边界框
            x, y, w, h = cv2.boundingRect(contour)

            # 过滤掉宽高比例不合理的轮廓
            aspect_ratio = float(w) / h
            if aspect_ratio > 2.0 or aspect_ratio < 0.3:
                continue

            # 过滤掉太大的轮廓
            if w > right_region_width / 5 or h > height / 5:
                continue

            valid_contours.append((contour, x, y, w, h))

        if debug and valid_contours:
            debug_image = right_region.copy()
            for contour, x, y, w, h in valid_contours:
                cv2.rectangle(debug_image, (x, y), (x + w, y + h), (255, 0, 0), 2)

            plt.figure(figsize=(8, 6))
            plt.imshow(debug_image)
            plt.title(f"筛选后的 {len(valid_contours)} 个有效轮廓")
            plt.axis('off')
            plt.savefig(os.path.join(debug_dir, "04_valid_contours.png"))
            plt.close()

        # 使用CNN模型预测每个有效轮廓
        best_prediction = None
        best_confidence = 0.0
        best_position = None

        for i, (contour, x, y, w, h) in enumerate(valid_contours):
            # 提取ROI
            roi = binary_adaptive[y:y + h, x:x + w]

            # 调整大小为模型输入尺寸
            roi_resized = cv2.resize(roi, (32, 32))

            # 归一化
            roi_normalized = roi_resized.astype(np.float32) / 255.0

            # 添加通道维度
            roi_input = roi_normalized.reshape(1, 32, 32, 1)

            # 模型预测
            prediction = self.model.predict(roi_input, verbose=0)[0]

            # 获取预测类别和置信度
            pred_class = np.argmax(prediction)
            confidence = prediction[pred_class]

            if debug:
                # 保存ROI和预测结果
                plt.figure(figsize=(6, 4))
                plt.imshow(roi_resized, cmap='gray')
                plt.title(
                    f"ROI {i + 1}: {'背景' if pred_class == 0 else 'B点' if pred_class == 1 else 'S点'} ({confidence:.2f})")
                plt.axis('off')
                plt.savefig(os.path.join(debug_dir, f"05_roi_{i + 1}.png"))
                plt.close()

            # 如果预测为B点或S点，且置信度高于阈值和之前的最佳预测
            if pred_class > 0 and confidence > 0.5 and confidence > best_confidence:
                best_prediction = pred_class
                best_confidence = confidence
                best_position = (x, y, w, h)

        # 更新结果
        if best_prediction == 1:  # B点
            result["has_B_point"] = True
            result["detected_letter"] = "B"
            result["confidence"] = float(best_confidence)
            result["position"] = best_position
        elif best_prediction == 2:  # S点
            result["has_S_point"] = True
            result["detected_letter"] = "S"
            result["confidence"] = float(best_confidence)
            result["position"] = best_position

        return result


def visualize_result(image_path, result, debug=False):
    """
    可视化检测结果

    参数:
        image_path: 原始图像路径
        result: 检测结果字典
        debug: 是否显示调试信息
    """
    # 读取图像
    image = cv2.imread(str(image_path))
    image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # 获取图像尺寸
    height, width = image.shape[:2]

    # 创建图形
    plt.figure(figsize=(12, 8))
    plt.imshow(image_rgb)

    # 如果检测到字母，在图像上标记位置
    if result["detected_letter"] and result["position"]:
        # 计算实际位置（相对于右侧区域的偏移）
        right_region_width = int(width * 0.2)
        x, y, w, h = result["position"]
        abs_x = width - right_region_width + x

        # 在图像上绘制矩形框
        rect = plt.Rectangle((abs_x, y), w, h, linewidth=2, edgecolor='r', facecolor='none')
        plt.gca().add_patch(rect)

        # 添加标签
        plt.text(abs_x, y - 5, f"{result['detected_letter']} ({result['confidence']:.2f})",
                 color='red', fontsize=12, backgroundcolor='white')

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
    output_path = Path(image_path).parent / f"{Path(image_path).stem}_cnn_result.png"
    plt.savefig(output_path)
    plt.close()

    return output_path


def main():
    parser = argparse.ArgumentParser(description='使用CNN检测K线图中的B点和S点标记')
    parser.add_argument('image_path', type=str, help='K线图片的路径')
    parser.add_argument('--train', action='store_true', help='训练新模型')
    parser.add_argument('--model', type=str, default='bs_detector_model.h5', help='模型文件路径')
    parser.add_argument('--debug', action='store_true', help='输出调试图像')
    parser.add_argument('--samples', type=int, default=2000, help='训练样本数量')
    parser.add_argument('--epochs', type=int, default=20, help='训练轮数')
    args = parser.parse_args()

    try:
        # 创建检测器
        detector = CNNBSPointDetector(args.model if not args.train and os.path.exists(args.model) else None)

        # 如果需要训练
        if args.train:
            print(f"开始训练模型，样本数量: {args.samples}，训练轮数: {args.epochs}")

            # 创建样本保存目录
            samples_dir = os.path.join(os.path.dirname(args.model), "training_samples")

            # 训练模型
            history = detector.train(
                epochs=args.epochs,
                generate_samples=args.samples,
                save_samples_dir=samples_dir if args.debug else None
            )

            # 保存模型
            detector.save_model(args.model)

            # 绘制训练历史
            plt.figure(figsize=(12, 4))

            plt.subplot(121)
            plt.plot(history.history['accuracy'])
            plt.plot(history.history['val_accuracy'])
            plt.title('模型准确率')
            plt.ylabel('准确率')
            plt.xlabel('轮数')
            plt.legend(['训练集', '验证集'], loc='lower right')

            plt.subplot(122)
            plt.plot(history.history['loss'])
            plt.plot(history.history['val_loss'])
            plt.title('模型损失')
            plt.ylabel('损失')
            plt.xlabel('轮数')
            plt.legend(['训练集', '验证集'], loc='upper right')

            plt.tight_layout()
            plt.savefig(os.path.join(os.path.dirname(args.model), "training_history.png"))
            plt.close()

            print(f"模型训练完成，已保存至: {args.model}")

        # 执行检测
        result = detector.detect_bs_points(args.image_path, debug=args.debug)

        # 输出检测结果
        print("\n===== 检测结果 =====")
        print(f"最新K线柱:")
        print(f"B点标记: {'是' if result['has_B_point'] else '否'}")
        print(f"S点标记: {'是' if result['has_S_point'] else '否'}")
        if result["detected_letter"]:
            print(f"检测到的字母: {result['detected_letter']}")
            print(f"置信度: {result['confidence']:.2f}")
            if result["position"]:
                print(f"位置: {result['position']}")
        else:
            print("未检测到B点或S点标记")

        # 可视化结果
        output_path = visualize_result(args.image_path, result, debug=args.debug)
        print(f"\n结果图像已保存至: {output_path}")

        # 如果使用调试模式，输出调试目录位置
        if args.debug:
            debug_dir = os.path.join(os.path.dirname(args.image_path), "cnn_debug")
            print(f"\n调试图像已保存至: {debug_dir}")

    except Exception as e:
        import traceback
        print(f"错误: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
