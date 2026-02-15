#!/usr/bin/env python3
"""
项目初始化脚本
用于创建必要的目录结构和验证环境
"""
import os
import sys

def create_directories():
    """创建项目所需的目录结构"""
    directories = [
        'data/raw',
        'data/processed',
        'outputs/daily',
        'outputs/charts',
        'outputs/backtest',
        'logs'
    ]
    
    print("创建目录结构...")
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        print(f"  ✓ {directory}")
    
    print("\n目录结构创建完成!")

def check_dependencies():
    """检查依赖包是否安装"""
    required_packages = [
        'pandas',
        'numpy',
        'yaml',
        'openpyxl'
    ]
    
    optional_packages = [
        'akshare',
        'matplotlib',
        'scipy'
    ]
    
    print("\n检查必需依赖...")
    missing_required = []
    for package in required_packages:
        try:
            __import__(package)
            print(f"  ✓ {package}")
        except ImportError:
            print(f"  ✗ {package} (缺失)")
            missing_required.append(package)
    
    print("\n检查可选依赖...")
    for package in optional_packages:
        try:
            __import__(package)
            print(f"  ✓ {package}")
        except ImportError:
            print(f"  - {package} (未安装,可选)")
    
    if missing_required:
        print(f"\n警告: 缺少必需的包: {', '.join(missing_required)}")
        print("请运行: pip install -r requirements.txt")
        return False
    else:
        print("\n所有必需依赖已安装!")
        return True

def create_sample_config():
    """创建示例配置文件的备份"""
    config_file = 'configs/config.yaml'
    backup_file = 'configs/config.yaml.example'
    
    if os.path.exists(config_file):
        if not os.path.exists(backup_file):
            import shutil
            shutil.copy(config_file, backup_file)
            print(f"\n创建配置示例: {backup_file}")

def main():
    """主函数"""
    print("="*60)
    print("每日收盘复盘系统 - 项目初始化")
    print("="*60 + "\n")
    
    # 创建目录
    create_directories()
    
    # 检查依赖
    deps_ok = check_dependencies()
    
    # 创建配置示例
    create_sample_config()
    
    # 最终提示
    print("\n" + "="*60)
    if deps_ok:
        print("初始化完成! 可以开始使用系统了.")
        print("\n快速开始:")
        print("  1. 查看配置: configs/config.yaml")
        print("  2. 运行示例: python example.py")
        print("  3. 查看文档: README.md")
    else:
        print("初始化未完成,请先安装缺失的依赖包.")
        print("  运行: pip install -r requirements.txt")
    print("="*60 + "\n")

if __name__ == '__main__':
    main()
