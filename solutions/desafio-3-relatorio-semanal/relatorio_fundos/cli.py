"""
Interface de linha de comando: orquestra o pipeline ponta a ponta.

Fluxo:
    fetch (download+cache) -> load (normaliza) -> detect (movimentações)
    -> report (Markdown/HTML)

Exemplos:
    python run.py                                  # última semana completa
    python run.py --start 2026-07-14 --end 2026-07-20
    python run.py --start 2026-07-14 --end 2026-07-20 --format html
    python run.py --offline                        # usa apenas o cache local
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, config, cvm_client, loader, movements, periods, report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="relatorio-fundos",
        description=(
            "Gera o relatório semanal de movimentações de fundos a partir dos "
            "dados cadastrais públicos da CVM."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--start", help="Início da semana (AAAA-MM-DD).")
    p.add_argument("--end", help="Fim da semana (AAAA-MM-DD).")
    p.add_argument(
        "--format",
        choices=["md", "html", "both"],
        default="both",
        help="Formato de saída do relatório.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=config.DEFAULT_OUTPUT_DIR,
        help="Diretório onde o relatório será salvo.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=config.DEFAULT_CACHE_DIR,
        help="Diretório de cache do download da CVM.",
    )
    p.add_argument(
        "--force-download",
        action="store_true",
        help="Ignora o cache e rebaixa o arquivo da CVM.",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="Não acessa a rede; usa somente o cache local já existente.",
    )
    p.add_argument(
        "--data-file",
        type=Path,
        help="Usa um registro_fundo.csv local (pula o download). Útil para testes.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Log detalhado.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _force_utf8_console() -> None:
    """
    Garante saída UTF-8 no console (Windows usa cp1252 por padrão, o que quebra
    a impressão de acentos/símbolos). Falha graciosamente se não for suportado.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _force_utf8_console()
    _configure_logging(args.verbose)

    # 1) Resolve a janela de referência (parâmetro de entrada, não hardcoded).
    try:
        start, end = periods.resolve_window(args.start, args.end)
    except ValueError as exc:
        print(f"Erro nos parâmetros de data: {exc}", file=sys.stderr)
        return 2

    print(f"-> Semana de referência: {start:%d/%m/%Y} a {end:%d/%m/%Y}")

    # 2) Obtém os dados (arquivo local ou download automatizado da CVM).
    try:
        if args.data_file:
            csv_path = args.data_file
            if not csv_path.exists():
                print(f"Arquivo não encontrado: {csv_path}", file=sys.stderr)
                return 1
            vintage_label = "arquivo local"
            print(f"-> Usando arquivo local: {csv_path}")
        else:
            print("-> Obtendo dados cadastrais da CVM...")
            source = cvm_client.fetch_registro_fundo(
                cache_dir=args.cache_dir,
                force_download=args.force_download,
                offline=args.offline,
            )
            csv_path = source.csv_path
            vintage_label = source.vintage_label
            print(f"-> Dados prontos (retrato de {vintage_label}).")
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"Erro ao obter os dados da CVM: {exc}", file=sys.stderr)
        return 1

    # 3) Carrega e normaliza.
    try:
        df = loader.load_registro_fundo(csv_path)
    except (ValueError, OSError) as exc:
        print(f"Erro ao ler o arquivo de dados: {exc}", file=sys.stderr)
        return 1

    # 4) Detecta as movimentações na janela.
    result = movements.detect_movements(df, start, end)

    # 5) Gera o(s) relatório(s).
    written = report.write_reports(
        result, vintage_label, output_dir=args.output_dir, fmt=args.format
    )

    # 6) Resumo no console.
    _print_summary(result)
    print("\n-> Relatório(s) gerado(s):")
    for path in written:
        print(f"   • {path}")
    return 0


def _print_summary(result: movements.MovementsResult) -> None:
    print("\n" + "=" * 52)
    print(f"  Base analisada........: {result.total_fundos_base:,} fundos".replace(",", "."))
    print(f"  Total de movimentações: {result.total_movimentacoes}")
    for cat in [
        movements.CAT_NOVOS,
        movements.CAT_PRE_OPERACIONAL,
        movements.CAT_LIQUIDACAO,
        movements.CAT_CANCELADOS,
    ]:
        label = movements.CATEGORY_LABELS[cat]
        print(f"    - {label:.<34}: {result.counts[cat]}")
    print("=" * 52)


if __name__ == "__main__":
    raise SystemExit(main())
