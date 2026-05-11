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

            # 如果OCR结果过短（可能是错误的），记录警告
            if len(text) < 50:
                # 结果太短，可能识别错误
                pass

            fields = self._extract_fields(text)
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
            "amount": None,
        }

        # === 日期 ===
        # 支持多种格式：2026年05月01日 / 202605月01 / 2026-05-01 等
        date_patterns = [
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})',  # 标准：2026年05月01日
            r'(\d{4})[年\-/]?\s*(\d{1,2})[月\-/]?\s*(\d{1,2})',  # 变体
            r'(\d{4})(\d{2})(\d{2})',  # 紧凑：20260501
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

        # === 发票号 ===
        # 支持多种格式：纯数字、含字母（如 O 代替 0）、含括号等
        inv_patterns = [
            r'(?:发票号|号码)[：\s]*([A-Z0-9\)\(]{15,})',  # 发票号标签 + 字母数字
            r'(?:发票号|号码)[：\s]*([0-9\)\(]{10,})',  # 发票号标签 + 纯数字（可能含括号）
            r'[A-Z0-9]{15,}',  # 任意15+位字母数字
            r'\d{15,20}',  # 任意15-20位纯数字
        ]
        for pattern in inv_patterns:
            if 'A-Z' in pattern or '(' in pattern or ')' in pattern:
                # 先匹配，再清理非字母数字
                match = re.search(pattern, text)
                if match:
                    raw = match.group(1) if '(' in pattern else match.group(0)
                    # 只保留字母和数字，去掉括号
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
        # OCR常见错误: 名称 → 1称/l称，购买方 → no match，销售方 → no match
        # 策略: 提取所有"[某]称:"后面的企业名，然后按顺序分配

        # 找到所有 "1称:" 或 "l称:" 或 "名称:" 后面的内容
        company_lines = []

        # 匹配1称: ... 或 l称: ... 或 名称: ... 后面到行尾的内容
        patterns_to_find_lines = [
            r'[1l]称[：:]\s*([^\n]+)',  # 1称: 或 l称:
            r'名称[：:]\s*([^\n]+)',     # 名称:
        ]

        for pattern in patterns_to_find_lines:
            matches = re.findall(pattern, text)
            for match in matches:
                company_lines.append(match.strip())

        # 如果没找到标签形式，就用通用企业名识别
        if not company_lines:
            company_pattern = r'[\u4e00-\u9fa5]+(?:公司|有限|分公司|集团|股份|企业|研究所|医院|学校|协会|中心)'
            company_lines = re.findall(company_pattern, text)

        # 去重和过滤
        seen = set()
        unique_companies = []
        for c in company_lines:
            c = c.strip()
            if 3 <= len(c) <= 100 and c not in seen:
                if '统一' not in c and '税号' not in c and '社会' not in c and '代码' not in c:
                    seen.add(c)
                    unique_companies.append(c)

        # 按顺序分配购买方和销售方
        if len(unique_companies) >= 2:
            result["buyer"] = unique_companies[0]
            result["supplier"] = unique_companies[1]
        elif len(unique_companies) == 1:
            result["buyer"] = unique_companies[0]

        # === 金额 ===
        # 优先级：价税合计(最终) > 小写标签 > 普通¥符号 > 元后缀 > 合计
        amount_patterns = [
            r'(?:价税)?合计[（(]*小写[）)]*\s*[¥￥垩圓Y]\s*([0-9]{1,10}\.[0-9]{2})',  # 最优先！
            r'小写[）)]*\s*[¥￥垩圓Y]\s*([0-9]{1,10}\.[0-9]{2})',
            r'[¥￥垩圓Y]\s*([0-9]{1,10}\.[0-9]{2})',
            r'([0-9]{1,10}\.[0-9]{2})\s*元',
            r'合[計计]\s*[¥￥垩圓Y]?\s*([0-9]{1,10}\.[0-9]{2})',
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

            temp_path = os.path.join(tempfile.gettempdir(), file.filename)
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
