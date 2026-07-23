"""
Utilidades de janela temporal (semana de referência).

Mantém separada a lógica de datas: parsing de argumentos e cálculo da última
semana completa (segunda a domingo), usada como default quando o usuário não
informa o período — deixando o script pronto para rodar como um job semanal.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


def parse_iso_date(value: str) -> date:
    """Converte 'AAAA-MM-DD' em date, com mensagem de erro amigável."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(
            f"Data inválida: '{value}'. Use o formato AAAA-MM-DD (ex.: 2026-07-14)."
        ) from exc


def last_complete_week(reference: date | None = None) -> tuple[date, date]:
    """
    Retorna a última semana completa (segunda a domingo) anterior à `reference`.

    Ex.: se hoje é quinta 2026-07-23, a última semana completa é
    2026-07-13 (seg) a 2026-07-19 (dom).
    """
    today = reference or date.today()
    # weekday(): segunda=0 ... domingo=6. Domingo mais recente já encerrado:
    last_sunday = today - timedelta(days=today.weekday() + 1)
    last_monday = last_sunday - timedelta(days=6)
    return last_monday, last_sunday


def resolve_window(
    start: str | None,
    end: str | None,
    reference: date | None = None,
) -> tuple[date, date]:
    """
    Resolve a janela a partir dos argumentos do usuário.

    Regras:
    - ambos informados  -> usa-os (valida start <= end);
    - nenhum informado  -> última semana completa (segunda a domingo);
    - apenas um informado -> erro (evita janelas ambíguas).
    """
    if start and end:
        s, e = parse_iso_date(start), parse_iso_date(end)
        if s > e:
            raise ValueError(f"--start ({s}) não pode ser posterior a --end ({e}).")
        return s, e
    if not start and not end:
        return last_complete_week(reference)
    raise ValueError(
        "Informe --start e --end juntos, ou nenhum dos dois "
        "(nesse caso usa-se a última semana completa)."
    )
