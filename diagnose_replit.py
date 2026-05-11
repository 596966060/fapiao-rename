#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断脚本 - 检查Replit上的app.py版本
运行方式: 在Replit Shell中执行: python diagnose_replit.py
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print("=" * 80)
print("Replit诊断 - app.py版本检查")
print("=" * 80)

# 检查app.py大小
import os
if os.path.exists('app.py'):
    size = os.path.getsize('app.py')
    print(f"\napp.py 文件大小: {size} bytes")
    if size < 10000:
        print("  ⚠️ 警告: 文件可能不完整（应该 > 15000 bytes）")
else:
    print("\n❌ app.py 不存在!")
    sys.exit(1)

# 检查_extract_fields方法
with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 检查关键内容
checks = {
    '_extract_fields': '_extract_fields' in content,
    '日期提取': 'date_patterns' in content,
    '发票号提取': 'inv_patterns' in content,
    '公司名提取': 'company_lines' in content,
    '金额提取': 'amount_patterns' in content,
    '小写标签优先': 'r\'小写' in content,
    '垒字符处理': '垒' in content,
}

print("\n检查内容:")
for name, found in checks.items():
    status = "✓" if found else "✗"
    print(f"  {status} {name}")

# 统计
found_count = sum(1 for v in checks.values() if v)
print(f"\n结果: {found_count}/{len(checks)} 关键内容存在")

if found_count < len(checks):
    print("\n❌ 问题: app.py内容不完整!")
    print("   原因: Replit上的代码版本过旧或未正确更新")
    print("\n   修复方法:")
    print("   1. 进入Shell")
    print("   2. 执行: git pull origin main --force")
    print("   3. 确认 app.py 已更新")
    print("   4. 重启应用 (点击 Run)")
else:
    print("\n✅ app.py 内容完整 - 逻辑应该正常")

# 尝试导入并测试
print("\n" + "=" * 80)
print("测试 OCR 提取逻辑")
print("=" * 80)

try:
    from app import InvoiceExtractor
    import easyocr

    # 模拟OCR输出
    test_text = """电子发票
    发票号码: 26127000000227474407
    开票日期: 2026年05月01日
    名称:恭亲科技(上海)有限公司
    名称:北京艺龙国际旅行社有限公司天津分公司
    价税合计(小写) ¥168.17"""

    reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    extractor = InvoiceExtractor(reader)

    fields = extractor._extract_fields(test_text)

    print(f"\nOCR提取测试:")
    print(f"  日期: {fields.get('date')}")
    print(f"  发票号: {fields.get('invoice_number')}")
    print(f"  购买方: {fields.get('buyer')}")
    print(f"  销售方: {fields.get('supplier')}")
    print(f"  金额: {fields.get('amount')}")

    if all([fields.get('date'), fields.get('invoice_number'), fields.get('amount')]):
        print("\n✅ OCR提取逻辑正常")
    else:
        print("\n❌ OCR提取逻辑有问题")

except Exception as e:
    print(f"\n❌ 错误: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 80)
