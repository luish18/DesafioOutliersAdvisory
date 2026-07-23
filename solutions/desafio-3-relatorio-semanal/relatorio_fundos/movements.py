"""
Detecção de movimentações de fundos em uma semana de referência.

Este é o núcleo do desafio e é propositalmente uma função **pura**: recebe um
DataFrame já carregado e uma janela de datas, e devolve as movimentações. Não
faz I/O nem depende de rede — o que a torna trivialmente testável e reutilizável.

Categorias detectadas (conforme enunciado):

1. Novos            -> `Data_Registro` dentro da janela.
2. Pré-operacional  -> `Situacao == 'Fase Pré-Operacional'` e
                       `Data_Inicio_Situacao` dentro da janela.
3. Em liquidação    -> `Situacao == 'Em Liquidação'` e
                       `Data_Inicio_Situacao` dentro da janela.
4. Cancelados       -> `Situacao == 'Cancelado'` e
                       `Data_Cancelamento` dentro da janela.

--------------------------------------------------------------------------------
LIMITAÇÃO CONHECIDA (documentada de propósito):
O `registro_fundo.csv` é um *retrato* (snapshot) do estado atual de cada fundo,
não um log de eventos. Portanto, só conseguimos reconstruir a *transição mais
recente* de cada fundo, a partir das colunas de data. Para um relatório semanal
rodado sobre o arquivo atualizado diariamente pela CVM, essa abordagem é correta
e suficiente. A solução teoricamente mais robusta (diffar snapshots diários
consecutivos, ou usar cad_fi_hist.zip) está descrita no README.
--------------------------------------------------------------------------------
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from . import config

# Categorias de movimentação (chaves estáveis usadas em todo o pipeline).
CAT_NOVOS = "novos"
CAT_PRE_OPERACIONAL = "pre_operacional"
CAT_LIQUIDACAO = "liquidacao"
CAT_CANCELADOS = "cancelados"

# Rótulos legíveis (usados no relatório).
CATEGORY_LABELS: dict[str, str] = {
    CAT_NOVOS: "Fundos novos (registrados)",
    CAT_PRE_OPERACIONAL: "Entraram em fase pré-operacional",
    CAT_LIQUIDACAO: "Entraram em liquidação",
    CAT_CANCELADOS: "Cancelados / encerrados",
}

# Colunas apresentadas em cada tabela de movimentação e seus rótulos.
DISPLAY_COLUMNS: dict[str, str] = {
    config.COL_NOME: "Fundo",
    config.COL_GESTOR: "Gestora",
    config.COL_ADMINISTRADOR: "Administrador",
    config.COL_TIPO: "Classe/Tipo",
    "data_movimentacao": "Data",
    config.COL_CNPJ: "CNPJ",
}


@dataclass(frozen=True)
class MovementsResult:
    """Resultado completo da análise de uma janela semanal."""

    start: date
    end: date
    total_fundos_base: int
    # category -> DataFrame com as colunas de exibição (já ordenado por data).
    movements: dict[str, pd.DataFrame]

    @property
    def counts(self) -> dict[str, int]:
        """Quantidade de fundos por categoria."""
        return {cat: len(df) for cat, df in self.movements.items()}

    @property
    def total_movimentacoes(self) -> int:
        return sum(self.counts.values())


def _within_window(series: pd.Series, start: date, end: date) -> pd.Series:
    """Máscara booleana: datas (datetime) dentro de [start, end] inclusive."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    return series.notna() & (series >= start_ts) & (series <= end_ts)


def _build_view(df: pd.DataFrame, date_col: str) -> pd.DataFrame:
    """
    Monta a visão padronizada de uma categoria: seleciona as colunas de
    exibição, cria a coluna genérica `data_movimentacao` e ordena por data.
    """
    view = df.copy()
    view["data_movimentacao"] = view[date_col]
    cols = [c for c in DISPLAY_COLUMNS if c in view.columns]
    view = view[cols].sort_values("data_movimentacao").reset_index(drop=True)
    return view


def detect_movements(
    df: pd.DataFrame,
    start: date,
    end: date,
) -> MovementsResult:
    """
    Detecta as movimentações de fundos na janela [start, end] (inclusive).

    Parâmetros
    ----------
    df : DataFrame normalizado (ver `loader.load_registro_fundo`).
    start, end : limites da semana de referência (datas inclusivas).

    Retorna
    -------
    MovementsResult com um DataFrame por categoria.
    """
    if start > end:
        raise ValueError(f"Data inicial ({start}) é posterior à final ({end}).")

    sit = df[config.COL_SITUACAO]

    # 1) Novos: registrados na janela (independente da situação atual).
    novos_mask = _within_window(df[config.COL_DATA_REGISTRO], start, end)

    # 2) Pré-operacional: situação atual é pré-op e iniciou na janela.
    pre_op_mask = (sit == config.SIT_PRE_OPERACIONAL) & _within_window(
        df[config.COL_DATA_INICIO_SIT], start, end
    )

    # 3) Em liquidação: situação atual é liquidação e iniciou na janela.
    liq_mask = (sit == config.SIT_LIQUIDACAO) & _within_window(
        df[config.COL_DATA_INICIO_SIT], start, end
    )

    # 4) Cancelados: situação atual é cancelado e a data de cancelamento está
    #    na janela (Data_Cancelamento é o campo específico do evento).
    canc_mask = (sit == config.SIT_CANCELADO) & _within_window(
        df[config.COL_DATA_CANCELAMENTO], start, end
    )

    movements = {
        CAT_NOVOS: _build_view(df[novos_mask], config.COL_DATA_REGISTRO),
        CAT_PRE_OPERACIONAL: _build_view(df[pre_op_mask], config.COL_DATA_INICIO_SIT),
        CAT_LIQUIDACAO: _build_view(df[liq_mask], config.COL_DATA_INICIO_SIT),
        CAT_CANCELADOS: _build_view(df[canc_mask], config.COL_DATA_CANCELAMENTO),
    }

    return MovementsResult(
        start=start,
        end=end,
        total_fundos_base=len(df),
        movements=movements,
    )


# --------------------------------------------------------------------------- #
# Estatísticas agregadas (BÔNUS do enunciado)
# --------------------------------------------------------------------------- #
def aggregate_by_class(result: MovementsResult) -> pd.DataFrame:
    """
    Resumo de movimentações por classe/tipo de fundo.

    Retorna um DataFrame: linhas = Tipo_Fundo, colunas = categorias, valores =
    contagem. Inclui coluna 'Total' e é ordenado pelo total desc.
    """
    frames = []
    for cat, df in result.movements.items():
        if df.empty or config.COL_TIPO not in df.columns:
            continue
        counts = df.groupby(config.COL_TIPO).size().rename(CATEGORY_LABELS[cat])
        frames.append(counts)

    if not frames:
        return pd.DataFrame()

    table = pd.concat(frames, axis=1).fillna(0).astype(int)
    table["Total"] = table.sum(axis=1)
    table = table.sort_values("Total", ascending=False)
    table.index.name = "Classe/Tipo"
    return table


def top_gestoras(result: MovementsResult, category: str, top_n: int = 10) -> pd.DataFrame:
    """
    Ranking das gestoras com mais fundos em uma categoria (ex.: lançamentos).

    Retorna DataFrame com colunas ['Gestora', 'Quantidade'].
    """
    df = result.movements.get(category, pd.DataFrame())
    if df.empty or config.COL_GESTOR not in df.columns:
        return pd.DataFrame(columns=["Gestora", "Quantidade"])

    ranking = (
        df[config.COL_GESTOR]
        .fillna("(não informada)")
        .replace("", "(não informada)")
        .value_counts()
        .head(top_n)
        .rename_axis("Gestora")
        .reset_index(name="Quantidade")
    )
    return ranking
