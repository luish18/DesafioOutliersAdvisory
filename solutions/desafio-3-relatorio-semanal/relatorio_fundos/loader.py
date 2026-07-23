"""
Carga e normalização do `registro_fundo.csv`.

Isola a leitura do CSV (encoding, separador, parsing de datas e limpeza de
texto) do restante do pipeline. A saída é um DataFrame tipado e limpo, pronto
para a lógica de detecção de movimentações.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from . import config

logger = logging.getLogger(__name__)


def load_registro_fundo(csv_path: Path) -> pd.DataFrame:
    """
    Lê o CSV cadastral da CVM e devolve um DataFrame normalizado.

    Tratamentos aplicados:
    - encoding latin-1 e separador ';' (padrão da CVM);
    - colunas de data convertidas para datetime (valores inválidos viram NaT);
    - strings com espaços colapsados e aparados (a CVM usa espaços duplos);
    - `Patrimonio_Liquido` convertido para número.
    """
    logger.info("Lendo %s ...", csv_path)
    df = pd.read_csv(
        csv_path,
        sep=config.CSV_SEP,
        encoding=config.CSV_ENCODING,
        dtype=str,           # lê tudo como texto e converte controladamente
        keep_default_na=True,
        na_values=[""],
    )
    logger.info("Linhas lidas: %d | colunas: %d", len(df), df.shape[1])

    _assert_expected_columns(df)

    # --- Datas -------------------------------------------------------------
    for col in config.DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], format="%Y-%m-%d", errors="coerce")

    # --- Texto -------------------------------------------------------------
    text_cols = [
        config.COL_NOME,
        config.COL_SITUACAO,
        config.COL_GESTOR,
        config.COL_ADMINISTRADOR,
        config.COL_TIPO,
        config.COL_CNPJ,
    ]
    for col in text_cols:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype("string")
                .str.replace(r"\s+", " ", regex=True)
                .str.strip()
            )

    # --- Numéricos ---------------------------------------------------------
    if config.COL_PL in df.columns:
        df[config.COL_PL] = pd.to_numeric(df[config.COL_PL], errors="coerce")

    return df


def _assert_expected_columns(df: pd.DataFrame) -> None:
    """Falha cedo e com mensagem clara se o layout da CVM mudar."""
    required = {
        config.COL_CNPJ,
        config.COL_TIPO,
        config.COL_NOME,
        config.COL_SITUACAO,
        config.COL_DATA_REGISTRO,
        config.COL_DATA_INICIO_SIT,
        config.COL_DATA_CANCELAMENTO,
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            "O layout do registro_fundo.csv mudou: colunas ausentes "
            f"{sorted(missing)}. Colunas disponíveis: {sorted(df.columns)}"
        )
