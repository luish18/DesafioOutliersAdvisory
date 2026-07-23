# Desafio 2 — ETL de Informes Mensais de FIDCs (CVM)

Pipeline automatizado que baixa os informes mensais de FIDCs do Portal de Dados
Abertos da CVM, trata e normaliza os dados, e calcula os retornos de cada
**subclasse de cota** nas janelas de mês atual, 6, 12, 24 e 36 meses a partir da
variação do valor da cota.

A entrega final é um arquivo tabular (CSV + Parquet) consolidado para a data de
referência mais recente disponível, na granularidade da subclasse, contendo
CNPJ, identificação da subclasse, data de referência, PL, número de cotistas e
os retornos calculados.

---

## Sumário

- [Como executar](#como-executar)
- [Arquitetura do pipeline](#arquitetura-do-pipeline)
- [O arquivo de saída](#o-arquivo-de-saída)
- [Decisões de projeto e racionais](#decisões-de-projeto-e-racionais)
- [Limitações conhecidas](#limitações-conhecidas)
- [Testes](#testes)

---

## Como executar

### Pré-requisitos

- **Python 3.13+**
- **pip** (nenhuma outra ferramenta é necessária)

### Instalação

```bash
# Crie e ative um ambiente virtual (recomendado), depois instale as dependências.
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows (PowerShell):
# .venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### Passo a passo

```bash
# 1. Baixar e extrair os informes da CVM.
#    Sem --hist: apenas os ~18 meses recentes do diretório DADOS/.
#    Com  --hist: inclui os ZIPs anuais históricos (2013+), necessários para a
#    janela de 36 meses. O download histórico é da ordem de 1,6 GB.
python src/__main__.py download --hist

# 2. Tratar e normalizar os CSVs no painel mensal (materializa o DuckDB).
python src/__main__.py normalize

# 3. Calcular os retornos e exportar o consolidado da data de referência.
python src/__main__.py returns
```

Ao final, o arquivo consolidado estará em
`output/retornos_subclasse_<AAAAMM>.csv` e `.parquet`.

### Parâmetros úteis

Todos os comandos aceitam:

| Flag | Padrão | Descrição |
|------|--------|-----------|
| `--raw-dir` | `data/` | diretório dos CSVs extraídos |
| `--db` | `data/warehouse/fidc.duckdb` | banco DuckDB materializado |
| `--output-dir` | `output/` | destino dos arquivos consolidados |
| `-v`, `--verbose` | — | logging em nível DEBUG |

Os caminhos também podem ser sobrescritos por variáveis de ambiente
(`SAVE_PATH`, `DB_PATH`, `OUTPUT_PATH`, etc.) — ver [`src/config.py`](src/config.py).

---

## Arquitetura do pipeline

O pipeline tem três etapas independentes, cada uma um subcomando da CLI. O
DuckDB é o motor de transformação: em vez de carregar dezenas de milhões de
linhas em memória com pandas, as regras são expressas em SQL e executadas
diretamente sobre os CSVs.

```
                 download                normalize                 returns
  Portal CVM  ─────────────▶  data/  ─────────────▶  DuckDB  ─────────────▶  output/
  (ZIPs)        scraper.py    (CSVs)   loader.py +    (fato_    retornos.py    CSV +
                                       sanitizer.py   subclasse)               Parquet
```

| Módulo | Responsabilidade |
|--------|------------------|
| [`config.py`](src/config.py) | Configuração central (URLs, caminhos, encoding, constantes) |
| [`scraper.py`](src/scraper.py) | Descobre os ZIPs na página da CVM, baixa de forma assíncrona e extrai |
| [`sanitizer.py`](src/sanitizer.py) | Regras de normalização como **macros SQL do DuckDB** (fonte única de verdade) |
| [`loader.py`](src/loader.py) | Consolida os CSVs no painel `fato_subclasse_mensal` |
| [`retornos.py`](src/retornos.py) | Atribui a chave de série estável e calcula os retornos |
| [`__main__.py`](src/__main__.py) | Interface de linha de comando |

### Modelo de dados (DuckDB)

- **`raw_*`** — views sobre os CSVs brutos, um por tabela de origem (`tab_I`, `tab_IV`, `tab_X_1`, `tab_X_2`, `tab_X_3`).
- **`stg_*`** — staging normalizado e deduplicado (uma linha por chave, última republicação vence).
- **`fato_subclasse_mensal`** — painel mensal na granularidade da subclasse (o coração da normalização).
- **`dim_ponte_subclasse`** — de-para de subclasses através da fronteira da Resolução CVM 175.
- **`panel_keyed` / `serie_cota_mensal`** — painel com a chave de série estável e as âncoras de cota válida.
- **`saida_retornos`** — o consolidado final da data de referência.
- **`qa_normalizacao`** — relatório de qualidade por competência (contagens e flags).

---

## O arquivo de saída

`output/retornos_subclasse_<AAAAMM>.csv` (e `.parquet`), uma linha por subclasse
na data de referência. Colunas:

| Coluna | Descrição |
|--------|-----------|
| `data_referencia` | Data de referência (fim do mês mais recente) |
| `cnpj_fundo` | CNPJ da classe/fundo, com máscara |
| `denominacao_social` | Nome do fundo |
| `tipo_registro` | `Fundo` ou `Classe` (layout do informe) |
| `identificacao_subclasse` | Rótulo canônico da subclasse |
| `tipo_subclasse` | `SENIOR` / `MEZANINO` / `SUBORDINADA` / `UNICA` / … |
| `id_subclasse_cvm` | Identificador oficial da CVM, quando informado |
| `chave_serie` | Chave da série temporal usada no cálculo |
| `metodo_chave` | `TIPO` (colapsado por tipo) ou `ROTULO` (fallback) |
| `origem_chave_res175` | `DIRETO` / `PONTE_TIPO` / `SEM_PONTE` (ver decisões) |
| `pl_classe` | PL da **classe** na data de referência (fonte: `tab_IV`) |
| `pl_subclasse_estimado` | PL implícito da subclasse (`qt_cota × vl_cota`) |
| `numero_cotistas` | Número de cotistas da subclasse |
| `vl_cota_referencia` | Valor da cota na data de referência |
| `retorno_mes_atual` … `retorno_36m` | Retornos por janela (fração: `0.1234` = 12,34%) |
| `data_base_mes_atual` … `data_base_36m` | Data efetivamente usada como âncora de cada janela |
| `alerta_retorno_estrutural` | `true` quando o retorno reflete evento estrutural da cota, não desempenho |

Cada retorno é `vl_cota_referencia / vl_cota_âncora − 1`, e `data_base_*`
torna-o auditável: mostra exatamente de qual mês veio o denominador.

---

## Decisões de projeto e racionais

Esta seção documenta as decisões não óbvias. A maioria nasceu de patologias
reais encontradas nos dados da CVM, não de preferência de estilo.

### 1. DuckDB + macros SQL em vez de pandas

O corpus histórico completo tem **~770 mil linhas de painel** a partir de **~1,6
GB de CSV**. Carregar isso em memória com pandas seria lento e frágil. O DuckDB
lê os CSVs direto do disco, faz *streaming* das agregações e executa as regras
como SQL vetorizado.

As regras de normalização ficam em [`sanitizer.py`](src/sanitizer.py) como
**macros SQL** (`CREATE MACRO`), não como funções Python. Isso garante uma
**única fonte de verdade**: o `loader`, o `retornos` e os testes exercitam
exatamente a mesma lógica de limpeza, sem risco de divergência entre "a regra do
código" e "a regra do teste".

### 2. Encoding: `latin-1` com reparo de encoding misto

Os CSVs da CVM são `latin-1` com separador `;`. Porém, **4 arquivos de 2024-04**
(FIDC Stone IV) trazem *dupla codificação*: a denominação social vem em latin-1
correto (`\xc3` = Ã) mas o rótulo da subclasse, na mesma linha, traz bytes UTF-8
crus (`\xc3\x83` = Ã). O byte `\x83` não existe em ISO-8859-1 e o DuckDB recusa o
arquivo inteiro.

Corrigir só o byte inválido produziria `CARTAƒO`, quebrando a subclasse em duas
séries distintas. A solução (`decode_mixed_encoding` em `loader.py`) decodifica
de verdade — onde há sequência UTF-8 válida ela vence, senão cai para latin-1 — e
só é aplicada aos arquivos que o DuckDB de fato recusa. Os arquivos reparados vão
para `.cache/csv_utf8/`, **fora** de `data/` (senão o glob leria o mesmo arquivo
duas vezes e duplicaria linhas).

### 3. Schema drift entre layouts (Resolução CVM 175)

O layout dos informes mudou ao longo do tempo:

- **Até 2018**: um arquivo **anual** (`..._tab_X_2_2018.csv`) com os 12 meses dentro.
- **2019 → 2023-09**: um arquivo **mensal** por competência, chave `CNPJ_FUNDO`, rótulo `"Classe Subordinada 1"`.
- **2023-10 em diante** (Res. CVM 175): chave `CNPJ_FUNDO_CLASSE`, rótulo `"Subclasse Subordinada …"`.

O loader absorve isso de três formas:

- **Glob por nº de dígitos** — aceita tanto o sufixo anual (`AAAA`) quanto o mensal (`AAAAMM`), sem confundir `tab_X_1` com `tab_X_1_1` (que é outra tabela).
- **`_alias()` com `coalesce`** — resolve `CNPJ_FUNDO_CLASSE` **e** `CNPJ_FUNDO` juntos. Escolher só uma das colunas faria as linhas do outro layout virarem NULL e serem descartadas em silêncio (bug real que apagava 10 anos de histórico).
- **`log_discarded_rows()`** — reporta em warning qualquer linha que não sobreviva à normalização, para que perda de dados nunca mais seja muda.

### 4. Ponte da Res. 175 para a série temporal

Na virada de 2023-10, a CVM **reescreveu os rótulos** das subclasses. Casando por
texto, apenas 136 de 9.512 chaves sobrevivem à fronteira — o que zeraria a janela
de 36 meses. Mas o **CNPJ atravessa** a fronteira (2.253 classes) e o **tipo**
(sênior/mezanino/subordinada) continua legível dos dois lados.

`dim_ponte_subclasse` casa `(CNPJ, tipo)` **apenas quando o tipo é único dos dois
lados** da fronteira. Onde a classe tem duas subclasses do mesmo tipo, o
casamento seria arbitrário e nada é mapeado. A coluna `origem_chave_res175`
registra a proveniência (`DIRETO` / `PONTE_TIPO` / `SEM_PONTE`). A fronteira é
descoberta pela marca de layout de cada linha, **não** cravada em código.

### 5. Chave da série temporal: `(CNPJ, tipo_subclasse)`, não o rótulo

O rótulo publicado pela CVM **não é estável nem dentro do layout novo**. A mesma
cota sênior aparece como `Série 1`, depois `Sênior` (nos meses em que o
administrador não reporta e a cota vem zerada), depois `Subclasse 1`. Casar por
rótulo fragmentaria a série e derrubaria as janelas longas.

A chave da série (`retornos.py`) colapsa para o **tipo** quando ele é único na
classe dentro do horizonte analisado (últimos 40 meses) — método `TIPO`. Quando a
classe tem duas subclasses do mesmo tipo, a colapsagem seria arbitrária e a chave
cai para o rótulo — método `ROTULO`, marcado em `metodo_chave`. O escopo de 40
meses evita que uma multiplicidade antiga e já extinta force o método de rótulo
numa série hoje única.

### 6. `vl_cota = 0` significa "não reportado", não "vale zero"

Cerca de **28%** das linhas de valor de cota vêm zeradas — são meses sem reporte,
não cotas que valem zero. Tratadas como valor, produziriam retornos de −100% e
+∞. No cálculo de retorno, `vl_cota ≤ 0` (e NULL) contam como **ausentes**: a
âncora de cada janela é a cota **válida** mais próxima da data-alvo, dentro de uma
tolerância que cresce com a janela (0/1/2/2/3 meses). A tolerância é estrita no
mês atual (só o mês imediatamente anterior) porque um mês de folga distorce muito
um retorno de 1 mês e pouco um de 36 meses. A `data_base_*` no output revela a
âncora efetivamente usada.

### 7. PL da classe vs. PL da subclasse

O enunciado pede PL na linha da subclasse, mas a CVM só publica PL no nível da
**classe** (`tab_IV`). Em vez de esconder isso, o output carrega os dois lado a
lado: `pl_classe` (o valor oficial) e `pl_subclasse_estimado`
(`qt_cota × vl_cota`, o PL implícito da subclasse).

### 8. Flag de retorno estrutural

Cotas **subordinadas** absorvem o PL residual da classe e seu valor unitário é
ruidoso — em alguns casos a cota-âncora é um resíduo perto de zero (~1e-8),
produzindo "retornos" de milhões por cento que são matematicamente corretos e
economicamente sem sentido. Em vez de capar silenciosamente (o que esconderia
retornos legítimos altos), a coluna `alerta_retorno_estrutural` sinaliza esses
casos: variação mensal fora de [−50%, +100%] ou janela longa com magnitude
extrema. O valor bruto permanece no arquivo; a pesquisa decide se filtra. O
limiar vem da própria `tab_X_3` da CVM, onde a rentabilidade informada
praticamente nunca sai de um dígito.

### Validação

A implementação foi validada cruzando o `retorno_mes_atual` calculado com a
rentabilidade que a **própria CVM informa** na `tab_X_3`: **77,8%** batem dentro
de 0,05 p.p., com **erro mediano de 0,003 p.p.** Os ~22% que divergem são meses
de amortização/capitalização de cota — o valor da cota cai sem que haja perda —,
o que motiva a flag da decisão 8 e é uma limitação conhecida (abaixo).

---

## Limitações conhecidas

- **Cobertura de 36 meses é baixa (~8%)** — e isso é um limite **real dos dados**,
  não da metodologia: apenas ~2.281 subclasses têm ≥37 meses de cota válida. A
  maioria dos FIDCs é jovem demais para 3 anos de histórico. Cobertura por janela:
  ~69% (1m), 61% (6m), 44% (12m), 26% (24m), 8% (36m).
- **Amortização de cota não é separada de retorno** — nos ~22% de casos em que a
  variação da cota reflete amortização/capitalização em vez de desempenho, o
  retorno fica distorcido. Hoje esses casos são **sinalizados** (`alerta_retorno_estrutural`),
  não corrigidos. Separá-los exigiria cruzar com as tabelas de captação/resgate
  (`tab_X_4`) e é uma evolução natural do pipeline.
- **Fallback por rótulo herda a instabilidade do rótulo** — nos ~50% de casos com
  tipo múltiplo na classe (método `ROTULO`), a série ainda sofre com a deriva de
  nome, reduzindo a cobertura das janelas longas para esses fundos.

---

## Testes

```bash
pytest
```

A suíte (44 testes) cobre as regras de normalização e o cálculo de retornos com
casos derivados de patologias **reais** dos dados:

- normalização de CNPJ, decimais, datas e rótulos de subclasse;
- casamento de rótulos divergentes entre `tab_X_1` e `tab_X_2`;
- reparo de encoding misto (o caso Stone IV de 2024-04);
- `_alias` com `coalesce` quando o corpus mistura dois layouts;
- série que atravessa a deriva de rótulo via chave `TIPO`;
- `vl_cota = 0` tratada como ausente, com recuo da âncora;
- fallback para `ROTULO` quando o tipo é múltiplo;
- sinalização de retorno estrutural.

Os testes de retorno montam um `fato_subclasse_mensal` mínimo em memória — não
dependem do download nem do banco materializado.
