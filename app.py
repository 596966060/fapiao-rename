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
        # 策略1: 明确的购买方/销售方标签
        buyer_match = re.search(
            r'(?:购买方|购\s*方|买\s*方)[^\n]*?(?:[1l名]称|称)[：:]\s*([^\n]+)', text)
        supplier_match = re.search(
            r'(?:销售方|销\s*方|卖\s*方)[^\n]*?(?:[1l名]称|称)[：:]\s*([^\n]+)', text)

        def clean_company(name):
            """清理公司名，要求包含中文字符"""
            name = name.strip()
            # 去掉税号、数字串等杂质
            name = re.sub(r'\s+\d{10,}.*$', '', name)
            name = name.strip()
            # 必须包含中文字符才算有效公司名
            if not re.search(r'[\u4e00-\u9fa5]', name):
                return None
            if len(name) < 3 or len(name) > 60:
                return None
            return name

        if buyer_match:
            result["buyer"] = clean_company(buyer_match.group(1))
        if supplier_match:
            result["supplier"] = clean_company(supplier_match.group(1))

        # 策略2: 如果明确标签没找到，找所有 "名称:" / "1称:" / "l称:" 后面的内容
        if not result["buyer"] and not result["supplier"]:
            company_lines = []
            label_patterns = [
                r'[1l]称[：:]\s*([^\n]+)',
                r'名称[：:]\s*([^\n]+)',
            ]
            for pattern in label_patterns:
                for m in re.findall(pattern, text):
                    c = clean_company(m)
                    if c:
                        company_lines.append(c)

            # 策略3: 通用企业名识别（要求包含中文 + 企业词尾）
            if not company_lines:
                company_pattern = r'[\u4e00-\u9fa5]{2,}(?:公司|有限|分公司|集团|股份|企业|研究所|医院|学校|协会|中心)'
                for m in re.findall(company_pattern, text):
                    c = clean_company(m)
                    if c:
                        company_lines.append(c)

            # 去重
            seen = set()
            unique_companies = []
            for c in company_lines:
                if c not in seen:
                    seen.add(c)
                    unique_companies.append(c)

            if len(unique_companies) >= 2:
                result["buyer"] = unique_companies[0]
                result["supplier"] = unique_companies[1]
            elif len(unique_companies) == 1:
                result["buyer"] = unique_companies[0]

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

        # === 合计行：同时捕获不含税金额 + 税额 ===
        # 典型格式：合计  ¥232.57  ¥33.74  （金额在左，税额在右，同一行）
        amtpat = r'[¥￥垒垩圓Y]?\s*([0-9]{1,10}\.[0-9]{2})'
        two_num = re.search(
            r'合计\s*' + amtpat + r'\s*' + amtpat, text)
        if two_num:
            result["tax_free_amount"] = f"{float(two_num.group(1)):.2f}"
            result["tax_amount"]      = f"{float(two_num.group(2)):.2f}"
        else:
            # OCR 可能将合计行拆成多行，逐列尝试
            # 不含税金额：合计 / 金额 标签附近的第一个小数
            tax_free_patterns = [
                r'合计\s*' + amtpat,
                r'(?:不含税|金额)[：:\s]*' + amtpat,
            ]
            result["tax_free_amount"] = _find_amount(tax_free_patterns, text)

            # 税额：明确带"税额"标签
            tax_patterns = [
                r'(?:合计税额|税\s*额)[：:\s]*' + amtpat,
                r'税\s*额[^0-9\n]{0,10}([0-9]{1,10}\.[0-9]{2})',
            ]
            result["tax_amount"] = _find_amount(tax_patterns, text)

        # 兜底推算：有价税合计 + 不含税金额 → 推出税额
        if result["amount"] and result["tax_free_amount"] and not result["tax_amount"]:
            try:
                tax = round(float(result["amount"]) - float(result["tax_free_amount"]), 2)
                if tax > 0:
                    result["tax_amount"] = f"{tax:.2f}"
            except:
                pass

        # 兜底推算：有价税合计 + 税额 → 推出不含税金额
        if result["amount"] and result["tax_amount"] and not result["tax_free_amount"]:
            try:
                base = round(float(result["amount"]) - float(result["tax_amount"]), 2)
                if base > 0:
                    result["tax_free_amount"] = f"{base:.2f}"
            except:
                pass

        return result


def generate_filename(data: dict, original_ext: str) -> str:
    """生成新文件名"""
    date = data.get('date') or '0000-01-01'
    invoice_num = data.get('invoice_number') or '000000'
    buyer = (data.get('buyer') or '')[:20]
    supplier = (data.get('supplier') or '')[:20]
    amount = data.get('amount') or '0.00'

    new_name = f"{date}_{invoice_num}_{buyer}_{supplier}_{amount}元{original_ext}"
    new_name = re.sub(r'[\\/:*?"<>|]', '', new_name)
    new_name = re.sub(r'_+', '_', new_name).strip('_')
    return new_name or f"invoice_{datetime.now().strftime('%Y%m%d%H%M%S')}{original_ext}"


# ======================== Flask 应用 ========================

app = Flask(__name__, template_folder='templates')
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

READER = None
READER_READY = False
UPLOAD_RESULTS = {}


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

        extractor = InvoiceExtractor(reader)
        results = []

        # 创建会话文件夹存储重命名后的文件
        session_id = datetime.now().strftime('%Y%m%d%H%M%S%f')[-15:]
        session_dir = os.path.join(tempfile.gettempdir(), f'invoice_session_{session_id}')
        os.makedirs(session_dir, exist_ok=True)

        for file in files:
            if file.filename == '':
                continue

            ext = Path(file.filename).suffix.lower()
            allowed_ext = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.zip'}

            if ext not in allowed_ext:
                results.append({
                    'filename': file.filename,
                    'status': 'error',
                    'error': '不支持的格式'
                })
                continue

            safe_name = re.sub(r'[^\w.\-]', '_', file.filename)
            temp_path = os.path.join(tempfile.gettempdir(), f"{session_id}_{safe_name}")
            file.save(temp_path)

            try:
                if ext == '.zip':
                    zip_results = process_zip_file(temp_path, extractor, session_dir)
                    results.extend(zip_results)
                else:
                    data = extractor.extract(temp_path)
                    new_name = generate_filename(data, ext)

                    # 保存重命名后的文件到会话目录
                    new_path = os.path.join(session_dir, new_name)
                    shutil.copy(temp_path, new_path)

                    results.append({
                        'filename': file.filename,
                        'new_name': new_name,
                        'data': data,
                        'status': 'success'
                    })
            except Exception as e:
                results.append({
                    'filename': file.filename,
                    'status': 'error',
                    'error': str(e)
                })
            finally:
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        # 保存会话
        UPLOAD_RESULTS[session_id] = {
            'results': results,
            'session_dir': session_dir
        }

        return jsonify({
            'session_id': session_id,
            'total': len(results),
            'results': results
        })

    except Exception as e:
        return jsonify({'error': f'处理失败: {str(e)}'}), 500


def process_zip_file(zip_path: str, extractor: InvoiceExtractor, session_dir: str) -> list:
    """处理 ZIP 文件"""
    results = []
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
                        data = extractor.extract(file_path)
                        new_name = generate_filename(data, ext)

                        # 保存到会话目录
                        new_path = os.path.join(session_dir, new_name)
                        shutil.copy(file_path, new_path)

                        results.append({
                            'filename': file,
                            'new_name': new_name,
                            'data': data,
                            'status': 'success'
                        })
                    except Exception as e:
                        results.append({
                            'filename': file,
                            'status': 'error',
                            'error': str(e)
                        })
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
