"""
Testes unitários da lógica de detecção de movimentações.

Usa dados sintéticos (sem rede) para validar as regras de cada categoria e o
comportamento de fronteira da janela — a parte do sistema que precisa
generalizar para qualquer semana informada pelo avaliador.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from relatorio_fundos import config, movements, periods


def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Constrói um DataFrame no mesmo formato do loader (datas já em datetime)."""
    df = pd.DataFrame(rows)
    for col in config.DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
        else:
            df[col] = pd.NaT
    # Garante presença das colunas de texto usadas.
    for col in [config.COL_NOME, config.COL_GESTOR, config.COL_ADMINISTRADOR,
                config.COL_TIPO, config.COL_CNPJ, config.COL_SITUACAO]:
        if col not in df.columns:
            df[col] = None
    return df


@pytest.fixture
def sample_df() -> pd.DataFrame:
    return _make_df([
        # Novo, registrado dentro da janela.
        {config.COL_CNPJ: "1", config.COL_NOME: "Fundo Novo A",
         config.COL_TIPO: "FI", config.COL_GESTOR: "Gestora X",
         config.COL_SITUACAO: config.SIT_NORMAL,
         config.COL_DATA_REGISTRO: "2026-07-15"},
        # Novo na fronteira inicial (segunda).
        {config.COL_CNPJ: "2", config.COL_NOME: "Fundo Novo B",
         config.COL_TIPO: "FII", config.COL_GESTOR: "Gestora X",
         config.COL_SITUACAO: config.SIT_PRE_OPERACIONAL,
         config.COL_DATA_REGISTRO: "2026-07-13",
         config.COL_DATA_INICIO_SIT: "2026-07-13"},
        # Registrado ANTES da janela -> não é novo.
        {config.COL_CNPJ: "3", config.COL_NOME: "Fundo Antigo",
         config.COL_TIPO: "FI", config.COL_GESTOR: "Gestora Y",
         config.COL_SITUACAO: config.SIT_NORMAL,
         config.COL_DATA_REGISTRO: "2026-06-01"},
        # Em liquidação dentro da janela.
        {config.COL_CNPJ: "4", config.COL_NOME: "Fundo Liquidando",
         config.COL_TIPO: "FIDC", config.COL_GESTOR: "Gestora Z",
         config.COL_SITUACAO: config.SIT_LIQUIDACAO,
         config.COL_DATA_REGISTRO: "2020-01-01",
         config.COL_DATA_INICIO_SIT: "2026-07-17"},
        # Cancelado dentro da janela.
        {config.COL_CNPJ: "5", config.COL_NOME: "Fundo Cancelado",
         config.COL_TIPO: "FIP", config.COL_GESTOR: "Gestora Z",
         config.COL_SITUACAO: config.SIT_CANCELADO,
         config.COL_DATA_REGISTRO: "2019-01-01",
         config.COL_DATA_CANCELAMENTO: "2026-07-19",
         config.COL_DATA_INICIO_SIT: "2026-07-19"},
        # Cancelado FORA da janela (domingo seguinte) -> não entra.
        {config.COL_CNPJ: "6", config.COL_NOME: "Fundo Fora",
         config.COL_TIPO: "FI", config.COL_GESTOR: "Gestora Y",
         config.COL_SITUACAO: config.SIT_CANCELADO,
         config.COL_DATA_REGISTRO: "2019-01-01",
         config.COL_DATA_CANCELAMENTO: "2026-07-20"},
    ])


WEEK_START = date(2026, 7, 13)  # segunda
WEEK_END = date(2026, 7, 19)    # domingo


def test_detect_novos(sample_df):
    res = movements.detect_movements(sample_df, WEEK_START, WEEK_END)
    nomes = set(res.movements[movements.CAT_NOVOS][config.COL_NOME])
    assert nomes == {"Fundo Novo A", "Fundo Novo B"}


def test_detect_pre_operacional(sample_df):
    res = movements.detect_movements(sample_df, WEEK_START, WEEK_END)
    nomes = set(res.movements[movements.CAT_PRE_OPERACIONAL][config.COL_NOME])
    assert nomes == {"Fundo Novo B"}


def test_detect_liquidacao(sample_df):
    res = movements.detect_movements(sample_df, WEEK_START, WEEK_END)
    nomes = set(res.movements[movements.CAT_LIQUIDACAO][config.COL_NOME])
    assert nomes == {"Fundo Liquidando"}


def test_detect_cancelados_respeita_fronteira(sample_df):
    """Cancelamento em 2026-07-20 (fora) não deve aparecer; 07-19 (dentro) sim."""
    res = movements.detect_movements(sample_df, WEEK_START, WEEK_END)
    nomes = set(res.movements[movements.CAT_CANCELADOS][config.COL_NOME])
    assert nomes == {"Fundo Cancelado"}
    assert "Fundo Fora" not in nomes


def test_counts_and_total(sample_df):
    res = movements.detect_movements(sample_df, WEEK_START, WEEK_END)
    assert res.counts[movements.CAT_NOVOS] == 2
    assert res.counts[movements.CAT_PRE_OPERACIONAL] == 1
    assert res.counts[movements.CAT_LIQUIDACAO] == 1
    assert res.counts[movements.CAT_CANCELADOS] == 1
    assert res.total_movimentacoes == 5


def test_empty_window(sample_df):
    """Janela sem movimentações retorna tudo vazio, sem erro."""
    res = movements.detect_movements(sample_df, date(2000, 1, 1), date(2000, 1, 7))
    assert res.total_movimentacoes == 0


def test_invalid_window_raises(sample_df):
    with pytest.raises(ValueError):
        movements.detect_movements(sample_df, WEEK_END, WEEK_START)


def test_aggregate_by_class(sample_df):
    res = movements.detect_movements(sample_df, WEEK_START, WEEK_END)
    table = movements.aggregate_by_class(res)
    assert "Total" in table.columns
    assert table["Total"].sum() == res.total_movimentacoes


def test_top_gestoras(sample_df):
    res = movements.detect_movements(sample_df, WEEK_START, WEEK_END)
    ranking = movements.top_gestoras(res, movements.CAT_NOVOS)
    # Gestora X lançou 2 fundos novos -> deve liderar.
    assert ranking.iloc[0]["Gestora"] == "Gestora X"
    assert ranking.iloc[0]["Quantidade"] == 2


# --- periods -------------------------------------------------------------- #
def test_last_complete_week_from_thursday():
    # Quinta 2026-07-23 -> última semana completa 07-13 (seg) a 07-19 (dom).
    start, end = periods.last_complete_week(date(2026, 7, 23))
    assert (start, end) == (date(2026, 7, 13), date(2026, 7, 19))


def test_resolve_window_requires_both():
    with pytest.raises(ValueError):
        periods.resolve_window("2026-07-13", None)


def test_resolve_window_rejects_inverted():
    with pytest.raises(ValueError):
        periods.resolve_window("2026-07-20", "2026-07-13")
