#!/usr/bin/env python3
"""
Ponto de entrada do Relatório Semanal de Movimentações de Fundos.

Uso:
    python run.py --start 2026-07-14 --end 2026-07-20
    python run.py            # última semana completa (segunda a domingo)

Veja `python run.py --help` para todas as opções.
"""

from relatorio_fundos.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
