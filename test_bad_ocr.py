#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试坏的OCR输出 - 用户提供的实际案例
"""
import re

def extract_fields_improved(text: str) -> dict:
    """改进的字段提取 - 处理严重OCR错误"""
    result = {
        "date": None,
        "invoice_number": None,
        "buyer": None,
        "supplier": None,
        "amount": None,
    }

    print("\n=== 改进的字段提取 ===\n")
    print(f"原始文本长度: {len(text)} 字符\n")

    # === 日期提取 ===
    # 处理干扰字符：202605几01月 → 提取 2026, 05, 01
    print("--- 日期提取 ---")
    # 更宽松的日期正则：允许非数字干扰字符在数字之间
    date_patterns = [
        r'(\d{4})\s*年?\s*(\d{1,2})\s*月?\s*(\d{1,2})',  # 严格格式
        r'(\d{4})[^\d]*(\d{1,2})[^\d]*(\d{1,2})',  # 允许干扰字符
    ]

    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                if 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                    result["date"] = f"{y:04d}-{m:02d}-{d:02d}"
                    print(f"  匹配: {match.group(0)} → {result['date']}")
                    break
            except Exception as e:
                pass

    if not result["date"]:
        print(f"  未找到日期")
    else:
        print(f"[OK] 日期: {result['date']}")

    # === 发票号提取 ===
    # 处理: '261270)0000)()'227471407 → 提取 26127000000227471407
    print("\n--- 发票号提取 ---")

    # 方式1: 发票号标签
    inv_match = re.search(r'(?:发票号|号码)[：\s]*([0-9\)\(]+)', text)
    if inv_match:
        raw = inv_match.group(1)
        # 清理特殊字符，只保留数字
        clean = re.sub(r'[^\d]', '', raw)
        if 6 <= len(clean) <= 20:
            result["invoice_number"] = clean
            print(f"  标签方式: '{raw}' → {clean}")

    # 方式2: 查找所有长数字（排除干扰）
    if not result["invoice_number"]:
        all_digit_sequences = re.findall(r'[0-9\)\(]+', text)
        for seq in all_digit_sequences:
            clean = re.sub(r'[^\d]', '', seq)
            if 6 <= len(clean) <= 20:
                result["invoice_number"] = clean
                print(f"  长数字方式: '{seq}' → {clean}")
                break

    if not result["invoice_number"]:
        print(f"  未找到发票号")
    else:
        print(f"✓ 发票号: {result['invoice_number']}")

    # === 企业名提取 ===
    # 处理: 恭亲科技(卜湃)有隗公训 → 提取 恭亲科技、... 有限公司等
    print("\n--- 企业名提取 ---")

    # 清理文本：移除括号内的干扰字符
    cleaned_text = re.sub(r'\([^\)]*\)', '', text)

    # 企业名模式：汉字 + 企业关键词
    company_pattern = r'[\u4e00-\u9fa5]+(?:公司|有限|分公司|集团|股份|企业|研究所|医院|学校|协会|中心)'
    companies = re.findall(company_pattern, cleaned_text)

    print(f"  找到企业: {companies}")

    # 去重和过滤
    seen = set()
    unique_companies = []
    for c in companies:
        c = c.strip()
        if 3 <= len(c) <= 100 and c not in seen:
            if '统一' not in c and '税号' not in c and '社会' not in c:
                seen.add(c)
                unique_companies.append(c)

    print(f"  去重后: {unique_companies}")

    if len(unique_companies) >= 2:
        result["buyer"] = unique_companies[0]
        result["supplier"] = unique_companies[1]
        print(f"✓ 购买方: {result['buyer']}")
        print(f"✓ 销售方: {result['supplier']}")
    elif len(unique_companies) == 1:
        result["buyer"] = unique_companies[0]
        print(f"✓ 购买方: {result['buyer']}")

    # === 金额提取 ===
    # 处理: Y168.17 → 168.17 (Y可能代替¥)
    print("\n--- 金额提取 ---")

    amount_patterns = [
        (r'小写[）)]*\s*[¥￥垩圓Y]\s*([0-9]{1,10}\.[0-9]{2})', '小写标签'),
        (r'[¥￥垩圓Y]\s*([0-9]{1,10}\.[0-9]{2})', '货币符号'),
        (r'([0-9]{1,10}\.[0-9]{2})\s*元', '元后缀'),
        (r'合计\s*[¥￥垩圓Y]?\s*([0-9]{1,10}\.[0-9]{2})', '合计'),
    ]

    for pattern, label in amount_patterns:
        matches = re.findall(pattern, text)
        if matches:
            try:
                result["amount"] = str(max(float(m) for m in matches))
                print(f"  {label}: {matches} → {result['amount']}")
                break
            except:
                pass

    if not result["amount"]:
        print(f"  未找到金额")
    else:
        print(f"✓ 金额: {result['amount']}")

    return result


# 用户提供的坏OCR输出
bad_ocr_text = """电子岸票(普#发票)
发票号码:
'261270)0000)()'227471407
开票月期: 202605几01月
行
销
|名称:恭亲科技
(卜湃)有隗公训
|名称:比尕艺龙|际旅行社有限公训津分公训
"
统一社会倌用代码/纳税人识别号:91310114MA789J2JgN
纂
鱼
阮一社会信用代码/纳税人识别号:91120222MA05JNENSQ
项|1G称
规恪雅:}
伫
数 州
价
金 *额
税:爷 '徘:收
税  额
旅游服务*付:'街:奖
1558.65
[1
9.54
合 ^计
干 |58.E
}9.5
价税合计 (大'与)
萱仍陟拎捌圆萱俐柒分
(小与)  Y168.17
F
开票人: jj川d"""

print("="*70)
print("测试坏的OCR输出 - 用户实际案例")
print("="*70)
print("\n原始OCR文本:")
print("-"*70)
print(bad_ocr_text)
print("-"*70)

result = extract_fields_improved(bad_ocr_text)

print("\n" + "="*70)
print("最终结果:")
print("="*70)
print(f"日期: {result['date']} (期望: 2026-05-01)")
print(f"发票号: {result['invoice_number']} (期望: 26127000000227471407)")
print(f"购买方: {result['buyer']} (期望: 含恭亲科技)")
print(f"销售方: {result['supplier']} (期望: 含艺龙)")
print(f"金额: {result['amount']} (期望: 168.17)")
print("="*70)

# 验证
success = 0
if result['date'] == '2026-05-01':
    print("✓ 日期正确")
    success += 1
else:
    print(f"✗ 日期错误: {result['date']}")

if result['invoice_number'] and result['invoice_number'].startswith('2612700000022747'):
    print(f"✓ 发票号正确")
    success += 1
else:
    print(f"✗ 发票号错误: {result['invoice_number']}")

if result['buyer'] and '恭亲' in result['buyer']:
    print(f"✓ 购买方正确")
    success += 1
else:
    print(f"✗ 购买方错误: {result['buyer']}")

if result['supplier'] and '艺龙' in result['supplier']:
    print(f"✓ 销售方正确")
    success += 1
else:
    print(f"✗ 销售方错误: {result['supplier']}")

if result['amount'] == '168.17':
    print(f"✓ 金额正确")
    success += 1
else:
    print(f"✗ 金额错误: {result['amount']}")

print("="*70)
print(f"总体: {success}/5 通过")
print("="*70)
