# Desafios Técnicos — Outliers Advisory (Processo Seletivo 2026)

Estágio em Research, Dados & IA.

As soluções ficam em [`solutions/`](solutions/), uma pasta por desafio.

## Desafio 2 — ETL de Informes Mensais de FIDCs (CVM)

Pipeline automatizado que baixa os informes mensais de FIDCs da CVM, trata e
normaliza os dados, e calcula os retornos de cada subclasse de cota nas janelas
de mês atual, 6, 12, 24 e 36 meses. A saída é um consolidado tabular
(CSV + Parquet) na granularidade da subclasse para a data de referência mais
recente.

➡️ **Solução completa e instruções:** [`solutions/desafio2/`](solutions/desafio2/README.md)

```bash
cd solutions/desafio2
uv run python src/__main__.py download --hist
uv run python src/__main__.py normalize
uv run python src/__main__.py returns
```

## Desafio 3 — Relatório Semanal: Movimentações de Fundos

Pipeline automatizado que consome os dados cadastrais de fundos da CVM e gera um
relatório gerencial semanal (Markdown + HTML) com fundos novos, em fase
pré-operacional e liquidados/cancelados na semana informada.

➡️ **Solução completa e instruções:** [`solutions/desafio-3-relatorio-semanal/`](solutions/desafio-3-relatorio-semanal/README.md)

```bash
cd solutions/desafio-3-relatorio-semanal
pip install -r requirements.txt
python run.py --start 2026-07-14 --end 2026-07-20
```
