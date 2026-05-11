#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自动部署脚本 - 一键生成Replit部署包
使用方式: python auto_deploy.py
"""
import os
import shutil
import zipfile
from pathlib import Path
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 80)
print("自动部署 - Replit一键部署包生成")
print("=" * 80)

# 创建部署文件夹
deploy_dir = Path("replit_deploy_package")
if deploy_dir.exists():
    shutil.rmtree(deploy_dir)
deploy_dir.mkdir()

print(f"\n[1] 创建部署包文件夹: {deploy_dir}")

# 复制必要文件
files_to_copy = [
    ('app.py', 'app.py'),
    ('requirements.txt', 'requirements.txt'),
    ('Procfile', 'Procfile'),
    ('.gitignore', '.gitignore'),
]

print(f"\n[2] 复制必要文件:")
for src, dst in files_to_copy:
    if Path(src).exists():
        shutil.copy(src, deploy_dir / dst)
        print(f"    ✓ {src}")
    else:
        print(f"    ✗ {src} (不存在，跳过)")

# 复制templates文件夹
if Path('templates').is_dir():
    shutil.copytree('templates', deploy_dir / 'templates')
    print(f"    ✓ templates/")

# 创建部署说明
deploy_instruction = """【Replit 一键部署说明】

这个文件夹包含所有需要的文件来部署到Replit。

方法1：直接上传到Replit（推荐）
===============================
1. 进入 https://replit.com
2. 删除旧项目（如果有的话）
3. 点击 "New Repl"
4. 选择语言: Python
5. 项目名: invoice-tool
6. 点击 Create

7. 在Files区，删除默认的main.py

8. 上传这个文件夹中的所有文件：
   - app.py
   - requirements.txt
   - Procfile
   - .gitignore
   - templates/ (整个文件夹)

9. 点击 "Run"

10. 等待1-2分钟，OCR模型会初始化

11. 右边会显示 "Webview" 和网址
    点击网址就能开始使用了！

方法2：从GitHub导入（更简单）
===============================
1. 进入 https://replit.com
2. 删除旧项目
3. 点击 "Import from GitHub"
4. 输入: https://github.com/596966060/fapiao-rename
5. 点击 Import
6. Replit会自动拉取最新代码
7. 点击 Run

建议用方法2，这样以后有更新时，Replit可以自动同步。

测试验证
========
部署完成后，上传 screenshot-20260511-160543.png，应该看到：
✓ 日期: 2026-05-07
✓ 发票号: 26324000000154072606
✓ 金额: 6.31 元

如果识别结果还是空白，在Replit Shell运行：
python diagnose_replit.py

然后把输出结果提供给开发者。

问题排查
=========
- 如果显示 "OCR 还未初始化"，等待2-3分钟重试
- 如果文件上传失败，确保使用了 Chrome 或 Firefox
- 如果识别失败，检查图片质量（发票必须清晰可读）

支持的文件格式
==============
- PDF (.pdf)
- JPG/JPEG (.jpg, .jpeg)
- PNG (.png)
- BMP (.bmp)
- GIF (.gif)
- TIFF (.tiff, .tif)
- ZIP (.zip) - 可以包含多个上述格式的文件

特性
====
✓ 完全离线，无需API Key
✓ 支持批量上传
✓ 自动识别发票信息
✓ 自动重命名文件
✓ 批量下载结果
✓ 多人并发使用

完成!
"""

with open(deploy_dir / 'README.txt', 'w', encoding='utf-8') as f:
    f.write(deploy_instruction)

print(f"    ✓ README.txt (部署说明)")

# 创建ZIP包
zip_path = Path("replit_deploy.zip")
if zip_path.exists():
    zip_path.unlink()

print(f"\n[3] 创建ZIP部署包: {zip_path}")
shutil.make_archive("replit_deploy", "zip", ".", "replit_deploy_package")

# 显示大小
zip_size = zip_path.stat().st_size / 1024 / 1024
print(f"    大小: {zip_size:.1f} MB")

# 清理临时文件夹
shutil.rmtree(deploy_dir)

print(f"\n[4] 生成完成!")
print(f"    ZIP文件: {zip_path}")
print(f"    位置: 当前目录")

print("\n" + "=" * 80)
print("【下一步】")
print("=" * 80)
print("""
1. 解压 replit_deploy.zip（得到replit_deploy_package文件夹）

2. 打开 https://replit.com

3. 删除旧的invoice项目（如果有）

4. 选择方法A或B：

【方法A】从GitHub导入（最简单，推荐）
  - 点击 "Import from GitHub"
  - 输入: https://github.com/596966060/fapiao-rename
  - 点击 Import
  - 等待导入完成
  - 点击 Run

【方法B】手动上传文件
  - 点击 "New Repl"
  - 选择 Python
  - 名称: invoice-tool
  - 点击 Create
  - 删除 main.py
  - 上传 replit_deploy_package 中的所有文件到Replit
  - 点击 Run

5. 等待1-2分钟，OCR初始化完成

6. 右边会出现 Webview 和网址

7. 点击网址，上传发票测试

8. 应该看到正确的识别结果（日期、发票号、金额都有值）

================================
有问题?
================================
在Replit Shell中运行:
  python diagnose_replit.py

把输出结果提供给开发者诊断。

""")

print("=" * 80)
