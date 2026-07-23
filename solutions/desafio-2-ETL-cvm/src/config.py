
from dataclasses import dataclass
from os import environ

@dataclass
class Config:
    DOWNLOAD_PAGE: str = environ.get("DOWNLOAD_PAGE", "https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS/")
    HIST_PAGE: str = environ.get("HIST_PAGE", "https://dados.cvm.gov.br/dados/FIDC/DOC/INF_MENSAL/DADOS/HIST/")
    SAVE_PATH: str = environ.get("SAVE_PATH", "data/")

    # Layout dos CSVs publicados pela CVM.
    CSV_ENCODING: str = environ.get("CSV_ENCODING", "latin-1")
    CSV_DELIMITER: str = environ.get("CSV_DELIMITER", ";")

    # Cache de arquivos reencodados. Fica FORA de SAVE_PATH de propósito: se
    # caísse dentro, o glob da etapa de normalização leria o mesmo arquivo duas
    # vezes (o original e o reparado) e duplicaria linhas.
    REPAIR_PATH: str = environ.get("REPAIR_PATH", ".cache/csv_utf8/")

    # Saídas da etapa de tratamento/normalização.
    DB_PATH: str = environ.get("DB_PATH", "data/warehouse/fidc.duckdb")
    OUTPUT_PATH: str = environ.get("OUTPUT_PATH", "output/")

    # Janela mínima de histórico necessária para a maior janela de retorno (36m)
    # mais o mês de referência.
    MIN_MESES_HISTORICO: int = int(environ.get("MIN_MESES_HISTORICO", "37"))
