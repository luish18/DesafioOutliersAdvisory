"""
Configuração central do pipeline.

Mantém em um único lugar tudo que é dependente de ambiente ou do formato dos
dados da CVM (URLs, nomes de arquivos, rótulos de situação e nomes de colunas).
Assim, se a CVM mudar um nome de coluna ou um rótulo, a correção é pontual.
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# Fontes de dados (Portal de Dados Abertos da CVM)
# --------------------------------------------------------------------------- #
CVM_BASE_URL = "https://dados.cvm.gov.br/dados/FI/CAD"
CVM_DADOS_ZIP_URL = f"{CVM_BASE_URL}/DADOS/registro_fundo_classe.zip"
CVM_META_ZIP_URL = f"{CVM_BASE_URL}/META/meta_registro_fundo_classe.zip"

# Arquivo de interesse dentro do ZIP (conforme enunciado do desafio).
REGISTRO_FUNDO_CSV = "registro_fundo.csv"

# A CVM publica os arquivos em latin-1 (ISO-8859-1) e separados por ";".
CSV_ENCODING = "latin-1"
CSV_SEP = ";"

# --------------------------------------------------------------------------- #
# Diretórios locais
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = PROJECT_ROOT / "data_cache"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "reports"

# Reaproveita o download se o arquivo em cache tiver menos que este tempo de vida.
CACHE_MAX_AGE_HOURS = 12

# --------------------------------------------------------------------------- #
# Rótulos de situação usados pela CVM (coluna "Situacao")
# --------------------------------------------------------------------------- #
SIT_NORMAL = "Em Funcionamento Normal"
SIT_PRE_OPERACIONAL = "Fase Pré-Operacional"
SIT_LIQUIDACAO = "Em Liquidação"
SIT_CANCELADO = "Cancelado"

# --------------------------------------------------------------------------- #
# Colunas do dataset registro_fundo.csv que o pipeline efetivamente usa
# (documentadas em meta_registro_fundo.txt).
# --------------------------------------------------------------------------- #
COL_ID = "ID_Registro_Fundo"
COL_CNPJ = "CNPJ_Fundo"
COL_TIPO = "Tipo_Fundo"
COL_NOME = "Denominacao_Social"
COL_SITUACAO = "Situacao"
COL_GESTOR = "Gestor"
COL_ADMINISTRADOR = "Administrador"
COL_PL = "Patrimonio_Liquido"

# Colunas de data relevantes para detecção de movimentações.
COL_DATA_REGISTRO = "Data_Registro"          # entrada do fundo no cadastro (novos)
COL_DATA_INICIO_SIT = "Data_Inicio_Situacao"  # início da situação atual
COL_DATA_CANCELAMENTO = "Data_Cancelamento"   # data do cancelamento

# Todas as colunas tratadas como data (parseadas para datetime).
DATE_COLUMNS = [
    COL_DATA_REGISTRO,
    COL_DATA_INICIO_SIT,
    COL_DATA_CANCELAMENTO,
    "Data_Constituicao",
    "Data_Patrimonio_Liquido",
    "Data_Adaptacao_RCVM175",
]
