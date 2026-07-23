"""Regras de normalização dos informes mensais de FIDC da CVM.

Os arquivos publicados pela CVM chegam com um conjunto conhecido de
inconsistências que precisam ser resolvidas antes de qualquer cálculo:

* encoding ``latin-1`` e separador ``;``;
* acentuação divergente para o *mesmo* rótulo entre tabelas — a ``tab_X_1``
  publica ``"Subclasse Sênior Série 1"`` e a ``tab_X_2`` publica
  ``"Subclasse Senior Série 1"`` para a mesma subclasse;
* pipe residual no rótulo quando o nome da subclasse vem vazio
  (``"Subclasse Subordinada  |"``);
* quebra de layout da Resolução CVM 175: até 2022 a chave é ``CNPJ_FUNDO`` e o
  rótulo é ``"Classe Subordinada 1"``; a partir de 2023 a chave é
  ``CNPJ_FUNDO_CLASSE`` e o rótulo vira ``"Subclasse Subordinada ..."``;
* CNPJ ora mascarado (``05.754.060/0001-13``), ora só com dígitos
  (``05754060000113``), com placeholders ``00000000000000``;
* campos numéricos textuais, eventualmente vazios.

As regras ficam expressas como macros SQL do DuckDB e não como funções Python:
o volume é de dezenas de milhões de linhas e uma UDF Python seria o gargalo do
pipeline. Concentrar tudo aqui mantém uma única fonte de verdade — o
:mod:`loader` aplica as macros e os testes exercitam exatamente as mesmas.
"""

import logging

import duckdb


logger = logging.getLogger(__name__)


# Cada macro é independente e idempotente (``CREATE OR REPLACE``), de modo que
# instalar duas vezes na mesma conexão é inofensivo.
MACROS_SQL = r"""
-- Texto canônico: sem acento, caixa alta, espaços colapsados, vazio -> NULL.
-- É o que permite casar "Sênior" (tab_X_1) com "Senior" (tab_X_2).
CREATE OR REPLACE MACRO norm_texto(s) AS
    nullif(trim(regexp_replace(upper(strip_accents(coalesce(s, ''))), '\s+', ' ', 'g')), '');

-- Texto de exibição: preserva acentuação, apenas colapsa espaços.
-- Denominações sociais da CVM vêm com espaços duplicados no meio do nome.
CREATE OR REPLACE MACRO norm_exibicao(s) AS
    nullif(trim(regexp_replace(coalesce(s, ''), '\s+', ' ', 'g')), '');

CREATE OR REPLACE MACRO so_digitos(s) AS
    regexp_replace(coalesce(s, ''), '[^0-9]', '', 'g');

-- CNPJ canônico: 14 dígitos sem máscara. Placeholders zerados (usados pela CVM
-- quando o campo não se aplica) viram NULL em vez de virar um "fundo" fantasma.
CREATE OR REPLACE MACRO norm_cnpj(s) AS
    CASE
        WHEN length(so_digitos(s)) <> 14 THEN NULL
        WHEN so_digitos(s) = repeat('0', 14) THEN NULL
        ELSE so_digitos(s)
    END;

-- Reaplica a máscara para o arquivo de saída (formato esperado pelo negócio).
CREATE OR REPLACE MACRO fmt_cnpj(s) AS
    CASE WHEN norm_cnpj(s) IS NULL THEN NULL ELSE
        substr(norm_cnpj(s), 1, 2) || '.' ||
        substr(norm_cnpj(s), 3, 3) || '.' ||
        substr(norm_cnpj(s), 6, 3) || '/' ||
        substr(norm_cnpj(s), 9, 4) || '-' ||
        substr(norm_cnpj(s), 13, 2)
    END;

-- Decimal tolerante aos dois formatos. A remoção do separador de milhar só
-- acontece quando há vírgula decimal explícita, senão "1.234" (formato en-US,
-- que é o usado nestes arquivos) seria lido como 1234.
CREATE OR REPLACE MACRO norm_decimal(s) AS
    try_cast(
        CASE
            WHEN nullif(trim(coalesce(s, '')), '') IS NULL THEN NULL
            WHEN contains(s, ',') THEN replace(replace(trim(s), '.', ''), ',', '.')
            ELSE trim(s)
        END
    AS DECIMAL(28, 10));

CREATE OR REPLACE MACRO norm_inteiro(s) AS
    try_cast(nullif(trim(coalesce(s, '')), '') AS BIGINT);

-- DT_COMPTC vem como YYYY-MM-DD nos layouts atuais; o fallback dd/mm/yyyy
-- cobre arquivos históricos mais antigos.
CREATE OR REPLACE MACRO norm_data(s) AS
    try_cast(coalesce(
        try_strptime(trim(coalesce(s, '')), '%Y-%m-%d'),
        try_strptime(trim(coalesce(s, '')), '%d/%m/%Y')
    ) AS DATE);

-- Rótulo canônico da subclasse: texto canônico, sem o pipe residual e sem o
-- prefixo "SUBCLASSE"/"CLASSE" — é o prefixo que muda entre o layout pré e
-- pós Resolução 175, então removê-lo aproxima as duas gerações de arquivo.
CREATE OR REPLACE MACRO norm_subclasse(s) AS
    nullif(
        regexp_replace(
            trim(regexp_replace(
                regexp_replace(coalesce(norm_texto(s), ''), '[|]', ' ', 'g'),
                '\s+', ' ', 'g')),
            '^(SUB)?CLASSE\s+', ''),
    '');

-- Classificação da subclasse. MEZANINO é testado primeiro porque a CVM o
-- publica como "Subclasse Subordinada Mezanino 1" — casar por SUBORDINADA
-- antes engoliria toda a camada mezanino.
CREATE OR REPLACE MACRO tipo_subclasse(s) AS
    CASE
        WHEN norm_subclasse(s) IS NULL THEN 'NAO INFORMADO'
        WHEN contains(norm_subclasse(s), 'MEZANINO') THEN 'MEZANINO'
        WHEN contains(norm_subclasse(s), 'SENIOR') THEN 'SENIOR'
        WHEN contains(norm_subclasse(s), 'SUBORDINAD') THEN 'SUBORDINADA'
        WHEN contains(norm_subclasse(s), 'UNICA') THEN 'UNICA'
        ELSE 'OUTRA'
    END;

-- Competência YYYY-MM, usada como chave de janela nos cálculos de retorno.
CREATE OR REPLACE MACRO competencia(d) AS strftime(d, '%Y-%m');

-- Extrai o sufixo do nome do arquivo: AAAAMM nos arquivos mensais
-- (…_tab_X_2_202606.csv) ou AAAA nos anuais, usados até 2018
-- (…_tab_X_2_2018.csv). Serve para auditar divergências entre o arquivo e o
-- DT_COMPTC das linhas.
CREATE OR REPLACE MACRO competencia_arquivo(caminho) AS
    nullif(regexp_extract(coalesce(caminho, ''), '(\d{4,6})\.csv$', 1), '');
"""


def install_macros(con: duckdb.DuckDBPyConnection) -> None:
    """Registra as macros de normalização na conexão."""
    con.execute(MACROS_SQL)
    logger.debug("Macros de normalização instaladas")


def new_connection(db_path: str | None = None) -> duckdb.DuckDBPyConnection:
    """Abre uma conexão DuckDB já com as macros instaladas."""
    con = duckdb.connect(db_path) if db_path else duckdb.connect()
    install_macros(con)
    return con
