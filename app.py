from flask import Flask, render_template, request, send_from_directory, jsonify, url_for
from werkzeug.utils import secure_filename
import os
import re
import io
import pymupdf  # PyMuPDF
import mysql.connector
import pytesseract
from PIL import Image, ImageEnhance

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER


# --- DB CONNECTION ---
def get_db_connection():
    return mysql.connector.connect(
        user='augusto',
        password='somma13',
        host='smiconsult.com.br',
        database='smiconsult'
    )


def get_fund_name_from_db(cnpj_raw):
    if not cnpj_raw: return None
    clean_cnpj = re.sub(r'[^\d]', '', cnpj_raw)
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = f"""
            SELECT nome_fundo FROM gerador_fundos
            WHERE (cnpj_fundo LIKE '%{cnpj_raw}%' OR cnpj_fundo LIKE '%{clean_cnpj}%')
            ORDER BY periodo_fundo DESC LIMIT 1
        """
        cursor.execute(query)
        result = cursor.fetchone()
        conn.close()
        if result: return result[0]
    except Exception as e:
        print(f"   [DB ERRO] Falha ao buscar nome para CNPJ {cnpj_raw}: {e}")
    return None


# --- HELPERS ---
def detect_text_orientation(image):
    try:
        custom_config = r'--psm 0'
        result = pytesseract.image_to_osd(image, config=custom_config)
        for line in result.split('\n'):
            if 'Rotate' in line: return int(line.split(':')[1].strip())
    except:
        return 0
    return 0


def rotate_image(image, angle):
    return image.rotate(angle, expand=True)


def detect_bank_type(text):
    text_upper = text.upper()
    if "ITAÚ" in text_upper or "ITAU" in text_upper: return '341'
    if "CAIXA" in text_upper or "FUNDO DE INVESTIMENTO" in text_upper: return '104'
    if "BANCO DO BRASIL" in text_upper or "BB" in text_upper: return '1'
    if "BRADESCO" in text_upper: return '237'
    if "SAFRA" in text_upper: return '41'
    return '11'


# --- NOVA LÓGICA: Extrair Conta ---
def extract_account_hint(filename):
    base = os.path.splitext(filename)[0]
    match = re.search(r'(\d+-[0-9xX])$', base, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None


# --- EXTRAÇÃO DE DADOS ---
def extract_advanced_data(pdf_path):
    filename = os.path.basename(pdf_path)
    print(f"\n--- Processando: {filename} ---")

    account_hint = extract_account_hint(filename)
    if account_hint:
        print(f"   [DICA] Conta identificada no arquivo: {account_hint}")

    extracted_data = {
        'cnpjs': [],
        'target_names': [],
        'account_hint': account_hint,
        'valores': [],
        'banco_detectado': None
    }

    doc = pymupdf.open(pdf_path)

    pg = doc.load_page(0)
    if len(pg.get_text()) == 0:
        ocr = True
        print("   [INFO] PDF é imagem. Ativando OCR...")
    else:
        ocr = False

    full_text_accumulated = ""

    for page in doc:
        text = ""
        split_text = []

        if ocr:
            matrix = pymupdf.Matrix(2, 2)
            pix = page.get_pixmap(matrix=matrix)
            img = Image.open(io.BytesIO(pix.tobytes()))
            img = img.convert('L')
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(2)
            rot = detect_text_orientation(img)
            if rot != 0: img = rotate_image(img, -rot)
            text = pytesseract.image_to_string(img, lang='por', config=r'--oem 3 --psm 6')
            split_text = text.split()
        else:
            page.set_rotation((page.rotation + 90) % 360)
            raw_text = page.get_text()
            text = raw_text
            split_text = raw_text.split()

        full_text_accumulated += text

        if not extracted_data['banco_detectado']:
            extracted_data['banco_detectado'] = detect_bank_type(text)
            print(f"   [INFO] Banco Detectado: {extracted_data['banco_detectado']}")

        extrato = extracted_data['banco_detectado']

        # BUSCA CNPJs
        cnpjs_found = re.findall(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', text)
        for c in cnpjs_found: extracted_data['cnpjs'].append(c)

        if extrato == '341':
            for i in range(len(split_text)):
                if split_text[i] == 'CNPJ' and (i + 2 < len(split_text)) and split_text[i + 2] == 'Taxa':
                    extracted_data['cnpjs'].append(split_text[i + 1])

        # --- BUSCA VALORES (ARRASTÃO) ---
        if extrato in ['1', '11', '104', '237', '41', '341']:

            # 1. Regex Padrão (Isolado)
            # Captura 1.000,00 isolado
            raw_values = re.findall(r'(?:\s|^)([1-9]\d{0,2}(?:\.\d{3})*,\d{2})(?:\s|$)', text)
            extracted_data['valores'].extend(raw_values)

            # 2. Regex Específico CAIXA (Sufixo C/D)
            # Captura 1.000,00C ou 1.000,00 D
            # O grupo de captura é apenas o número
            if extrato == '104':
                caixa_values = re.findall(r'(?:\s|^)([1-9]\d{0,2}(?:\.\d{3})*,\d{2})(?:\s*[CDcd])(?:\s|$)', text)
                extracted_data['valores'].extend(caixa_values)

            # 3. Regex por Contexto (Palavras-chave)
            keywords = r'(?:SALDO|TOTAL|VALOR|BRUTO|LÍQUIDO|ATUAL|FINAL|RESGATADO|APLICADO)'
            context_values = re.findall(rf'{keywords}.*?([1-9]\d{{0,2}}(?:\.\d{{3}})*,\d{{2}})', text, re.IGNORECASE)
            extracted_data['valores'].extend(context_values)

    extracted_data['cnpjs'] = list(set(extracted_data['cnpjs']))

    for cnpj in extracted_data['cnpjs']:
        nome = get_fund_name_from_db(cnpj)
        if nome:
            extracted_data['target_names'].append(nome)
            print(f"   [DB] {cnpj} -> {nome}")

    # --- LIMPEZA DE VALORES (Remover C, D, R$, Espaços) ---
    clean_vals = []
    for v in extracted_data['valores']:
        if not v: continue

        # Remove letras e símbolos, mantém apenas dígitos, ponto e vírgula
        # Isso garante que '1.000,00C' vire '1.000,00'
        v_clean = re.sub(r'[^\d,.]', '', v)

        try:
            # Valida se é um número válido > 0
            check_v = v_clean.replace('.', '').replace(',', '.')
            if float(check_v) > 0:
                clean_vals.append(v_clean)
        except:
            pass

    extracted_data['valores'] = list(set(clean_vals))
    print(f"   [VALORES] Encontrados {len(extracted_data['valores'])} candidatos.")

    return extracted_data


# --- AUDITORIA INTELIGENTE ---
def highlight_audit_file(apuracao_path, output_path, extratos_data):
    print("\n========== INICIANDO MARCAÇÃO CRUZADA ==========")
    doc = pymupdf.open(apuracao_path)
    total_marks = 0

    for item in extratos_data:
        targets = item['target_names']
        valores = item['valores']
        account_hint = item['account_hint']
        banco = item.get('banco_detectado', '?')

        if not targets and not account_hint: continue

        print(f">> Auditando: {targets} (Conta Dica: {account_hint})")

        found_account = False

        for page in doc:

            # ESTRATÉGIA 1: CONTA (Esquerda)
            if account_hint and len(account_hint) >= 3:
                account_instances = page.search_for(account_hint)
                valid_account_instances = [rect for rect in account_instances if rect.x0 < 200]

                if valid_account_instances:
                    print(f"   [ALVO] Conta {account_hint} localizada!")
                    found_account = True
                    for rect in valid_account_instances:
                        annot = page.add_highlight_annot(rect)
                        annot.set_colors(stroke=(1, 0.6, 0))
                        annot.update()

                        search_rect = pymupdf.Rect(0, rect.y0 - 2, page.rect.width, rect.y1 + 2)

                        for val in valores:
                            val_instances = page.search_for(val, clip=search_rect)
                            if val_instances:
                                print(f"      [CONFIRMADO] Valor {val} batido!")
                                for v_rect in val_instances:
                                    annot_v = page.add_highlight_annot(v_rect)
                                    annot_v.set_colors(stroke=(0, 1, 0))  # Verde
                                    annot_v.update()
                                    total_marks += 1

            # ESTRATÉGIA 2: NOME DO FUNDO (Fallback)
            if not found_account:
                for target_name in targets:
                    if len(target_name) < 5: continue
                    text_instances = page.search_for(target_name)
                    valid_name_instances = [rect for rect in text_instances if rect.x0 < 300]

                    if valid_name_instances:
                        print(f"   [ALVO] Localizado pelo NOME: {target_name}")
                        for rect in valid_name_instances:
                            annot = page.add_highlight_annot(rect)
                            annot.set_colors(stroke=(0, 0.8, 1))
                            annot.update()

                            search_rect = pymupdf.Rect(0, rect.y0 - 2, page.rect.width, rect.y1 + 2)

                            for val in valores:
                                val_instances = page.search_for(val, clip=search_rect)
                                if val_instances:
                                    print(f"      [CONFIRMADO] Valor {val} batido!")
                                    for v_rect in val_instances:
                                        annot_v = page.add_highlight_annot(v_rect)
                                        annot_v.set_colors(stroke=(0, 1, 0))
                                        annot_v.update()
                                        total_marks += 1

    doc.save(output_path)
    return total_marks


# --- ROTAS ---
@app.route('/process', methods=['POST'])
def process_audit():
    if 'apuracao_pdf' not in request.files: return jsonify({'error': 'Erro arquivo'}), 400
    apuracao = request.files['apuracao_pdf']
    extratos = request.files.getlist('extratos_pdfs')

    path_apuracao = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(apuracao.filename))
    apuracao.save(path_apuracao)

    dados_audit = []
    for ext in extratos:
        if not ext.filename: continue
        p = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(ext.filename))
        ext.save(p)
        res = extract_advanced_data(p)
        if res:
            dados_audit.append(res)

    filename_out = "Auditoria_" + secure_filename(apuracao.filename)
    path_out = os.path.join(app.config['OUTPUT_FOLDER'], filename_out)

    total = highlight_audit_file(path_apuracao, path_out, dados_audit)

    msg = f"Auditoria Concluída! {total} itens validados."
    return jsonify({'message': msg, 'file_url': url_for('download_file', filename=filename_out)})


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)


@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True, port=5001, host='0.0.0.0')
