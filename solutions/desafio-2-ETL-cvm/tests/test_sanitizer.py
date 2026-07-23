"""Testes das regras de normalização.

Os casos vêm de valores reais observados nos CSVs da CVM — em especial a
divergência de acentuação entre ``tab_X_1`` e ``tab_X_2``, que é o que torna o
join por subclasse possível.
"""

from decimal import Decimal

import pytest

from sanitizer import new_connection


@pytest.fixture(scope="module")
def con():
    conexao = new_connection()
    yield conexao
    conexao.close()


def scalar(con, expressao: str, valor):
    return con.execute(f"SELECT {expressao}", [valor]).fetchone()[0]


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("  Sênior   Série 1 ", "SENIOR SERIE 1"),
        ("Senior Serie 1", "SENIOR SERIE 1"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_norm_texto(con, entrada, esperado):
    assert scalar(con, "norm_texto(?)", entrada) == esperado


def test_subclasse_casa_entre_tabelas(con):
    """tab_X_1 e tab_X_2 escrevem o mesmo rótulo de formas diferentes."""
    da_tab_x1 = scalar(con, "norm_subclasse(?)", "Subclasse Sênior Série 1")
    da_tab_x2 = scalar(con, "norm_subclasse(?)", "Subclasse Senior Série 1")
    assert da_tab_x1 == da_tab_x2 == "SENIOR SERIE 1"


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("Subclasse Subordinada  |", "SUBORDINADA"),
        ("Subclasse Subordinada Subordinada 1 |", "SUBORDINADA SUBORDINADA 1"),
        ("Subclasse Subordinada Sub | Subordinada 1", "SUBORDINADA SUB SUBORDINADA 1"),
        # Layout pré-Resolução 175: o prefixo era "Classe".
        ("Classe Subordinada 1", "SUBORDINADA 1"),
        ("", None),
    ],
)
def test_norm_subclasse(con, entrada, esperado):
    assert scalar(con, "norm_subclasse(?)", entrada) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("Subclasse Sênior Série 1", "SENIOR"),
        # Mezanino é publicado dentro do rótulo "Subordinada" e precisa vencer.
        ("Subclasse Subordinada Mezanino 1", "MEZANINO"),
        ("Subclasse Subordinada  |", "SUBORDINADA"),
        ("Classe Única", "UNICA"),
        ("", "NAO INFORMADO"),
    ],
)
def test_tipo_subclasse(con, entrada, esperado):
    assert scalar(con, "tipo_subclasse(?)", entrada) == esperado


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("05.754.060/0001-13", "05754060000113"),
        ("05754060000113", "05754060000113"),
        ("00000000000000", None),  # placeholder da CVM, não é um CNPJ
        ("123", None),
        ("", None),
    ],
)
def test_norm_cnpj(con, entrada, esperado):
    assert scalar(con, "norm_cnpj(?)", entrada) == esperado


def test_fmt_cnpj_reaplica_mascara(con):
    assert scalar(con, "fmt_cnpj(?)", "05754060000113") == "05.754.060/0001-13"
    assert scalar(con, "fmt_cnpj(?)", "00000000000000") is None


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("55.94715500", "55.94715500"),
        ("1276051396.17", "1276051396.17"),
        ("-0.69", "-0.69"),
        # Formato brasileiro: só aqui o ponto é separador de milhar.
        ("1.234,56", "1234.56"),
        ("", None),
        ("n/d", None),
    ],
)
def test_norm_decimal(con, entrada, esperado):
    resultado = scalar(con, "norm_decimal(?)", entrada)
    assert resultado == (Decimal(esperado) if esperado is not None else None)


def test_norm_decimal_nao_confunde_decimal_com_milhar(con):
    """"1.234" sem vírgula é 1,234 e não 1234 — é o formato dos arquivos."""
    assert scalar(con, "norm_decimal(?)", "1.234") == Decimal("1.234")


@pytest.mark.parametrize("entrada", ["2026-06-30", "30/06/2026"])
def test_norm_data(con, entrada):
    assert str(scalar(con, "norm_data(?)", entrada)) == "2026-06-30"


def test_norm_data_invalida(con):
    assert scalar(con, "norm_data(?)", "") is None


def test_competencia_arquivo(con):
    caminho = "data/inf_mensal_fidc_202606/inf_mensal_fidc_tab_X_2_202606.csv"
    assert scalar(con, "competencia_arquivo(?)", caminho) == "202606"


def test_norm_exibicao_preserva_acento(con):
    entrada = "RIVO FUNDO DE INVESTIMENTO EM DIREITOS  CREDITÓRIOS"
    assert scalar(con, "norm_exibicao(?)", entrada) == (
        "RIVO FUNDO DE INVESTIMENTO EM DIREITOS CREDITÓRIOS"
    )
