"""Cálculo dos retornos por subclasse de cota.

Consome o painel ``fato_subclasse_mensal`` produzido pelo :mod:`loader` e
calcula os retornos de cada subclasse nas janelas exigidas pelo desafio — mês
atual, 6, 12, 24 e 36 meses — a partir da variação do valor da cota.

Duas decisões sustentam a qualidade do resultado e valem registro:

**1. A chave da série temporal é ``(CNPJ, tipo_subclasse)``, não o rótulo.**
O rótulo publicado pela CVM não é estável nem dentro do layout novo: a mesma
cota sênior aparece como ``"Série 1"``, depois ``"Sênior"`` (nos meses sem
reporte, com cota zerada) e depois ``"Subclasse 1"``. Casar por rótulo
fragmentaria a série e zeraria as janelas longas. Colapsamos para o *tipo*
(sênior/mezanino/subordinada) **apenas quando ele é único na classe** dentro do
horizonte analisado; quando a classe tem duas subclasses do mesmo tipo, a
colapsagem seria arbitrária e caímos de volta para o rótulo — marcado em
``metodo_chave``.

**2. ``vl_cota = 0`` significa "não reportado", não "a cota vale zero".**
São ~28% das linhas. Tratadas como valor, produziriam retornos de −100% e
+∞. Aqui contam como ausentes: a âncora de cada janela é a cota **válida** mais
próxima da data-alvo, dentro de uma tolerância que cresce com a janela. A data
efetivamente usada vai no output (``data_base_*``), para o retorno ser
auditável.
"""

import logging
import os

import duckdb

from config import Config
from sanitizer import new_connection


logger = logging.getLogger(__name__)


# (coluna de saída, defasagem em meses, tolerância de busca da âncora em meses).
# A tolerância cresce com a janela: quanto mais longe a data-alvo, mais provável
# que o mês exato esteja sem reporte, e um mês de folga distorce menos um
# retorno de 36 meses do que um de 1 mês — por isso a janela do mês atual é
# estrita (só aceita o mês imediatamente anterior).
JANELAS: tuple[tuple[str, int, int], ...] = (
    ("retorno_mes_atual", 1, 0),
    ("retorno_6m", 6, 1),
    ("retorno_12m", 12, 2),
    ("retorno_24m", 24, 2),
    ("retorno_36m", 36, 3),
)

# Meses de história considerados ao decidir se um (CNPJ, tipo) é "multi". Cobre
# a maior janela mais a tolerância, de modo que uma multiplicidade antiga e já
# extinta não force o método de rótulo numa série hoje única.
HORIZONTE_MULTI_MESES = 40


def _janelas_values() -> str:
    linhas = ", ".join(f"('{nome}', {n}, {tol})" for nome, n, tol in JANELAS)
    return f"(VALUES {linhas}) AS j(janela, n_meses, tol)"


def build_series_key(con: duckdb.DuckDBPyConnection) -> None:
    """Atribui a cada linha do painel uma chave de série temporal estável.

    Materializa ``panel_keyed`` (o painel + ``chave_serie``/``metodo_chave`` +
    índice mensal) e ``serie_cota_mensal`` (só as observações com cota válida,
    que servem de âncora para os retornos).
    """
    con.execute(
        f"""
        CREATE OR REPLACE TABLE panel_keyed AS
        WITH ref_idx AS (
            SELECT max(year(dt_comptc) * 12 + month(dt_comptc)) AS idx_ref
            FROM fato_subclasse_mensal
        ),
        multi AS (
            -- Um (CNPJ, tipo) é "multi" se em algum mês do horizonte analisado
            -- a classe teve mais de uma subclasse daquele tipo.
            SELECT cnpj_classe, tipo_subclasse, max(cnt) > 1 AS is_multi
            FROM (
                SELECT f.cnpj_classe, f.tipo_subclasse, f.competencia, count(*) AS cnt
                FROM fato_subclasse_mensal f, ref_idx
                WHERE year(f.dt_comptc) * 12 + month(f.dt_comptc) > idx_ref - {HORIZONTE_MULTI_MESES}
                GROUP BY 1, 2, 3
            )
            GROUP BY 1, 2
        )
        SELECT
            f.*,
            year(f.dt_comptc) * 12 + month(f.dt_comptc) AS idx_mes,
            CASE WHEN m.is_multi THEN 'ROTULO' ELSE 'TIPO' END AS metodo_chave,
            CASE
                WHEN m.is_multi THEN f.cnpj_classe || '|R|' || f.subclasse
                ELSE f.cnpj_classe || '|T|' || f.tipo_subclasse
            END AS chave_serie
        FROM fato_subclasse_mensal f
        JOIN multi m USING (cnpj_classe, tipo_subclasse)
        """
    )

    # Âncoras: apenas cota estritamente positiva. Zero e NULL = não reportado.
    # O QUALIFY é defensivo — no método TIPO a chave já é única por mês, mas se
    # a bridge criasse um empate ficaríamos com a observação mais recente.
    con.execute(
        """
        CREATE OR REPLACE TABLE serie_cota_mensal AS
        SELECT chave_serie, idx_mes, dt_comptc,
               CAST(vl_cota AS DOUBLE) AS vl_cota
        FROM panel_keyed
        WHERE vl_cota IS NOT NULL AND vl_cota > 0
        QUALIFY row_number() OVER (
            PARTITION BY chave_serie, idx_mes ORDER BY dt_comptc DESC
        ) = 1
        """
    )
    (n_series,) = con.sql("SELECT count(DISTINCT chave_serie) FROM serie_cota_mensal").fetchone()
    logger.info("Chave de série atribuída: %d séries com ao menos uma cota válida", n_series)


def build_returns(con: duckdb.DuckDBPyConnection) -> str:
    """Calcula os retornos por subclasse para a data de referência mais recente.

    Retorna a competência de referência (``YYYY-MM``).
    """
    (competencia_ref,) = con.sql(
        "SELECT max(competencia) FROM fato_subclasse_mensal"
    ).fetchone()

    con.execute(
        f"""
        CREATE OR REPLACE TABLE retornos_longo AS
        WITH ref AS (
            -- Linha de referência: a subclasse na competência mais recente.
            -- A cota do numerador é estrita nesta data (sem tolerância): não
            -- faz sentido afirmar um retorno "na data de referência" a partir
            -- de um mês em que a cota não foi reportada.
            SELECT cnpj_classe, subclasse, chave_serie, idx_mes,
                   CAST(vl_cota AS DOUBLE) AS vl_cota_ref
            FROM panel_keyed
            WHERE competencia = '{competencia_ref}'
        ),
        alvos AS (
            SELECT r.cnpj_classe, r.subclasse, r.chave_serie, r.vl_cota_ref,
                   j.janela, j.tol, (r.idx_mes - j.n_meses) AS idx_alvo
            FROM ref r
            CROSS JOIN {_janelas_values()}
        ),
        ancora AS (
            SELECT
                a.cnpj_classe, a.subclasse, a.janela, a.vl_cota_ref,
                s.vl_cota AS vl_cota_ancora,
                s.dt_comptc AS data_base,
                abs(s.idx_mes - a.idx_alvo) AS defasagem
            FROM alvos a
            JOIN serie_cota_mensal s
              ON s.chave_serie = a.chave_serie
             AND s.idx_mes BETWEEN a.idx_alvo - a.tol AND a.idx_alvo + a.tol
            -- Âncora mais próxima da data-alvo; empate favorece a mais antiga
            -- (janela mais longa, mais conservadora).
            QUALIFY row_number() OVER (
                PARTITION BY a.cnpj_classe, a.subclasse, a.janela
                ORDER BY defasagem ASC, s.idx_mes ASC
            ) = 1
        )
        SELECT
            cnpj_classe, subclasse, janela, data_base, defasagem,
            CASE
                WHEN vl_cota_ref IS NULL OR vl_cota_ref <= 0 THEN NULL
                ELSE round(vl_cota_ref / vl_cota_ancora - 1, 8)
            END AS retorno
        FROM ancora
        """
    )
    return competencia_ref


def build_output_table(con: duckdb.DuckDBPyConnection, competencia_ref: str) -> None:
    """Monta ``saida_retornos``, o consolidado tabular da data de referência.

    Uma linha por subclasse existente na competência de referência, com os
    campos pedidos pelo enunciado e os retornos pivotados em colunas. O PL da
    CVM é da *classe*; ao lado dele vai o PL implícito da subclasse
    (``qt_cota × vl_cota``), sem esconder a diferença.
    """
    colunas_retorno = ",\n".join(
        f"            max(retorno)  FILTER (WHERE janela = '{nome}') AS {nome},\n"
        f"            max(data_base) FILTER (WHERE janela = '{nome}') AS "
        f"data_base_{nome.removeprefix('retorno_')}"
        for nome, _, _ in JANELAS
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE saida_retornos AS
        WITH ret AS (
            SELECT cnpj_classe, subclasse,
{colunas_retorno}
            FROM retornos_longo
            GROUP BY cnpj_classe, subclasse
        )
        SELECT
            p.dt_comptc                         AS data_referencia,
            p.cnpj_classe_formatado             AS cnpj_fundo,
            p.denom_social                      AS denominacao_social,
            p.tp_fundo_classe                   AS tipo_registro,
            p.subclasse                         AS identificacao_subclasse,
            p.tipo_subclasse,
            p.id_subclasse                      AS id_subclasse_cvm,
            p.chave_serie,
            p.metodo_chave,
            p.origem_chave                      AS origem_chave_res175,
            p.vl_pl_classe                      AS pl_classe,
            p.vl_pl_subclasse_estimado          AS pl_subclasse_estimado,
            p.nr_cotistas                       AS numero_cotistas,
            p.vl_cota                           AS vl_cota_referencia,
            r.retorno_mes_atual,
            r.retorno_6m,
            r.retorno_12m,
            r.retorno_24m,
            r.retorno_36m,
            r.data_base_mes_atual,
            r.data_base_6m,
            r.data_base_12m,
            r.data_base_24m,
            r.data_base_36m,
            -- Sinaliza retorno dominado por evento estrutural da cota
            -- (amortização, capitalização, cota residual perto de zero) em vez
            -- de desempenho. É soft: o valor bruto continua no arquivo, mas a
            -- pesquisa consegue filtrar. O limiar do mês vem da tab_X_3, onde a
            -- rentabilidade informada praticamente nunca sai de um dígito;
            -- variação mensal fora de [-50%, +100%] é quase sempre estrutural.
            coalesce(r.retorno_mes_atual NOT BETWEEN -0.5 AND 1.0, false)
            OR coalesce(greatest(r.retorno_6m, r.retorno_12m, r.retorno_24m,
                                 r.retorno_36m) > 5.0, false)
            OR coalesce(least(r.retorno_6m, r.retorno_12m, r.retorno_24m,
                              r.retorno_36m) < -0.95, false)
                                                AS alerta_retorno_estrutural
        FROM panel_keyed p
        LEFT JOIN ret r USING (cnpj_classe, subclasse)
        WHERE p.competencia = '{competencia_ref}'
        ORDER BY p.cnpj_classe, p.tipo_subclasse, p.subclasse
        """
    )


def export(con: duckdb.DuckDBPyConnection, competencia_ref: str, output_dir: str) -> dict[str, str]:
    """Grava o consolidado em CSV e Parquet, nomeados pela competência."""
    os.makedirs(output_dir, exist_ok=True)
    sufixo = competencia_ref.replace("-", "")
    csv_path = os.path.join(output_dir, f"retornos_subclasse_{sufixo}.csv")
    parquet_path = os.path.join(output_dir, f"retornos_subclasse_{sufixo}.parquet")

    con.execute(
        f"COPY (SELECT * FROM saida_retornos) TO '{csv_path}' "
        "(FORMAT CSV, HEADER true, DELIMITER ',')"
    )
    con.execute(
        f"COPY (SELECT * FROM saida_retornos) TO '{parquet_path}' (FORMAT PARQUET)"
    )
    logger.info("Consolidado exportado: %s | %s", csv_path, parquet_path)
    return {"csv": csv_path, "parquet": parquet_path}


def coverage(con: duckdb.DuckDBPyConnection) -> list[tuple[str, int, float]]:
    """Percentual de subclasses com retorno calculado, por janela."""
    linhas = []
    total = con.sql("SELECT count(*) FROM saida_retornos").fetchone()[0]
    for nome, _, _ in JANELAS:
        (n,) = con.sql(f"SELECT count({nome}) FROM saida_retornos").fetchone()
        linhas.append((nome, n, round(100.0 * n / total, 1) if total else 0.0))
    return linhas


def run(db_path: str | None = None, output_dir: str | None = None) -> dict[str, object]:
    """Executa o cálculo de retornos sobre o painel já normalizado."""
    db_path = db_path or Config.DB_PATH
    output_dir = output_dir or Config.OUTPUT_PATH

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Banco {db_path!r} não encontrado. Rode a normalização antes: "
            "`python src/__main__.py normalize`."
        )

    con = new_connection(db_path)
    try:
        build_series_key(con)
        competencia_ref = build_returns(con)
        build_output_table(con, competencia_ref)
        arquivos = export(con, competencia_ref, output_dir)

        cob = coverage(con)
        total = con.sql("SELECT count(*) FROM saida_retornos").fetchone()[0]
        for nome, n, pct in cob:
            logger.info("Cobertura %-18s %6d/%d subclasses (%.1f%%)", nome, n, total, pct)

        return {
            "competencia_referencia": competencia_ref,
            "subclasses": total,
            "cobertura": cob,
            **arquivos,
        }
    finally:
        con.close()
