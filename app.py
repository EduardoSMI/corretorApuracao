from flask import Flask, render_template, request, send_from_directory, jsonify, url_for
from werkzeug.utils import secure_filename
import os
import re
import pymupdf
import mysql.connector

app = Flask(__name__)
app.secret_key = 'sua_chave_secreta'

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER


def get_db_connection():
    return mysql.connector.connect(
        user='augusto', password='somma13', host='smiconsult.com.br', database='smiconsult'
    )


# --- LIMPEZA DE VALORES ---
def normalize_value(value_str):
    if not value_str: return None
    # Remove tudo que não é dígito, vírgula ou ponto
    clean = re.sub(r'[^\d.,]', '', value_str)

    # Se sobrou vazio
    if not clean: return None

    # Padrão brasileiro: 1.000,00
    if ',' in clean and '.' in clean:
        return clean

    # Corrige 1.234.56 -> 1.234,56 (Erro comum de OCR)
    if clean.count('.') > 1:
        parts = clean.rsplit('.', 1)
        return parts[0].replace('.', '') + ',' + parts[1]

    return clean


# --- CONSULTA INTELIGENTE (A MUDANÇA CHAVE) ---
def get_target_name_from_db(identifier, tipo_identificador='CNPJ'):
    """
    Retorna o alvo da busca.
    Se for conta, retorna o PRÓPRIO NÚMERO DA CONTA para buscar a linha exata na apuração.
    Se for CNPJ, busca o nome do fundo.
    """
    print(f"   [DB] Consultando: {identifier} ({tipo_identificador})...")

    # Se for CONTA, a prioridade é achar essa conta impressa na apuração
    if tipo_identificador == 'CONTA':
        # Retorna o próprio número da conta para buscar na apuração (ex: 25165-8)
        # Isso resolve o problema de fundos com múltiplas contas
        return identifier

        # Se for CNPJ, buscamos o nome do fundo
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        nome_fundo = None

        if tipo_identificador == 'CNPJ':
            clean_cnpj = identifier.replace('.', '').replace('/', '').replace('-', '')
            query = f"""
                SELECT nome_fundo FROM gerador_fundos
                WHERE (cnpj_fundo LIKE '%{identifier}%' OR cnpj_fundo LIKE '%{clean_cnpj}%')
                ORDER BY periodo_fundo DESC LIMIT 1
            """
            cursor.execute(query)
            result = cursor.fetchone()
            if result:
                nome_fundo = result[0]

        conn.close()
        return nome_fundo
    except Exception as e:
        print(f"   [DB] ERRO: {e}")
        return None


# --- EXTRAÇÃO DE DADOS (REGEX MELHORADO) ---
def extract_data_structured(file_path):
    print(f"\n--- Processando: {os.path.basename(file_path)} ---")
    try:
        doc = pymupdf.open(file_path)
        # Usamos sort=True para tentar manter a ordem de leitura humana
        full_text = ""
        for page in doc:
            full_text += page.get_text("text", sort=True) + "\n"
        doc.close()
    except Exception as e:
        print(f"Erro PDF: {e}")
        return {'target_name': None, 'valores': []}

    # 1. Identificar Ativo (Conta tem prioridade sobre CNPJ no regex para garantir o match específico)
    identifier = None
    type_id = None

    # Regex para conta BB (ex: 25.165-8 ou 25165-8)
    conta_match = re.search(r'Conta\s+[:.]?\s*([\d.-]+)', full_text, re.IGNORECASE)
    cnpj_match = re.search(r'\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}', full_text)

    if conta_match:
        # Limpa a conta para ficar igual na apuração (ex: remove pontos extras, mantém traço)
        raw_conta = conta_match.group(1)
        identifier = raw_conta
        type_id = 'CONTA'
    elif cnpj_match:
        identifier = cnpj_match.group(0)
        type_id = 'CNPJ'

    target_name = None
    if identifier:
        target_name = get_target_name_from_db(identifier, type_id)
        if not target_name:
            target_name = identifier  # Fallback

    print(f"   Alvo definido para busca: '{target_name}' (Tipo: {type_id})")

    # 2. Extrair Valores (Regex "Guloso" que ignora quebras de linha)
    extracted_vals = []
    full_text_lower = full_text.lower()

    # Regex genérico poderoso para capturar valores monetários após palavras-chave
    # Captura: "SALDO ATUAL" ... (espaços/enters) ... "10.000,00"
    keywords = [
        r"SALDO ATUAL", r"SALDO BRUTO", r"TOTAL APLICADO", r"TOTAL RESGATADO",
        r"APLICAÇÕES", r"RESGATES", r"RENDIMENTO BRUTO", r"SALDO EM", r"SALDO FINAL"
    ]

    for key in keywords:
        # Procura a palavra chave, seguida de qualquer coisa não-digito (incluindo enters), seguida do valor
        # O [^\d]*? é o segredo para pular o " =" ou "\n"
        matches = re.findall(key + r"[^\d\n]*\n?\s*([0-9]{1,3}(?:\.[0-9]{3})*,\d{2})", full_text, re.IGNORECASE)
        for m in matches:
            val = normalize_value(m)
            if val and val != '0,00':
                extracted_vals.append(val)

    # Remove duplicatas
    valores_finais = list(set(extracted_vals))
    print(f"   Valores encontrados: {valores_finais}")

    return {'target_name': target_name, 'valores': valores_finais}


# --- MARCAÇÃO (Busca Exata) ---
def highlight_audit_file(apuracao_path, output_path, extratos_data):
    print("\n========== INICIANDO AUDITORIA ==========")
    doc = pymupdf.open(apuracao_path)
    total_marks = 0

    for item in extratos_data:
        target = item['target_name']
        valores = item['valores']

        if not target or not valores: continue

        # Limpeza do alvo para busca
        target_clean = target.strip()
        print(f">> Procurando linha de: '{target_clean}'")

        found_line = False
        for page in doc:
            # Busca onde está escrito o NOME DA CONTA ou NOME DO FUNDO
            # clip=None busca na página toda
            text_instances = page.search_for(target_clean)

            # Se não achou e é nome longo, tenta parcial
            if not text_instances and len(target_clean) > 20:
                text_instances = page.search_for(target_clean[:20])

            if text_instances:
                found_line = True
                for rect in text_instances:
                    # Define a "Zona da Linha" (Horizontal)
                    # Pegamos a altura do texto encontrado e expandimos um pouco para direita
                    y_center = (rect.y0 + rect.y1) / 2
                    line_height = rect.y1 - rect.y0

                    # Cria um retângulo que pega a largura TOTAL da página naquela altura Y
                    # y0 - 2 e y1 + 2 dá uma margem de erro para desalinhamento
                    search_rect = pymupdf.Rect(0, rect.y0 - 2, page.rect.width, rect.y1 + 2)

                    # Agora procura os valores SÓ DENTRO DESSA FAIXA
                    for val in valores:
                        val_instances = page.search_for(val, clip=search_rect)
                        if val_instances:
                            print(f"   [SUCESSO] Valor {val} validado e marcado!")
                            for v_rect in val_instances:
                                page.add_highlight_annot(v_rect)  # Marca o valor
                                # Opcional: Marcar o nome também para facilitar visualização
                                # page.add_underline_annot(rect)
                                total_marks += 1

        if not found_line:
            print(f"   [ALERTA] Não encontrei o texto '{target_clean}' na apuração.")

    doc.save(output_path)
    return total_marks


# --- ROTAS ---
@app.route('/process', methods=['POST'])
def process_audit():
    if 'apuracao_pdf' not in request.files:
        return jsonify({'error': 'Faltou o arquivo de apuração'}), 400

    apuracao = request.files['apuracao_pdf']
    extratos = request.files.getlist('extratos_pdfs')

    path_apuracao = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(apuracao.filename))
    apuracao.save(path_apuracao)

    dados_para_auditoria = []

    for ext in extratos:
        if not ext.filename: continue
        path_ext = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(ext.filename))
        ext.save(path_ext)

        data = extract_data_structured(path_ext)
        if data['target_name']:
            dados_para_auditoria.append(data)

    filename_out = "Auditoria_" + secure_filename(apuracao.filename)
    path_out = os.path.join(app.config['OUTPUT_FOLDER'], filename_out)

    total = highlight_audit_file(path_apuracao, path_out, dados_para_auditoria)

    msg = f"Concluído! {total} itens validados." if total > 0 else "Processado. Nenhum valor exato encontrado na mesma linha do ativo."

    return jsonify({
        'message': msg,
        'file_url': url_for('download_file', filename=filename_out)
    })


@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename)


@app.route('/')
def index():
    return render_template('index.html')


if __name__ == '__main__':
    app.run(debug=True, port=5001)