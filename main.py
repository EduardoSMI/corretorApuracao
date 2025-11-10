import pymupdf
import os
import re

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
    """
    Extrai valores de um extrato usando a lógica validada do app_smiconsult.py,
    adaptada para retornar uma lista de valores a serem marcados.
    """
    try:
        doc = pymupdf.open(file_path)
        full_text_unsplit = ""
        for page in doc:
            full_text_unsplit += page.get_text("text", sort=True)
        
        text_list = full_text_unsplit.replace('\n', ' ').split()
        doc.close()

        extracted = []
        full_text_lower = full_text_unsplit.lower()

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
        elif 'banco do brasil' in full_text_lower or 'bb previd' in full_text_lower:
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
        print(f"\nIniciando marcação no arquivo '{input_pdf}'...")
        total_highlights = 0
        for page in doc:
            for value in values_to_find:
                # Garante que o valor a ser buscado é uma string e não está vazio
                if not value:
                    continue
                
                areas = page.search_for(str(value))
                if areas:
                    for rect in areas:
                        page.add_highlight_annot(rect)
                        total_highlights += 1
                    print(f"  -> Valor '{value}' marcado na página {page.number + 1}.")
        
        if total_highlights > 0:
            doc.save(output_pdf)
            print(f"\nSucesso! {total_highlights} marcações foram feitas.")
            print(f"Arquivo final salvo como '{output_pdf}'.")
        else:
            print("\nNenhum valor encontrado para marcar no arquivo de apuração.")
        
        doc.close()
    except Exception as e:
        print(f"Erro ao tentar marcar o PDF '{input_pdf}': {e}")


# --- Bloco Principal de Execução ---
if __name__ == "__main__":
    input_folder = "arquivos"
    target_pdf = "Apuração.pdf"
    output_pdf = "Apuracao_marcada.pdf"

    if not os.path.isdir(input_folder):
        print(f"Erro: A pasta '{input_folder}' não foi encontrada.")
    elif not os.path.exists(target_pdf):
        print(f"Erro: O arquivo '{target_pdf}' não foi encontrado.")
    else:
        all_values_to_find = []
        print("--- Fase 1: Extraindo dados dos extratos ---")
        
        for filename in sorted(os.listdir(input_folder)):
            if filename.lower().endswith(".pdf"):
                file_path = os.path.join(input_folder, filename)
                print(f"Lendo arquivo: {filename}...")
                
                extracted_data = extract_values_from_pdf(file_path)
                
                if extracted_data:
                    print(f"  -> Valores extraídos: {extracted_data}")
                    all_values_to_find.extend(extracted_data)
        
        print("\n--- Extração concluída ---")
        
        if all_values_to_find:
            # Remove duplicatas e valores nulos
            unique_values = sorted(list(set(v for v in all_values_to_find if v)))
            print(f"Total de valores únicos a serem procurados: {len(unique_values)}")
            print(unique_values)

            # Inicia a fase de marcação no PDF de apuração
            highlight_pdf(target_pdf, output_pdf, unique_values)
        else:
            print("\nNenhum valor foi extraído dos arquivos na pasta 'arquivos'.")