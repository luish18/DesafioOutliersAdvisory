"""Tratamento e consolidação dos informes mensais de FIDC.

O pipeline lê os CSVs brutos extraídos dos ZIPs da CVM, aplica as macros de
:mod:`sanitizer` e materializa um painel mensal na granularidade de subclasse
de cota — que é a granularidade exigida pelo desafio e a base sobre a qual os
retornos de 1/6/12/24/36 meses são calculados.

Tabelas de origem efetivamente usadas (as demais são de carteira/risco e não
entram no cálculo de retorno):

===========  ======================================================
Arquivo      Conteúdo aproveitado
===========  ======================================================
``tab_I``    cadastro da classe (administrador, condomínio)
``tab_IV``   PL e PL médio, na granularidade da *classe*
``tab_X_1``  nº de cotistas por subclasse (+ ``ID_SUBCLASSE``)
``tab_X_2``  quantidade e **valor da cota** por subclasse
``tab_X_3``  rentabilidade do mês informada pelo administrador
===========  ======================================================

Duas decisões de modelagem que valem registro:

1. **A chave da série temporal é ``(cnpj_classe, subclasse)``**, e não o
   ``ID_SUBCLASSE``. O identificador oficial só existe nos layouts recentes e,
   mesmo neles, está preenchido em pouco mais da metade das linhas — não
   sustenta um histórico de 36 meses.
2. **O PL vem da ``tab_IV``, que é da classe, não da subclasse.** Como o
   desafio pede PL na linha da subclasse, o painel carrega o PL da classe e,
   ao lado, um PL implícito da subclasse (``qt_cota * vl_cota``), deixando a
   diferença explícita em vez de escondê-la.
"""

import glob
import logging
import os
from dataclasses import dataclass
from typing import Sequence

import duckdb

from config import Config
from sanitizer import new_connection


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceTable:
    """Uma das tabelas do informe mensal, espalhada em um CSV por competência."""

    name: str
    infix: str
    description: str


SOURCE_TABLES: tuple[SourceTable, ...] = (
    SourceTable("cadastro", "tab_I", "Cadastro e patrimônio da classe"),
    SourceTable("patrimonio", "tab_IV", "PL e PL médio da classe"),
    SourceTable("cotistas", "tab_X_1", "Número de cotistas por subclasse"),
    SourceTable("cotas", "tab_X_2", "Quantidade e valor da cota por subclasse"),
    SourceTable("rentabilidade", "tab_X_3", "Rentabilidade mensal informada"),
)

# Colunas que trocam de nome entre o layout pré e pós Resolução CVM 175.
# A ordem importa: o primeiro nome presente no arquivo é o que vale.
COLUMN_ALIASES: dict[str, Sequence[str]] = {
    "cnpj": ("CNPJ_FUNDO_CLASSE", "CNPJ_FUNDO"),
    "tp_fundo_classe": ("TP_FUNDO_CLASSE",),
    "id_subclasse": ("ID_SUBCLASSE",),
}


# Sufixos aceitos: até 2018 a CVM publicava um arquivo por ano (AAAA) com os
# doze meses dentro; de 2019 em diante passou a publicar um por competência
# (AAAAMM). O número de dígitos é exigido explicitamente porque um `*` faria
# `tab_X_1` casar também com `tab_X_1_1` (cotistas por tipo de investidor),
# que é outra tabela.
_SUFIXO_GLOBS = ("[0-9]" * 4, "[0-9]" * 6)


def _raw_globs(raw_dir: str, infix: str) -> list[str]:
    """Padrões que casam os arquivos de uma tabela em todas as competências."""
    return [
        os.path.join(raw_dir, "**", f"inf_mensal_fidc_{infix}_{sufixo}.csv").replace("\\", "/")
        for sufixo in _SUFIXO_GLOBS
    ]


def _read_csv_expr(files: Sequence[str], encoding: str) -> str:
    """Expressão ``read_csv`` para uma lista explícita de CSVs da CVM.

    ``all_varchar`` desliga a inferência de tipos: os casts passam a ser
    explícitos, via macros, e um valor sujo em uma competência isolada não
    derruba a leitura das outras. ``union_by_name`` absorve a mudança de
    layout de 2023 — colunas ausentes em um arquivo vêm como NULL.

    A lista é explícita (e não um glob) porque nem todos os arquivos têm o
    mesmo encoding — ver :func:`_partition_by_encoding`.
    """
    lista = ", ".join("'" + f.replace("\\", "/").replace("'", "''") + "'" for f in files)
    return (
        f"read_csv([{lista}], "
        f"delim='{Config.CSV_DELIMITER}', header=true, encoding='{encoding}', "
        "all_varchar=true, union_by_name=true, filename=true, "
        "ignore_errors=false, null_padding=true)"
    )


def decode_mixed_encoding(data: bytes) -> str:
    """Decodifica bytes que misturam latin-1 com trechos UTF-8.

    Alguns informes da CVM saem com dupla codificação: o arquivo é latin-1,
    mas um campo isolado carrega bytes UTF-8 crus. Em 2024-04, por exemplo, o
    rótulo de subclasse do FIDC Stone IV traz ``CART\\xc3\\x83O`` (UTF-8 de
    ``Ã``) enquanto a denominação social do mesmo registro traz ``CART\\xc3O``
    (latin-1 de ``Ã``). O byte ``\\x83`` não existe em ISO-8859-1 e o DuckDB
    recusa o arquivo inteiro.

    A estratégia é gulosa: onde houver uma sequência UTF-8 válida ela vence,
    senão cai para latin-1 byte a byte. Isso pode, em tese, converter por
    engano um par latin-1 que por acaso forme UTF-8 válido — por isso a função
    só é aplicada aos arquivos que o DuckDB de fato recusa, e não ao corpus.
    """
    partes: list[str] = []
    i, n = 0, len(data)
    while i < n:
        if data[i] < 0x80:
            # Trecho ASCII: consome de uma vez, é a esmagadora maioria.
            fim = i
            while fim < n and data[fim] < 0x80:
                fim += 1
            partes.append(data[i:fim].decode("ascii"))
            i = fim
            continue

        texto, consumidos = _decode_next(data, i)
        partes.append(texto)
        i += consumidos
    return "".join(partes)


def _decode_next(data: bytes, i: int) -> tuple[str, int]:
    """Decodifica um caractere não-ASCII em ``data[i:]``.

    Devolve o texto e quantos bytes foram consumidos — sempre ao menos 1, para
    o laço chamador nunca estacionar.
    """
    for tamanho in (2, 3, 4):
        trecho = data[i : i + tamanho]
        if len(trecho) < tamanho:
            break  # acabou o buffer: só resta o fallback latin-1
        try:
            return trecho.decode("utf-8"), tamanho
        except UnicodeDecodeError:
            continue
    return data[i : i + 1].decode("latin-1"), 1


def _needs_repair(path: str) -> bool:
    """Detecta bytes 0x80-0x9F, que não têm ponto de código em ISO-8859-1.

    É exatamente o critério que faz o DuckDB abortar com "File is not latin-1
    encoded".
    """
    with open(path, "rb") as arquivo:
        while chunk := arquivo.read(1 << 20):
            if any(0x80 <= b < 0xA0 for b in chunk):
                return True
    return False


def _repair_file(path: str, cache_root: str) -> str:
    """Reescreve o arquivo em UTF-8 no cache e devolve o novo caminho."""
    destino = os.path.join(cache_root, os.path.basename(path))
    if os.path.exists(destino) and os.path.getmtime(destino) >= os.path.getmtime(path):
        return destino

    os.makedirs(cache_root, exist_ok=True)
    with open(path, "rb") as origem:
        conteudo = origem.read()
    with open(destino, "w", encoding="utf-8", newline="") as saida:
        saida.write(decode_mixed_encoding(conteudo))

    logger.warning("Encoding misto corrigido: %s -> %s", path, destino)
    return destino


def _partition_by_encoding(files: Sequence[str], cache_root: str) -> tuple[list[str], list[str]]:
    """Separa os arquivos legíveis em latin-1 dos que precisaram de reparo."""
    latin1: list[str] = []
    utf8: list[str] = []
    for path in files:
        if _needs_repair(path):
            utf8.append(_repair_file(path, cache_root))
        else:
            latin1.append(path)
    return latin1, utf8


def _available_columns(con: duckdb.DuckDBPyConnection, relation: str) -> set[str]:
    return {c.upper() for c in con.sql(f"SELECT * FROM {relation} LIMIT 0").columns}


def _alias(available: set[str], logical: str, fallback: str = "NULL") -> str:
    """Resolve uma coluna lógica para as variantes presentes na view.

    Precisa ser um ``coalesce`` sobre *todas* as variantes presentes, não a
    primeira: o corpus mistura layouts. A Resolução CVM 175 trocou
    ``CNPJ_FUNDO`` por ``CNPJ_FUNDO_CLASSE`` na competência 2023-10, então uma
    base com histórico longo tem as duas colunas — escolher só uma faria as
    linhas do outro layout virarem NULL e serem descartadas em silêncio.

    Referenciar apenas o que existe também evita quebrar a query numa base que
    só tenha um dos layouts.
    """
    presentes = [c for c in COLUMN_ALIASES[logical] if c in available]
    if not presentes:
        return fallback
    if len(presentes) == 1:
        return presentes[0]
    return f"coalesce({', '.join(presentes)})"


def _discover(raw_dir: str, table: SourceTable) -> list[str]:
    """Lista os arquivos da tabela em todas as competências baixadas."""
    padroes = _raw_globs(raw_dir, table.infix)
    matches = sorted({m for padrao in padroes for m in glob.glob(padrao, recursive=True)})
    if not matches:
        logger.warning("Nenhum arquivo encontrado para %s (%s)", table.name, padroes)
    else:
        logger.info("%s: %d arquivos encontrados", table.name, len(matches))
    return matches


def create_raw_views(
    con: duckdb.DuckDBPyConnection, raw_dir: str, cache_root: str | None = None
) -> set[str]:
    """Cria uma view ``raw_<tabela>`` por tabela de origem disponível.

    Os arquivos com encoding misto são lidos em um bloco separado, em UTF-8, e
    unidos ao restante por ``UNION ALL BY NAME`` — o ``read_csv`` aceita um só
    encoding por chamada.
    """
    cache_root = cache_root or Config.REPAIR_PATH
    created: set[str] = set()
    for table in SOURCE_TABLES:
        arquivos = _discover(raw_dir, table)
        if not arquivos:
            continue

        latin1, utf8 = _partition_by_encoding(arquivos, cache_root)
        blocos = [
            f"SELECT * FROM {_read_csv_expr(grupo, encoding)}"
            for grupo, encoding in ((latin1, Config.CSV_ENCODING), (utf8, "utf-8"))
            if grupo
        ]
        con.execute(
            f"CREATE OR REPLACE VIEW raw_{table.name} AS "
            + "\nUNION ALL BY NAME\n".join(blocos)
        )
        created.add(table.name)
    if "cotas" not in created:
        raise FileNotFoundError(
            f"tab_X_2 (valor da cota) não encontrada em {raw_dir!r}: sem ela não há "
            "como calcular retornos. Rode a etapa de download antes."
        )
    return created


def build_staging(con: duckdb.DuckDBPyConnection, available_tables: set[str]) -> None:
    """Materializa as tabelas de staging já normalizadas e deduplicadas.

    A deduplicação é necessária porque a CVM republica competências corrigidas:
    a mesma ``(classe, subclasse, data)`` pode aparecer em mais de um arquivo.
    Mantemos a última ocorrência por ordem de arquivo.
    """
    _build_cotas(con)
    if "cotistas" in available_tables:
        _build_cotistas(con)
    else:
        _empty_staging(
            con, "stg_cotistas", "nr_cotistas BIGINT, id_subclasse VARCHAR, denom_social VARCHAR"
        )
    if "patrimonio" in available_tables:
        _build_patrimonio(con)
    else:
        _empty_staging(con, "stg_patrimonio", "vl_pl DECIMAL(28,10), vl_pl_medio DECIMAL(28,10)", subclasse=False)
    if "rentabilidade" in available_tables:
        _build_rentabilidade(con)
    else:
        _empty_staging(con, "stg_rentabilidade", "rentab_mes_informada_pct DECIMAL(28,10)")
    if "cadastro" in available_tables:
        _build_cadastro(con)
    else:
        _empty_staging(
            con,
            "stg_cadastro",
            "cnpj_admin VARCHAR, admin VARCHAR, condominio VARCHAR, classe_unica VARCHAR",
            subclasse=False,
        )


def _empty_staging(
    con: duckdb.DuckDBPyConnection, name: str, extra_columns: str, subclasse: bool = True
) -> None:
    """Cria um staging vazio para uma tabela ausente, preservando o schema.

    Assim os JOINs do painel continuam válidos quando o usuário baixou apenas
    parte das tabelas, em vez de o pipeline quebrar.
    """
    key = "cnpj VARCHAR, dt_comptc DATE" + (", subclasse VARCHAR" if subclasse else "")
    con.execute(f"CREATE OR REPLACE TABLE {name} ({key}, {extra_columns})")
    logger.warning("%s criada vazia (arquivo de origem ausente)", name)


def _layout_expr(available: set[str]) -> str:
    """Marca a geração do layout de cada linha.

    ``CNPJ_FUNDO_CLASSE`` só existe a partir da competência 2023-10, quando a
    Resolução CVM 175 passou a tratar cada classe como entidade própria. A
    marca é o que permite localizar a fronteira sem cravar a data no código.
    """
    if "CNPJ_FUNDO_CLASSE" not in available:
        return "'PRE_175'"
    return "CASE WHEN CNPJ_FUNDO_CLASSE IS NOT NULL THEN 'POS_175' ELSE 'PRE_175' END"


def _build_cotas(con: duckdb.DuckDBPyConnection) -> None:
    cols = _available_columns(con, "raw_cotas")
    cnpj = _alias(cols, "cnpj")
    tp = _alias(cols, "tp_fundo_classe", "'FUNDO'")
    layout = _layout_expr(cols)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE stg_cotas AS
        SELECT
            norm_cnpj({cnpj})                       AS cnpj,
            norm_data(DT_COMPTC)                    AS dt_comptc,
            norm_subclasse(TAB_X_CLASSE_SERIE)      AS subclasse,
            norm_texto(TAB_X_CLASSE_SERIE)          AS subclasse_original,
            tipo_subclasse(TAB_X_CLASSE_SERIE)      AS tipo_subclasse,
            norm_exibicao(DENOM_SOCIAL)             AS denom_social,
            norm_texto({tp})                        AS tp_fundo_classe,
            {layout}                                AS layout,
            norm_decimal(TAB_X_QT_COTA)             AS qt_cota,
            norm_decimal(TAB_X_VL_COTA)             AS vl_cota,
            filename                                AS arquivo_origem
        FROM raw_cotas
        WHERE norm_cnpj({cnpj}) IS NOT NULL
          AND norm_data(DT_COMPTC) IS NOT NULL
          AND norm_subclasse(TAB_X_CLASSE_SERIE) IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY cnpj, dt_comptc, subclasse ORDER BY arquivo_origem DESC
        ) = 1
        """
    )


def _build_cotistas(con: duckdb.DuckDBPyConnection) -> None:
    cols = _available_columns(con, "raw_cotistas")
    cnpj = _alias(cols, "cnpj")
    id_sub = _alias(cols, "id_subclasse")
    layout = _layout_expr(cols)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE stg_cotistas AS
        SELECT
            norm_cnpj({cnpj})                  AS cnpj,
            norm_data(DT_COMPTC)               AS dt_comptc,
            norm_subclasse(TAB_X_CLASSE_SERIE) AS subclasse,
            norm_texto({id_sub})               AS id_subclasse,
            norm_inteiro(TAB_X_NR_COTST)       AS nr_cotistas,
            norm_exibicao(DENOM_SOCIAL)        AS denom_social,
            {layout}                           AS layout,
            filename                           AS arquivo_origem
        FROM raw_cotistas
        WHERE norm_cnpj({cnpj}) IS NOT NULL
          AND norm_data(DT_COMPTC) IS NOT NULL
          AND norm_subclasse(TAB_X_CLASSE_SERIE) IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY cnpj, dt_comptc, subclasse ORDER BY arquivo_origem DESC
        ) = 1
        """
    )


def _build_patrimonio(con: duckdb.DuckDBPyConnection) -> None:
    cols = _available_columns(con, "raw_patrimonio")
    cnpj = _alias(cols, "cnpj")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE stg_patrimonio AS
        SELECT
            norm_cnpj({cnpj})               AS cnpj,
            norm_data(DT_COMPTC)            AS dt_comptc,
            norm_decimal(TAB_IV_A_VL_PL)    AS vl_pl,
            norm_decimal(TAB_IV_B_VL_PL_MEDIO) AS vl_pl_medio,
            filename                        AS arquivo_origem
        FROM raw_patrimonio
        WHERE norm_cnpj({cnpj}) IS NOT NULL
          AND norm_data(DT_COMPTC) IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY cnpj, dt_comptc ORDER BY arquivo_origem DESC
        ) = 1
        """
    )


def _build_rentabilidade(con: duckdb.DuckDBPyConnection) -> None:
    cols = _available_columns(con, "raw_rentabilidade")
    cnpj = _alias(cols, "cnpj")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE stg_rentabilidade AS
        SELECT
            norm_cnpj({cnpj})                    AS cnpj,
            norm_data(DT_COMPTC)                 AS dt_comptc,
            norm_subclasse(TAB_X_CLASSE_SERIE)   AS subclasse,
            norm_decimal(TAB_X_VL_RENTAB_MES)    AS rentab_mes_informada_pct,
            filename                             AS arquivo_origem
        FROM raw_rentabilidade
        WHERE norm_cnpj({cnpj}) IS NOT NULL
          AND norm_data(DT_COMPTC) IS NOT NULL
          AND norm_subclasse(TAB_X_CLASSE_SERIE) IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY cnpj, dt_comptc, subclasse ORDER BY arquivo_origem DESC
        ) = 1
        """
    )


def _build_cadastro(con: duckdb.DuckDBPyConnection) -> None:
    cols = _available_columns(con, "raw_cadastro")
    cnpj = _alias(cols, "cnpj")
    con.execute(
        f"""
        CREATE OR REPLACE TABLE stg_cadastro AS
        SELECT
            norm_cnpj({cnpj})        AS cnpj,
            norm_data(DT_COMPTC)     AS dt_comptc,
            norm_cnpj(CNPJ_ADMIN)    AS cnpj_admin,
            norm_exibicao(ADMIN)     AS admin,
            norm_texto(CONDOM)       AS condominio,
            norm_texto(CLASSE_UNICA) AS classe_unica,
            filename                 AS arquivo_origem
        FROM raw_cadastro
        WHERE norm_cnpj({cnpj}) IS NOT NULL
          AND norm_data(DT_COMPTC) IS NOT NULL
        QUALIFY row_number() OVER (
            PARTITION BY cnpj, dt_comptc ORDER BY arquivo_origem DESC
        ) = 1
        """
    )


def build_panel(con: duckdb.DuckDBPyConnection) -> None:
    """Materializa ``fato_subclasse_mensal``, o painel normalizado.

    O FULL JOIN entre cotas e cotistas é intencional: em 2026-06, 179 chaves
    aparecem só na ``tab_X_1`` e 756 só na ``tab_X_2``. Um INNER JOIN
    descartaria silenciosamente essas subclasses; o FULL JOIN as mantém e as
    marca via ``flag_sem_cotistas``/``flag_sem_cota``.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE fato_subclasse_mensal AS
        WITH base AS (
            SELECT
                coalesce(c.cnpj, t.cnpj)             AS cnpj,
                coalesce(c.dt_comptc, t.dt_comptc)   AS dt_comptc,
                coalesce(c.subclasse, t.subclasse)   AS subclasse_informada,
                coalesce(c.layout, t.layout)         AS layout,
                c.subclasse_original,
                coalesce(c.tipo_subclasse, tipo_subclasse(t.subclasse)) AS tipo_subclasse,
                coalesce(c.denom_social, t.denom_social) AS denom_social,
                c.tp_fundo_classe,
                c.qt_cota,
                c.vl_cota,
                t.nr_cotistas,
                t.id_subclasse
            FROM stg_cotas c
            FULL JOIN stg_cotistas t
              ON  c.cnpj      = t.cnpj
              AND c.dt_comptc = t.dt_comptc
              AND c.subclasse = t.subclasse
        )
        SELECT
            b.dt_comptc,
            competencia(b.dt_comptc)                      AS competencia,
            b.cnpj                                        AS cnpj_classe,
            fmt_cnpj(b.cnpj)                              AS cnpj_classe_formatado,
            -- Chave canônica da série temporal: para as linhas pré-175 usa o
            -- rótulo do outro lado da fronteira, quando o de-para o conhece.
            coalesce(ponte.subclasse_pos, b.subclasse_informada) AS subclasse,
            b.subclasse_informada,
            CASE
                WHEN ponte.subclasse_pos IS NOT NULL THEN 'PONTE_TIPO'
                WHEN b.layout = 'PRE_175' THEN 'SEM_PONTE'
                ELSE 'DIRETO'
            END                                           AS origem_chave,
            b.layout,
            b.subclasse_original,
            b.id_subclasse,
            b.tipo_subclasse,
            b.denom_social,
            b.tp_fundo_classe,
            b.qt_cota,
            b.vl_cota,
            -- PL implícito da subclasse; a tab_IV só publica PL da classe.
            -- O produto sai em DOUBLE de propósito: em DECIMAL(28,10) as 20
            -- casas do resultado estouram o limite de 38 dígitos do DuckDB.
            round(CAST(b.qt_cota AS DOUBLE) * CAST(b.vl_cota AS DOUBLE), 2)
                                                          AS vl_pl_subclasse_estimado,
            pat.vl_pl                                     AS vl_pl_classe,
            pat.vl_pl_medio                               AS vl_pl_medio_classe,
            b.nr_cotistas,
            r.rentab_mes_informada_pct,
            cad.cnpj_admin,
            cad.admin,
            cad.condominio,
            cad.classe_unica,
            -- Sinalizações de qualidade. Cota nula ou <= 0 inviabiliza o
            -- cálculo de retorno naquela ponta da janela.
            (b.vl_cota IS NULL OR b.vl_cota <= 0)         AS flag_cota_invalida,
            (b.qt_cota IS NULL OR b.qt_cota = 0)          AS flag_sem_cota_emitida,
            (b.nr_cotistas IS NULL)                       AS flag_sem_cotistas,
            (b.vl_cota IS NULL)                           AS flag_sem_cota,
            (pat.vl_pl IS NULL)                           AS flag_sem_pl
        FROM base b
        LEFT JOIN stg_patrimonio pat
          ON pat.cnpj = b.cnpj AND pat.dt_comptc = b.dt_comptc
        LEFT JOIN stg_rentabilidade r
          ON r.cnpj = b.cnpj AND r.dt_comptc = b.dt_comptc
         AND r.subclasse = b.subclasse_informada
        LEFT JOIN stg_cadastro cad
          ON cad.cnpj = b.cnpj AND cad.dt_comptc = b.dt_comptc
        -- Só as linhas do layout antigo passam pelo de-para.
        LEFT JOIN dim_ponte_subclasse ponte
          ON  b.layout = 'PRE_175'
          AND ponte.cnpj = b.cnpj
          AND ponte.subclasse_pre = b.subclasse_informada
        """
    )


def build_bridge(con: duckdb.DuckDBPyConnection) -> int:
    """Constrói o de-para de subclasses através da fronteira da Res. CVM 175.

    Na virada (competência 2023-10) a CVM reescreveu os rótulos: o que era
    ``"Classe Subordinada 1"`` virou ``"Subclasse Subordinada Subordinada 1"``,
    ``"Classe Senior 1"`` virou ``"Subclasse Sênior Série Única"``, e assim por
    diante. Só 136 das 9.512 chaves casam por texto, o que zera a janela de 36
    meses — mas o CNPJ atravessa a fronteira em 2.253 classes e o *tipo*
    (sênior/mezanino/subordinada) continua legível dos dois lados.

    O de-para casa ``(cnpj, tipo)`` **apenas quando o tipo é único dos dois
    lados**. Onde a classe tem duas subclasses do mesmo tipo o casamento seria
    um chute — nesses casos nada é mapeado e a série fica descontinuada, o que
    o painel sinaliza em ``origem_chave``.

    A fronteira é descoberta a partir da marca de layout, não cravada no
    código: se a CVM publicar uma competência atrasada no formato antigo, o
    de-para se ajusta sozinho.
    """
    con.execute(
        """
        CREATE OR REPLACE TABLE dim_ponte_subclasse AS
        WITH fronteira AS (
            SELECT max(dt_comptc) FILTER (WHERE layout = 'PRE_175') AS ultima_pre,
                   min(dt_comptc) FILTER (WHERE layout = 'POS_175') AS primeira_pos
            FROM stg_cotas
        ),
        -- Um lado só é elegível quando a classe tem exatamente uma subclasse
        -- daquele tipo; com duas, o casamento seria arbitrário.
        pre AS (
            SELECT c.cnpj, c.tipo_subclasse, any_value(c.subclasse) AS subclasse
            FROM stg_cotas c, fronteira f
            WHERE c.dt_comptc = f.ultima_pre
            GROUP BY 1, 2
            HAVING count(*) = 1
        ),
        pos AS (
            SELECT c.cnpj, c.tipo_subclasse, any_value(c.subclasse) AS subclasse
            FROM stg_cotas c, fronteira f
            WHERE c.dt_comptc = f.primeira_pos
            GROUP BY 1, 2
            HAVING count(*) = 1
        )
        SELECT
            pre.cnpj,
            pre.tipo_subclasse,
            pre.subclasse AS subclasse_pre,
            pos.subclasse AS subclasse_pos
        FROM pre
        JOIN pos USING (cnpj, tipo_subclasse)
        WHERE pre.subclasse <> pos.subclasse
        """
    )
    (mapeadas,) = con.sql("SELECT count(*) FROM dim_ponte_subclasse").fetchone()
    logger.info("Ponte Res. 175: %d subclasses mapeadas por (CNPJ, tipo)", mapeadas)
    return mapeadas


def log_discarded_rows(con: duckdb.DuckDBPyConnection) -> int:
    """Reporta linhas de ``tab_X_2`` que não sobreviveram à normalização.

    Existe por causa de um bug real: quando o alias de CNPJ resolvia para uma
    única variante, todo o histórico pré-Resolução 175 era descartado sem uma
    linha de log. Uma competência inteira zerando aqui é sinal de layout não
    tratado, não de sujeira pontual.
    """
    cols = _available_columns(con, "raw_cotas")
    cnpj = _alias(cols, "cnpj")
    descartes = con.sql(
        f"""
        SELECT competencia_arquivo(filename) AS competencia,
               count(*) AS descartadas,
               sum((norm_cnpj({cnpj}) IS NULL)::INT) AS sem_cnpj,
               sum((norm_data(DT_COMPTC) IS NULL)::INT) AS sem_data,
               sum((norm_subclasse(TAB_X_CLASSE_SERIE) IS NULL)::INT) AS sem_subclasse
        FROM raw_cotas
        WHERE norm_cnpj({cnpj}) IS NULL
           OR norm_data(DT_COMPTC) IS NULL
           OR norm_subclasse(TAB_X_CLASSE_SERIE) IS NULL
        GROUP BY 1
        ORDER BY 2 DESC
        """
    ).fetchall()
    if not descartes:
        return 0

    total = sum(linha[1] for linha in descartes)
    logger.warning(
        "%d linhas de tab_X_2 descartadas na normalização, em %d competências. "
        "Piores casos (competência, total, sem_cnpj, sem_data, sem_subclasse): %s",
        total, len(descartes), descartes[:5],
    )
    return total


def build_quality_report(con: duckdb.DuckDBPyConnection) -> None:
    """Resumo por competência, para inspecionar o resultado da normalização."""
    con.execute(
        """
        CREATE OR REPLACE TABLE qa_normalizacao AS
        WITH pl_por_data AS (
            -- Agregado a partir do staging: no painel o PL da classe se repete
            -- em cada subclasse e somá-lo direto multiplicaria o total.
            SELECT dt_comptc, sum(vl_pl) AS pl_total_classes
            FROM stg_patrimonio
            GROUP BY 1
        )
        SELECT
            f.competencia,
            f.dt_comptc,
            count(*)                        AS qt_subclasses,
            count(DISTINCT f.cnpj_classe)   AS qt_classes,
            sum(f.flag_cota_invalida::INT)  AS qt_cota_invalida,
            sum(f.flag_sem_cotistas::INT)   AS qt_sem_cotistas,
            sum(f.flag_sem_cota::INT)       AS qt_sem_cota,
            sum(f.flag_sem_pl::INT)         AS qt_sem_pl,
            any_value(p.pl_total_classes)   AS pl_total_classes
        FROM fato_subclasse_mensal f
        LEFT JOIN pl_por_data p ON p.dt_comptc = f.dt_comptc
        GROUP BY 1, 2
        ORDER BY 2
        """
    )


def assert_panel_integrity(con: duckdb.DuckDBPyConnection) -> None:
    """Falha alto se a chave do painel não for única.

    Chave duplicada aqui significa que a normalização do rótulo colapsou duas
    subclasses distintas — o tipo de erro que passa despercebido e contamina
    todos os retornos adiante.
    """
    duplicated = con.sql(
        """
        SELECT cnpj_classe, dt_comptc, subclasse, count(*) AS n
        FROM fato_subclasse_mensal
        GROUP BY 1, 2, 3
        HAVING count(*) > 1
        ORDER BY n DESC
        LIMIT 5
        """
    ).fetchall()
    if duplicated:
        raise ValueError(
            "Chave (cnpj_classe, dt_comptc, subclasse) duplicada no painel. "
            f"Exemplos: {duplicated}"
        )


def summarize(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    linhas, classes, subclasses, inicio, fim, meses = con.sql(
        """
        SELECT count(*), count(DISTINCT cnpj_classe),
               count(DISTINCT (cnpj_classe, subclasse)),
               min(dt_comptc), max(dt_comptc), count(DISTINCT competencia)
        FROM fato_subclasse_mensal
        """
    ).fetchone()
    return {
        "linhas": linhas,
        "classes": classes,
        "subclasses": subclasses,
        "primeira_competencia": inicio,
        "ultima_competencia": fim,
        "meses": meses,
    }


def run(raw_dir: str | None = None, db_path: str | None = None) -> dict[str, object]:
    """Executa a normalização completa e persiste o painel no DuckDB."""
    raw_dir = raw_dir or Config.SAVE_PATH
    db_path = db_path or Config.DB_PATH

    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = new_connection(db_path)
    try:
        available = create_raw_views(con, raw_dir)
        build_staging(con, available)
        log_discarded_rows(con)
        build_bridge(con)
        build_panel(con)
        assert_panel_integrity(con)
        build_quality_report(con)

        resumo = summarize(con)
        logger.info(
            "Painel normalizado: %s linhas, %s classes, %s subclasses, %s meses (%s a %s)",
            resumo["linhas"], resumo["classes"], resumo["subclasses"],
            resumo["meses"], resumo["primeira_competencia"], resumo["ultima_competencia"],
        )
        meses = int(resumo["meses"] or 0)
        if meses < Config.MIN_MESES_HISTORICO:
            logger.warning(
                "Apenas %d competências disponíveis; a janela de 36 meses exige %d. "
                "Baixe também os ZIPs anuais de %s",
                meses, Config.MIN_MESES_HISTORICO, Config.HIST_PAGE,
            )
        return resumo
    finally:
        con.close()
