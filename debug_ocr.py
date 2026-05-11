#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调试脚本：直接测试 OCR 识别
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import easyocr
import cv2
import numpy as np
from pathlib import Path

# 初始化 OCR
print("正在初始化 OCR 模型...")
reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
print("✓ OCR 模型已加载\n")

# 测试文件
test_file = "D:/lianglidong/Desktop/网页版重命名工具/test_invoice.jpg"

if not Path(test_file).exists():
    print(f"❌ 找不到测试文件: {test_file}")
    print("请把发票放在: D:/lianglidong/Desktop/网页版重命名工具/test_invoice.jpg")
    sys.exit(1)

# 读取图片
print(f"读取图片: {test_file}")
image_bytes = np.fromfile(test_file, dtype=np.uint8)
image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)

if image is None:
    print("❌ 无法读取图片")
    sys.exit(1)

# 图像增强
gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(10, 10))
enhanced = clahe.apply(gray)
enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

# OCR 识别
print("正在识别...")
results = reader.readtext(enhanced_bgr, detail=0)

if not results:
    print("❌ OCR 无法识别任何内容")
    sys.exit(1)

# 输出原始文本
print("\n" + "="*60)
print("原始 OCR 识别文本：")
print("="*60)
text = '\n'.join(results)
print(text)
print("="*60)

# 尝试提取字段
print("\n字段提取结果：")
print("-"*60)

import re

# 日期
match = re.search(r'(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})', text)
date = match.group(0) if match else "未找到"
print(f"日期: {date}")

# 发票号
for pattern in [r'发票(?:代)?号[码]*[：:\s]*([0-9]{6,20})',
                r'编号[：\s]*([0-9]{6,20})',
                r'([0-9]{6,20})']:
    match = re.search(pattern, text)
    if match:
        invoice_num = match.group(1) if len(match.group(1)) >= 6 else None
        if invoice_num:
            print(f"发票号: {invoice_num}")
            break
else:
    print("发票号: 未找到")

# 销售方
supplier = "未找到"
lines = text.split('\n')
for line in lines:
    if re.search(r'销售方|卖方|供应商', line):
        print(f"包含销售方的行: {line}")
        match = re.search(r'[销卖][方者]?[：\s]*([^\n：]{2,80})', line)
        if match:
            supplier = match.group(1).strip()
        break
print(f"销售方: {supplier}")

# 购买方
buyer = "未找到"
for line in lines:
    if re.search(r'购买方|买方|采购方', line):
        print(f"包含购买方的行: {line}")
        match = re.search(r'[购买][方者]?[：\s]*([^\n：]{2,80})', line)
        if match:
            buyer = match.group(1).strip()
        break
print(f"购买方: {buyer}")

# 金额
amount = "未找到"
for pattern in [r'合计[：\s]*[¥]?\s*([0-9]{1,10}\.[0-9]{2})',
                r'[¥]\s*([0-9]{1,10}\.[0-9]{2})']:
    matches = re.findall(pattern, text)
    if matches:
        amount = max(matches, key=lambda x: float(x))
        break
print(f"金额: {amount}")

print("-"*60)
