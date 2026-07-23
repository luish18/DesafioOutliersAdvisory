"""Testes do cálculo de retornos.

Em vez de depender do banco real, cada teste monta um ``fato_subclasse_mensal``
mínimo em memória, reproduzindo exatamente as patologias que motivaram o
desenho: deriva de rótulo dentro do layout novo e ``vl_cota = 0`` como mês não
reportado.
"""

from datetime import date

import pytest

import retornos
from sanitizer import new_connection


COLUNAS = [
    "dt_comptc", "competencia", "cnpj_classe", "cnpj_classe_formatado",
    "subclasse", "subclasse_informada", "origem_chave", "layout",
    "subclasse_original", "id_subclasse", "tipo_subclasse", "denom_social",
    "tp_fundo_classe", "qt_cota", "vl_cota", "vl_pl_subclasse_estimado",
    "vl_pl_classe", "vl_pl_medio_classe", "nr_cotistas",
]


def _linha(cnpj, comp, subclasse, tipo, vl_cota, **over):
    ano, mes = int(comp[:4]), int(comp[5:7])
    # último dia do mês, suficiente para o índice ano*12+mês
    dt = date(ano, mes, 28)
    base = dict(
        dt_comptc=dt, competencia=comp, cnpj_classe=cnpj,
        cnpj_classe_formatado=cnpj, subclasse=subclasse,
        subclasse_informada=subclasse, origem_chave="DIRETO", layout="POS_175",
        subclasse_original=subclasse, id_subclasse=None, tipo_subclasse=tipo,
        denom_social="FUNDO TESTE", tp_fundo_classe="CLASSE",
        qt_cota=1000.0, vl_cota=vl_cota,
        vl_pl_subclasse_estimado=(vl_cota or 0) * 1000,
        vl_pl_classe=1_000_000.0, vl_pl_medio_classe=1_000_000.0, nr_cotistas=10,
    )
    base.update(over)
    return tuple(base[c] for c in COLUNAS)


@pytest.fixture
def con():
    conexao = new_connection()
    conexao.execute(
        "CREATE TABLE fato_subclasse_mensal ("
        "dt_comptc DATE, competencia VARCHAR, cnpj_classe VARCHAR, "
        "cnpj_classe_formatado VARCHAR, subclasse VARCHAR, subclasse_informada VARCHAR, "
        "origem_chave VARCHAR, layout VARCHAR, subclasse_original VARCHAR, "
        "id_subclasse VARCHAR, tipo_subclasse VARCHAR, denom_social VARCHAR, "
        "tp_fundo_classe VARCHAR, qt_cota DOUBLE, vl_cota DOUBLE, "
        "vl_pl_subclasse_estimado DOUBLE, vl_pl_classe DOUBLE, "
        "vl_pl_medio_classe DOUBLE, nr_cotistas BIGINT)"
    )
    yield conexao
    conexao.close()


def _inserir(con, linhas):
    con.executemany(
        f"INSERT INTO fato_subclasse_mensal VALUES ({', '.join(['?'] * len(COLUNAS))})",
        linhas,
    )


def _retorno(con, cnpj, janela):
    return con.execute(
        f"SELECT {janela} FROM saida_retornos WHERE cnpj_fundo = ?", [cnpj]
    ).fetchone()[0]


def test_serie_atravessa_deriva_de_rotulo(con):
    """Sênior única cujo rótulo muda de nome ao longo do tempo.

    Rótulo diferente a cada mês; como o tipo é único na classe, a chave TIPO
    une tudo e o retorno de 6 meses fecha 10.00 -> 11.00 = 10%.
    """
    linhas = [
        _linha("C1", "2025-12", "SENIOR SERIE 1", "SENIOR", 10.00),
        _linha("C1", "2026-01", "SENIOR", "SENIOR", 10.10),
        _linha("C1", "2026-02", "SENIOR", "SENIOR", 10.30),
        _linha("C1", "2026-03", "SENIOR SUBCLASSE 1", "SENIOR", 10.50),
        _linha("C1", "2026-04", "SENIOR SUBCLASSE 1", "SENIOR", 10.70),
        _linha("C1", "2026-05", "SENIOR SUBCLASSE 1", "SENIOR", 10.90),
        _linha("C1", "2026-06", "SENIOR SUBCLASSE A", "SENIOR", 11.00),
    ]
    _inserir(con, linhas)
    retornos.build_series_key(con)
    ref = retornos.build_returns(con)
    retornos.build_output_table(con, ref)

    assert con.execute("SELECT metodo_chave FROM saida_retornos").fetchone()[0] == "TIPO"
    assert _retorno(con, "C1", "retorno_6m") == pytest.approx(0.10)
    assert _retorno(con, "C1", "retorno_mes_atual") == pytest.approx(11.00 / 10.90 - 1, abs=1e-8)


def test_cota_zero_tratada_como_ausente(con):
    """O mês-alvo vem zerado; a âncora recua para o mês válido mais próximo."""
    linhas = [
        _linha("C2", "2025-12", "SENIOR", "SENIOR", 10.00),  # alvo de 6m
        _linha("C2", "2026-01", "SENIOR", "SENIOR", 0.00),   # não reportado
        _linha("C2", "2026-02", "SENIOR", "SENIOR", 0.00),
        _linha("C2", "2026-03", "SENIOR", "SENIOR", 10.50),
        _linha("C2", "2026-04", "SENIOR", "SENIOR", 10.70),
        _linha("C2", "2026-05", "SENIOR", "SENIOR", 0.00),   # mês anterior zerado
        _linha("C2", "2026-06", "SENIOR", "SENIOR", 11.00),
    ]
    _inserir(con, linhas)
    retornos.build_series_key(con)
    ref = retornos.build_returns(con)
    retornos.build_output_table(con, ref)

    # mês atual: 2026-05 está zerado; com tolerância 0 não há âncora -> NULL.
    assert _retorno(con, "C2", "retorno_mes_atual") is None
    # 6m: alvo 2025-12 é válido (10.00) -> 11/10 - 1 = 10%.
    assert _retorno(con, "C2", "retorno_6m") == pytest.approx(0.10)
    base_6m = con.execute("SELECT data_base_6m FROM saida_retornos WHERE cnpj_fundo='C2'").fetchone()[0]
    assert base_6m == date(2025, 12, 28)


def test_tipo_multiplo_cai_para_rotulo(con):
    """Classe com duas subclasses subordinadas: a chave não pode ser o tipo."""
    linhas = [
        _linha("C3", "2025-12", "SUBORDINADA MEZANINO 1", "SUBORDINADA", 100.0),
        _linha("C3", "2025-12", "SUBORDINADA JUNIOR 2", "SUBORDINADA", 50.0),
        _linha("C3", "2026-06", "SUBORDINADA MEZANINO 1", "SUBORDINADA", 110.0),
        _linha("C3", "2026-06", "SUBORDINADA JUNIOR 2", "SUBORDINADA", 60.0),
    ]
    _inserir(con, linhas)
    retornos.build_series_key(con)
    ref = retornos.build_returns(con)
    retornos.build_output_table(con, ref)

    metodos = con.execute(
        "SELECT DISTINCT metodo_chave FROM saida_retornos WHERE cnpj_fundo='C3'"
    ).fetchall()
    assert metodos == [("ROTULO",)]
    # Cada rótulo mantém sua própria série: 110/100-1 e 60/50-1.
    mez = con.execute(
        "SELECT retorno_6m FROM saida_retornos WHERE identificacao_subclasse='SUBORDINADA MEZANINO 1'"
    ).fetchone()[0]
    assert mez == pytest.approx(0.10)


def test_alerta_retorno_estrutural(con):
    """Cota-âncora residual gera retorno gigante e deve ser sinalizada."""
    linhas = [
        _linha("C4", "2025-12", "SUBORDINADA", "SUBORDINADA", 0.00000001),
        _linha("C4", "2026-06", "SUBORDINADA", "SUBORDINADA", 1.00),
    ]
    _inserir(con, linhas)
    retornos.build_series_key(con)
    ref = retornos.build_returns(con)
    retornos.build_output_table(con, ref)

    assert con.execute(
        "SELECT alerta_retorno_estrutural FROM saida_retornos WHERE cnpj_fundo='C4'"
    ).fetchone()[0] is True
