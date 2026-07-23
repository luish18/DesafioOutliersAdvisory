
import argparse
import asyncio
import logging
import os

import loader
import retornos
from config import Config
from scraper import download_zips, get_urls_from_page


logger = logging.getLogger(__name__)


def download(args: argparse.Namespace) -> None:
    urls = get_urls_from_page(Config.DOWNLOAD_PAGE)
    if args.hist:
        # DADOS/ só publica ~18 competências; a janela de 36 meses depende dos
        # ZIPs anuais em DADOS/HIST/.
        urls += get_urls_from_page(Config.HIST_PAGE)

    os.makedirs(args.raw_dir, exist_ok=True)
    asyncio.run(download_zips(urls, args.raw_dir))


def normalize(args: argparse.Namespace) -> None:
    resumo = loader.run(raw_dir=args.raw_dir, db_path=args.db)
    for chave, valor in resumo.items():
        print(f"{chave}: {valor}")


def returns(args: argparse.Namespace) -> None:
    resumo = retornos.run(db_path=args.db, output_dir=args.output_dir)
    print(f"competencia_referencia: {resumo['competencia_referencia']}")
    print(f"subclasses: {resumo['subclasses']}")
    for nome, n, pct in resumo["cobertura"]:
        print(f"  {nome}: {n} ({pct}%)")
    print(f"csv: {resumo['csv']}")
    print(f"parquet: {resumo['parquet']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desafio2", description="ETL dos informes mensais de FIDC (CVM)")
    parser.add_argument("--raw-dir", default=Config.SAVE_PATH, help="diretório dos CSVs extraídos")
    parser.add_argument("--db", default=Config.DB_PATH, help="caminho do banco DuckDB de saída")
    parser.add_argument("--output-dir", default=Config.OUTPUT_PATH, help="diretório dos arquivos consolidados")
    parser.add_argument("-v", "--verbose", action="store_true")

    sub = parser.add_subparsers(dest="comando", required=True)

    p_download = sub.add_parser("download", help="baixa e extrai os ZIPs da CVM")
    p_download.add_argument(
        "--hist", action="store_true", help="inclui os ZIPs anuais históricos (necessário para 36m)"
    )
    p_download.set_defaults(func=download)

    p_normalize = sub.add_parser("normalize", help="trata e normaliza os CSVs no painel mensal")
    p_normalize.set_defaults(func=normalize)

    p_returns = sub.add_parser("returns", help="calcula os retornos por subclasse e exporta o consolidado")
    p_returns.set_defaults(func=returns)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
