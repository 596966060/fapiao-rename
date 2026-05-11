#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
直接测试 OCR 和字段提取
不依赖 Flask
"""
import sys
import os
from pathlib import Path

# 添加路径
sys.path.insert(0, os.path.dirname(__file__))

import re
import numpy as np
import cv2

# 模拟 InvoiceExtractor 的字段提取逻辑
def extract_fields(text: str) -> dict:
    result = {
        "date": None,
        "invoice_number": None,
        "buyer": None,
        "supplier": None,
        "amount": None,
    }

    print("\n=== 字段提取调试 ===\n")
    print(f"原始文本长度: {len(text)} 字符\n")

    # 日期
    match = re.search(r'(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})', text)
    if match:
        try:
            year, month, day = match.groups()
            year, m, d = int(year), int(month), int(day)
            if 1900 <= year <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                result["date"] = f"{year:04d}-{m:02d}-{d:02d}"
        except:
            pass
    print(f"✓ 日期: {result['date']}")

    # 发票号
    matches = re.findall(r'\b\d{6,20}\b', text)
    if matches:
        for m in matches:
            if 6 <= len(m) <= 20:
                result["invoice_number"] = m
                break
    print(f"✓ 发票号: {result['invoice_number']}")

    # 购买方 - 调试所有尝试
    print(f"\n--- 购买方检测 ---")

    # 方式 1: 使用"购买方信息"标签
    match = re.search(r'购买方信息[：\s]*(.{2,100}?)(?=统一|税号|纳税|销售|名称|$)', text, re.DOTALL)
    if match:
        buyer = match.group(1).strip()
        buyer = re.sub(r'[\n\s]+', '', buyer)
        print(f"  方式1 (购买方信息): 匹配到 -> '{buyer}'")
        if 3 <= len(buyer) <= 100:
            result["buyer"] = buyer
            print(f"  ✓ 已设置")
    else:
        print(f"  方式1 (购买方信息): 无匹配")

    # 方式 2: 直接查找"购买方"
    if not result["buyer"]:
        match = re.search(r'(?:购买方|买方)[：\s]*(.{2,100}?)(?=[\n统一税号销售名称]|$)', text)
        if match:
            buyer = match.group(1).strip()
            buyer = re.sub(r'[\n\s]+', '', buyer)
            print(f"  方式2 (购买方): 匹配到 -> '{buyer}'")
            if 3 <= len(buyer) <= 100:
                result["buyer"] = buyer
                print(f"  ✓ 已设置")
        else:
            print(f"  方式2 (购买方): 无匹配")

    if not result["buyer"]:
        print(f"✗ 购买方: 未找到")
    else:
        print(f"✓ 购买方: {result['buyer']}")

    # 销售方 - 调试所有尝试
    print(f"\n--- 销售方检测 ---")

    # 方式 1: 使用"销售方信息"标签
    match = re.search(r'销售方信息[：\s]*(.{2,100}?)(?=统一|税号|纳税|购买|名称|$)', text, re.DOTALL)
    if match:
        supplier = match.group(1).strip()
        supplier = re.sub(r'[\n\s]+', '', supplier)
        print(f"  方式1 (销售方信息): 匹配到 -> '{supplier}'")
        if 3 <= len(supplier) <= 100:
            result["supplier"] = supplier
            print(f"  ✓ 已设置")
    else:
        print(f"  方式1 (销售方信息): 无匹配")

    # 方式 2: 直接查找"销售方"
    if not result["supplier"]:
        match = re.search(r'(?:销售方|卖方)[：\s]*(.{2,100}?)(?=[\n统一税号购买名称]|$)', text)
        if match:
            supplier = match.group(1).strip()
            supplier = re.sub(r'[\n\s]+', '', supplier)
            print(f"  方式2 (销售方): 匹配到 -> '{supplier}'")
            if 3 <= len(supplier) <= 100:
                result["supplier"] = supplier
                print(f"  ✓ 已设置")
        else:
            print(f"  方式2 (销售方): 无匹配")

    if not result["supplier"]:
        print(f"✗ 销售方: 未找到")
    else:
        print(f"✓ 销售方: {result['supplier']}")

    # 金额 - 调试所有尝试
    print(f"\n--- 金额检测 ---")
    amount_patterns = [
        (r'价税合计[（(]?小写[）)]?[：\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})', '价税合计(小写)'),
        (r'(?:价税)?合计[（(]?小写[）)]?[：\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})', '合计(小写)'),
        (r'合计[：\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})', '合计'),
        (r'[¥￥]\s*([0-9]{1,10}\.[0-9]{2})', '¥符号'),
        (r'([0-9]{1,10}\.[0-9]{2})\s*元', '元后缀'),
    ]

    for pattern, label in amount_patterns:
        matches = re.findall(pattern, text)
        if matches:
            print(f"  {label}: 找到 {matches}")
            try:
                result["amount"] = str(max(float(m) for m in matches))
                print(f"  ✓ 已设置为: {result['amount']}")
                break
            except:
                pass
        else:
            print(f"  {label}: 无匹配")

    if not result["amount"]:
        print(f"✗ 金额: 未找到")
    else:
        print(f"✓ 金额: {result['amount']}")

    return result


# 主程序
if __name__ == '__main__':
    test_image = "D:/lianglidong/Desktop/img_v3_0211d_211fe46d-c33d-452c-86ca-e09022bf991g.jpg"

    if not Path(test_image).exists():
        print(f"❌ 找不到测试文件: {test_image}")
        sys.exit(1)

    # 这里本应用 EasyOCR，但为了调试，我们直接用发票图片的实际 OCR 文本
    # 根据你的发票截图，OCR 应该识别出这些内容：

    ocr_text = """电子发票(普通发票)
发票号码: 26127000000227474407
开票日期: 2026年05月01日

购买方信息
名称: 恭亲科技(上海)有限公司
统一社会信用代码/纳税人识别号: 91310114MA7B9J2J9N

销售方信息
名称: 北京艺龙国际旅行社有限公司天津分公司
统一社会信用代码/纳税人识别号: 91120222MA05JNEN5Q

项目名称          规格型号    单位    数量    单价    金额    税率/征收率    税额
*旅游服务*住宿费                              158.65  6%              9.52

合计                                                    ¥158.65            ¥9.52

价税合计(大写)          壹佰陆拾捌元壹角柒分
价税合计(小写)                                                    ¥168.17"""

    print("="*60)
    print("发票 OCR 识别测试")
    print("="*60)
    print("\n原始 OCR 文本:")
    print("-"*60)
    print(ocr_text)
    print("-"*60)

    result = extract_fields(ocr_text)

    print("\n" + "="*60)
    print("最终识别结果:")
    print("="*60)
    print(f"日期: {result['date']}")
    print(f"发票号: {result['invoice_number']}")
    print(f"购买方: {result['buyer']}")
    print(f"销售方: {result['supplier']}")
    print(f"金额: {result['amount']}")
    print("="*60)
