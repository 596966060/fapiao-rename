#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整OCR测试和改进 - 用户提供的清晰发票图片
需要: invoices/invoice.jpg (清晰的发票图片)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
import re
import cv2
import numpy as np
from PIL import Image
import easyocr

print("=" * 80)
print("OCR 完整诊断和改进")
print("=" * 80)

# ============================================================================
# 第1步：初始化 EasyOCR
# ============================================================================
print("\n[第1步] 初始化EasyOCR阅读器...")
try:
    reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    print("  [OK] EasyOCR已加载")
except Exception as e:
    print(f"  [ERROR] {e}")
    sys.exit(1)

# ============================================================================
# 第2步：寻找测试图片
# ============================================================================
print("\n[第2步] 寻找测试图片...")
test_image = None

# 查找 invoices 文件夹中的图片
invoices_dir = Path("invoices")
if invoices_dir.exists():
    candidates = list(invoices_dir.glob("*.jpg")) + list(invoices_dir.glob("*.png"))
    if candidates:
        test_image = str(candidates[0])
        print(f"  [OK] 找到: {test_image}")

if not test_image:
    print(f"  [ERROR] 找不到测试图片")
    print(f"  请将清晰发票图片放在 invoices/ 文件夹中")
    sys.exit(1)

# ============================================================================
# 第3步：测试图像预处理
# ============================================================================
print(f"\n[第3步] 图像预处理...")

image = Image.open(test_image).convert('RGB')
image_array = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

print(f"  原始尺寸: {image_array.shape}")

# 测试不同的预处理方法
def preprocess_v1(img):
    """当前方法：CLAHE + 二值化 + 降噪"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    denoised = cv2.medianBlur(binary, 3)
    result = cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)
    return result

def preprocess_v2(img):
    """改进方法1：只用CLAHE增强"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

def preprocess_v3(img):
    """改进方法2：CLAHE + 双边滤波"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(10, 10))
    enhanced = clahe.apply(gray)
    denoised = cv2.bilateralFilter(enhanced, 9, 75, 75)
    return cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)

# ============================================================================
# 第4步：对每个预处理方法进行 OCR
# ============================================================================
print(f"\n[第4步] OCR识别...")

methods = [
    ("原始图像", image_array),
    ("v1_CLAHE+二值化+降噪", preprocess_v1(image_array)),
    ("v2_CLAHE简化", preprocess_v2(image_array)),
    ("v3_CLAHE+双边滤波", preprocess_v3(image_array)),
]

ocr_results = {}

for method_name, processed_img in methods:
    print(f"\n  {method_name}:")
    try:
        results = reader.readtext(processed_img, detail=0)
        text = '\n'.join(results)
        ocr_results[method_name] = text
        print(f"    识别字符数: {len(text)}")

        # 显示前200字符
        preview = text[:200].replace('\n', ' ')
        print(f"    预览: {preview}...")
    except Exception as e:
        print(f"    [ERROR] {e}")

# ============================================================================
# 第5步：字段提取测试
# ============================================================================
print(f"\n[第5步] 字段提取...")

def extract_fields(text: str) -> dict:
    """改进的字段提取"""
    result = {
        "date": None,
        "invoice_number": None,
        "buyer": None,
        "supplier": None,
        "amount": None,
    }

    # 日期
    date_patterns = [
        r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})',
        r'(\d{4})[^\d]*(\d{1,2})[^\d]*(\d{1,2})',
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                if 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                    result["date"] = f"{y:04d}-{m:02d}-{d:02d}"
                    break
            except:
                pass

    # 发票号
    inv_patterns = [
        r'(?:发票号|号码)[：\s]*([0-9\)\(]+)',
        r'[0-9\)\(]{15,}',
    ]
    for pattern in inv_patterns:
        match = re.search(pattern, text)
        if match:
            raw = match.group(1) if isinstance(match.group(1), str) else match.group(0)
            clean = re.sub(r'[^\d]', '', raw)
            if 6 <= len(clean) <= 20:
                result["invoice_number"] = clean
                break

    # 企业名
    company_pattern = r'[\u4e00-\u9fa5]+(?:公司|有限|分公司|集团|股份|企业|研究所|医院|学校|协会|中心)'
    companies = re.findall(company_pattern, text)
    seen = set()
    unique_companies = []
    for c in companies:
        c = c.strip()
        if 3 <= len(c) <= 100 and c not in seen:
            if '统一' not in c and '税号' not in c and '社会' not in c:
                seen.add(c)
                unique_companies.append(c)

    if len(unique_companies) >= 2:
        result["buyer"] = unique_companies[0]
        result["supplier"] = unique_companies[1]
    elif len(unique_companies) == 1:
        result["buyer"] = unique_companies[0]

    # 金额
    amount_patterns = [
        r'小写[）)]*\s*[¥￥Y]\s*([0-9]{1,10}\.[0-9]{2})',
        r'[¥￥Y]\s*([0-9]{1,10}\.[0-9]{2})',
        r'([0-9]{1,10}\.[0-9]{2})\s*元',
    ]
    for pattern in amount_patterns:
        matches = re.findall(pattern, text)
        if matches:
            try:
                result["amount"] = str(max(float(m) for m in matches))
                break
            except:
                pass

    return result

# 对每个OCR结果进行字段提取
extraction_results = {}
for method_name, text in ocr_results.items():
    fields = extract_fields(text)
    extraction_results[method_name] = fields

# ============================================================================
# 第6步：结果对比
# ============================================================================
print(f"\n{'=' * 80}")
print("结果对比")
print(f"{'=' * 80}\n")

expected = {
    "date": "2026-05-01",
    "invoice_number": "2612700000022747407",
    "buyer": "恭亲科技",
    "supplier": "艺龙",
    "amount": "168.17",
}

print(f"期望值:")
for k, v in expected.items():
    print(f"  {k}: {v}")

print(f"\n各个方法的识别结果:")
print("-" * 80)

for method_name, fields in extraction_results.items():
    print(f"\n{method_name}:")
    for field_name, value in fields.items():
        status = "OK" if value else "MISS"
        print(f"  {field_name}: {value} [{status}]")

# ============================================================================
# 第7步：推荐最佳方案
# ============================================================================
print(f"\n{'=' * 80}")
print("推荐最佳预处理方案")
print(f"{'=' * 80}\n")

scores = {}
for method_name, fields in extraction_results.items():
    score = 0
    if fields["date"] == expected["date"]:
        score += 1
    if fields["invoice_number"] == expected["invoice_number"]:
        score += 1
    if fields["buyer"] and expected["buyer"] in fields["buyer"]:
        score += 1
    if fields["supplier"] and expected["supplier"] in fields["supplier"]:
        score += 1
    if fields["amount"] == expected["amount"]:
        score += 1
    scores[method_name] = score
    print(f"{method_name}: {score}/5")

best_method = max(scores, key=scores.get)
print(f"\n最佳方案: {best_method}")

print(f"\n{'=' * 80}")
print("测试完成")
print(f"{'=' * 80}")
