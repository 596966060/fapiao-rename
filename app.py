#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发票批量重命名工具 - 纯网页版
支持批量上传、识别、重命名、下载
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

# Windows 编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import easyocr
import cv2
import numpy as np
import fitz

# ======================== 发票提取 ========================

class InvoiceExtractor:
    """发票 OCR 提取"""

    def __init__(self, reader):
        self.reader = reader

    def _pdf_to_image(self, pdf_path: str) -> np.ndarray:
        try:
            doc = fitz.open(pdf_path)
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(0.75, 0.75))
            image_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n
            )
            if pix.n == 3:
                image_array = cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR)
            elif pix.n == 4:
                image_array = cv2.cvtColor(image_array, cv2.COLOR_RGBA2BGR)
            doc.close()
            return image_array
        except Exception as e:
            raise Exception(f"PDF 转图片失败: {e}")

    def _image_file_to_array(self, image_path: str) -> np.ndarray:
        try:
            image_bytes = np.fromfile(image_path, dtype=np.uint8)
            image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
            if image is None:
                raise Exception("无法读取图片")
            return image
        except Exception as e:
            raise Exception(f"图片读取失败: {e}")

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

            # 图像增强
            gray = cv2.cvtColor(image_array, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(10, 10))
            enhanced = clahe.apply(gray)
            enhanced_bgr = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

            # OCR
            results = self.reader.readtext(enhanced_bgr, detail=0)
            if not results:
                raise Exception("OCR 无法识别")

            text = '\n'.join(results)

            # 提取字段
            fields = self._extract_fields(text)
            # 保存原始文本用于调试
            fields['_raw_text'] = text
            return fields
        except Exception as e:
            raise Exception(f"提取失败: {e}")

    def _extract_fields(self, text: str) -> dict:
        result = {
            "date": None,
            "invoice_number": None,
            "buyer": None,
            "supplier": None,
            "amount": None,
        }

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

        # 发票号 - 多个模式
        for pattern in [r'发票(?:代)?号[码]*[：:\s]*([0-9]{6,20})',
                        r'编号[：:\s]*([0-9]{6,20})',
                        r'发票号码[：:\s]*([0-9]{6,20})',
                        r'([0-9]{15,20})']:
            match = re.search(pattern, text)
            if match and 6 <= len(match.group(1)) <= 20:
                result["invoice_number"] = match.group(1)
                break

        # 购买方 - 使用"购买方信息"标签
        match = re.search(r'购买方信息[：:\s]*([^\n]+?)(?:税号|统一|纳税|$)', text)
        if match:
            buyer = match.group(1).strip()
            # 过滤掉太短或不合理的
            if len(buyer) > 2 and buyer not in ['信息', '-', '']:
                result["buyer"] = buyer[:80]

        # 销售方 - 使用"销售方信息"标签
        match = re.search(r'销售方信息[：:\s]*([^\n]+?)(?:税号|统一|纳税|$)', text)
        if match:
            supplier = match.group(1).strip()
            if len(supplier) > 2 and supplier not in ['信息', '-', '']:
                result["supplier"] = supplier[:80]

        # 金额 - 优先"价税合计"
        for pattern in [r'价税合计[（(]小写[）)][：:\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})',
                        r'价税合计[：:\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})',
                        r'合计[（(]小写[）)][：:\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})',
                        r'合计[：:\s]*[¥￥]?\s*([0-9]{1,10}\.[0-9]{2})',
                        r'[¥￥]\s*([0-9]{1,10}\.[0-9]{2})元?']:
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
    date = data.get('date') or '2026-01-01'
    invoice_num = data.get('invoice_number') or '未知'
    buyer = (data.get('buyer') or '')[:30]
    supplier = (data.get('supplier') or '')[:30]
    amount = data.get('amount') or '0.00'

    new_name = f"{date}_{invoice_num}_{buyer}_{supplier}_{amount}元{original_ext}"
    new_name = re.sub(r'[\\/:*?"<>|]', '', new_name)
    new_name = re.sub(r'_+', '_', new_name)
    new_name = new_name.strip('_ ')

    # 确保返回非空字符串
    return new_name if new_name else f"invoice_{datetime.now().strftime('%Y%m%d%H%M%S')}{original_ext}"


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

def get_reader():
    """获取 OCR reader，如未准备好则初始化"""
    global READER, READER_READY

    if READER_READY and READER is not None:
        return READER

    # 如果没有准备好，同步初始化
    if READER is None:
        init_ocr_background()

    return READER


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


@app.route('/api/debug/<session_id>')
def debug_ocr(session_id):
    """调试：查看原始 OCR 输出"""
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400

    # 返回原始数据用于调试
    return jsonify({
        'session_id': session_id,
        'results': UPLOAD_RESULTS[session_id]['results']
    })


@app.route('/api/debug-ocr', methods=['POST'])
def debug_ocr_endpoint():
    """调试端点：返回原始 OCR 文本"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': '未选择文件'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': '未选择文件'}), 400

        if not READER_READY:
            return jsonify({'error': 'OCR 还未初始化'}), 503

        ext = Path(file.filename).suffix.lower()
        if ext not in {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif'}:
            return jsonify({'error': '不支持的格式'}), 400

        temp_path = os.path.join(tempfile.gettempdir(), file.filename)
        file.save(temp_path)

        try:
            extractor = InvoiceExtractor(READER)
            data = extractor.extract(temp_path)

            return jsonify({
                'filename': file.filename,
                'raw_text': data.get('_raw_text', ''),
                'extracted': {
                    'date': data.get('date'),
                    'invoice_number': data.get('invoice_number'),
                    'buyer': data.get('buyer'),
                    'supplier': data.get('supplier'),
                    'amount': data.get('amount')
                }
            })
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/upload', methods=['POST'])
def upload_file():
    """处理上传"""
    try:
        if 'files[]' not in request.files:
            return jsonify({'error': '未选择文件'}), 400

        if not READER_READY:
            return jsonify({'error': 'OCR 还未初始化，请稍候片刻后重试'}), 503

        files = request.files.getlist('files[]')
        reader = get_reader()

        if reader is None:
            return jsonify({'error': 'OCR 初始化失败'}), 500

        extractor = InvoiceExtractor(reader)

        results = []
        temp_dir = tempfile.gettempdir()

        for file in files:
            if file.filename == '':
                continue

            ext = Path(file.filename).suffix.lower()
            allowed_ext = {'.pdf', '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.zip'}

            if ext not in allowed_ext:
                results.append({
                    'filename': file.filename,
                    'status': 'error',
                    'error': f'不支持的格式'
                })
                continue

            temp_path = os.path.join(temp_dir, file.filename)
            file.save(temp_path)

            try:
                if ext == '.zip':
                    zip_results = process_zip_file(temp_path, extractor)
                    results.extend(zip_results)
                else:
                    data = extractor.extract(temp_path)
                    new_name = generate_filename(data, ext)
                    results.append({
                        'filename': file.filename,
                        'new_name': new_name or 'Unknown',
                        'data': data or {},
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

        # 保存结果
        session_id = datetime.now().strftime('%Y%m%d%H%M%S')
        UPLOAD_RESULTS[session_id] = {
            'results': results,
            'upload_dir': temp_dir
        }

        return jsonify({
            'session_id': session_id,
            'total': len(results),
            'results': results
        })

    except Exception as e:
        return jsonify({'error': f'处理失败: {str(e)}'}), 500


def process_zip_file(zip_path: str, extractor: InvoiceExtractor) -> list:
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
                        results.append({
                            'filename': file,
                            'new_name': new_name or 'Unknown',
                            'data': data or {},
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
    """下载重命名列表"""
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400

    session_data = UPLOAD_RESULTS[session_id]
    results = session_data['results']

    output_zip = io.BytesIO()

    with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        # 添加重命名列表
        z.writestr("重命名列表.txt", generate_list_text(results))

    output_zip.seek(0)

    return send_file(
        output_zip,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'发票重命名列表_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip'
    )


def generate_list_text(results: list) -> str:
    """生成重命名列表文本"""
    text = "发票重命名列表\n"
    text += "=" * 60 + "\n\n"

    for item in results:
        if item['status'] == 'success':
            text += f"原文件名: {item['filename']}\n"
            text += f"新文件名: {item['new_name']}\n"
            text += f"开票日期: {item['data'].get('date', '-')}\n"
            text += f"发票号码: {item['data'].get('invoice_number', '-')}\n"
            text += f"购买方: {item['data'].get('buyer', '-')}\n"
            text += f"销售方: {item['data'].get('supplier', '-')}\n"
            text += f"金额: {item['data'].get('amount', '-')}\n"
            text += "-" * 60 + "\n\n"

    return text


@app.errorhandler(413)
def file_too_large(e):
    return jsonify({'error': '文件过大，最大支持 500MB'}), 413


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'

    print(f"\n{'='*60}")
    print("发票批量重命名工具 - 网页版")
    print(f"{'='*60}")
    print(f"访问地址: http://127.0.0.1:{port}")
    print(f"{'='*60}\n")

    # 后台初始化 OCR（不阻塞启动）
    ocr_thread = threading.Thread(target=init_ocr_background, daemon=True)
    ocr_thread.start()

    try:
        app.run(host='0.0.0.0', port=port, debug=debug, threaded=True, use_reloader=False)
    except OSError as e:
        if "Address already in use" in str(e):
            print(f"\n❌ 错误: 端口 {port} 已被占用")
            print(f"请尝试关闭占用此端口的程序或更改端口")
            print(f"要更改端口，编辑此文件最后一行:")
            print(f"  app.run(..., port=5001, ...)")
        else:
            print(f"\n❌ 启动失败: {e}")
    except KeyboardInterrupt:
        print("\n已停止服务")
