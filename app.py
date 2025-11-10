import os
import re
import pymupdf
from flask import Flask, render_template, request, send_from_directory, flash, redirect, url_for, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'chave_final_correta'

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
ALLOWED_EXTENSIONS = {'pdf'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)


def normalize_value(value_str):
    """
    Limpa e padroniza a string do valor para o formato encontrado no PDF de apuração (ex: 1.234,56).
    Agora inclui uma verificação para garantir que é um valor financeiro.
    """
    if not value_str or not any(char.isdigit() for char in value_str):
        return None
    
    # Remove caracteres monetários, espaços, e letras que podem acompanhar os números
    cleaned_str = value_str.strip().replace("R$", "").replace("C", "").replace("D", "").strip()

    # Se o valor já está no formato correto (contém vírgula e ponto), retorna
    if ',' in cleaned_str and '.' in cleaned_str:
        return cleaned_str
    
    # Converte o formato '1.234.56' para '1.234,56'
    if cleaned_str.count('.') > 1:
        parts = cleaned_str.rsplit('.', 1)
        return parts[0].replace('.', '') + ',' + parts[1]

    # Substitui o último ponto por vírgula se for um separador decimal
    if '.' in cleaned_str:
        parts = cleaned_str.rsplit('.', 1)
        if len(parts[1]) == 2:
             return cleaned_str.replace('.', ',')

    return cleaned_str

def extract_values_from_pdf(file_path):
    try:
        doc = pymupdf.open(file_path)
        full_text_unsplit = ""
        for page in doc:
            full_text_unsplit += page.get_text("text", sort=True)
        
        text_list = full_text_unsplit.replace('\n', ' ').split()
        doc.close()

        extracted = []
        full_text_lower = full_text_unsplit.lower()
        text = text_list

        # --- Lógica para XP ---
        if 'xp' in full_text_lower or 'xp investimentos' in full_text_lower:
            # Padrão para Títulos Públicos (saldo anterior e atual na mesma linha)
            padrao_tp = r'(\d{1,3}(?:\.\d{3})*,\d{2})\s+(\d{1,3}(?:\.\d{3})*,\d{2})\s+\d{1,3}(?:\.\d{3})*,\d+'
            matches_tp = re.findall(padrao_tp, full_text_unsplit)
            for match in matches_tp:
                extracted.append(normalize_value(match[0])) # Saldo Anterior
                extracted.append(normalize_value(match[1])) # Saldo Atual
            
            # Padrões para outros valores em extratos XP
            patterns_xp = [
                r"Saldo bruto atual:\s*R\$\s*([\d.,]+)",
                r"Total aplicado:\s*R\$\s*([\d.,]+)",
                r"Total resgatado:\s*R\$\s*([\d.,]+)",
                r"SALDO FINAL\s+[\d,.]+\s+([\d.,]+)",
                r"Rendimento Bruto\s+([\d.,]+)"
            ]
            for pattern in patterns_xp:
                 matches = re.findall(pattern, full_text_unsplit.replace("\n", " "))
                 for m in matches:
                     if m != "00,00": extracted.append(normalize_value(m))

        # --- Lógica para BANCO DO BRASIL ---
        elif 'banco do brasil' in full_text_lower or 'bb' in full_text_lower:
            resumo_sections = re.findall(r"Resumo do m[êe]s\s+(.*?)(?=Resumo do m[êe]s|Transação efetuada com sucesso)", full_text_unsplit, re.DOTALL)
            if not resumo_sections: resumo_sections = [full_text_unsplit]

            for section in resumo_sections:
                patterns = {
                    'saldo_atual': r"SALDO ATUAL\s*=\s*([\d.,]+)",
                    'aplicacoes': r"APLICAÇÕES\s*\(\+\)\s*([\d.,]+)",
                    'resgates': r"RESGATES\s*\(\-\)\s*([\d.,]+)",
                    'rendimento': r"RENDIMENTO BRUTO\s*\(\+\)\s*([\d.,]+)"
                }
                for key, pattern in patterns.items():
                    match = re.search(pattern, section.replace("\n", " "))
                    if match and match.group(1) != '0,00':
                        extracted.append(normalize_value(match.group(1)))

        # --- Lógica para CAIXA (CEF) ---
        elif 'caixa' in full_text_lower or 'cef' in full_text_lower:
            patterns = [
                r"Aplicações\s+([\d.,]+C?)",
                r"Resgates\s+([\d.,]+D?)",
                r"Saldo Bruto\*\s+([\d.,]+C?)",
                r"Rendimento Bruto no Mês\s+([\d.,]+C?)",
                r"Rendimento Bruto\s+R\$\s*([\d.,]+)",
                r"Saldo Bruto Final\s+R\$\s*([\d.,]+)"
            ]
            for pattern in patterns:
                matches = re.findall(pattern, full_text_unsplit.replace("\n", " "))
                for match in matches:
                    if match and match != '0,00':
                        extracted.append(normalize_value(match))

        # --- Lógica para BRADESCO ---
        elif 'bradesco' in full_text_lower:
            patterns = [
                r"Saldo em\s+\d{2}/\d{2}/\d{4}\s+([\d.,]+)",
                r"Saldo Final\s+[\d.,]+\s+[\d,.]+\s+([\d.,]+)",
                r"Rendimento\s+Bruto\s+([\d.,]+)",
                r"Aplicações no Período\s+([\d.,]+)",
                r"Resgates no Período\s+[\d,.]+\s+([\d.,]+)"
            ]
            for pattern in patterns:
                matches = re.findall(pattern, full_text_unsplit.replace("\n", " "))
                for match in matches:
                    if match and match not in ['0.00', '0,00']:
                        extracted.append(normalize_value(match))
        
        # --- Lógica para ITAÚ ---
        elif 'itaú' in full_text_lower:
            patterns = [
                r"SALDO BRUTO ATUAL\s+[\d,.]+\s+([\d.,]+)",
                r"APLICACOES\s+([\d.,]+)",
                r"RESGATES\s+([\d.,]+)",
                r"RENDIMENTO BRUTO NO MES\s+([\d.,]+)"
            ]
            for pattern in patterns:
                 matches = re.findall(pattern, full_text_unsplit.replace("\n", " "))
                 for match in matches:
                    if match and match != '0,00':
                        extracted.append(normalize_value(match))

        return list(set([v for v in extracted if v]))
    
    except Exception as e:
        print(f"Erro ao processar o arquivo {os.path.basename(file_path)}: {e}")
        return []

def highlight_pdf(input_pdf, output_pdf, values_to_find):
    """
    Abre um PDF e destaca todas as ocorrências dos valores financeiros fornecidos.
    """
    try:
        doc = pymupdf.open(input_pdf)
        total_highlights = 0
        for page in doc:
            for value in values_to_find:
                if not value:
                    continue
                
                areas = page.search_for(str(value))
                if areas:
                    for rect in areas:
                        page.add_highlight_annot(rect)
                    total_highlights += len(areas)
        
        if total_highlights > 0:
            doc.save(output_pdf, garbage=4, deflate=True, clean=True)
            print(f"\nSucesso! {total_highlights} marcações foram feitas.")
        else:
            print("\nNenhum valor encontrado para marcar no arquivo de apuração.")
        
        doc.close()
        return total_highlights
    except Exception as e:
        print(f"Erro ao tentar marcar o PDF '{os.path.basename(input_pdf)}': {e}")
        return 0


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- Rotas da Aplicação ---

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')


@app.route('/process', methods=['POST'])
def process_files():
    # Verifica se os arquivos foram enviados
    if 'apuracao_pdf' not in request.files or 'extratos_pdfs' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    apuracao_file = request.files['apuracao_pdf']
    extratos_files = request.files.getlist('extratos_pdfs')

    if apuracao_file.filename == '' or not any(f.filename for f in extratos_files):
        return jsonify({'error': 'Por favor, selecione o arquivo de apuração e pelo menos um extrato.'}), 400

    # Salva o arquivo de apuração
    if apuracao_file and allowed_file(apuracao_file.filename):
        apuracao_filename = secure_filename(apuracao_file.filename)
        apuracao_path = os.path.join(app.config['UPLOAD_FOLDER'], apuracao_filename)
        apuracao_file.save(apuracao_path)
    else:
        return jsonify({'error': 'Arquivo de apuração inválido.'}), 400

    all_values_to_find = []
    
    # Processa cada extrato
    for extrato_file in extratos_files:
        if extrato_file and allowed_file(extrato_file.filename):
            extrato_filename = secure_filename(extrato_file.filename)
            extrato_path = os.path.join(app.config['UPLOAD_FOLDER'], extrato_filename)
            extrato_file.save(extrato_path)
            
            extracted_data = extract_values_from_pdf(extrato_path)
            if extracted_data:
                all_values_to_find.extend(extracted_data)

    if not all_values_to_find:
        return jsonify({'error': 'Nenhum valor financeiro foi extraído dos extratos fornecidos.'}), 400

    unique_values = sorted(list(set(v for v in all_values_to_find if v)))

    output_filename = f"marcado_{apuracao_filename}"
    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)

    # Executa a marcação no PDF
    total_highlights = highlight_pdf(apuracao_path, output_path, unique_values)

    if total_highlights > 0:
        # Em vez de redirecionar, retorna um JSON com a URL do arquivo de saída
        file_url = url_for('get_output_file', filename=output_filename)
        return jsonify({'file_url': file_url, 'message': f'Processamento concluído! {total_highlights} valores marcados.'})
    else:
        return jsonify({'error': 'Nenhum valor correspondente foi encontrado para marcar no arquivo de apuração.'}), 400

@app.route('/output/<filename>')
def get_output_file(filename):
    return send_from_directory(app.config['OUTPUT_FOLDER'], filename, as_attachment=False)

if __name__ == '__main__':
    app.run(debug=True)
