#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
启动脚本 - 检查环境 + 启动 Flask
"""
import os
import sys
import subprocess
import time
from pathlib import Path

def check_python():
    """检查 Python 版本"""
    ver = sys.version_info
    print(f"Python {ver.major}.{ver.minor}.{ver.micro}")
    if ver.major < 3 or (ver.major == 3 and ver.minor < 7):
        print("ERROR: Python 3.7+ required")
        return False
    return True

def check_files():
    """检查必要文件"""
    required = ['app.py', 'requirements.txt', 'templates']
    for f in required:
        if not Path(f).exists():
            print(f"ERROR: {f} not found")
            print(f"Current directory: {os.getcwd()}")
            return False
    return True

def check_flask():
    """检查 Flask 是否已安装"""
    try:
        import flask
        return True
    except ImportError:
        return False

def install_deps():
    """安装依赖"""
    print("Installing dependencies...")
    result = subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt'],
                          capture_output=False)
    return result.returncode == 0

def main():
    print()
    print("=" * 50)
    print("Invoice Rename Tool - Starting")
    print("=" * 50)
    print()

    # 检查路径
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    print(f"Current directory: {os.getcwd()}")
    print()

    # 检查文件
    print("Checking files...")
    if not check_files():
        print()
        input("Press Enter to exit...")
        return 1

    print("Files OK")
    print()

    # 检查 Python
    print("Checking Python...")
    if not check_python():
        print()
        input("Press Enter to exit...")
        return 1

    print()

    # 检查依赖
    print("Checking Flask...")
    if not check_flask():
        print()
        if not install_deps():
            print("ERROR: Failed to install dependencies")
            print()
            input("Press Enter to exit...")
            return 1
        print()

    # 启动 Flask
    print("Starting Flask server...")
    print("Access: http://127.0.0.1:5000")
    print()
    print("Opening browser in 2 seconds...")
    print()

    time.sleep(2)

    # 打开浏览器
    try:
        import webbrowser
        webbrowser.open('http://127.0.0.1:5000')
    except:
        pass

    # 启动 Flask
    print()
    os.system(f"{sys.executable} app.py")

    return 0

if __name__ == '__main__':
    sys.exit(main())
