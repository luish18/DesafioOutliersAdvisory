# Relatório Semanal de Movimentações de Fundos (CVM)

> **Desafio 3 — Processo Seletivo Outliers Advisory 2026**
> Estágio em Research, Dados & IA

Pipeline **totalmente automatizado** que consome os dados cadastrais de fundos do
[Portal de Dados Abertos da CVM](https://dados.cvm.gov.br/dados/FI/CAD/DADOS/) e
gera um **relatório gerencial semanal** das movimentações do universo de fundos:
lançamentos, fundos em fase pré-operacional e liquidações/cancelamentos.

O relatório é pensado para **leitura por pessoas não-técnicas** (gestão/research):
sai em **Markdown e HTML**, com resumo executivo, tabelas por categoria e
estatísticas agregadas.

---

## Índice

- [Como funciona (visão rápida)](#como-funciona-visão-rápida)
- [Configuração do ambiente](#configuração-do-ambiente)
- [Como rodar](#como-rodar)
- [O que é considerado "movimentação"](#o-que-é-considerado-movimentação)
- [Arquitetura e organização do código](#arquitetura-e-organização-do-código)
- [Testes](#testes)
- [Decisões técnicas](#decisões-técnicas)
- [Limitação conhecida e solução teórica mais robusta](#limitação-conhecida-e-solução-teórica-mais-robusta)
- [Exemplo de saída](#exemplo-de-saída)

---

## Como funciona (visão rápida)

```
                ┌──────────────┐   ┌──────────┐   ┌──────────────┐   ┌────────────┐
  CVM (ZIP)  →  │ cvm_client   │ → │ loader   │ → │ movements    │ → │ report     │  →  .md / .html
  download      │ + cache      │   │ normaliza│   │ (regras)     │   │ (md+html)  │
                └──────────────┘   └──────────┘   └──────────────┘   └────────────┘
```

1. **Download automatizado** do `registro_fundo_classe.zip` (com cache local e
   _retries_), extraindo o `registro_fundo.csv`.
2. **Normalização** do CSV (encoding latin-1, datas, limpeza de texto).
3. **Detecção de movimentações** na semana informada — uma função **pura e testável**.
4. **Geração do relatório** em Markdown e HTML.

A semana de referência é um **parâmetro de entrada** (`--start` / `--end`). O código
**não é hardcoded** para nenhuma semana: sem argumentos, ele usa automaticamente a
última semana completa (segunda a domingo), pronto para rodar como um job semanal.

---

## Configuração do ambiente

**Requisitos:** Python 3.10+ (testado em 3.14).

```bash
# 1) (recomendado) crie um ambiente virtual
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 2) instale as dependências
pip install -r requirements.txt
```

Dependências: apenas **pandas** (manipulação de dados) e **jinja2** (template HTML).
O download usa apenas a biblioteca padrão (`urllib`) — **sem `requests`** — para
minimizar atrito ao rodar em outra máquina.

---

## Como rodar

Do diretório `desafio-3-relatorio-semanal/`:

```bash
# Última semana completa (segunda a domingo). Baixa os dados da CVM automaticamente.
python run.py

# Semana específica (formato AAAA-MM-DD)
python run.py --start 2026-07-14 --end 2026-07-20

# Escolher formato de saída
python run.py --start 2026-07-14 --end 2026-07-20 --format html   # md | html | both

# Reprocessar sem baixar de novo (usa o cache local)
python run.py --offline

# Forçar novo download
python run.py --force-download

# Ver todas as opções
python run.py --help
```

Os relatórios são salvos em `reports/` como
`relatorio_fundos_<inicio>_<fim>.md` / `.html`.

| Argumento | Descrição | Default |
| --- | --- | --- |
| `--start` / `--end` | Semana de referência (AAAA-MM-DD). Informe os dois ou nenhum. | última semana completa |
| `--format` | `md`, `html` ou `both`. | `both` |
| `--output-dir` | Pasta de saída do relatório. | `reports/` |
| `--cache-dir` | Pasta de cache do download. | `data_cache/` |
| `--force-download` | Ignora o cache e rebaixa da CVM. | — |
| `--offline` | Usa apenas o cache local (sem rede). | — |
| `--data-file` | Usa um `registro_fundo.csv` local (pula download). | — |
| `-v` / `--verbose` | Log detalhado do pipeline. | — |

---

## O que é considerado "movimentação"

A fonte (`registro_fundo.csv`) traz, para cada fundo, a **situação atual** e um
conjunto de **datas de eventos**. Combinando situação + data, classificamos quatro
tipos de movimentação dentro da janela `[início, fim]` (inclusive):

| Categoria | Regra aplicada |
| --- | --- |
| **Fundos novos** | `Data_Registro` dentro da janela |
| **Fase pré-operacional** | `Situacao = "Fase Pré-Operacional"` **e** `Data_Inicio_Situacao` dentro da janela |
| **Em liquidação** | `Situacao = "Em Liquidação"` **e** `Data_Inicio_Situacao` dentro da janela |
| **Cancelados / encerrados** | `Situacao = "Cancelado"` **e** `Data_Cancelamento` dentro da janela |

Cada movimentação é apresentada com **nome do fundo, gestora, administrador,
classe/tipo, data e CNPJ**, atendendo (e ampliando) o solicitado no enunciado
(nome, gestora, classe/tipo e data).

> Um fundo recém-registrado que já nasce em fase pré-operacional aparece
> legitimamente em **duas** seções (novo _e_ pré-operacional) — são recortes
> diferentes da mesma semana. Isso é intencional e transparente no relatório.

### Bônus — estatísticas agregadas

- **Movimentações por classe/tipo de fundo** (FI, FIDC, FII, FIP, ...), com total.
- **Top gestoras por lançamentos** (ranking de quem mais lançou fundos novos).

---

## Arquitetura e organização do código

Separação clara de responsabilidades — cada módulo faz uma coisa e é testável
isoladamente:

```
desafio-3-relatorio-semanal/
├── run.py                       # ponto de entrada (python run.py ...)
├── requirements.txt
├── pyproject.toml               # config do pytest
├── relatorio_fundos/
│   ├── config.py                # URLs, nomes de colunas, rótulos de situação
│   ├── cvm_client.py            # download + cache + extração do ZIP (só stdlib)
│   ├── loader.py                # leitura e normalização do CSV → DataFrame
│   ├── periods.py               # parsing de datas e "última semana completa"
│   ├── movements.py             # NÚCLEO: detecção de movimentações (função pura)
│   ├── report.py                # geração de Markdown e HTML
│   └── templates/report.html.j2 # template HTML (Jinja2), CSS inline
├── tests/
│   └── test_movements.py        # testes unitários (dados sintéticos, sem rede)
└── reports/                     # saída gerada (+ exemplo/ versionado)
```

O **núcleo de regras** (`movements.detect_movements`) é uma **função pura**: recebe
um `DataFrame` e a janela, e devolve as movimentações — sem I/O, sem rede. Isso
torna a lógica trivialmente testável e reutilizável (ex.: chamar de um notebook,
de uma API ou de um agendador).

---

## Testes

```bash
pip install pytest
pytest -q
```

Os testes usam **dados sintéticos** (sem depender da rede) e cobrem:
- cada uma das quatro categorias de movimentação;
- **comportamento de fronteira** da janela (datas exatamente no limite);
- janela vazia e janela invertida (validação de erro);
- as estatísticas agregadas (por classe e top gestoras);
- o cálculo da "última semana completa".

Esse conjunto protege exatamente a parte que precisa **generalizar** para qualquer
semana que o avaliador venha a usar.

---

## Decisões técnicas

- **`urllib` no lugar de `requests`:** evita uma dependência externa no caminho de
  download, reduzindo o risco de não rodar na máquina do avaliador. Ainda assim há
  _retries_ com _backoff_ e _timeout_.
- **Cache local com validade (12h):** execuções repetidas não rebaixam 6 MB à toa;
  `--force-download` e `--offline` dão controle total.
- **Função pura para as regras:** a lógica de negócio fica isolada de I/O, o que
  viabiliza os testes unitários e a reutilização.
- **pandas** para o tratamento tabular (datas, agregações) e **Jinja2** para o HTML
  — o template com CSS inline gera um arquivo autocontido, fácil de abrir no
  navegador ou anexar em e-mail.
- **Config centralizada** (`config.py`): se a CVM renomear uma coluna ou um rótulo
  de situação, o ajuste é pontual. O `loader` inclusive **falha cedo** com mensagem
  clara se o layout mudar.
- **Robustez de encoding no console:** a saída é forçada para UTF-8 (o console do
  Windows usa cp1252 por padrão), evitando erros ao imprimir acentos.

---

## Limitação conhecida e solução teórica mais robusta

**O `registro_fundo.csv` é um _retrato_ (snapshot) do estado atual de cada fundo,
não um _log de eventos_.** Ele guarda a **situação atual** e a **data em que essa
situação começou** (`Data_Inicio_Situacao`), além de `Data_Registro` e
`Data_Cancelamento`.

Consequências:

- Conseguimos reconstruir com precisão a **transição mais recente** de cada fundo.
  Para um relatório semanal rodado sobre o arquivo — que a CVM **atualiza
  diariamente** — essa abordagem é correta e suficiente.
- **Porém**, se um fundo tiver passado por mais de uma transição, só enxergamos a
  última. Ex.: um fundo que entrou em pré-operacional e, na mesma janela histórica,
  já virou "Em Funcionamento Normal", apareceria hoje apenas como normal — a
  passagem por pré-operacional não seria recuperável a partir do snapshot atual.

**Solução teoricamente mais robusta** (fora do escopo de um único arquivo, mas
descrita para deixar o raciocínio explícito):

1. **Diff de snapshots diários:** persistir o `registro_fundo.csv` a cada dia
   (ex.: em um banco/Parquet particionado por data) e, para a semana pedida,
   comparar o estado de cada `CNPJ_Fundo` no início vs. no fim da janela. Assim
   qualquer transição de `Situacao` vira um evento — inclusive as intermediárias —
   sem depender de as datas do snapshot "sobreviverem".
2. **Base histórica da CVM:** complementar com o `cad_fi_hist.zip` (histórico
   cadastral) para reconstruir transições passadas.

O código já está **estruturado para isso**: como as regras são uma função pura que
recebe um `DataFrame`, migrar para uma estratégia de _diff_ significa trocar a
camada de dados (o que entra em `movements`), sem reescrever a lógica de relatório.

---

## Exemplo de saída

Um exemplo real (semana de 13 a 19/07/2026) está versionado em
[`reports/exemplo/`](reports/exemplo/) — abra o `.html` no navegador para ver o
layout completo (resumo executivo em cartões, tabelas por categoria e estatísticas
agregadas).

Resumo daquela semana:

| Categoria | Quantidade |
| --- | ---: |
| Fundos novos (registrados) | 88 |
| Entraram em fase pré-operacional | 83 |
| Entraram em liquidação | 20 |
| Cancelados / encerrados | 21 |

---

_Fonte de dados: Portal de Dados Abertos da CVM — `FI/CAD/DADOS/registro_fundo_classe.zip`._
