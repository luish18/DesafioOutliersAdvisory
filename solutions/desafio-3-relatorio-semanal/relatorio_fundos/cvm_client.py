"""
Cliente de download dos dados cadastrais da CVM.

Responsável apenas por *obter* o arquivo `registro_fundo.csv` de forma
automatizada e resiliente:

- baixa o ZIP `registro_fundo_classe.zip` do Portal de Dados Abertos;
- usa um cache local (evita rebaixar a cada execução);
- extrai o CSV de interesse;
- expõe a "data de vintage" do arquivo (Last-Modified do servidor), usada no
  relatório para deixar claro a que dia o retrato dos dados se refere.

Usa apenas a biblioteca padrão (`urllib`) — sem dependência de `requests` —
para maximizar a chance de rodar sem atrito na máquina do avaliador.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

# User-Agent explícito: alguns servidores públicos rejeitam o default do urllib.
_HTTP_HEADERS = {"User-Agent": "outliers-desafio3/1.0 (+relatorio-semanal-fundos)"}
_DOWNLOAD_TIMEOUT = 120  # segundos
_MAX_RETRIES = 3


@dataclass(frozen=True)
class DataSource:
    """Resultado do download: caminho do CSV extraído e metadados de origem."""

    csv_path: Path
    zip_path: Path
    downloaded_at: datetime
    remote_last_modified: datetime | None

    @property
    def vintage_label(self) -> str:
        """Rótulo legível da data de referência dos dados (retrato da CVM)."""
        if self.remote_last_modified is not None:
            return self.remote_last_modified.strftime("%d/%m/%Y %H:%M")
        return self.downloaded_at.strftime("%d/%m/%Y %H:%M")


def _is_cache_fresh(path: Path, max_age_hours: float) -> bool:
    """True se o arquivo existe e é mais novo que `max_age_hours`."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours < max_age_hours


def _download_with_retries(url: str, dest: Path) -> datetime | None:
    """
    Baixa `url` para `dest` com algumas tentativas.

    Retorna o header Last-Modified (como datetime UTC) quando disponível,
    para registrarmos a data de referência dos dados.
    """
    last_error: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.info("Baixando %s (tentativa %d/%d)...", url, attempt, _MAX_RETRIES)
            request = urllib.request.Request(url, headers=_HTTP_HEADERS)
            with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as resp:
                last_modified_raw = resp.headers.get("Last-Modified")
                dest.write_bytes(resp.read())
            logger.info("Download concluído: %s (%d bytes)", dest.name, dest.stat().st_size)

            remote_dt: datetime | None = None
            if last_modified_raw:
                try:
                    remote_dt = parsedate_to_datetime(last_modified_raw)
                except (TypeError, ValueError):
                    remote_dt = None
            return remote_dt
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # rede instável
            last_error = exc
            logger.warning("Falha no download (tentativa %d): %s", attempt, exc)
            if attempt < _MAX_RETRIES:
                time.sleep(2 * attempt)  # backoff simples

    raise RuntimeError(
        f"Não foi possível baixar {url} após {_MAX_RETRIES} tentativas. "
        f"Último erro: {last_error}"
    )


def fetch_registro_fundo(
    cache_dir: Path | None = None,
    *,
    force_download: bool = False,
    offline: bool = False,
    max_age_hours: float = config.CACHE_MAX_AGE_HOURS,
) -> DataSource:
    """
    Garante a presença local do `registro_fundo.csv` e o devolve.

    Parâmetros
    ----------
    cache_dir : diretório onde o ZIP/CSV são armazenados (default: config).
    force_download : ignora o cache e rebaixa o ZIP.
    offline : usa somente o que já está em cache (útil para testes / sem rede).
    max_age_hours : idade máxima do cache antes de rebaixar.
    """
    cache_dir = cache_dir or config.DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    zip_path = cache_dir / "registro_fundo_classe.zip"
    csv_path = cache_dir / config.REGISTRO_FUNDO_CSV

    downloaded_at = datetime.now(timezone.utc)
    remote_last_modified: datetime | None = None

    use_cache = _is_cache_fresh(zip_path, max_age_hours) and not force_download
    if offline:
        if not zip_path.exists():
            raise FileNotFoundError(
                f"Modo offline, mas nenhum cache encontrado em {zip_path}. "
                "Rode ao menos uma vez com acesso à internet."
            )
        use_cache = True

    if use_cache:
        logger.info("Usando cache local: %s", zip_path)
    else:
        remote_last_modified = _download_with_retries(config.CVM_DADOS_ZIP_URL, zip_path)

    # (Re)extrai o CSV do ZIP.
    _extract_csv(zip_path, config.REGISTRO_FUNDO_CSV, csv_path)

    # Se veio do cache, aproxima o vintage pela data de modificação do arquivo.
    if remote_last_modified is None and zip_path.exists():
        remote_last_modified = datetime.fromtimestamp(
            zip_path.stat().st_mtime, tz=timezone.utc
        )

    return DataSource(
        csv_path=csv_path,
        zip_path=zip_path,
        downloaded_at=downloaded_at,
        remote_last_modified=remote_last_modified,
    )


def _extract_csv(zip_path: Path, member: str, dest: Path) -> None:
    """Extrai um membro específico do ZIP para `dest`."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if member not in zf.namelist():
                raise FileNotFoundError(
                    f"'{member}' não encontrado em {zip_path.name}. "
                    f"Conteúdo: {zf.namelist()}"
                )
            with zf.open(member) as src, open(dest, "wb") as out:
                out.write(src.read())
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"Arquivo {zip_path} corrompido ou incompleto ({exc}). "
            "Rode novamente com --force-download."
        ) from exc
    logger.info("CSV extraído para %s", dest)
