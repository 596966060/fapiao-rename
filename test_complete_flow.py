#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
完整流程测试：上传 → OCR → 识别 → 重命名 → 下载
"""
import sys
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

import re
from app import InvoiceExtractor, generate_filename
import easyocr

print("=" * 80)
print("完整发票处理流程测试")
print("=" * 80)

# ============================================================================
# 步骤1：初始化
# ============================================================================
print("\n[步骤 1] 初始化OCR阅读器...")
try:
    reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    extractor = InvoiceExtractor(reader)
    print("  [OK] 已初始化")
except Exception as e:
    print(f"  [ERROR] {e}")
    sys.exit(1)

# ============================================================================
# 步骤2：模拟上传和处理
# ============================================================================
print("\n[步骤 2] 创建会话目录...")
session_id = datetime.now().strftime('%Y%m%d%H%M%S%f')[-15:]
session_dir = os.path.join(tempfile.gettempdir(), f'invoice_session_{session_id}')
os.makedirs(session_dir, exist_ok=True)
print(f"  会话ID: {session_id}")
print(f"  目录: {session_dir}")

# ============================================================================
# 步骤3：测试数据
# ============================================================================
print("\n[步骤 3] 准备测试数据...")

# 模拟从OCR得到的文本
test_ocr_text = """电子普通发票(普通发票)
发票号码：2612700000022747407
开票日期：2026年05月01日

购买方名称：恭亲科技（上海）有限公司
统一社会信用代码/纳税人识别号：91310114MA7B9J2J9N

销售方名称：北京艺龙国际旅行社有限公司天津分公司
统一社会信用代码/纳税人识别号：91120222MA05JNEN5Q

项目名称          规格型号    单位    数量    单价    金额    税率/征收率    税额
*旅游服务*住宿费                              158.65  6%              9.52

合计                                                    ¥158.65            ¥9.52

价税合计(大写)      壹佰陆拾捌圆壹角柒分
价税合计(小写)      ¥168.17"""

# ============================================================================
# 步骤4：字段提取
# ============================================================================
print("\n[步骤 4] 提取字段...")
fields = extractor._extract_fields(test_ocr_text)
print(f"  日期: {fields['date']}")
print(f"  发票号: {fields['invoice_number']}")
print(f"  购买方: {fields['buyer']}")
print(f"  销售方: {fields['supplier']}")
print(f"  金额: {fields['amount']}")

# ============================================================================
# 步骤5：文件重命名
# ============================================================================
print("\n[步骤 5] 生成新文件名...")
original_ext = ".pdf"
new_filename = generate_filename(fields, original_ext)
print(f"  原始名称: invoice{original_ext}")
print(f"  新文件名: {new_filename}")

# ============================================================================
# 步骤6：模拟文件保存
# ============================================================================
print("\n[步骤 6] 保存文件到会话目录...")
# 创建虚拟文件
dummy_file = os.path.join(session_dir, new_filename)
with open(dummy_file, 'w') as f:
    f.write("Invoice content")
print(f"  已保存: {dummy_file}")
print(f"  文件大小: {os.path.getsize(dummy_file)} bytes")

# ============================================================================
# 步骤7：模拟下载（打包为ZIP）
# ============================================================================
print("\n[步骤 7] 打包为ZIP...")
import zipfile
import io

zip_path = os.path.join(tempfile.gettempdir(), f'发票重命名_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip')
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
    z.write(dummy_file, arcname=new_filename)

print(f"  ZIP文件: {zip_path}")
print(f"  ZIP大小: {os.path.getsize(zip_path)} bytes")

# ============================================================================
# 步骤8：验证
# ============================================================================
print("\n[步骤 8] 验证...")

# 验证字段
checks = []

if fields['date'] == '2026-05-01':
    checks.append(("日期", True))
else:
    checks.append(("日期", False))

if fields['invoice_number'] == '2612700000022747407':
    checks.append(("发票号", True))
else:
    checks.append(("发票号", False))

if fields['buyer'] and '恭亲科技' in fields['buyer']:
    checks.append(("购买方", True))
else:
    checks.append(("购买方", False))

if fields['supplier'] and '艺龙' in fields['supplier']:
    checks.append(("销售方", True))
else:
    checks.append(("销售方", False))

if fields['amount'] == '168.17':
    checks.append(("金额", True))
else:
    checks.append(("金额", False))

# 验证文件名格式
expected_name_parts = ['2026-05-01', '2612700000022747407', '168.17']
all_in_name = all(part in new_filename for part in expected_name_parts)
checks.append(("文件名格式", all_in_name))

# 验证ZIP
zip_ok = os.path.exists(zip_path) and os.path.getsize(zip_path) > 0
checks.append(("ZIP打包", zip_ok))

# 验证ZIP内容
with zipfile.ZipFile(zip_path, 'r') as z:
    files_in_zip = z.namelist()
    zip_content_ok = new_filename in files_in_zip
    checks.append(("ZIP内容", zip_content_ok))

print("\n验证结果:")
passed = 0
for check_name, result in checks:
    status = "PASS" if result else "FAIL"
    print(f"  [{status}] {check_name}")
    if result:
        passed += 1

print(f"\n总计: {passed}/{len(checks)} 通过")

# ============================================================================
# 清理
# ============================================================================
print("\n[清理] 删除临时文件...")
shutil.rmtree(session_dir, ignore_errors=True)
os.remove(zip_path)
print("  完成")

print("\n" + "=" * 80)
if passed == len(checks):
    print("✓ 完整流程测试成功！")
    print("  - 字段识别: OK")
    print("  - 文件重命名: OK")
    print("  - ZIP打包: OK")
    print("  - 下载功能: OK")
else:
    print(f"✗ 测试失败: {len(checks) - passed} 个检查未通过")

print("=" * 80)

sys.exit(0 if passed == len(checks) else 1)
