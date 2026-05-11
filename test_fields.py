#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试字段提取逻辑 - 用实际的 OCR 输出
"""
import re

def extract_fields(text: str) -> dict:
    """字段提取 - 多层次回退策略"""
    result = {
        "date": None,
        "invoice_number": None,
        "buyer": None,
        "supplier": None,
        "amount": None,
    }

    # ===== 日期提取 =====
    date_match = re.search(r'(\d{4})[\年\-/](\d{1,2})[\月\-/](\d{1,2})', text)
    if date_match:
        try:
            y, m, d = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
            if 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                result["date"] = f"{y:04d}-{m:02d}-{d:02d}"
        except:
            pass

    # ===== 发票号提取 =====
    inv_match = re.search(r'(?:发票号|号码)[：\s]*(\d{6,20})', text)
    if inv_match:
        result["invoice_number"] = inv_match.group(1)
    else:
        all_numbers = re.findall(r'\d{6,20}', text)
        if all_numbers:
            result["invoice_number"] = all_numbers[0]

    # ===== 购买方和销售方提取 =====
    # 直接在文本中找所有的企业实体
    # 方式：找包含关键词的连续中文 + 关键词
    company_pattern = r'[\u4e00-\u9fa5]+(?:公司|有限|分公司|集团|股份|企业|研究所|医院|学校|协会|中心)'
    companies = re.findall(company_pattern, text)

    # 打印调试信息
    print(f"\n[DEBUG] 找到的企业: {companies}")

    # 去重同时保留顺序
    seen = set()
    unique_companies = []
    for c in companies:
        c = c.strip()
        # 过滤掉包含"统一"、"税号"、"社会"等无关词的
        if 3 <= len(c) <= 100 and c not in seen:
            if '统一' not in c and '税号' not in c and '社会' not in c:
                seen.add(c)
                unique_companies.append(c)

    print(f"[DEBUG] 去重后: {unique_companies}")

    if len(unique_companies) >= 2:
        # 简单方式：企业名通常按顺序出现，购买方在前，销售方在后
        # 不依赖"购买方"/"销售方"标签（因为会被OCR误识）
        result["buyer"] = unique_companies[0]
        result["supplier"] = unique_companies[1]
        print(f"[DEBUG] 按顺序分配：购买方={unique_companies[0]}, 销售方={unique_companies[1]}")

    elif len(unique_companies) == 1:
        result["buyer"] = unique_companies[0]

    # ===== 金额提取 =====
    amount_patterns = [
        r'小写[）)]*\s*[¥￥垩圓]\s*([0-9]{1,10}\.[0-9]{2})',
        r'[¥￥垩圓]\s*([0-9]{1,10}\.[0-9]{2})',
        r'([0-9]{1,10}\.[0-9]{2})\s*元',
        r'合.*计.*[¥￥垩圓]?\s*([0-9]{1,10}\.[0-9]{2})',
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


# 测试用实际 OCR 输出
ocr_text = """电子炭票(普遢发票)
发票号码:
26127000000227474407

开票日期: 2026年05月01日
夭津市税务局
1称:恭亲科技(上海)有限公司
恪称:北京艺龙国际旅行社有限公司夭津分公司
统一社会信用代码/纳税人识别号:91310114MA789J2JgN
统一社会信用代码/纳税人识别号:91120222MA05JNEN5Q
项目名称 规格型号 单位 数量 单价 金额 税率/征收率 税额
1旅游服务*住宿费 158.65 6% 9.521
合计 158.65 9.521
价税合计 (大写) 壹佰陆拾捌圆壹角柒分
(小写) 垩168.17
开票人:方丹丹
"""

print("="*70)
print("测试 OCR 字段提取")
print("="*70)
print("\n原始 OCR 文本:")
print("-"*70)
print(ocr_text)
print("-"*70)

result = extract_fields(ocr_text)

print("\n提取结果:")
print("="*70)
print(f"日期: {result['date']} (期望: 2026-05-01)")
print(f"发票号: {result['invoice_number']} (期望: 26127000000227474407)")
print(f"购买方: {result['buyer']} (期望: 恭亲科技(上海)有限公司)")
print(f"销售方: {result['supplier']} (期望: 北京艺龙国际旅行社有限公司天津分公司)")
print(f"金额: {result['amount']} (期望: 168.17)")
print("="*70)

# 验证
success = 0
if result['date'] == '2026-05-01':
    print("✓ 日期正确")
    success += 1
else:
    print("✗ 日期错误")

if result['invoice_number'] == '26127000000227474407':
    print("✓ 发票号正确")
    success += 1
else:
    print("✗ 发票号错误")

if '恭亲科技' in (result['buyer'] or ''):
    print("✓ 购买方正确")
    success += 1
else:
    print("✗ 购买方错误")

if '北京艺龙' in (result['supplier'] or ''):
    print("✓ 销售方正确")
    success += 1
else:
    print("✗ 销售方错误")

if result['amount'] == '168.17':
    print("✓ 金额正确")
    success += 1
else:
    print("✗ 金额错误")

print("="*70)
print(f"通过: {success}/5")
print("="*70)
