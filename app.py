#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发票批量重命名工具 - 完整版
支持：上传 → 识别 → 重命名 → 下载重命名后的发票文件
"""

from flask import Flask, render_template, request, jsonify, send_file
import os
import sys
import tempfile
import shutil
import zipfile
from pathlib import Path
import re
import io
from datetime import datetime
import threading
import openpyxl
from openpyxl.styles import (Font, PatternFill, Alignment, Border, Side,
                              GradientFill)
from openpyxl.utils import get_column_letter

# PDF 和图像处理
from PIL import Image
import numpy as np
import cv2

# OCR
import easyocr

# Windows 编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ======================== 发票提取 ========================

class InvoiceExtractor:
    """发票 OCR 提取"""

    def __init__(self, reader):
        self.reader = reader

    def _pdf_to_image(self, pdf_path: str) -> np.ndarray:
        """PDF 转图片"""
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(pdf_path, first_page=1, last_page=1, dpi=150)
            if not images:
                raise Exception("PDF 无法转换")
            image = images[0]
            return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        except ImportError:
            raise Exception("缺少 pdf2image 库")
        except Exception as e:
            raise Exception(f"PDF 转图片失败: {e}")

    def _image_file_to_array(self, image_path: str) -> np.ndarray:
        """图片转数组"""
        try:
            image = Image.open(image_path).convert('RGB')
            return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        except Exception as e:
            raise Exception(f"图片读取失败: {e}")

    def _preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """图像预处理 - 仅做对比度增强，不做二值化（会破坏OCR）"""
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            # 自适应对比度增强
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            # 返回增强后的灰度图转BGR
            return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        except:
            return image

    def _extract_from_filename(self, stem: str) -> dict:
        """从文件名中提取发票信息作为补充（当OCR识别不全时使用）
        常见格式: dzfp_263220000004482288706_上海松鼠创科技术有限责任公司_20260604112321
        """
        result = {}
        parts = stem.split('_')

        for part in parts:
            # 提取发票号：15-25位纯数字
            if re.match(r'^\d{15,25}$', part):
                result.setdefault('invoice_number', part)
            # 提取日期：8位紧凑格式 20260604，严格月日校验
            elif re.match(r'^20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d*$', part):
                date_str = part[:8]
                y, m, d = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
                if 1 <= m <= 12 and 1 <= d <= 31:
                    result.setdefault('date', f"{y:04d}-{m:02d}-{d:02d}")
            # 提取公司名：包含中文且带有企业词尾
            elif re.search(r'[\u4e00-\u9fa5]', part) and len(part) >= 4:
                result.setdefault('buyer', part)

        return result

    def extract(self, file_path: str) -> dict:
        """提取发票信息 - 支持多个OCR引擎降级"""
        try:
            file_path = Path(file_path)
            ext = file_path.suffix.lower()

            if ext == ".pdf":
                image_array = self._pdf_to_image(str(file_path))
            else:
                image_array = self._image_file_to_array(str(file_path))

            if image_array is None or image_array.size == 0:
                raise Exception("无法读取图像")

            image_array = self._preprocess_image(image_array)

            # 尝试 EasyOCR
            results = self.reader.readtext(image_array, detail=0)
            if not results:
                raise Exception("OCR 无法识别")

            text = '\n'.join(results)

            fields = self._extract_fields(text)

            # 如果关键字段缺失，尝试从文件名补充
            filename_fields = self._extract_from_filename(file_path.stem)
            for key in ('date', 'invoice_number', 'buyer', 'supplier'):
                if not fields.get(key) and filename_fields.get(key):
                    fields[key] = filename_fields[key]

            # 保留原始 OCR 文本供前端调试展示
            fields['_raw_text'] = text
            return fields
        except Exception as e:
            raise Exception(f"提取失败: {e}")

    def _extract_fields(self, text: str) -> dict:
        """字段提取 - 改进版，优先用标签，备选用通用模式"""
        result = {
            "date": None,
            "invoice_number": None,
            "buyer": None,
            "supplier": None,
            "amount": None,        # 价税合计
            "tax_free_amount": None,  # 合计金额（不含税）
            "tax_amount": None,    # 合计税额
        }

        # === 日期 ===
        # 优先匹配有明确标签的日期，再匹配年月日格式，最后才用紧凑数字（严格校验避免误匹配发票号）
        date_patterns = [
            # 有标签：开票日期 / 日期 后面跟年月日
            (r'(?:开票日期|日期)[：:\s]*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', 1, 2, 3),
            # 标准年月日
            (r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', 1, 2, 3),
            # 分隔符格式 2026-06-04 / 2026/06/04
            (r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', 1, 2, 3),
            # 紧凑格式：严格要求月份01-12，日期01-31，且前后不是数字（避免匹配发票号）
            (r'(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)', 1, 2, 3),
        ]
        for entry in date_patterns:
            pattern, gi, gm, gd = entry
            match = re.search(pattern, text)
            if match:
                try:
                    y = int(match.group(gi))
                    m = int(match.group(gm))
                    d = int(match.group(gd))
                    if 1900 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                        result["date"] = f"{y:04d}-{m:02d}-{d:02d}"
                        break
                except:
                    pass

        # === 发票号 ===
        # 支持多种格式：纯数字、含字母（如 O 代替 0）、含括号等
        inv_patterns = [
            r'(?:发票号|号码)[：\s]*([A-Z0-9\)\(]{15,})',  # 发票号标签 + 字母数字
            r'(?:发票号|号码)[：\s]*([0-9\)\(]{10,})',  # 发票号标签 + 纯数字（可能含括号）
            r'[A-Z0-9]{15,}',  # 任意15+位字母数字
            r'\d{15,25}',  # 任意15-25位纯数字（支持更长的全电发票号）
        ]
        for pattern in inv_patterns:
            if 'A-Z' in pattern or '(' in pattern or ')' in pattern:
                match = re.search(pattern, text)
                if match:
                    raw = match.group(1) if '(' in pattern else match.group(0)
                    clean = re.sub(r'[^\dA-Z0-9]', '', raw)
                    if 13 <= len(clean) <= 25:
                        result["invoice_number"] = clean
                        break
            else:
                matches = re.findall(pattern, text)
                if matches:
                    result["invoice_number"] = matches[0]
                    break

        # === 购买方和销售方 ===
        def clean_company(name):
            """清理公司名：截断噪音、校验合法性"""
            if not name:
                return None
            name = name.strip()
            # 同行存在另一方标签时截断（如 "A公司 销售方名称: B公司"）
            name = re.split(r'(?:销售方|购买方)\s*名称', name)[0]
            # 截断在纳税人/地址/开户/电话等发票字段标签处
            name = re.split(r'(?:纳税人|识别号|地址[、，,]|开户|电话|统一社会)', name)[0]
            # 去掉末尾长数字串（税号等）
            name = re.sub(r'\s*\d{8,}.*$', '', name)
            # 去掉末尾标点/空白
            name = re.sub(r'[：:\s，,。.]+$', '', name).strip()
            # 必须含中文
            if not re.search(r'[\u4e00-\u9fa5]', name):
                return None
            if len(name) < 2 or len(name) > 60:
                return None
            return name

        # 策略0: 同行双名称（OCR 将两列合并到一行，如 "名称: A公司  名称: B公司"）
        same_line_both = re.search(
            r'[1l名]称[：:]\s*(.+?)\s{2,}[1l名]称[：:]\s*([^\n]+)', text)

        # 策略0b: "购买方名称: A公司 销售方名称: B公司" 同行完整格式
        explicit_both = re.search(
            r'购买方\s*名称[：:]\s*(.+?)\s+销售方\s*名称[：:]\s*([^\n]+)', text)

        # 策略1: 购买方——多模式，允许标签和名称之间有换行
        _buyer_pats = [
            r'购买方\s*名称[：:]\s*([^\n]+)',
            r'购\s*买\s*方[^\n]{0,8}\n[^\n]{0,5}名称[：:]\s*([^\n]+)',
            r'(?:购买方|购\s*方|买\s*方)[^\n]{0,30}?[1l名]称[：:]\s*([^\n]+)',
        ]
        buyer_raw = None
        for p in _buyer_pats:
            m = re.search(p, text, re.MULTILINE)
            if m:
                buyer_raw = m.group(1)
                break

        # 策略1: 销售方——多模式，允许标签和名称之间有换行
        _supplier_pats = [
            r'销售方\s*名称[：:]\s*([^\n]+)',
            r'销\s*售\s*方[^\n]{0,8}\n[^\n]{0,5}名称[：:]\s*([^\n]+)',
            r'(?:销售方|销\s*方|卖\s*方)[^\n]{0,30}?[1l名]称[：:]\s*([^\n]+)',
            # 兜底：识别"销"后跟任意空白再跟名称
            r'销[^\n]{0,20}?名称[：:]\s*([^\n]+)',
        ]
        supplier_raw = None
        for p in _supplier_pats:
            m = re.search(p, text, re.MULTILINE)
            if m:
                supplier_raw = m.group(1)
                break

        # 优先级：explicit_both > 策略1个别匹配 > same_line_both
        if explicit_both:
            if not buyer_raw:
                buyer_raw = explicit_both.group(1)
            if not supplier_raw:
                supplier_raw = explicit_both.group(2)

        if buyer_raw:
            result["buyer"] = clean_company(buyer_raw)
        if supplier_raw:
            result["supplier"] = clean_company(supplier_raw)

        # 策略0回填（同行双名称）
        if same_line_both and (not result["buyer"] or not result["supplier"]):
            b = clean_company(same_line_both.group(1))
            s = clean_company(same_line_both.group(2))
            if b and not result["buyer"]:
                result["buyer"] = b
            if s and not result["supplier"]:
                result["supplier"] = s

        # 策略2: 按出现顺序收集所有 "名称:" 内容，补全缺失的一方
        # （条件修正为 OR：任意一方缺失就运行）
        if not result["buyer"] or not result["supplier"]:
            company_lines = []
            for pat in (r'[1l名]称[：:]\s*([^\n]+)', r'名称[：:]\s*([^\n]+)'):
                for m in re.findall(pat, text):
                    c = clean_company(m)
                    if c and c not in company_lines:
                        company_lines.append(c)
                if company_lines:
                    break

            # 策略3: 通用企业词尾识别
            if len(company_lines) < 2:
                for m in re.findall(
                    r'[\u4e00-\u9fa5]{2,}(?:公司|有限|分公司|集团|股份|企业|'
                    r'研究所|医院|学校|协会|中心|局|院|所|厂|部)', text
                ):
                    c = clean_company(m)
                    if c and c not in company_lines:
                        company_lines.append(c)

            if not result["buyer"] and len(company_lines) >= 1:
                result["buyer"] = company_lines[0]
            if not result["supplier"]:
                buyer_val = result.get("buyer")
                for c in company_lines:
                    if c != buyer_val:
                        result["supplier"] = c
                        break

        def _find_amount(patterns, text):
            """通用金额提取，返回匹配到的第一个有效金额字符串"""
            for pattern in patterns:
                matches = re.findall(pattern, text)
                if matches:
                    try:
                        vals = []
                        for m in matches:
                            if isinstance(m, tuple):
                                vals.append(f"{m[0]}.{m[1]}")
                            else:
                                vals.append(str(m))
                        best = str(max(float(v) for v in vals if v))
                        # 格式化为两位小数
                        return f"{float(best):.2f}"
                    except:
                        pass
            return None

        # === 价税合计（总金额）===
        # 优先找"价税合计(小写)"标签后面的数字
        total_patterns = [
            r'价税合计[^0-9\n]{0,10}小写[）)]*\s*[垒¥￥垩圓Y]?\s*([0-9]{1,10}\.[0-9]{2})',
            r'小写[）)]*\s*[垒¥￥垩圓Y]?\s*([0-9]{1,10}\.[0-9]{2})',
            r'价税合计[^0-9\n]{0,20}([0-9]{1,10}\.[0-9]{2})',
            r'[¥￥垩圓Y垒]\s*([0-9]{1,10}\.[0-9]{2})',
            r'([0-9]{1,10}\.[0-9]{2})',
        ]
        result["amount"] = _find_amount(total_patterns, text)

        # === 合计金额（不含税）+ 税额 ===
        # OCR 经常丢失¥符号或将其误读为"半/垩/垒"，也经常拆散表格行。
        # 最可靠的方法：找所有小数，找两个加起来约等于价税合计的组合。

        # 策略1：标签直接匹配（有标签时最精准）
        amtpat = r'[¥￥垒垩圓Y半]?\s*([0-9]{1,10}\.[0-9]{2})'
        # 合计行两列同行
        two_num = re.search(r'合计\s*' + amtpat + r'[\s\n]+' + amtpat, text)
        if two_num:
            result["tax_free_amount"] = f"{float(two_num.group(1)):.2f}"
            result["tax_amount"]      = f"{float(two_num.group(2)):.2f}"
        else:
            # 带税额标签
            m = re.search(r'(?:合计税额|税\s*额)[：:\s]*' + amtpat, text)
            if m:
                result["tax_amount"] = f"{float(m.group(1)):.2f}"
            m = re.search(r'(?:不含税|合计金额)[：:\s]*' + amtpat, text)
            if m:
                result["tax_free_amount"] = f"{float(m.group(1)):.2f}"

        # 策略2：若标签匹配失败，用"配对加法"推断
        # 找所有小数（排除价税合计本身），看哪两个之和约等于总金额
        if result["amount"] and (not result["tax_free_amount"] or not result["tax_amount"]):
            try:
                total = float(result["amount"])
                # 提取文本中所有 x.xx 格式小数，去掉和总价相同的
                candidates = list(dict.fromkeys(
                    float(m) for m in re.findall(r'\b(\d{1,8}\.\d{2})\b', text)
                    if abs(float(m) - total) > 0.01 and float(m) > 0
                ))
                found = False
                for i, a in enumerate(candidates):
                    for b in candidates[i+1:]:
                        if abs(a + b - total) <= 0.05:   # 允许0.05元的舍入误差
                            bigger  = max(a, b)
                            smaller = min(a, b)
                            result["tax_free_amount"] = f"{bigger:.2f}"
                            result["tax_amount"]      = f"{smaller:.2f}"
                            found = True
                            break
                    if found:
                        break
            except:
                pass

        # 策略3：只知道其中一个，推算另一个
        if result["amount"] and result["tax_free_amount"] and not result["tax_amount"]:
            try:
                tax = round(float(result["amount"]) - float(result["tax_free_amount"]), 2)
                if tax > 0:
                    result["tax_amount"] = f"{tax:.2f}"
            except:
                pass
        if result["amount"] and result["tax_amount"] and not result["tax_free_amount"]:
            try:
                base = round(float(result["amount"]) - float(result["tax_amount"]), 2)
                if base > 0:
                    result["tax_free_amount"] = f"{base:.2f}"
            except:
                pass

        return result


def generate_filename(data: dict, original_ext: str) -> str:
    """生成发票新文件名"""
    date = data.get('date') or '0000-01-01'
    invoice_num = data.get('invoice_number') or '000000'
    buyer = (data.get('buyer') or '')[:20]
    supplier = (data.get('supplier') or '')[:20]
    amount = data.get('amount') or '0.00'

    new_name = f"{date}_{invoice_num}_{buyer}_{supplier}_{amount}元{original_ext}"
    new_name = re.sub(r'[\\/:*?"<>|]', '', new_name)
    new_name = re.sub(r'_+', '_', new_name).strip('_')
    return new_name or f"invoice_{datetime.now().strftime('%Y%m%d%H%M%S')}{original_ext}"


def generate_train_filename(data: dict, original_ext: str) -> str:
    """生成火车票新文件名: 日期_车次_出发站-到达站_姓名_票价元.ext"""
    date      = data.get('date') or '0000-01-01'
    train     = data.get('train_number') or '000'
    from_st   = (data.get('from_station') or '')[:10]
    to_st     = (data.get('to_station') or '')[:10]
    name      = (data.get('passenger_name') or '')[:10]
    price     = data.get('price') or '0.00'
    route     = f"{from_st}-{to_st}" if (from_st or to_st) else ''
    parts     = [p for p in [date, train, route, name, f"{price}元"] if p]
    new_name  = '_'.join(parts) + original_ext
    new_name  = re.sub(r'[\\/:*?"<>|]', '', new_name)
    new_name  = re.sub(r'_+', '_', new_name).strip('_')
    return new_name or f"train_{datetime.now().strftime('%Y%m%d%H%M%S')}{original_ext}"


# ======================== 火车票提取 ========================

# 火车票关键词集合（用于类型检测）
_TRAIN_KEYWORDS = re.compile(
    r'车\s*次|检\s*票|候\s*车|动\s*车|高\s*铁|火\s*车\s*票|硬\s*卧|软\s*卧|硬\s*座|'
    r'二\s*等\s*座|一\s*等\s*座|商\s*务\s*座|无\s*座|出\s*发\s*站|到\s*达\s*站|'
    r'网络购票|铁路电子客票|中国铁路|12306|票价[：:\s]*[¥￥]?\d'
)
_TRAIN_NUMBER_RE = re.compile(r'\b([GDTZKCY]\d{1,4})\b')


def detect_doc_type(text: str) -> str:
    """根据 OCR 文本判断是火车票还是发票，返回 'train' 或 'invoice'"""
    if _TRAIN_KEYWORDS.search(text):
        return 'train'
    if _TRAIN_NUMBER_RE.search(text):
        return 'train'
    return 'invoice'


class TrainTicketExtractor:
    """火车票 OCR 字段提取"""

    # 站名结尾词（帮助识别站名）
    _STATION_SUFFIX = re.compile(r'[\u4e00-\u9fa5]{2,8}(?:站|虹桥|南|北|东|西|高铁)?')
    # 座位类型
    _SEAT_TYPES = ['商务座', '特等座', '一等座', '二等座', '软卧上', '软卧下', '硬卧上', '硬卧中',
                   '硬卧下', '软卧', '硬卧', '硬座', '无座', '动卧']

    def __init__(self, reader):
        self.reader = reader

    # 复用 InvoiceExtractor 的图像读取方法（避免重复）
    def _pdf_to_image(self, pdf_path):
        try:
            from pdf2image import convert_from_path
            images = convert_from_path(pdf_path, first_page=1, last_page=1, dpi=200)
            if not images:
                raise Exception("PDF 无法转换")
            return cv2.cvtColor(np.array(images[0]), cv2.COLOR_RGB2BGR)
        except ImportError:
            raise Exception("缺少 pdf2image 库")
        except Exception as e:
            raise Exception(f"PDF 转图片失败: {e}")

    def _image_file_to_array(self, image_path):
        try:
            image = Image.open(image_path).convert('RGB')
            return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        except Exception as e:
            raise Exception(f"图片读取失败: {e}")

    def _preprocess(self, image):
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            return cv2.cvtColor(clahe.apply(gray), cv2.COLOR_GRAY2BGR)
        except:
            return image

    def extract(self, file_path: str) -> dict:
        file_path = Path(file_path)
        ext = file_path.suffix.lower()
        if ext == '.pdf':
            img = self._pdf_to_image(str(file_path))
        else:
            img = self._image_file_to_array(str(file_path))
        if img is None or img.size == 0:
            raise Exception("无法读取图像")
        img = self._preprocess(img)
        results = self.reader.readtext(img, detail=0)
        if not results:
            raise Exception("OCR 无法识别")
        text = '\n'.join(results)
        fields = self._extract_fields(text, str(file_path.stem))
        fields['_raw_text'] = text
        return fields

    def _extract_fields(self, text: str, stem: str = '') -> dict:
        result = {
            'date': None,
            'train_number': None,
            'from_station': None,
            'to_station': None,
            'passenger_name': None,
            'seat': None,
            'seat_type': None,
            'price': None,
            'depart_time': None,
        }

        # === 车次 ===
        m = _TRAIN_NUMBER_RE.search(text)
        if m:
            result['train_number'] = m.group(1)

        # === 日期 ===
        date_pats = [
            (r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', 1, 2, 3),
            (r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', 1, 2, 3),
            (r'(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)', 1, 2, 3),
        ]
        for pat, gi, gm, gd in date_pats:
            m = re.search(pat, text)
            if m:
                try:
                    y, mo, d = int(m.group(gi)), int(m.group(gm)), int(m.group(gd))
                    if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                        result['date'] = f'{y:04d}-{mo:02d}-{d:02d}'
                        break
                except:
                    pass

        # === 出发时间 ===
        m = re.search(r'(\d{2}):(\d{2})(?::(\d{2}))?', text)
        if m:
            result['depart_time'] = m.group(0)[:5]

        # === 出发站 / 到达站 ===
        # 优先：标签法
        from_m = re.search(r'(?:出发站|始发站)[：:\s]*([^\n\s→\-]{2,8})', text)
        to_m   = re.search(r'(?:到达站|终到站|目的地)[：:\s]*([^\n\s→\-]{2,8})', text)
        if from_m:
            result['from_station'] = from_m.group(1).strip()
        if to_m:
            result['to_station'] = to_m.group(1).strip()

        # 备用：箭头/横线分隔  上海虹桥→北京南  或  上海虹桥-北京南
        if not result['from_station'] or not result['to_station']:
            arrow = re.search(
                r'([\u4e00-\u9fa5]{2,8}(?:站)?)\s*[→\-\—]\s*([\u4e00-\u9fa5]{2,8}(?:站)?)',
                text)
            if arrow:
                result.setdefault('from_station', arrow.group(1).strip()) or \
                    result.update({'from_station': arrow.group(1).strip()})
                result.setdefault('to_station', arrow.group(2).strip()) or \
                    result.update({'to_station': arrow.group(2).strip()})
                if not result['from_station']:
                    result['from_station'] = arrow.group(1).strip()
                if not result['to_station']:
                    result['to_station'] = arrow.group(2).strip()

        # 备用：连续中文（X站 Y站）
        if not result['from_station'] or not result['to_station']:
            stations = re.findall(r'([\u4e00-\u9fa5]{2,8}站)', text)
            if len(stations) >= 2 and not result['from_station']:
                result['from_station'] = stations[0].rstrip('站') if stations[0].endswith('站') else stations[0]
                result['to_station']   = stations[1].rstrip('站') if stations[1].endswith('站') else stations[1]
            elif len(stations) == 1 and not result['from_station']:
                result['from_station'] = stations[0].rstrip('站')

        # === 乘客姓名 ===
        name_m = re.search(r'(?:姓\s*名|旅\s*客)[：:\s]*([\u4e00-\u9fa5]{2,4})', text)
        if name_m:
            result['passenger_name'] = name_m.group(1)
        else:
            # 备用：身份证号前面通常是姓名（2-4个中文字）
            id_m = re.search(r'([\u4e00-\u9fa5]{2,4})\s*\d{15,18}', text)
            if id_m:
                result['passenger_name'] = id_m.group(1)

        # === 座位类型 ===
        for st in self._SEAT_TYPES:
            if st in text:
                result['seat_type'] = st
                break

        # === 座位号 ===
        seat_m = re.search(r'(\d{1,2}\s*车\s*\d{1,2}[A-F号]?)', text)
        if seat_m:
            result['seat'] = seat_m.group(1).strip()

        # === 票价 ===
        # 优先：票价标签
        price_m = re.search(r'(?:票\s*价|价\s*格|金\s*额)[：:\s]*[¥￥]?\s*(\d+\.?\d*)', text)
        if price_m:
            result['price'] = f"{float(price_m.group(1)):.2f}"
        else:
            # 备用：¥ + 数字
            price_m = re.search(r'[¥￥]\s*(\d+\.?\d*)', text)
            if price_m:
                result['price'] = f"{float(price_m.group(1)):.2f}"
            else:
                # 备用：文中最大 x.x 小数（票价通常是最大金额）
                decimals = [float(x) for x in re.findall(r'\b(\d{1,5}\.\d{1,2})\b', text)]
                if decimals:
                    result['price'] = f"{max(decimals):.2f}"

        # === 从文件名补充 ===
        parts = stem.split('_')
        for part in parts:
            tn = _TRAIN_NUMBER_RE.match(part)
            if tn and not result['train_number']:
                result['train_number'] = tn.group(1)

        return result


# ======================== Flask 应用 ========================

app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

READER = None
READER_READY = False
UPLOAD_RESULTS = {}

# ---- 全局批次 (单服务器单用户) ----
BATCH_RESULTS = []        # 累积的所有 result 条目
BATCH_SESSION_DIRS = []   # 累积的 session 目录（用于下载 ZIP）
BATCH_LOCK = threading.Lock()


def init_ocr_background():
    """后台初始化 OCR"""
    global READER, READER_READY
    try:
        print("正在初始化 OCR 模型...")
        READER = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
        READER_READY = True
        print("✅ OCR 模型已准备好")
    except Exception as e:
        print(f"❌ OCR 初始化失败: {e}")
        READER_READY = False


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status')
def status():
    """获取 OCR 准备状态"""
    return jsonify({
        'ready': READER_READY,
        'message': 'OCR 已准备好' if READER_READY else '正在初始化 OCR 模型，请稍候...'
    })


def smart_extract(file_path: str, reader) -> tuple:
    """OCR 一次，自动判断发票或火车票，返回 (data_dict, doc_type)"""
    inv  = InvoiceExtractor(reader)
    train = TrainTicketExtractor(reader)

    fp  = Path(file_path)
    ext = fp.suffix.lower()

    if ext == '.pdf':
        img = inv._pdf_to_image(str(fp))
    else:
        img = inv._image_file_to_array(str(fp))

    if img is None or img.size == 0:
        raise Exception("无法读取图像")

    img     = inv._preprocess_image(img)
    results = reader.readtext(img, detail=0)
    if not results:
        raise Exception("OCR 无法识别")

    text     = '\n'.join(results)
    doc_type = detect_doc_type(text)

    if doc_type == 'train':
        fields = train._extract_fields(text, fp.stem)
    else:
        fields = inv._extract_fields(text)
        # 从文件名补充发票缺失字段
        fn_fields = inv._extract_from_filename(fp.stem)
        for key in ('date', 'invoice_number', 'buyer', 'supplier'):
            if not fields.get(key) and fn_fields.get(key):
                fields[key] = fn_fields[key]

    fields['_raw_text'] = text
    return fields, doc_type


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """处理上传 - 保存文件，识别，重命名"""
    try:
        if 'files[]' not in request.files:
            return jsonify({'error': '未选择文件'}), 400

        if not READER_READY:
            return jsonify({'error': 'OCR 还未初始化，请稍候片刻后重试'}), 503

        files = request.files.getlist('files[]')
        reader = READER
        if reader is None:
            return jsonify({'error': 'OCR 初始化失败'}), 500

        results = []

        session_id  = datetime.now().strftime('%Y%m%d%H%M%S%f')[-15:]
        session_dir = os.path.join(tempfile.gettempdir(), f'invoice_session_{session_id}')
        os.makedirs(session_dir, exist_ok=True)

        for file in files:
            if file.filename == '':
                continue

            ext = Path(file.filename).suffix.lower()
            allowed_ext = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.zip'}

            if ext not in allowed_ext:
                results.append({'filename': file.filename, 'status': 'error', 'error': '不支持的格式'})
                continue

            safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
            temp_path = os.path.join(tempfile.gettempdir(), f"{session_id}_{safe_name}")
            file.save(temp_path)

            try:
                if ext == '.zip':
                    zip_results = process_zip_file(temp_path, reader, session_dir)
                    results.extend(zip_results)
                else:
                    data, doc_type = smart_extract(temp_path, reader)
                    new_name = (generate_train_filename(data, ext)
                                if doc_type == 'train'
                                else generate_filename(data, ext))

                    new_path = os.path.join(session_dir, new_name)
                    shutil.copy(temp_path, new_path)

                    results.append({
                        'filename': file.filename,
                        'new_name': new_name,
                        'data': data,
                        'doc_type': doc_type,
                        'status': 'success'
                    })
            except Exception as e:
                results.append({'filename': file.filename, 'status': 'error', 'error': str(e)})
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        UPLOAD_RESULTS[session_id] = {'results': results, 'session_dir': session_dir}
        return jsonify({'session_id': session_id, 'total': len(results), 'results': results})

    except Exception as e:
        return jsonify({'error': f'处理失败: {str(e)}'}), 500


def process_zip_file(zip_path: str, reader, session_dir: str) -> list:
    """处理 ZIP 文件，自动识别发票/火车票"""
    results      = []
    temp_extract = tempfile.mkdtemp()

    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(temp_extract)

        allowed_ext = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif'}

        for root, dirs, files in os.walk(temp_extract):
            for file in sorted(files):
                ext = Path(file).suffix.lower()
                if ext in allowed_ext:
                    file_path = os.path.join(root, file)
                    try:
                        data, doc_type = smart_extract(file_path, reader)
                        new_name = (generate_train_filename(data, ext)
                                    if doc_type == 'train'
                                    else generate_filename(data, ext))

                        new_path = os.path.join(session_dir, new_name)
                        shutil.copy(file_path, new_path)

                        results.append({
                            'filename': file,
                            'new_name': new_name,
                            'data': data,
                            'doc_type': doc_type,
                            'status': 'success'
                        })
                    except Exception as e:
                        results.append({'filename': file, 'status': 'error', 'error': str(e)})
    finally:
        shutil.rmtree(temp_extract, ignore_errors=True)

    return results


@app.route('/api/download/<session_id>', methods=['GET'])
def download_results(session_id):
    """下载重命名后的发票文件（ZIP 格式）"""
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400

    session_data = UPLOAD_RESULTS[session_id]
    session_dir = session_data['session_dir']
    results = session_data['results']

    if not os.path.exists(session_dir):
        return jsonify({'error': '文件已过期'}), 400

    # 创建 ZIP 文件，包含所有重命名后的文件
    output_zip = io.BytesIO()

    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        # 添加成功识别的文件
        for item in results:
            if item['status'] == 'success':
                new_name = item['new_name']
                file_path = os.path.join(session_dir, new_name)
                if os.path.exists(file_path):
                    z.write(file_path, arcname=new_name)

    output_zip.seek(0)

    return send_file(
        output_zip,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'发票重命名_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
    )


def _build_csv_bytes(results: list) -> bytes:
    """将 result 列表序列化为带 BOM 的 UTF-8 CSV bytes（支持发票 + 火车票）"""
    import csv
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow([
        '原文件名', '新文件名', '状态', '类型', '日期',
        # 发票专属
        '发票号码', '购买方', '销售方', '金额（不含税）', '税额', '价税合计',
        # 火车票专属
        '车次', '出发站', '到达站', '乘客姓名', '座位', '座位类型', '票价',
        '错误信息'
    ])

    total_invoice = total_train = 0.0
    success_count = fail_count = 0

    for item in results:
        if item['status'] == 'success':
            d        = item.get('data') or {}
            dtype    = item.get('doc_type', 'invoice')
            is_train = dtype == 'train'

            if is_train:
                price = d.get('price', '')
                writer.writerow([
                    item.get('filename', ''), item.get('new_name', ''), '成功', '🚄 火车票',
                    d.get('date', ''),
                    '', '', '', '', '', '',          # 发票列留空
                    d.get('train_number', ''),
                    d.get('from_station', ''),
                    d.get('to_station', ''),
                    d.get('passenger_name', ''),
                    d.get('seat', ''),
                    d.get('seat_type', ''),
                    price, ''
                ])
                try: total_train += float(price) if price else 0
                except ValueError: pass
            else:
                tax_free = d.get('tax_free_amount', '')
                tax      = d.get('tax_amount', '')
                amount   = d.get('amount', '')
                writer.writerow([
                    item.get('filename', ''), item.get('new_name', ''), '成功', '🧾 发票',
                    d.get('date', ''),
                    d.get('invoice_number', ''), d.get('buyer', ''), d.get('supplier', ''),
                    tax_free, tax, amount,
                    '', '', '', '', '', '', '',      # 火车票列留空
                    ''
                ])
                try: total_invoice += float(amount) if amount else 0
                except ValueError: pass

            success_count += 1
        else:
            writer.writerow([
                item.get('filename', ''), '', '失败', '', '',
                '', '', '', '', '', '',
                '', '', '', '', '', '', '',
                item.get('error', '')
            ])
            fail_count += 1

    writer.writerow([])
    writer.writerow([
        f'合计（成功 {success_count} 张，失败 {fail_count} 张）',
        '', '', '', '',
        '', '', '', '', '', f'{total_invoice:.2f}',
        '', '', '', '', '', '', f'{total_train:.2f}',
        ''
    ])
    return output.getvalue().encode('utf-8-sig')


def _build_excel_bytes(results: list, title: str = '识别结果汇总') -> bytes:
    """将 result 列表序列化为带格式的 .xlsx bytes（支持发票 + 火车票）"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '汇总表'

    # ---- 颜色 & 样式常量 ----
    C_INVOICE_HDR = 'FF276749'   # 深绿（发票表头）
    C_TRAIN_HDR   = 'FF2B6CB0'   # 深蓝（火车票表头）
    C_META_HDR    = 'FF4A5568'   # 深灰（公共表头）
    C_INVOICE_ROW = 'FFE6F4EA'   # 浅绿（发票行底色）
    C_TRAIN_ROW   = 'FFE8F0FE'   # 浅蓝（火车票行底色）
    C_FAIL_ROW    = 'FFFFF3CD'   # 浅黄（失败行底色）
    C_SUM_ROW     = 'FFFFF8E1'   # 浅橙（汇总行底色）
    WHITE         = 'FFFFFFFF'

    thin = Side(style='thin', color='FFCCCCCC')
    bdr  = Border(left=thin, right=thin, top=thin, bottom=thin)

    def hdr_font(color='FFFFFFFF', bold=True):
        return Font(name='微软雅黑', bold=bold, color=color, size=10)

    def cell_font(bold=False, color='FF333333'):
        return Font(name='微软雅黑', bold=bold, color=color, size=9)

    def fill(hex_color):
        return PatternFill('solid', fgColor=hex_color)

    def center():
        return Alignment(horizontal='center', vertical='center', wrap_text=True)

    def left():
        return Alignment(horizontal='left', vertical='center', wrap_text=True)

    # ---- 标题行 ----
    NCOLS = 20
    ws.merge_cells(f'A1:{get_column_letter(NCOLS)}1')
    title_cell = ws['A1']
    title_cell.value     = title
    title_cell.font      = Font(name='微软雅黑', bold=True, size=14, color=WHITE)
    title_cell.fill      = fill('FF4A5568')
    title_cell.alignment = center()
    ws.row_dimensions[1].height = 32

    # ---- 表头（第2行）----
    HEADERS = [
        '原文件名', '新文件名', '状态', '类型', '日期',
        # 发票专属（6列）
        '发票号码', '购买方', '销售方', '金额(不含税)', '税额', '价税合计',
        # 火车票专属（7列）
        '车次', '出发站', '到达站', '乘客姓名', '座位', '座位类型', '票价',
        '错误信息', '备注',
    ]
    # 列宽
    COL_WIDTHS = [26, 36, 8, 10, 12,
                  18, 20, 20, 14, 12, 12,
                  10, 14, 14, 14, 14, 10, 10,
                  20, 8]

    # 表头颜色分组
    HDR_COLORS = {
        1: C_META_HDR, 2: C_META_HDR, 3: C_META_HDR, 4: C_META_HDR, 5: C_META_HDR,
        6: C_INVOICE_HDR, 7: C_INVOICE_HDR, 8: C_INVOICE_HDR,
        9: C_INVOICE_HDR, 10: C_INVOICE_HDR, 11: C_INVOICE_HDR,
        12: C_TRAIN_HDR, 13: C_TRAIN_HDR, 14: C_TRAIN_HDR,
        15: C_TRAIN_HDR, 16: C_TRAIN_HDR, 17: C_TRAIN_HDR, 18: C_TRAIN_HDR,
        19: C_META_HDR, 20: C_META_HDR,
    }
    for col_i, (hdr, width) in enumerate(zip(HEADERS, COL_WIDTHS), start=1):
        c = ws.cell(row=2, column=col_i, value=hdr)
        c.font      = hdr_font(color=WHITE)
        c.fill      = fill(HDR_COLORS.get(col_i, C_META_HDR))
        c.alignment = center()
        c.border    = bdr
        ws.column_dimensions[get_column_letter(col_i)].width = width
    ws.row_dimensions[2].height = 22

    # ---- 数据行 ----
    total_invoice = total_train = 0.0
    success_count = fail_count  = 0
    data_start_row = 3

    for row_i, item in enumerate(results, start=data_start_row):
        is_train = item.get('doc_type') == 'train'
        is_ok    = item['status'] == 'success'
        d        = item.get('data') or {}

        # 行底色
        if not is_ok:
            row_fill = fill(C_FAIL_ROW)
        elif is_train:
            row_fill = fill(C_TRAIN_ROW)
        else:
            row_fill = fill(C_INVOICE_ROW)

        def set_cell(col, val, bold=False, num_fmt=None, align=None):
            c = ws.cell(row=row_i, column=col, value=val)
            c.font      = cell_font(bold=bold)
            c.fill      = row_fill
            c.border    = bdr
            c.alignment = align or left()
            if num_fmt:
                c.number_format = num_fmt

        if is_ok:
            type_label = '火车票' if is_train else '发票'
            set_cell(1, item.get('filename', ''))
            set_cell(2, item.get('new_name', ''), bold=True)
            set_cell(3, '成功', align=center())
            set_cell(4, type_label, align=center())
            set_cell(5, d.get('date', ''), align=center())

            if is_train:
                price_str = d.get('price', '')
                # 发票列留空
                for col in range(6, 12):
                    set_cell(col, '')
                set_cell(12, d.get('train_number', ''), align=center())
                set_cell(13, d.get('from_station', ''))
                set_cell(14, d.get('to_station', ''))
                set_cell(15, d.get('passenger_name', ''))
                set_cell(16, d.get('seat', ''), align=center())
                set_cell(17, d.get('seat_type', ''), align=center())
                try:
                    price_val = float(price_str) if price_str else None
                    c = ws.cell(row=row_i, column=18, value=price_val)
                    c.font = cell_font(); c.fill = row_fill; c.border = bdr
                    c.alignment = center(); c.number_format = '#,##0.00'
                    if price_val: total_train += price_val
                except (ValueError, TypeError):
                    set_cell(18, price_str, align=center())
            else:
                tax_free = d.get('tax_free_amount', '')
                tax      = d.get('tax_amount', '')
                amount   = d.get('amount', '')
                def num_cell(col, val_str):
                    try:
                        v = float(val_str) if val_str else None
                        c = ws.cell(row=row_i, column=col, value=v)
                        c.font = cell_font(); c.fill = row_fill; c.border = bdr
                        c.alignment = center(); c.number_format = '#,##0.00'
                        return v or 0
                    except (ValueError, TypeError):
                        set_cell(col, val_str, align=center())
                        return 0
                set_cell(6,  d.get('invoice_number', ''), align=center())
                set_cell(7,  d.get('buyer', ''))
                set_cell(8,  d.get('supplier', ''))
                num_cell(9,  tax_free)
                num_cell(10, tax)
                total_invoice += num_cell(11, amount)
                # 火车票列留空
                for col in range(12, 19):
                    set_cell(col, '')
            set_cell(19, '')
            set_cell(20, '')
            success_count += 1
        else:
            set_cell(1, item.get('filename', ''))
            for col in range(2, 19):
                set_cell(col, '')
            set_cell(3, '失败', align=center())
            set_cell(19, item.get('error', ''))
            set_cell(20, '')
            fail_count += 1

        ws.row_dimensions[row_i].height = 18

    # ---- 汇总行 ----
    sum_row = data_start_row + len(results)
    ws.merge_cells(f'A{sum_row}:D{sum_row}')
    sc = ws.cell(row=sum_row, column=1,
                 value=f'合计：成功 {success_count} 张，失败 {fail_count} 张')
    sc.font = Font(name='微软雅黑', bold=True, size=10, color='FF333333')
    sc.fill = fill(C_SUM_ROW)
    sc.alignment = center()
    sc.border = bdr

    # 发票合计（第11列）
    inv_sum = ws.cell(row=sum_row, column=11, value=total_invoice)
    inv_sum.font = Font(name='微软雅黑', bold=True, size=10, color=C_INVOICE_HDR[2:])
    inv_sum.fill = fill(C_SUM_ROW); inv_sum.border = bdr
    inv_sum.alignment = center(); inv_sum.number_format = '#,##0.00'

    inv_lbl = ws.cell(row=sum_row, column=10, value='发票总额 ▶')
    inv_lbl.font = Font(name='微软雅黑', bold=True, size=9); inv_lbl.fill = fill(C_SUM_ROW)
    inv_lbl.border = bdr; inv_lbl.alignment = center()

    # 火车票合计（第18列）
    trn_sum = ws.cell(row=sum_row, column=18, value=total_train)
    trn_sum.font = Font(name='微软雅黑', bold=True, size=10, color=C_TRAIN_HDR[2:])
    trn_sum.fill = fill(C_SUM_ROW); trn_sum.border = bdr
    trn_sum.alignment = center(); trn_sum.number_format = '#,##0.00'

    trn_lbl = ws.cell(row=sum_row, column=17, value='火车票总额 ▶')
    trn_lbl.font = Font(name='微软雅黑', bold=True, size=9); trn_lbl.fill = fill(C_SUM_ROW)
    trn_lbl.border = bdr; trn_lbl.alignment = center()

    # 其余汇总格
    for col in [5,6,7,8,9,12,13,14,15,16,19,20]:
        c = ws.cell(row=sum_row, column=col, value='')
        c.fill = fill(C_SUM_ROW); c.border = bdr

    ws.row_dimensions[sum_row].height = 22

    # ---- 冻结首两行 ----
    ws.freeze_panes = 'A3'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


@app.route('/api/export/<session_id>', methods=['GET'])
def export_csv(session_id):
    """导出本次识别结果为 CSV 表格"""
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400
    csv_bytes = _build_csv_bytes(UPLOAD_RESULTS[session_id]['results'])
    return send_file(
        io.BytesIO(csv_bytes),
        mimetype='text/csv; charset=utf-8-sig',
        as_attachment=True,
        download_name=f'发票识别结果_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )


@app.route('/api/export-excel/<session_id>', methods=['GET'])
def export_excel(session_id):
    """导出本次识别结果为 Excel 表格"""
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400
    results = UPLOAD_RESULTS[session_id]['results']
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    xlsx_bytes = _build_excel_bytes(results, title=f'识别结果汇总 — {ts}')
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'发票识别结果_{ts}.xlsx'
    )


# ======================== 批次管理 API ========================

@app.route('/api/update/<session_id>/<int:item_index>', methods=['POST'])
def update_item(session_id, item_index):
    """手动修改识别字段（发票或火车票），并同步重命名实体文件"""
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 404

    results     = UPLOAD_RESULTS[session_id]['results']
    session_dir = UPLOAD_RESULTS[session_id]['session_dir']

    if item_index < 0 or item_index >= len(results):
        return jsonify({'error': '索引越界'}), 400

    item = results[item_index]
    if item['status'] != 'success':
        return jsonify({'error': '只能编辑成功识别的条目'}), 400

    payload      = request.get_json(force=True, silent=True) or {}
    old_new_name = item.get('new_name', '')
    doc_type     = item.get('doc_type', 'invoice')
    d            = item.get('data') or {}

    # 更新字段（发票 + 火车票字段均接受，只更新有值的）
    all_fields = ('date', 'invoice_number', 'buyer', 'supplier',
                  'tax_free_amount', 'tax_amount', 'amount',
                  'train_number', 'from_station', 'to_station',
                  'passenger_name', 'seat', 'seat_type', 'price')
    for field in all_fields:
        if field in payload:
            d[field] = payload[field].strip()
    item['data'] = d

    ext          = os.path.splitext(old_new_name)[1].lower()
    new_new_name = (generate_train_filename(d, ext)
                    if doc_type == 'train'
                    else generate_filename(d, ext))
    item['new_name'] = new_new_name

    old_path = os.path.join(session_dir, old_new_name)
    new_path = os.path.join(session_dir, new_new_name)
    if os.path.exists(old_path) and old_path != new_path:
        try:
            os.rename(old_path, new_path)
        except OSError:
            pass

    results[item_index] = item
    return jsonify({'new_name': new_new_name, 'data': d, 'doc_type': doc_type})


@app.route('/api/batch/add/<session_id>', methods=['POST'])
def batch_add(session_id):
    """把一次会话的结果追加到全局批次"""
    global BATCH_RESULTS, BATCH_SESSION_DIRS
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 404
    session_data = UPLOAD_RESULTS[session_id]
    with BATCH_LOCK:
        BATCH_RESULTS.extend(session_data['results'])
        BATCH_SESSION_DIRS.append(session_data['session_dir'])
        total   = len(BATCH_RESULTS)
        success = sum(1 for r in BATCH_RESULTS if r['status'] == 'success')
    return jsonify({'total': total, 'success': success})


@app.route('/api/batch/status', methods=['GET'])
def batch_status():
    """返回当前批次累积统计"""
    with BATCH_LOCK:
        total   = len(BATCH_RESULTS)
        success = sum(1 for r in BATCH_RESULTS if r['status'] == 'success')
    return jsonify({'total': total, 'success': success})


@app.route('/api/batch/download', methods=['GET'])
def batch_download():
    """下载批次中所有重命名文件的 ZIP"""
    with BATCH_LOCK:
        session_dirs = list(BATCH_SESSION_DIRS)
        results      = list(BATCH_RESULTS)
    if not results:
        return jsonify({'error': '批次为空'}), 404

    zip_buf = io.BytesIO()
    seen: dict = {}
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for sdir in session_dirs:
            if not os.path.isdir(sdir):
                continue
            for fname in os.listdir(sdir):
                fpath = os.path.join(sdir, fname)
                arcname = fname
                if arcname in seen:
                    seen[arcname] += 1
                    stem, ext = os.path.splitext(fname)
                    arcname = f'{stem}_{seen[fname]}{ext}'
                else:
                    seen[arcname] = 1
                zf.write(fpath, arcname)
    zip_buf.seek(0)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(zip_buf, mimetype='application/zip',
                     as_attachment=True,
                     download_name=f'发票批次_{ts}.zip')


@app.route('/api/batch/export', methods=['GET'])
def batch_export():
    """导出批次全部结果为 CSV"""
    with BATCH_LOCK:
        results = list(BATCH_RESULTS)
    if not results:
        return jsonify({'error': '批次为空'}), 404
    csv_bytes = _build_csv_bytes(results)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(io.BytesIO(csv_bytes),
                     mimetype='text/csv; charset=utf-8-sig',
                     as_attachment=True,
                     download_name=f'发票批次汇总_{ts}.csv')


@app.route('/api/batch/export-excel', methods=['GET'])
def batch_export_excel():
    """导出批次全部结果为 Excel"""
    with BATCH_LOCK:
        results = list(BATCH_RESULTS)
    if not results:
        return jsonify({'error': '批次为空'}), 404
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    xlsx_bytes = _build_excel_bytes(results, title=f'批次汇总表 — {ts}')
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'发票批次汇总_{ts}.xlsx'
    )


@app.route('/api/batch/clear', methods=['POST'])
def batch_clear():
    """清空全局批次"""
    global BATCH_RESULTS, BATCH_SESSION_DIRS
    with BATCH_LOCK:
        BATCH_RESULTS = []
        BATCH_SESSION_DIRS = []
    return jsonify({'message': '批次已清空'})


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({'error': '文件过大，最大支持 500MB'}), 413


@app.errorhandler(Exception)
def handle_exception(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    import traceback
    print(f"未处理异常: {traceback.format_exc()}")
    return jsonify({'error': f'服务器内部错误: {str(e)}'}), 500


@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': '服务器内部错误，请重试'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'

    print(f"\n{'='*60}")
    print("发票批量重命名工具")
    print(f"{'='*60}")
    print(f"访问地址: http://127.0.0.1:{port}")
    print(f"{'='*60}\n")

    ocr_thread = threading.Thread(target=init_ocr_background, daemon=True)
    ocr_thread.start()

    try:
        app.run(host='0.0.0.0', port=port, debug=debug, threaded=True, use_reloader=False)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"\n❌ 错误: 端口 {port} 已被占用")
        else:
            print(f"\n❌ 启动失败: {e}")
    except KeyboardInterrupt:
        print("\n已停止服务")
