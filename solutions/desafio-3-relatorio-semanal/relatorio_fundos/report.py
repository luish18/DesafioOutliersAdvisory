"""
Geração do relatório gerencial (Markdown e HTML).

Consome um `MovementsResult` e produz um relatório legível para pessoas
não-técnicas, com:
- resumo executivo (contagens por categoria);
- uma seção com tabela para cada categoria de movimentação;
- estatísticas agregadas (bônus): resumo por classe e ranking de gestoras.

O HTML é renderizado por um template Jinja2 autocontido (CSS inline), fácil de
abrir no navegador ou anexar em e-mail.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, movements
from .movements import CATEGORY_LABELS, DISPLAY_COLUMNS, MovementsResult

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_MESES_PT = [
    "", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]

# Ordem em que as categorias aparecem no relatório.
_CATEGORY_ORDER = [
    movements.CAT_NOVOS,
    movements.CAT_PRE_OPERACIONAL,
    movements.CAT_LIQUIDACAO,
    movements.CAT_CANCELADOS,
]


def _fmt_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _fmt_periodo(start: date, end: date) -> str:
    return f"{_fmt_date(start)} a {_fmt_date(end)}"


def _prepare_display(df: pd.DataFrame) -> pd.DataFrame:
    """Renomeia colunas para rótulos amigáveis e formata datas/CNPJ."""
    view = df.copy()
    if "data_movimentacao" in view.columns:
        view["data_movimentacao"] = view["data_movimentacao"].dt.strftime("%d/%m/%Y")
    if config.COL_CNPJ in view.columns:
        view[config.COL_CNPJ] = view[config.COL_CNPJ].map(_format_cnpj)
    view = view.rename(columns=DISPLAY_COLUMNS)
    # Preenche vazios para não poluir a tabela.
    return view.fillna("—").replace("", "—")


def _format_cnpj(cnpj: str | float | None) -> str:
    """Formata 14 dígitos como 00.000.000/0000-00."""
    if cnpj is None or (isinstance(cnpj, float) and pd.isna(cnpj)):
        return "—"
    digits = "".join(ch for ch in str(cnpj) if ch.isdigit()).zfill(14)
    if len(digits) != 14:
        return str(cnpj)
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


# --------------------------------------------------------------------------- #
# Modelo de dados intermediário para os templates
# --------------------------------------------------------------------------- #
def _build_context(result: MovementsResult, vintage_label: str) -> dict:
    """Monta o dicionário de contexto compartilhado por Markdown e HTML."""
    sections = []
    for cat in _CATEGORY_ORDER:
        df = result.movements[cat]
        sections.append(
            {
                "key": cat,
                "titulo": CATEGORY_LABELS[cat],
                "quantidade": len(df),
                "tabela": _prepare_display(df),
            }
        )

    by_class = movements.aggregate_by_class(result)
    top_lancamentos = movements.top_gestoras(result, movements.CAT_NOVOS, top_n=10)

    return {
        "periodo": _fmt_periodo(result.start, result.end),
        "gerado_em": datetime.now().strftime("%d/%m/%Y às %H:%M"),
        "vintage": vintage_label,
        "total_base": result.total_fundos_base,
        "total_movimentacoes": result.total_movimentacoes,
        "counts": {cat: result.counts[cat] for cat in _CATEGORY_ORDER},
        "labels": CATEGORY_LABELS,
        "category_order": _CATEGORY_ORDER,
        "sections": sections,
        "by_class": by_class,
        "top_lancamentos": top_lancamentos,
    }


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def render_markdown(result: MovementsResult, vintage_label: str) -> str:
    ctx = _build_context(result, vintage_label)
    lines: list[str] = []
    a = lines.append

    a("# Relatório Semanal de Movimentações de Fundos")
    a("")
    a(f"**Período de referência:** {ctx['periodo']}  ")
    a(f"**Gerado em:** {ctx['gerado_em']}  ")
    a(f"**Fonte:** Portal de Dados Abertos da CVM — registro_fundo.csv "
      f"(retrato de {ctx['vintage']})  ")
    a(f"**Base analisada:** {ctx['total_base']:,} fundos".replace(",", "."))
    a("")

    # Resumo executivo
    a("## Resumo executivo")
    a("")
    if ctx["total_movimentacoes"] == 0:
        a("_Nenhuma movimentação identificada no período._")
    else:
        a(f"No período foram identificadas **{ctx['total_movimentacoes']} "
          f"movimentações**:")
        a("")
        a("| Categoria | Quantidade |")
        a("| --- | ---: |")
        for cat in ctx["category_order"]:
            a(f"| {ctx['labels'][cat]} | {ctx['counts'][cat]} |")
    a("")

    # Seções por categoria
    for sec in ctx["sections"]:
        a(f"## {sec['titulo']} ({sec['quantidade']})")
        a("")
        if sec["quantidade"] == 0:
            a("_Sem ocorrências no período._")
            a("")
            continue
        a(_df_to_markdown(sec["tabela"]))
        a("")

    # Bônus: estatísticas agregadas
    a("## Estatísticas agregadas")
    a("")
    a("### Movimentações por classe/tipo de fundo")
    a("")
    if ctx["by_class"].empty:
        a("_Sem dados._")
    else:
        a(_df_to_markdown(ctx["by_class"].reset_index()))
    a("")
    a("### Top gestoras por lançamentos (fundos novos)")
    a("")
    if ctx["top_lancamentos"].empty:
        a("_Sem lançamentos no período._")
    else:
        a(_df_to_markdown(ctx["top_lancamentos"]))
    a("")

    a("---")
    a("")
    a("<sub>Relatório gerado automaticamente. Os dados refletem o retrato mais "
      "recente publicado pela CVM; ver limitações no README.</sub>")
    return "\n".join(lines)


def _df_to_markdown(df: pd.DataFrame) -> str:
    """
    Converte um DataFrame para tabela Markdown sem depender do `tabulate`.
    Colunas numéricas são alinhadas à direita.
    """
    if df.empty:
        return "_Sem dados._"
    cols = list(df.columns)
    numeric = {c: pd.api.types.is_numeric_dtype(df[c]) for c in cols}

    header = "| " + " | ".join(str(c) for c in cols) + " |"
    sep = "| " + " | ".join("---:" if numeric[c] else "---" for c in cols) + " |"
    rows = [header, sep]
    for _, row in df.iterrows():
        cells = [_md_cell(row[c]) for c in cols]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def _md_cell(value) -> str:
    if pd.isna(value):
        return "—"
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------- #
# HTML (Jinja2)
# --------------------------------------------------------------------------- #
def render_html(result: MovementsResult, vintage_label: str) -> str:
    ctx = _build_context(result, vintage_label)

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["table"] = _df_to_html
    template = env.get_template("report.html.j2")
    return template.render(**ctx)


def _df_to_html(df: pd.DataFrame) -> str:
    """Renderiza um DataFrame como <table> semântica (sem índice)."""
    if df is None or df.empty:
        return '<p class="empty">Sem ocorrências no período.</p>'
    return df.to_html(
        index=False,
        border=0,
        classes="data-table",
        escape=True,
        justify="left",
        na_rep="—",
    )


# --------------------------------------------------------------------------- #
# Escrita em disco
# --------------------------------------------------------------------------- #
def write_reports(
    result: MovementsResult,
    vintage_label: str,
    output_dir: Path,
    fmt: str = "both",
) -> list[Path]:
    """
    Escreve o(s) relatório(s) em `output_dir` e devolve os caminhos gerados.
    `fmt` ∈ {'md', 'html', 'both'}.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"relatorio_fundos_{result.start:%Y%m%d}_{result.end:%Y%m%d}"
    written: list[Path] = []

    if fmt in ("md", "both"):
        md_path = output_dir / f"{stem}.md"
        md_path.write_text(render_markdown(result, vintage_label), encoding="utf-8")
        written.append(md_path)
        logger.info("Markdown gerado: %s", md_path)

    if fmt in ("html", "both"):
        html_path = output_dir / f"{stem}.html"
        html_path.write_text(render_html(result, vintage_label), encoding="utf-8")
        written.append(html_path)
        logger.info("HTML gerado: %s", html_path)

    return written
