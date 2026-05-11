#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
简单展示：看看现在OCR识别的文字
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
import easyocr

print("=" * 80)
print("OCR 识别文字展示")
print("=" * 80)

# 查找测试图片
invoices_dir = Path("invoices")
if not invoices_dir.exists():
    print("ERROR: invoices 文件夹不存在")
    sys.exit(1)

image_files = list(invoices_dir.glob("*.jpg")) + list(invoices_dir.glob("*.png"))
if not image_files:
    print("ERROR: invoices 文件夹中没有图片")
    sys.exit(1)

test_image = str(image_files[0])
print(f"\n测试图片: {image_files[0].name}\n")

# 初始化OCR
print("初始化 OCR 阅读器...")
reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
print("OK\n")

# 识别
print("=" * 80)
print("识别结果")
print("=" * 80 + "\n")

results = reader.readtext(test_image, detail=0)
text = '\n'.join(results)

print(text)

print("\n" + "=" * 80)
print(f"识别字符数: {len(text)}")
print("=" * 80)
