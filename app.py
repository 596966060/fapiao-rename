#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
发票批量重命名工具 - 完全重写版
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

# PDF 和图像处理
from PIL import Image
import numpy as np
import cv2

# OCR
import easyocr

# PDF 生成
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

# Windows 编码
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ======================== 发票提取（简化版） ========================

class InvoiceExtractor:
    """发票 OCR 提取 - 简化版"""

    def __init__(self, reader):
        self.reader = reader

    def _pdf_to_image(self, pdf_path: str) -> np.ndarray:
        """PDF 转图片 - 使用 pdf2image"""
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

    def extract(self, file_path: str) -> dict:
        """提取发票信息"""
        try:
            file_path = Path(file_path)
            ext = file_path.suffix.lower()

            # 转换为图片
            if ext == ".pdf":
                image_array = self._pdf_to_image(str(file_path))
            else:
                image_array = self._image_file_to_array(str(file_path))

            if image_array is None or image_array.size == 0:
                raise Exception("无法读取图像")

            # 图像预处理 - 提高 OCR 质量
            image_array = self._preprocess_image(image_array)

            # OCR
            results = self.reader.readtext(image_array, detail=0)
            if not results:
                raise Exception("OCR 无法识别")

            text = '\n'.join(results)

            # 提取字段
            fields = self._extract_fields(text)
            fields['_raw_text'] = text
            return fields
        except Exception as e:
            raise Exception(f"提取失败: {e}")

    def _preprocess_image(self, image: np.ndarray) -> np.ndarray:
        """图像预处理 - 提高 OCR 质量"""
        try:
            # 1. 转灰度
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # 2. 自适应对比度增强（CLAHE）
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)

            # 3. 二值化（自动阈值）
            _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            # 4. 轻度去噪
            denoised = cv2.medianBlur(binary, 3)

            # 5. 转回 BGR 供 OCR 使用
            result = cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)

            return result
        except:
            # 预处理失败，返回原始图像
            return image

    def _extract_fields(self, text: str) -> dict:
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
        # 优先找标记为"发票号"的，否则找最长数字串
        inv_match = re.search(r'(?:发票号|号码)[：\s]*(\d{6,20})', text)
        if inv_match:
            result["invoice_number"] = inv_match.group(1)
        else:
            all_numbers = re.findall(r'\d{6,20}', text)
            if all_numbers:
                result["invoice_number"] = all_numbers[0]

        # ===== 购买方和销售方提取 =====
        # 策略：找所有企业名（包含"公司"、"有限"、"集团"等关键字）
        company_pattern = r'[\u4e00-\u9fa5]+(?:公司|有限|分公司|集团|股份|企业|研究所|医院|学校|协会|中心|所|室|部|局|委|院)[\u4e00-\u9fa5]*'
        companies = re.findall(company_pattern, text)

        # 去重同时保留顺序
        seen = set()
        unique_companies = []
        for c in companies:
            c = c.strip()
            if len(c) >= 3 and c not in seen and '统一' not in c and '税号' not in c:
                seen.add(c)
                unique_companies.append(c)

        # 最多取前两个不同的企业名
        if len(unique_companies) >= 2:
            # 根据"购买方"和"销售方"的位置判断顺序
            buyer_idx = text.find('购买')
            supplier_idx = text.find('销售')

            # 如果都找不到，用"买"和"卖"
            if buyer_idx == -1:
                buyer_idx = text.find('买')
            if supplier_idx == -1:
                supplier_idx = text.find('卖')

            if buyer_idx >= 0 and supplier_idx >= 0:
                if buyer_idx < supplier_idx:
                    result["buyer"] = unique_companies[0]
                    result["supplier"] = unique_companies[1]
                else:
                    result["supplier"] = unique_companies[0]
                    result["buyer"] = unique_companies[1]
            else:
                # 如果找不到标记，就按顺序
                result["buyer"] = unique_companies[0]
                if len(unique_companies) > 1:
                    result["supplier"] = unique_companies[1]

        elif len(unique_companies) == 1:
            result["buyer"] = unique_companies[0]

        # ===== 金额提取 =====
        # 处理各种OCR误识的符号：¥、￥、垩、圓等
        amount_patterns = [
            r'小写[）)]*\s*[¥￥垩圓]\s*([0-9]{1,10}\.[0-9]{2})',  # 标明"小写"的最优先
            r'[¥￥垩圓]\s*([0-9]{1,10}\.[0-9]{2})',  # 任何货币符号
            r'([0-9]{1,10}\.[0-9]{2})\s*元',  # "元"后缀
            r'合.*计.*[¥￥垩圓]?\s*([0-9]{1,10}\.[0-9]{2})',  # "合计"附近
        ]

        for pattern in amount_patterns:
            matches = re.findall(pattern, text)
            if matches:
                try:
                    # 取最大金额（通常是总额）
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
    """处理上传"""
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
                    'error': '不支持的格式'
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
        session_id = datetime.now().strftime('%Y%m%d%H%M%S%f')[-15:]
        UPLOAD_RESULTS[session_id] = {'results': results}

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
    """下载重命名列表为 PDF"""
    if session_id not in UPLOAD_RESULTS:
        return jsonify({'error': '会话已过期'}), 400

    results = UPLOAD_RESULTS[session_id]['results']

    # 生成 PDF
    pdf_buffer = io.BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, encoding='utf-8')
    styles = getSampleStyleSheet()
    story = []

    # 标题
    title_style = ParagraphStyle(
        'Title',
        parent=styles['Heading1'],
        fontSize=14,
        textColor=colors.HexColor('#667eea'),
        alignment=TA_CENTER,
        spaceAfter=20
    )
    story.append(Paragraph('发票批量重命名结果', title_style))

    # 统计
    total = len(results)
    success = len([r for r in results if r['status'] == 'success'])
    failed = total - success
    story.append(Paragraph(f'总计: {total} | 成功: {success} | 失败: {failed}', styles['Normal']))
    story.append(Spacer(1, 0.5*cm))

    # 表格
    table_data = [['原文件名', '新文件名', '日期', '发票号', '金额']]
    for item in results:
        if item['status'] == 'success':
            data = item.get('data', {})
            table_data.append([
                item['filename'][:20],
                item['new_name'][:30],
                data.get('date', '-'),
                data.get('invoice_number', '-')[:12],
                data.get('amount', '-')
            ])
        else:
            table_data.append([
                item['filename'][:20],
                f"错误: {item['error'][:20]}",
                '-', '-', '-'
            ])

    table = Table(table_data, colWidths=[2*cm, 2.5*cm, 1.8*cm, 1.8*cm, 1.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#667eea')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
    ]))
    story.append(table)

    # 生成 PDF
    doc.build(story)
    pdf_buffer.seek(0)

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'发票结果_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
    )


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

    # 后台初始化 OCR
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
