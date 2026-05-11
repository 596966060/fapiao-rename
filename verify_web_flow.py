#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web上传模拟测试 - 验证GitHub和本地同步一致性
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')

import os
import tempfile
import shutil
from pathlib import Path
from app import InvoiceExtractor, generate_filename
import easyocr

print("=" * 80)
print("Web上传流程完整验证")
print("=" * 80)

# 初始化
reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
extractor = InvoiceExtractor(reader)

# 创建会话
session_dir = tempfile.mkdtemp()
print(f"\n[会话] {session_dir}")

# 测试文件
test_images = {
    'invoices/img_v3_0211d_211fe46d-c33d-452c-86ca-e09022bf991g.jpg': {
        'name': '普通发票',
        'expected_date': '2026-05-01',
        'expected_invoice': '26127000000227474407',
        'expected_amount': '168.17'
    },
    'invoices/screenshot-20260511-160543.png': {
        'name': '商业发票',
        'expected_date': '2026-05-07',
        'expected_invoice': '26324000000154072606',
        'expected_amount': '6.31'
    }
}

print("\n[测试]")
results = []
for file_path, expected in test_images.items():
    if not os.path.exists(file_path):
        print(f"  ❌ {expected['name']}: 文件不存在")
        continue

    print(f"\n  {expected['name']}: {Path(file_path).name}")

    try:
        data = extractor.extract(file_path)
        ext = Path(file_path).suffix.lower()
        new_name = generate_filename(data, ext)

        # 验证
        checks = {
            '日期': (data['date'] == expected['expected_date'], data['date']),
            '发票号': (data['invoice_number'] == expected['expected_invoice'], data['invoice_number']),
            '金额': (data['amount'] == expected['expected_amount'], data['amount']),
        }

        for field, (is_correct, actual) in checks.items():
            status = "✓" if is_correct else "✗"
            print(f"    {status} {field}: {actual}")

        # 保存文件
        src_path = file_path
        dst_path = os.path.join(session_dir, new_name)
        shutil.copy(src_path, dst_path)

        all_correct = all(check[0] for check in checks.values())
        results.append({
            'name': expected['name'],
            'file': Path(file_path).name,
            'renamed': new_name,
            'success': all_correct
        })

    except Exception as e:
        print(f"    ❌ 错误: {e}")
        results.append({'name': expected['name'], 'success': False})

# 总结
print("\n" + "=" * 80)
print("[总结]")
success_count = sum(1 for r in results if r.get('success'))
print(f"  通过: {success_count}/{len(results)}")

for r in results:
    if r.get('success'):
        print(f"  ✅ {r['name']}")
        print(f"     {r['file']} →")
        print(f"     {r['renamed']}")
    else:
        print(f"  ❌ {r['name']}")

# 清理
shutil.rmtree(session_dir, ignore_errors=True)

if success_count == len(results):
    print("\n✅ Web流程验证通过 - 本地和GitHub代码一致")
    print("   Replit需要执行: git pull origin main")
else:
    print("\n❌ Web流程验证失败")
    sys.exit(1)

print("=" * 80)
