#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发票批量重命名工具 - 鲁棒增强版
支持：发票（含住宿/打车）、火车票、飞机票、网约车、合同（Word/PDF/图片）
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
from openpyxl.styles import (Font, PatternFill, Alignment, Border, Side)
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
    """发票 OCR 提取（增强销售方和类型识别）"""

    def __init__(self, reader):
        self.reader = reader

    def _pdf_to_image(self, pdf_path: str) -> np.ndarray:
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
        try:
            image = Image.open(image_path).convert('RGB')
            return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        except Exception as e:
            raise Exception(f"图片读取失败: {e}")

    def _preprocess_image(self, image: np.ndarray) -> np.ndarray:
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
        except:
            return image

    def _extract_from_filename(self, stem: str) -> dict:
        result = {}
        parts = stem.split('_')
        for part in parts:
            if re.match(r'^\d{15,25}$', part):
                result.setdefault('invoice_number', part)
            elif re.match(r'^20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d*$', part):
                date_str = part[:8]
                y, m, d = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
                if 1 <= m <= 12 and 1 <= d <= 31:
                    result.setdefault('date', f"{y:04d}-{m:02d}-{d:02d}")
            elif re.search(r'[\u4e00-\u9fa5]', part) and len(part) >= 4:
                result.setdefault('buyer', part)
        return result

    def extract(self, file_path: str) -> dict:
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
            results = self.reader.readtext(image_array, detail=0)
            if not results:
                raise Exception("OCR 无法识别")
            text = '\n'.join(results)
            fields = self._extract_fields(text)
            # 从文件名补充
            filename_fields = self._extract_from_filename(file_path.stem)
            for key in ('date', 'invoice_number', 'buyer', 'supplier'):
                if not fields.get(key) and filename_fields.get(key):
                    fields[key] = filename_fields[key]
            fields['_raw_text'] = text
            return fields
        except Exception as e:
            raise Exception(f"提取失败: {e}")

    @staticmethod
    def _normalize_text(text: str) -> str:
        lines = text.split('\n')
        out = []
        i = 0
        while i < len(lines):
            raw = lines[i]
            stripped = raw.strip()
            if i + 1 < len(lines):
                nxt = lines[i + 1].strip()
                nxt_is_name = bool(re.match(r'[名1l][称称][：:﹕]', nxt))
                if nxt_is_name:
                    if re.fullmatch(r'购(?:买方?|方)?(?:信息)?', stripped):
                        out.append('购买方' + nxt)
                        i += 2
                        continue
                    if re.fullmatch(r'销(?:售方?|方)?(?:信息)?', stripped):
                        out.append('销售方' + nxt)
                        i += 2
                        continue
            stripped = re.sub(r'销货单位名称', '销售方名称', stripped)
            stripped = re.sub(r'购货单位名称', '购买方名称', stripped)
            stripped = re.sub(r'销货单位[：:]', '销售方名称：', stripped)
            stripped = re.sub(r'购货单位[：:]', '购买方名称：', stripped)
            out.append(raw[:raw.find(lines[i].lstrip())] + stripped if stripped != raw.strip() else raw)
            i += 1
        return '\n'.join(out)

    def _extract_special_invoice(self, text: str, result: dict) -> bool:
        if re.search(r'机动车销售统一发票|机动车(?:出售|发票)', text):
            result['invoice_type'] = '机动车发票'
            return False
        return False

    def _extract_fields(self, text: str) -> dict:
        text = self._normalize_text(text)
        result = {
            "date": None,
            "invoice_number": None,
            "buyer": None,
            "supplier": None,
            "amount": None,
            "tax_free_amount": None,
            "tax_amount": None,
            "invoice_type": None,
            "subtype": None,
        }

        # === 子类型检测 ===
        if re.search(r'住宿|酒店|宾馆|房费', text):
            result['subtype'] = '住宿费'
        elif re.search(r'出租车|计价器|网约车|T3出行|滴滴|曹操|首汽|美团打车', text):
            result['subtype'] = '打车票'

        # === 日期 ===
        date_patterns = [
            (r'(?:开票日期|日期)[：:\s]*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', 1, 2, 3),
            (r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', 1, 2, 3),
            (r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', 1, 2, 3),
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
        inv_patterns = [
            r'(?:发票号|号码)[：\s]*([A-Z0-9\)\(]{15,})',
            r'(?:发票号|号码)[：\s]*([0-9\)\(]{10,})',
            r'[A-Z0-9]{15,}',
            r'\d{15,25}',
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

        # === 购买方和销售方（增强鲁棒性） ===
        _SUFFIX_ONLY = re.compile(
            r'^(?:有限(?:责任)?公司|股份有限公司|集团公司|有限公司|责任公司|公司)$'
        )
        _LABEL_WORDS = frozenset({
            '名称', '金额', '税额', '地址', '电话', '合计', '税率',
            '备注', '开票人', '识别号', '统一社会', '纳税人', '规格',
            '项目', '单位', '数量', '单价', '信息',
        })
        _GOVT_WORDS = re.compile(
            r'税务[局所]|国家税务|地方税务|稽查局|国税局|地税局|财政局|监察局|'
            r'市场监督|行政管理局|公安局|政府|监制机关|主管税务'
        )

        def clean_company(name):
            if not name:
                return None
            name = name.strip()
            # 截断在可能的后续标签处
            name = re.split(r'(?:纳税人|识别号|地址[、，,]|开户|电话|统一社会|监制机关|主管税务)', name)[0]
            name = re.sub(r'\s*\d{8,}.*$', '', name)
            # 移除括号中的特定词（如个体工商户），但保留公司名主体
            name = re.sub(r'\s*[（(]\s*(?:个体工商户|个人独资|自然人|个人)\s*[）)]', '', name)
            name = re.sub(r'[：:\s，,。.]+$', '', name).strip()
            if not re.search(r'[\u4e00-\u9fa5]', name):
                return None
            if len(name) < 2 or len(name) > 60:
                return None
            if _SUFFIX_ONLY.match(name):
                return None
            if name in _LABEL_WORDS:
                return None
            if _GOVT_WORDS.search(name):
                return None
            return name

        # ---------- 销售方/购买方提取（支持大标题+名称行） ----------
        def _extract_from_section(text, section_label, name_label='名称'):
            """
            在文本中查找 section_label（如“销售方信息”），然后在其后几行内查找“名称”字段。
            返回清理后的名称或None。
            """
            # 先尝试直接匹配“section_label + 名称”
            pat = section_label + r'[^\n]*\n\s*' + name_label + r'[：:\s]*([^\n]+)'
            m = re.search(pat, text)
            if m:
                return clean_company(m.group(1))
            # 再尝试 section_label 后跨多行查找名称
            pos = text.find(section_label)
            if pos == -1:
                return None
            chunk = text[pos:pos+200]  # 取后面200字符
            m2 = re.search(name_label + r'[：:\s]*([^\n]+)', chunk)
            if m2:
                return clean_company(m2.group(1))
            return None

        # 先用标准标签匹配
        buyer_raw = None
        supplier_raw = None

        # 1) 优先使用显式标签（已支持跨行）
        def _multiline_company(text, label_pat):
            m = re.search(label_pat + r'[ \t]*([^\n]*)', text, re.MULTILINE)
            if not m:
                return None
            first = m.group(1).strip()
            rest = text[m.end():]
            next_line = re.match(r'[ \t]*([^\n]{1,40})', rest)
            cont = next_line.group(1).strip() if next_line else ''
            is_incomplete = (
                not first
                or len(first) < 6
                or re.search(r'(?:有限|股份|集团|科技|责任|管理|实业|发展|酒店|宾馆|个体工商户)\s*$', first)
            )
            is_continuation = bool(cont) and bool(
                re.search(r'(?:公司|有限|责任|集团|股份|管理|科技|发展|实业|酒店|宾馆|个体工商户)', cont)
            ) and not re.search(
                r'(?:纳税人|识别号|地址|开户|电话|购买方|销售方|统一社会|信用代码)', cont
            )
            if is_incomplete and is_continuation:
                first = (first + cont).strip()
            elif not first and cont and not re.search(
                r'(?:纳税人|识别号|地址|开户|电话|购买方|销售方)', cont
            ):
                first = cont
            return first or None

        buyer_raw = (
            _multiline_company(text, r'购买方\s*名称[：:]')
            or _multiline_company(text, r'(?:购买方|购\s*方|买\s*方)[^\n]{0,30}?[1l名]称[：:]')
        )
        supplier_raw = (
            _multiline_company(text, r'销售方\s*名称[：:]')
            or _multiline_company(text, r'(?:销售方|销\s*方|卖\s*方)[^\n]{0,30}?[1l名]称[：:]')
            or _multiline_company(text, r'销[^\n]{0,20}?名称[：:]')
            or _multiline_company(text, r'销售单位[：:]')
            or _multiline_company(text, r'销货单位[：:]')
            or _multiline_company(text, r'收款单位[：:]')
        )

        # 2) 若标准标签未提取到，尝试从“销售方信息”“购买方信息”大标题提取
        if not buyer_raw:
            buyer_raw = _extract_from_section(text, '购买方信息', '名称')
        if not supplier_raw:
            supplier_raw = _extract_from_section(text, '销售方信息', '名称')

        if buyer_raw and not result["buyer"]:
            result["buyer"] = clean_company(buyer_raw)
        if supplier_raw and not result["supplier"]:
            result["supplier"] = clean_company(supplier_raw)

        # 3) 同行双名称策略（“名称: A公司  名称: B公司”）
        same_line_both = re.search(
            r'[1l名]称[：:]\s*(.+?)\s{2,}[1l名]称[：:]\s*([^\n]+)', text)
        if same_line_both and (not result["buyer"] or not result["supplier"]):
            b = clean_company(same_line_both.group(1))
            s = clean_company(same_line_both.group(2))
            if b and not result["buyer"]:
                result["buyer"] = b
            if s and not result["supplier"]:
                result["supplier"] = s

        explicit_both = re.search(
            r'购买方\s*名称[：:]\s*(.+?)\s+销售方\s*名称[：:]\s*([^\n]+)', text)
        if explicit_both:
            if not result["buyer"]:
                result["buyer"] = clean_company(explicit_both.group(1))
            if not result["supplier"]:
                result["supplier"] = clean_company(explicit_both.group(2))

        # 4) 通用公司名提取（兜底）
        if not result["buyer"] or not result["supplier"]:
            company_lines = []
            for pat in (r'[1l名]称[：:][ \t]*([^\n]+)', r'名称[：:][ \t]*([^\n]+)'):
                for m in re.findall(pat, text):
                    c = clean_company(m)
                    if c and c not in company_lines:
                        company_lines.append(c)
                if company_lines:
                    break
            if len(company_lines) < 2:
                text_no_bank = re.sub(r'(?m)^[^\n]*(?:开户行|开户银行|银行账号|账号)[^\n]*$', '', text)
                for mat in re.finditer(
                    r'[\u4e00-\u9fa5]{2,}(?:公司|有限|分公司|集团|股份|企业|'
                    r'研究所|医院|学校|协会|中心|院|所|厂|部|酒店|宾馆|招待所|个体工商户)', text_no_bank
                ):
                    c = clean_company(mat.group(0))
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

        # === 金额提取 ===
        if not result["amount"]:
            total_patterns = [
                r'价税合计[^0-9\n]{0,10}小写[）)]*\s*[垒¥￥垩圓Y]?\s*([0-9]{1,10}\.[0-9]{2})',
                r'小写[）)]*\s*[垒¥￥垩圓Y]?\s*([0-9]{1,10}\.[0-9]{2})',
                r'价税合计[^0-9\n]{0,20}([0-9]{1,10}\.[0-9]{2})',
                r'(?:合计|实付|应付|票价|金额)[：:\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})',
                r'(?:合计|实付|应付|票价|金额)[：:\s]*[¥￥]?\s*([0-9]{1,6}(?:\.[0-9]{1,2})?)',
                r'[¥￥垩圓Y垒]\s*([0-9]{1,10}(?:\.[0-9]{1,2})?)',
                r'(?<![0-9])([0-9]{1,6})\s*元(?:整)?(?![0-9])',
                r'([0-9]{1,10}\.[0-9]{2})',
            ]
            for pattern in total_patterns:
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
                        result["amount"] = f"{float(best):.2f}"
                        break
                    except:
                        pass

        # 不含税金额和税额
        amtpat = r'[¥￥垒垩圓Y半]?\s*([0-9]{1,10}\.[0-9]{2})'
        two_num = re.search(r'合计\s*' + amtpat + r'[\s\n]+' + amtpat, text)
        if two_num:
            result["tax_free_amount"] = f"{float(two_num.group(1)):.2f}"
            result["tax_amount"]      = f"{float(two_num.group(2)):.2f}"
        else:
            m = re.search(r'(?:合计税额|税\s*额)[：:\s]*' + amtpat, text)
            if m:
                result["tax_amount"] = f"{float(m.group(1)):.2f}"
            m = re.search(r'(?:不含税|合计金额)[：:\s]*' + amtpat, text)
            if m:
                result["tax_free_amount"] = f"{float(m.group(1)):.2f}"

        if result["amount"] and (not result["tax_free_amount"] or not result["tax_amount"]):
            try:
                total = float(result["amount"])
                candidates = list(dict.fromkeys(
                    float(m) for m in re.findall(r'\b(\d{1,8}\.\d{2})\b', text)
                    if abs(float(m) - total) > 0.01 and float(m) > 0
                ))
                found = False
                for i, a in enumerate(candidates):
                    for b in candidates[i+1:]:
                        if abs(a + b - total) <= 0.05:
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


# ======================== 交通票据（火车/飞机/网约车）提取 ========================

_TRAIN_KEYWORDS = re.compile(
    r'车\s*次|检\s*票|候\s*车|动\s*车|高\s*铁|火\s*车\s*票|硬\s*卧|软\s*卧|硬\s*座|'
    r'二\s*等\s*座|一\s*等\s*座|商\s*务\s*座|无\s*座|出\s*发\s*站|到\s*达\s*站|'
    r'网络购票|铁路电子客票|中国铁路|12306|票价[：:\s]*[¥￥]?\d|'
    r'列\s*车\s*号|乘\s*车\s*日|席\s*别|始\s*发\s*站|终\s*到\s*站|补\s*票|'
    r'开\s*车\s*时\s*间|出\s*发\s*时\s*间|铁\s*路\s*客\s*票'
)
_TRAIN_NUMBER_RE = re.compile(r'(?<![A-Z\d])([GDTZKCY]\d{1,4})(?!\d)')

_CONTRACT_PARTY_A  = re.compile(r'甲\s*方|买\s*方|委\s*托\s*方|发\s*包\s*方|采\s*购\s*方|招\s*标\s*人')
_CONTRACT_PARTY_B  = re.compile(r'乙\s*方|卖\s*方|承\s*包\s*方|承\s*接\s*方|供\s*货\s*方|中\s*标\s*人')
_CONTRACT_STRONG   = re.compile(
    r'本\s*合\s*同|本\s*协\s*议|合\s*同\s*编\s*号|甲\s*乙\s*双\s*方|买\s*卖\s*双\s*方|'
    r'委\s*托\s*方|发\s*包\s*方|合\s*同\s*金\s*额|合\s*同\s*总\s*额|合\s*同\s*总\s*价|'
    r'平\s*等\s*自\s*愿|协\s*商\s*一\s*致|合\s*同\s*协\s*议\s*书|货\s*物\s*采\s*购\s*合\s*同|'
    r'采\s*购\s*合\s*同|服\s*务\s*合\s*同|工\s*程\s*合\s*同|建\s*设\s*工\s*程\s*合\s*同'
)

_FLIGHT_KEYWORDS = re.compile(
    r'航空运输电子客票|行程单|旅客姓名|航班号|登机|起飞|到达|'
    r'电子客票|民航|飞机票|代订机票'
)
_TRAVEL_KEYWORDS = re.compile(
    r'T3出行|滴滴出行|曹操出行|美团打车|首汽约车|网约车|'
    r'出行日期|出发地|到达地|交通工具类型'
)


def detect_doc_type(text: str) -> str:
    """返回 'travel' / 'flight' / 'train' / 'contract' / 'invoice'"""
    if _TRAVEL_KEYWORDS.search(text):
        return 'travel'
    if _FLIGHT_KEYWORDS.search(text):
        return 'flight'
    if _TRAIN_KEYWORDS.search(text) or _TRAIN_NUMBER_RE.search(text):
        return 'train'
    has_a = bool(_CONTRACT_PARTY_A.search(text))
    has_b = bool(_CONTRACT_PARTY_B.search(text))
    has_s = bool(_CONTRACT_STRONG.search(text))
    if has_s or (has_a and has_b):
        return 'contract'
    return 'invoice'


class TrainTicketExtractor:
    """火车票 / 飞机票 / 网约车 通用提取器"""

    _STATION_SUFFIX = re.compile(r'[\u4e00-\u9fa5]{2,8}(?:站|虹桥|南|北|东|西|高铁)?')
    _SEAT_TYPES = ['商务座', '特等座', '一等座', '二等座', '软卧上', '软卧下', '硬卧上', '硬卧中',
                   '硬卧下', '软卧', '硬卧', '硬座', '无座', '动卧']
    _STATION_BLACKLIST = re.compile(
        r'^(?:出发站|到达站|始发站|终到站|目的地|经由|中转|检票口|候车|开车|出发地|到达地)$'
    )

    def __init__(self, reader):
        self.reader = reader

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

    def _clean_station(self, name: str) -> str:
        name = name.strip()
        if self._STATION_BLACKLIST.match(name):
            return ''
        if name.endswith('站') and len(name) > 2:
            name = name[:-1]
        return name

    def _extract_fields(self, text: str, stem: str = '') -> dict:
        result = {
            'date':           None,
            'train_number':   None,
            'from_station':   None,
            'to_station':     None,
            'passenger_name': None,
            'seat':           None,
            'seat_type':      None,
            'price':          None,
            'depart_time':    None,
        }

        # === 车次 ===
        tn_labeled = re.search(r'(?:车\s*次|列\s*车\s*号)[：:\s]*([GDTZKCY]\d{1,4})', text)
        if tn_labeled:
            result['train_number'] = tn_labeled.group(1)
        else:
            m = _TRAIN_NUMBER_RE.search(text)
            if m:
                result['train_number'] = m.group(1)

        # === 日期 ===
        text_no_invoice_date = re.sub(r'开\s*票\s*日\s*期[：:\s]*\d{4}年\d{1,2}月\d{1,2}日', '', text)
        date_pats = [
            (r'(?:乘车日期|出发日期|乘\s*车\s*日|出行日期)[：:\s]*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', 2, 3, 4),
            (r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})', 1, 2, 3),
            (r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', 1, 2, 3),
        ]
        for pat, gi, gm, gd in date_pats:
            src = text if gi == 2 else text_no_invoice_date
            m = re.search(pat, src)
            if m:
                try:
                    y, mo, d = int(m.group(gi)), int(m.group(gm)), int(m.group(gd))
                    if 1900 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                        result['date'] = f'{y:04d}-{mo:02d}-{d:02d}'
                        break
                except Exception:
                    pass

        # === 出发时间 ===
        tm = re.search(r'(?:出发时间|开车时间|发车)[：:\s]*(\d{1,2}:\d{2})', text)
        if tm:
            result['depart_time'] = tm.group(1)
        else:
            tm = re.search(r'(\d{2}):(\d{2})(?::(\d{2}))?', text)
            if tm:
                result['depart_time'] = tm.group(0)[:5]

        # === 乘客姓名 ===
        _NAME_BLACKLIST = {
            '出发', '到达', '乘坐', '车次', '购票', '旅客', '列车', '中国',
            '铁路', '上海', '北京', '广州', '深圳', '成都', '武汉', '南京',
            '高铁', '动车', '候车', '检票', '开车', '席别', '座位', '票价',
        }
        name_pats = [
            r'(?:姓\s*名|旅\s*客|购\s*票\s*人|乘\s*客|乘客姓名)[：:\s]*([\u4e00-\u9fa5]{2,4})',
            r'([\u4e00-\u9fa5]{2,4})[（\(]?(?:居民身份证|身份证|护照)',
            r'([\u4e00-\u9fa5]{2,4})\s*\d{15,18}[Xx]?',
            r'([\u4e00-\u9fa5]{2,4})\s*\*{4,}',
            r'\*{4,}\d+\n([\u4e00-\u9fa5]{2,4})',
            r'(?:\*{4,}|\d{6,})[^\n]*\n([\u4e00-\u9fa5]{2,4})\n',
            r'([\u4e00-\u9fa5]{2,4})\s+(?:商务座|特等座|一等座|二等座|软卧|硬卧|硬座|无座)',
            r'([\u4e00-\u9fa5]{2,4})\s*[¥￥]\s*\d',
        ]
        for p in name_pats:
            nm = re.search(p, text)
            if nm:
                name = nm.group(1).strip()
                if name not in _NAME_BLACKLIST:
                    result['passenger_name'] = name
                    break

        _station_extra_excl = set()
        if result['passenger_name']:
            _station_extra_excl.add(result['passenger_name'])

        def _clean_st(name: str) -> str:
            s = self._clean_station(name)
            return '' if s in _station_extra_excl else s

        # === 出发站 / 到达站 ===
        def _find_labeled_station(label_regex):
            # 同行或跨行（跳过拼音行）
            m = re.search(label_regex + r'[ \t]*([\u4e00-\u9fa5]{2,20})', text)
            if m:
                s = _clean_st(m.group(1))
                if s:
                    return s
            m2 = re.search(label_regex, text)
            if m2:
                after = text[m2.end():]
                for line in after.split('\n')[:6]:
                    line = line.strip()
                    if not line:
                        continue
                    if re.match(r'^[A-Za-z0-9\s\-]+$', line):
                        continue
                    ch = re.search(r'([\u4e00-\u9fa5]{2,20})', line)
                    if ch:
                        s = _clean_st(ch.group(1))
                        if s:
                            return s
            return None

        _from_labeled = _find_labeled_station(r'(?:出\s*发\s*站|始\s*发\s*站|出发地)')
        _to_labeled   = _find_labeled_station(r'(?:到\s*达\s*站|终\s*到\s*站|目\s*的\s*地|到达地)')
        if _from_labeled: result['from_station'] = _from_labeled
        if _to_labeled:   result['to_station']   = _to_labeled

        # 网约车：从“出发地 到达地”同行提取
        if not result['from_station'] or not result['to_station']:
            travel_row = re.search(
                r'出发地\s*([\u4e00-\u9fa5\-0-9\s]+?)\s*到达地\s*([\u4e00-\u9fa5\-0-9\s]+?)(?:\s*等级|\n|$)',
                text
            )
            if travel_row:
                f = _clean_st(travel_row.group(1).strip())
                t = _clean_st(travel_row.group(2).strip())
                if f and not result['from_station']:
                    result['from_station'] = f
                if t and not result['to_station']:
                    result['to_station'] = t

        # 双栏格式
        if not result['from_station'] or not result['to_station']:
            two_col_hdr = re.search(r'(?:始发站|出发站)[ \t]+(?:终到站|到达站)', text)
            if two_col_hdr:
                after = text[two_col_hdr.end():]
                for line in after.split('\n')[:5]:
                    line = line.strip()
                    if not line:
                        continue
                    if re.match(r'^[A-Za-z0-9\s\-]+$', line):
                        continue
                    parts = re.findall(r'[\u4e00-\u9fa5]{2,12}', line)
                    if len(parts) >= 2:
                        f = _clean_st(parts[0])
                        t = _clean_st(parts[1])
                        if f and not result['from_station']: result['from_station'] = f
                        if t and not result['to_station']:   result['to_station']   = t
                        break

        # 铁路电子客票标准格式
        if not result['from_station'] or not result['to_station']:
            _tn = result.get('train_number') or ''
            if _tn:
                _m_t = re.search(r'(?<![A-Z\d])' + re.escape(_tn) + r'(?!\d)', text)
                if _m_t:
                    _before = text[:_m_t.start()]
                    _lines = [l.strip() for l in _before.split('\n') if l.strip()]
                    _stn_lines = []
                    for _ln in reversed(_lines[-10:]):
                        if re.search(r'(?:发票号码|开票日期|全国|监制|税务总局|统一发票)', _ln):
                            break
                        if re.match(r'^[A-Za-z0-9\s\'./:\-]+$', _ln):
                            continue
                        if re.match(r'^[\u4e00-\u9fa5]{2,8}站?$', _ln):
                            _stn_lines.insert(0, _ln)
                            if len(_stn_lines) >= 2:
                                break
                    if len(_stn_lines) >= 2:
                        f = _clean_st(_stn_lines[0])
                        t = _clean_st(_stn_lines[1])
                        if f and not result['from_station']: result['from_station'] = f
                        if t and not result['to_station']:   result['to_station']   = t

        # 箭头分隔
        if not result['from_station'] or not result['to_station']:
            arrow = re.search(
                r'([\u4e00-\u9fa5]{2,10}(?:站)?)\s*[→➜>—至]\s*([\u4e00-\u9fa5]{2,10}(?:站)?)',
                text)
            if arrow:
                f = _clean_st(arrow.group(1))
                t = _clean_st(arrow.group(2))
                if f and not result['from_station']:
                    result['from_station'] = f
                if t and not result['to_station']:
                    result['to_station'] = t

        # 复合方位词
        if not result['from_station'] or not result['to_station']:
            station_candidates = re.findall(
                r'([\u4e00-\u9fa5]{2,8}(?:虹桥|高铁|北站|南站|东站|西站)(?:站)?'
                r'|[\u4e00-\u9fa5]{4,8}(?:南|北|东|西)(?:站)?)',
                text)
            clean_cands = []
            for sc in station_candidates:
                s = _clean_st(sc)
                if s and s not in clean_cands:
                    clean_cands.append(s)
            if not result['from_station'] and len(clean_cands) >= 1:
                result['from_station'] = clean_cands[0]
            if not result['to_station'] and len(clean_cands) >= 2:
                result['to_station'] = clean_cands[1]

        # 带“站”字后缀
        if not result['from_station'] or not result['to_station']:
            _seen4 = set()
            with_zhan = []
            for s in [_clean_st(x) for x in re.findall(r'([\u4e00-\u9fa5]{2,8}站)', text)]:
                if s and s not in _seen4:
                    _seen4.add(s)
                    with_zhan.append(s)
            if not result['from_station'] and with_zhan:
                result['from_station'] = with_zhan[0]
            if not result['to_station']:
                for s in with_zhan:
                    if s != result['from_station']:
                        result['to_station'] = s
                        break

        # 防重复
        if (result['from_station'] and result['to_station']
                and result['from_station'] == result['to_station']):
            result['from_station'] = None

        # === 座位类型 ===
        for st in self._SEAT_TYPES:
            if st in text:
                result['seat_type'] = st
                break
        if not result['seat_type']:
            xi = re.search(r'席\s*别[：:\s]*([\u4e00-\u9fa5]{2,5})', text)
            if xi:
                result['seat_type'] = xi.group(1)

        # === 座位号 ===
        seat_pats = [
            r'(\d{1,2}\s*车\s*\d{1,2}\s*[A-F号])',
            r'([A-F]\d\s*车厢?\s*\d{1,2}\s*号?)',
            r'(\d{1,2}[A-F]\d?)',
        ]
        for sp in seat_pats:
            sm = re.search(sp, text)
            if sm:
                result['seat'] = sm.group(1).strip()
                break

        # === 票价 ===
        price_pats = [
            r'(?:票\s*价|价\s*格|金\s*额|票\s*款)[：:\s]*[¥￥]?\s*(\d+\.?\d*)',
            r'[¥￥]\s*(\d+\.?\d*)',
            r'(\d{2,5}\.\d{1,2})\s*元',
            r'合\s*计[：:\s]*[¥￥]?\s*(\d+\.?\d*)',
        ]
        for pp in price_pats:
            pm = re.search(pp, text)
            if pm:
                try:
                    result['price'] = f"{float(pm.group(1)):.2f}"
                    break
                except ValueError:
                    pass
        if not result['price']:
            decimals = [float(x) for x in re.findall(r'(?<!\d)(\d{1,5}\.\d{1,2})(?!\d)', text)
                        if 1 <= float(x) <= 10000]
            if decimals:
                result['price'] = f"{max(decimals):.2f}"

        # 从文件名补充车次
        if stem:
            parts = re.split(r'[_\-\s]', stem)
            for part in parts:
                tn = re.match(r'^([GDTZKCY]\d{1,4})$', part)
                if tn and not result['train_number']:
                    result['train_number'] = tn.group(1)

        return result


# ======================== 合同提取 ========================

def _abbreviate_party(name: str) -> str:
    if not name:
        return ''
    name = re.sub(r'[（(][^）)]{1,8}[）)]', '', name).strip()
    name = re.sub(r'\s+', '', name)
    _SUFFIXES = [
        '有限责任公司', '股份有限公司', '集团有限公司', '集团公司',
        '总公司', '分公司', '有限公司',
    ]
    for sfx in _SUFFIXES:
        if name.endswith(sfx):
            candidate = name[:-len(sfx)]
            if len(candidate) >= 2:
                name = candidate
            break
    if len(name) > 6:
        name = name[:6]
    return name


class ContractExtractor:
    def _extract_fields(self, text: str) -> dict:
        result = {
            'contract_name': '',
            'sign_date':     '',
            'party_a':       '',
            'party_b':       '',
            'amount':        '',
        }
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        self._extract_contract_name(lines, text, result)
        self._extract_sign_date(lines, text, result)
        self._extract_parties(lines, text, result)
        self._extract_amount(lines, text, result)
        return result

    def _extract_contract_name(self, lines, text, result):
        for line in lines:
            m = re.search(r'(?:合同名称|协议名称|项目名称)[：:]\s*(.{2,40})', line)
            if m:
                name = re.sub(r'\s+', '', m.group(1)).strip()
                name = re.sub(r'[（(].*?[）)]', '', name).strip()
                if name and len(name) >= 2:
                    result['contract_name'] = name[:20]
                    return
        for line in lines[:10]:
            clean = re.sub(r'[《》【】\[\]（(）)\s]+', '', line)
            if 2 < len(clean) <= 20 and re.search(r'合同|协议书', clean):
                if clean not in ('合同', '协议书', '协议', '本合同', '本协议'):
                    result['contract_name'] = clean
                    return
        m = re.search(r'[\u4e00-\u9fff]{2,12}(?:采购合同|服务合同|工程合同|合同|协议书)', text)
        if m:
            result['contract_name'] = m.group(0)[:20]

    def _extract_sign_date(self, lines, text, result):
        pats = [
            r'(?:签订|签署|签约|合同)\s*[日期时间]*\s*[：:\s]+(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'于\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',
        ]
        for pat in pats:
            m = re.search(pat, text)
            if m:
                result['sign_date'] = (
                    f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                )
                return

    def _extract_parties(self, lines, text, result):
        A_KW = r'(?:甲\s*方|买\s*方|委\s*托\s*方|发\s*包\s*方|采\s*购\s*方|招\s*标\s*人)'
        B_KW = r'(?:乙\s*方|卖\s*方|承\s*包\s*方|承\s*接\s*方|供\s*货\s*方|中\s*标\s*人)'
        a_pats = [A_KW + r'(?:\s*单位全称|\s*名称|\s*（盖章）|\s*\(盖章\))?\s*[：:\s]+([^\n]{2,35})']
        b_pats = [B_KW + r'(?:\s*单位全称|\s*名称|\s*（盖章）|\s*\(盖章\))?\s*[：:\s]+([^\n]{2,35})']

        def _clean(raw):
            val = re.sub(r'[（(][^）)]{1,8}[）)]', '', raw).strip()
            val = re.sub(r'\s+', '', val)
            val = re.split(r'甲方|乙方|买方|卖方|承包方|委托方|招标人|中标人', val)[0]
            return val[:30]

        for line in lines:
            if not result['party_a']:
                for pat in a_pats:
                    m = re.search(pat, line)
                    if m:
                        v = _clean(m.group(1))
                        if len(v) >= 2:
                            result['party_a'] = v
                            break
            if not result['party_b']:
                for pat in b_pats:
                    m = re.search(pat, line)
                    if m:
                        v = _clean(m.group(1))
                        if len(v) >= 2:
                            result['party_b'] = v
                            break
            if result['party_a'] and result['party_b']:
                break
        if not result['party_a']:
            m = re.search(A_KW + r'[\s：:]+([^\n（(]{2,30})', text)
            if m:
                result['party_a'] = _clean(m.group(1))
        if not result['party_b']:
            m = re.search(B_KW + r'[\s：:]+([^\n（(]{2,30})', text)
            if m:
                result['party_b'] = _clean(m.group(1))

    def _extract_amount(self, lines, text, result):
        amount_pats = [
            (r'(?:合同[总]?[额价款金]|总金额|总价款|合同总价|合同价款|价款总额)'
             r'[^0-9¥￥]{0,15}[¥￥]?\s*(\d[\d,，]*\.?\d*)\s*(万元|元)?', True),
            (r'人民币\s*[¥￥]?\s*(\d[\d,，]*\.?\d+)\s*(万元|元)?', True),
            (r'[¥￥]\s*(\d[\d,，]*\.?\d+)\s*(万元|元)?', True),
            (r'(\d[\d,，]{1,8}\.?\d*)\s*(万元)', True),
        ]
        def _parse(raw_num, unit):
            try:
                v = float(raw_num.replace(',', '').replace('，', ''))
                if unit and '万' in unit:
                    v *= 10000
                return f'{v:.2f}'
            except Exception:
                return raw_num
        for line in lines:
            for pat, _ in amount_pats:
                m = re.search(pat, line)
                if m:
                    unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ''
                    result['amount'] = _parse(m.group(1), unit or '')
                    return
        for pat, _ in amount_pats:
            m = re.search(pat, text)
            if m:
                unit = m.group(2) if m.lastindex and m.lastindex >= 2 else ''
                result['amount'] = _parse(m.group(1), unit or '')
                return


def generate_contract_filename(data: dict, original_ext: str) -> str:
    date          = data.get('sign_date') or '0000-01-01'
    contract_name = (data.get('contract_name') or '合同')[:15]
    party_a_abbr  = _abbreviate_party(data.get('party_a') or '')
    party_b_abbr  = _abbreviate_party(data.get('party_b') or '')
    amount        = data.get('amount') or ''
    parts = [date, contract_name]
    if party_a_abbr:
        parts.append(party_a_abbr)
    if party_b_abbr:
        parts.append(party_b_abbr)
    if amount:
        try:
            parts.append(f'{float(amount):.0f}元' if float(amount) == int(float(amount))
                         else f'{amount}元')
        except Exception:
            parts.append(f'{amount}元')
    new_name = '_'.join(parts) + original_ext
    new_name = re.sub(r'[\\/:*?"<>|]', '', new_name)
    new_name = re.sub(r'_+', '_', new_name).strip('_')
    return new_name or f"contract_{datetime.now().strftime('%Y%m%d%H%M%S')}{original_ext}"


# ======================== 通用命名函数 ========================

def generate_travel_filename(data: dict, travel_type: str | None, original_ext: str) -> str:
    date = data.get('date') or '0000-01-01'
    from_st = (data.get('from_station') or '')[:10]
    to_st   = (data.get('to_station') or '')[:10]
    price = data.get('price') or data.get('amount') or '0.00'
    parts = [date]
    if from_st:
        parts.append(from_st)
    if to_st:
        parts.append(to_st)
    if travel_type:
        parts.append(travel_type)
    parts.append(f'{price}元')
    new_name = '_'.join(parts) + original_ext
    new_name = re.sub(r'[\\/:*?"<>|]', '', new_name)
    new_name = re.sub(r'_+', '_', new_name).strip('_')
    return new_name or f"travel_{datetime.now().strftime('%Y%m%d%H%M%S')}{original_ext}"


def generate_filename(data: dict, original_ext: str) -> str:
    date     = data.get('date') or '0000-01-01'
    buyer    = (data.get('buyer')    or '')[:20]
    supplier = (data.get('supplier') or '')[:20]
    amount   = data.get('amount') or '0.00'
    subtype  = data.get('subtype') or ''
    parts = [date]
    if supplier:
        parts.append(supplier)
    if buyer:
        parts.append(buyer)
    if subtype:
        parts.append(subtype)
    parts.append(f'{amount}元')
    new_name = '_'.join(parts) + original_ext
    new_name = re.sub(r'[\\/:*?"<>|]', '', new_name)
    new_name = re.sub(r'_+', '_', new_name).strip('_')
    return new_name or f"invoice_{datetime.now().strftime('%Y%m%d%H%M%S')}{original_ext}"


# ======================== Word & PDF 文本提取辅助 ========================

def _extract_text_from_docx(docx_path: str) -> str:
    try:
        import docx
        doc = docx.Document(docx_path)
        return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ''


def _pdf_contract_pages_text(pdf_path: str, reader, inv_extractor, max_pages: int = 8) -> str:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        combined = []
        for i in range(min(max_pages, len(doc))):
            page = doc[i]
            mat  = fitz.Matrix(2, 2)
            pix  = page.get_pixmap(matrix=mat)
            from PIL import Image
            import io as _io
            img = Image.open(_io.BytesIO(pix.tobytes('png')))
            img_np = np.array(img.convert('RGB'))
            img_np = inv_extractor._preprocess_image(img_np)
            results = reader.readtext(img_np, detail=0)
            combined.append('\n'.join(results))
        return '\n'.join(combined)
    except Exception:
        return ''


def _image_to_pdf(image_path: str, output_pdf_path: str):
    from PIL import Image as _PIL_Image
    img = _PIL_Image.open(image_path).convert('RGB')
    img.save(output_pdf_path, 'PDF', resolution=150)


_IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif'}


def _resolve_output_ext(orig_ext: str, doc_type: str) -> str:
    if doc_type == 'contract':
        if orig_ext in ('.docx', '.doc'):
            return orig_ext
        if orig_ext in _IMAGE_EXTS:
            return '.pdf'
    return orig_ext


def _generate_any_filename(data: dict, doc_type: str, out_ext: str) -> str:
    if doc_type == 'train':
        return generate_travel_filename(data, '火车票', out_ext)
    elif doc_type == 'flight':
        return generate_travel_filename(data, '飞机票', out_ext)
    elif doc_type == 'travel':
        return generate_travel_filename(data, None, out_ext)
    elif doc_type == 'contract':
        return generate_contract_filename(data, out_ext)
    else:
        return generate_filename(data, out_ext)


def _save_file(src_path: str, src_ext: str, dst_path: str,
               out_ext: str, doc_type: str):
    if doc_type == 'contract' and src_ext in _IMAGE_EXTS and out_ext == '.pdf':
        _image_to_pdf(src_path, dst_path)
    else:
        shutil.copy(src_path, dst_path)


def _pdf_direct_text(pdf_path: str) -> str | None:
    try:
        import fitz
        doc = fitz.open(pdf_path)
        lines = []
        for page in doc:
            for line in page.get_text().splitlines():
                line = line.strip()
                if line:
                    lines.append(line)
        doc.close()
        full = '\n'.join(lines)
        cn_chars = sum(1 for c in full if '\u4e00' <= c <= '\u9fa5')
        return full if cn_chars >= 10 else None
    except Exception:
        return None


def _extract_stations_from_filename(stem: str) -> tuple:
    """
    从文件名中提取“城市-城市”模式，用于飞机票等。
    返回 (from_station, to_station)
    """
    # 匹配类似“沈阳-上海”或“北京南-上海虹桥”
    m = re.search(r'([\u4e00-\u9fa5]{2,8})[-—]([\u4e00-\u9fa5]{2,8})', stem)
    if m:
        return m.group(1), m.group(2)
    return None, None


def smart_extract(file_path: str, reader) -> tuple:
    """自动判断类型并提取字段，返回 (data_dict, doc_type)"""
    inv      = InvoiceExtractor(reader)
    train    = TrainTicketExtractor(reader)
    contract = ContractExtractor()

    fp  = Path(file_path)
    ext = fp.suffix.lower()
    text = None

    # === 1. Word 文件 ===
    if ext in ('.docx', '.doc'):
        text = _extract_text_from_docx(str(fp))
        if not text:
            raise Exception("无法读取 Word 文件内容")

    # === 2. 矢量 PDF 直提 ===
    elif ext == '.pdf':
        text = _pdf_direct_text(str(fp))

    # === 3. 图片 / PDF 需 OCR ===
    if not text:
        if ext == '.pdf':
            img = inv._pdf_to_image(str(fp))
        else:
            img = inv._image_file_to_array(str(fp))
        if img is None or img.size == 0:
            raise Exception("无法读取图像")
        img = inv._preprocess_image(img)
        results = reader.readtext(img, detail=0)
        if not results:
            raise Exception("OCR 无法识别")
        page1_text = '\n'.join(results)
        if detect_doc_type(page1_text) == 'contract' and ext == '.pdf':
            extra = _pdf_contract_pages_text(str(fp), reader, inv, max_pages=8)
            text = page1_text + '\n' + extra if extra else page1_text
        else:
            text = page1_text

    if text and detect_doc_type(text) == 'contract' and ext == '.pdf':
        extra = _pdf_contract_pages_text(str(fp), reader, inv, max_pages=8)
        if extra:
            text = text + '\n' + extra

    doc_type = detect_doc_type(text)

    if doc_type in ('train', 'flight', 'travel'):
        fields = train._extract_fields(text, fp.stem)
        # 若未提取到价格，用发票金额
        if not fields.get('price'):
            inv_fields = inv._extract_fields(text)
            if inv_fields.get('amount'):
                fields['price'] = inv_fields['amount']
        # 若为飞机票且未提取到出发地/到达地，从文件名提取
        if doc_type == 'flight':
            if not fields.get('from_station') or not fields.get('to_station'):
                f, t = _extract_stations_from_filename(fp.stem)
                if f and not fields.get('from_station'):
                    fields['from_station'] = f
                if t and not fields.get('to_station'):
                    fields['to_station'] = t
    elif doc_type == 'contract':
        fields = contract._extract_fields(text)
    else:
        fields = inv._extract_fields(text)
        fn_fields = inv._extract_from_filename(fp.stem)
        for key in ('date', 'invoice_number', 'buyer', 'supplier'):
            if not fields.get(key) and fn_fields.get(key):
                fields[key] = fn_fields[key]

    fields['_raw_text'] = text
    return fields, doc_type


# ======================== Flask 应用 ========================

app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

READER = None
READER_READY = False
UPLOAD_RESULTS = {}

BATCH_RESULTS = []
BATCH_SESSION_DIRS = []
BATCH_LOCK = threading.Lock()


def init_ocr_background():
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
    return jsonify({
        'ready': READER_READY,
        'message': 'OCR 已准备好' if READER_READY else '正在初始化 OCR 模型，请稍候...'
    })


@app.route('/api/upload', methods=['POST'])
def upload_file():
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
        session_id = datetime.now().strftime('%Y%m%d%H%M%S%f')[-15:]
        session_dir = os.path.join(tempfile.gettempdir(), f'invoice_session_{session_id}')
        os.makedirs(session_dir, exist_ok=True)

        for file in files:
            if file.filename == '':
                continue
            ext = Path(file.filename).suffix.lower()
            allowed_ext = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif',
                           '.docx', '.doc', '.zip'}
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
                    out_ext = _resolve_output_ext(ext, doc_type)
                    new_name = _generate_any_filename(data, doc_type, out_ext)
                    new_path = os.path.join(session_dir, new_name)
                    _save_file(temp_path, ext, new_path, out_ext, doc_type)
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
    results = []
    temp_extract = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(temp_extract)
        allowed_ext = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif',
                       '.docx', '.doc'}
        for root, dirs, files in os.walk(temp_extract):
            for file in sorted(files):
                ext = Path(file).suffix.lower()
                if ext in allowed_ext:
                    file_path = os.path.join(root, file)
                    try:
                        data, doc_type = smart_extract(file_path, reader)
                        out_ext = _resolve_output_ext(ext, doc_type)
                        new_name = _generate_any_filename(data, doc_type, out_ext)
                        new_path = os.path.join(session_dir, new_name)
                        _save_file(file_path, ext, new_path, out_ext, doc_type)
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


@app.route('/api/download-zip/<session_id>', methods=['GET'])
def download_zip(session_id):
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400
    session_data = UPLOAD_RESULTS[session_id]
    results = session_data['results']
    sdir = session_data['session_dir']
    success = [item for item in results if item['status'] == 'success']
    if not success:
        return jsonify({'error': '无可下载文件'}), 400
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in success:
            fpath = os.path.join(sdir, item['new_name'])
            if os.path.exists(fpath):
                zf.write(fpath, item['new_name'])
    zip_buf.seek(0)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    return send_file(zip_buf, mimetype='application/zip',
                     as_attachment=True,
                     download_name=f'发票_{ts}.zip')


@app.route('/api/download/<session_id>', methods=['GET'])
def download_results(session_id):
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400
    results = UPLOAD_RESULTS[session_id]['results']
    files = [
        {'new_name': item['new_name'], 'index': i}
        for i, item in enumerate(results)
        if item['status'] == 'success'
    ]
    return jsonify({'files': files})


@app.route('/api/download-file/<session_id>/<int:item_idx>', methods=['GET'])
def download_single_file(session_id, item_idx):
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400
    session_data = UPLOAD_RESULTS[session_id]
    results = session_data['results']
    if item_idx < 0 or item_idx >= len(results):
        return jsonify({'error': '索引越界'}), 400
    item = results[item_idx]
    if item['status'] != 'success':
        return jsonify({'error': '该文件未成功识别'}), 400
    new_name = item['new_name']
    file_path = os.path.join(session_data['session_dir'], new_name)
    if not os.path.exists(file_path):
        return jsonify({'error': '文件已过期'}), 400
    return send_file(file_path, as_attachment=True, download_name=new_name)


# ---- CSV 和 Excel 导出（完整保留） ----
def _build_csv_bytes(results: list) -> bytes:
    import csv
    output = io.StringIO()
    output.write('\ufeff')
    writer = csv.writer(output)
    writer.writerow([
        '原文件名', '新文件名', '状态', '类型', '日期',
        '发票号码', '购买方', '销售方', '金额（不含税）', '税额', '价税合计',
        '车次', '出发站', '到达站', '乘客姓名', '座位', '座位类型', '票价',
        '合同名称', '甲方', '乙方', '合同金额',
        '错误信息'
    ])
    total_invoice = total_train = total_contract = 0.0
    success_count = fail_count = 0
    EMPTY6 = [''] * 6
    EMPTY7 = [''] * 7
    EMPTY4 = [''] * 4
    for item in results:
        if item['status'] == 'success':
            d     = item.get('data') or {}
            dtype = item.get('doc_type', 'invoice')
            if dtype in ('train', 'flight', 'travel'):
                price = d.get('price', '')
                type_label = {'train':'火车票', 'flight':'飞机票', 'travel':'网约车'}[dtype]
                writer.writerow([
                    item.get('filename', ''), item.get('new_name', ''), '成功', type_label,
                    d.get('date', ''),
                    *EMPTY6,
                    d.get('train_number', ''), d.get('from_station', ''),
                    d.get('to_station', ''), d.get('passenger_name', ''),
                    d.get('seat', ''), d.get('seat_type', ''), price,
                    *EMPTY4, ''
                ])
                try: total_train += float(price) if price else 0
                except ValueError: pass
            elif dtype == 'contract':
                amount = d.get('amount', '')
                writer.writerow([
                    item.get('filename', ''), item.get('new_name', ''), '成功', '合同',
                    d.get('sign_date', ''),
                    *EMPTY6,
                    *EMPTY7,
                    d.get('contract_name', ''), d.get('party_a', ''),
                    d.get('party_b', ''), amount, ''
                ])
                try: total_contract += float(amount) if amount else 0
                except ValueError: pass
            else:
                tax_free = d.get('tax_free_amount', '')
                tax      = d.get('tax_amount', '')
                amount   = d.get('amount', '')
                writer.writerow([
                    item.get('filename', ''), item.get('new_name', ''), '成功', '发票',
                    d.get('date', ''),
                    d.get('invoice_number', ''), d.get('buyer', ''), d.get('supplier', ''),
                    tax_free, tax, amount,
                    *EMPTY7,
                    *EMPTY4, ''
                ])
                try: total_invoice += float(amount) if amount else 0
                except ValueError: pass
            success_count += 1
        else:
            writer.writerow([
                item.get('filename', ''), '', '失败', '', '',
                *EMPTY6, *EMPTY7, *EMPTY4,
                item.get('error', '')
            ])
            fail_count += 1
    writer.writerow([])
    writer.writerow([
        f'合计（成功 {success_count} 张，失败 {fail_count} 张）',
        '', '', '', '',
        '', '', '', '', '', f'{total_invoice:.2f}',
        '', '', '', '', '', '', f'{total_train:.2f}',
        '', '', '', f'{total_contract:.2f}', ''
    ])
    return output.getvalue().encode('utf-8-sig')


def _build_excel_bytes(results: list, title: str = '识别结果汇总') -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '汇总表'

    C_INVOICE_HDR  = 'FF276749'
    C_TRAIN_HDR    = 'FF2B6CB0'
    C_CONTRACT_HDR = 'FF6B21A8'
    C_META_HDR     = 'FF4A5568'
    C_INVOICE_ROW  = 'FFE6F4EA'
    C_TRAIN_ROW    = 'FFE8F0FE'
    C_CONTRACT_ROW = 'FFF3E8FE'
    C_FAIL_ROW     = 'FFFFF3CD'
    C_SUM_ROW      = 'FFFFF8E1'
    WHITE          = 'FFFFFFFF'

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

    HEADERS = [
        '原文件名', '新文件名', '状态', '类型', '日期',
        '发票号码', '购买方', '销售方', '金额(不含税)', '税额', '价税合计',
        '车次', '出发站', '到达站', '乘客姓名', '座位', '座位类型', '票价',
        '合同名称', '甲方', '乙方', '合同金额',
        '错误信息',
    ]
    NCOLS = len(HEADERS)
    COL_WIDTHS = [26, 36, 8, 10, 12,
                  18, 20, 20, 14, 12, 12,
                  10, 14, 14, 14, 14, 10, 10,
                  20, 16, 16, 14,
                  20]
    HDR_COLORS = {
        **{i: C_META_HDR     for i in range(1, 6)},
        **{i: C_INVOICE_HDR  for i in range(6, 12)},
        **{i: C_TRAIN_HDR    for i in range(12, 19)},
        **{i: C_CONTRACT_HDR for i in range(19, 23)},
        23: C_META_HDR,
    }

    ws.merge_cells(f'A1:{get_column_letter(NCOLS)}1')
    title_cell = ws['A1']
    title_cell.value     = title
    title_cell.font      = Font(name='微软雅黑', bold=True, size=14, color=WHITE)
    title_cell.fill      = fill('FF4A5568')
    title_cell.alignment = center()
    ws.row_dimensions[1].height = 32

    for col_i, (hdr, width) in enumerate(zip(HEADERS, COL_WIDTHS), start=1):
        c = ws.cell(row=2, column=col_i, value=hdr)
        c.font      = hdr_font(color=WHITE)
        c.fill      = fill(HDR_COLORS.get(col_i, C_META_HDR))
        c.alignment = center()
        c.border    = bdr
        ws.column_dimensions[get_column_letter(col_i)].width = width
    ws.row_dimensions[2].height = 22

    total_invoice = total_train = total_contract = 0.0
    success_count = fail_count  = 0
    data_start_row = 3

    for row_i, item in enumerate(results, start=data_start_row):
        dtype    = item.get('doc_type', 'invoice')
        is_ok    = item['status'] == 'success'
        d        = item.get('data') or {}

        if not is_ok:
            row_fill = fill(C_FAIL_ROW)
        elif dtype in ('train', 'flight', 'travel'):
            row_fill = fill(C_TRAIN_ROW)
        elif dtype == 'contract':
            row_fill = fill(C_CONTRACT_ROW)
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

        if is_ok:
            type_labels = {'train':'火车票', 'flight':'飞机票', 'travel':'网约车',
                           'contract':'合同', 'invoice':'发票'}
            set_cell(1, item.get('filename', ''))
            set_cell(2, item.get('new_name', ''), bold=True)
            set_cell(3, '成功', align=center())
            set_cell(4, type_labels.get(dtype, '发票'), align=center())

            if dtype in ('train', 'flight', 'travel'):
                set_cell(5, d.get('date', ''), align=center())
                for col in range(6, 12): set_cell(col, '')
                set_cell(12, d.get('train_number', ''), align=center())
                set_cell(13, d.get('from_station', ''))
                set_cell(14, d.get('to_station', ''))
                set_cell(15, d.get('passenger_name', ''))
                set_cell(16, d.get('seat', ''), align=center())
                set_cell(17, d.get('seat_type', ''), align=center())
                price_str = d.get('price', '')
                total_train += num_cell(18, price_str)
                for col in range(19, 23): set_cell(col, '')

            elif dtype == 'contract':
                set_cell(5, d.get('sign_date', ''), align=center())
                for col in range(6, 19): set_cell(col, '')
                set_cell(19, d.get('contract_name', ''))
                set_cell(20, d.get('party_a', ''))
                set_cell(21, d.get('party_b', ''))
                total_contract += num_cell(22, d.get('amount', ''))

            else:
                set_cell(5, d.get('date', ''), align=center())
                set_cell(6,  d.get('invoice_number', ''), align=center())
                set_cell(7,  d.get('buyer', ''))
                set_cell(8,  d.get('supplier', ''))
                num_cell(9,  d.get('tax_free_amount', ''))
                num_cell(10, d.get('tax_amount', ''))
                total_invoice += num_cell(11, d.get('amount', ''))
                for col in range(12, 23): set_cell(col, '')

            set_cell(23, '')
            success_count += 1
        else:
            set_cell(1, item.get('filename', ''))
            for col in range(2, 23): set_cell(col, '')
            set_cell(3, '失败', align=center())
            set_cell(23, item.get('error', ''))
            fail_count += 1

        ws.row_dimensions[row_i].height = 18

    sum_row = data_start_row + len(results)
    ws.merge_cells(f'A{sum_row}:D{sum_row}')
    sc = ws.cell(row=sum_row, column=1,
                 value=f'合计：成功 {success_count} 张，失败 {fail_count} 张')
    sc.font = Font(name='微软雅黑', bold=True, size=10, color='FF333333')
    sc.fill = fill(C_SUM_ROW); sc.alignment = center(); sc.border = bdr

    def sum_cell(col, val, color):
        c = ws.cell(row=sum_row, column=col, value=val)
        c.font = Font(name='微软雅黑', bold=True, size=10, color=color)
        c.fill = fill(C_SUM_ROW); c.border = bdr
        c.alignment = center(); c.number_format = '#,##0.00'

    def sum_lbl(col, txt):
        c = ws.cell(row=sum_row, column=col, value=txt)
        c.font = Font(name='微软雅黑', bold=True, size=9)
        c.fill = fill(C_SUM_ROW); c.border = bdr; c.alignment = center()

    sum_lbl(10, '发票总额 ▶'); sum_cell(11, total_invoice, C_INVOICE_HDR[2:])
    sum_lbl(17, '交通票总额 ▶'); sum_cell(18, total_train,   C_TRAIN_HDR[2:])
    sum_lbl(21, '合同总额 ▶');  sum_cell(22, total_contract, C_CONTRACT_HDR[2:])

    for col in [5,6,7,8,9,12,13,14,15,16,19,20,23]:
        c = ws.cell(row=sum_row, column=col, value='')
        c.fill = fill(C_SUM_ROW); c.border = bdr

    ws.row_dimensions[sum_row].height = 22
    ws.freeze_panes = 'A3'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


@app.route('/api/export/<session_id>', methods=['GET'])
def export_csv(session_id):
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


# ---- 批次管理 API ----
@app.route('/api/update/<session_id>/<int:item_index>', methods=['POST'])
def update_item(session_id, item_index):
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
    all_fields = ('date', 'invoice_number', 'buyer', 'supplier',
                  'tax_free_amount', 'tax_amount', 'amount',
                  'train_number', 'depart_time', 'from_station', 'to_station',
                  'passenger_name', 'seat', 'seat_type', 'price',
                  'contract_name', 'sign_date', 'party_a', 'party_b')
    for field in all_fields:
        if field in payload:
            d[field] = payload[field].strip()
    item['data'] = d
    ext          = os.path.splitext(old_new_name)[1].lower()
    new_new_name = _generate_any_filename(d, doc_type, ext)
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
    global BATCH_RESULTS, BATCH_SESSION_DIRS
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 404
    session_data = UPLOAD_RESULTS[session_id]
    with BATCH_LOCK:
        for idx, item in enumerate(session_data['results']):
            item_copy = dict(item)
            item_copy['session_id'] = session_id
            item_copy['_idx']       = idx
            BATCH_RESULTS.append(item_copy)
        BATCH_SESSION_DIRS.append(session_data['session_dir'])
        total   = len(BATCH_RESULTS)
        success = sum(1 for r in BATCH_RESULTS if r['status'] == 'success')
    return jsonify({'total': total, 'success': success})


@app.route('/api/batch/status', methods=['GET'])
def batch_status():
    with BATCH_LOCK:
        total   = len(BATCH_RESULTS)
        success = sum(1 for r in BATCH_RESULTS if r['status'] == 'success')
    return jsonify({'total': total, 'success': success})


@app.route('/api/batch/files', methods=['GET'])
def batch_files():
    with BATCH_LOCK:
        results      = list(BATCH_RESULTS)
        session_dirs = list(BATCH_SESSION_DIRS)
    if not results:
        return jsonify({'files': []})
    files = []
    for sdir in session_dirs:
        if not os.path.isdir(sdir):
            continue
        for item in results:
            fname = item.get('new_name', '')
            if not fname:
                continue
            fpath = os.path.join(sdir, fname)
            if not os.path.exists(fpath):
                continue
            sid = item.get('session_id', '')
            idx = item.get('_idx', None)
            if sid and idx is not None:
                files.append({'new_name': fname, 'session_id': sid, 'index': idx})
    return jsonify({'files': files})


@app.route('/api/batch/download', methods=['GET'])
def batch_download():
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
    print("发票批量重命名工具 (鲁棒增强版)")
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
