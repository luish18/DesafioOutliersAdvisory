# Roteiro do Vídeo de Apresentação — Desafio 3

**Duração alvo:** 7–8 min (dentro da faixa de 5–10 min)
**Formato:** gravação de tela rodando o código + narração explicando decisões.

> Como usar este roteiro: cada bloco tem **[TELA]** (o que mostrar) e **[FALA]**
> (o que dizer — pode adaptar com suas palavras, não precisa decorar). Os tempos
> são uma referência; o importante é o encadeamento do raciocínio.
>
> **Dica de preparação:** antes de gravar, rode uma vez com `--force-download`
> para popular o cache. Assim, durante a gravação, a execução é rápida e você não
> fica esperando o download.

---

## 0:00 – 0:45 | Abertura e contexto

**[TELA]** README aberto (topo), ou o enunciado do Desafio 3 ao lado.

**[FALA]**
- "Oi, eu sou o(a) [nome]. Escolhi o **Desafio 3 — Relatório Semanal de
  Movimentações de Fundos**."
- "O problema: toda semana a área quer saber, de forma gerencial, o que mudou no
  universo de fundos — quais gestoras lançaram produtos novos, quais fundos
  entraram em fase pré-operacional e quais foram liquidados ou cancelados."
- "A fonte é o cadastro de fundos do **Portal de Dados Abertos da CVM**, e o
  objetivo é **automatizar** essa visão: baixar o dado, detectar as mudanças de
  uma semana e gerar um relatório legível para pessoas não-técnicas."

---

## 0:45 – 1:45 | Demonstração rápida (mostrar o resultado primeiro)

> Estratégia: mostrar o **output** logo no começo prende a atenção. Depois você
> abre o capô e explica como chegou lá.

**[TELA]** Terminal na pasta `desafio-3-relatorio-semanal/`. Rode:
```bash
python run.py --start 2026-07-14 --end 2026-07-20
```

**[FALA]** (enquanto roda)
- "Rodando com uma semana de referência como parâmetro. Ele baixa o dado da CVM —
  aqui já está em cache —, processa os quase **90 mil fundos** da base e detecta
  as movimentações da semana."
- Quando terminar, aponte para o resumo no console: "Aqui já vejo o placar:
  X fundos novos, Y em pré-operacional, Z em liquidação, W cancelados."

**[TELA]** Abra o **HTML** gerado em `reports/` no navegador. Role de cima a baixo:
resumo em cartões → tabelas por categoria → estatísticas agregadas.

**[FALA]**
- "O relatório sai em **HTML e Markdown**. Este HTML é autocontido, dá pra abrir
  no navegador ou anexar num e-mail. No topo, o resumo executivo em cartões."
- "Cada movimentação vem com **nome do fundo, gestora, administrador, classe/tipo,
  data e CNPJ**."
- "E no fim, o **bônus**: movimentações agregadas por classe de fundo e o ranking
  de gestoras que mais lançaram produtos na semana."

---

## 1:45 – 2:45 | O parâmetro de semana (não é hardcoded)

**[TELA]** Rode uma segunda janela, diferente:
```bash
python run.py --offline --start 2026-06-30 --end 2026-07-06
```
Mostre que o placar muda.

**[FALA]**
- "Um requisito central: o código **não pode ser hardcoded** para uma semana. A
  janela é um parâmetro de entrada — troquei as datas e os números mudam."
- "Se eu rodar **sem argumentos**, ele assume automaticamente a **última semana
  completa**, de segunda a domingo — pensando no uso real, como um job semanal."
- (Opcional) Mostre `python run.py --help` rapidamente: "Tem controle de formato,
  cache, modo offline e um arquivo local pra testes."

---

## 2:45 – 4:15 | Arquitetura e organização do código

**[TELA]** Abra a estrutura de pastas (árvore de `relatorio_fundos/`).

**[FALA]** — percorra os módulos explicando a **separação de responsabilidades**:
- "Separei o pipeline em módulos, cada um com uma responsabilidade. Isso deixa o
  código testável e fácil de manter."
- **`cvm_client.py`** — "Cuida só de **obter o dado**: download, cache e extração
  do ZIP."
- **`loader.py`** — "**Normaliza** o CSV: encoding, datas, limpeza de texto."
- **`movements.py`** — "É o **núcleo**. Aqui ficam as regras de negócio."
- **`report.py`** + template Jinja2 — "Gera o Markdown e o HTML."
- **`periods.py`** — "Isola a lógica de datas, incluindo a 'última semana completa'."
- **`config.py`** — "Centralizei URLs, nomes de coluna e rótulos de situação. Se a
  CVM renomear algo, eu conserto num lugar só."

**[TELA]** Abra `movements.py` na função `detect_movements`.

**[FALA]** — **destaque a decisão mais importante de design:**
- "A função de detecção é **pura**: recebe um DataFrame e a janela de datas, e
  devolve as movimentações. Ela **não faz download nem lê arquivo** — não tem
  I/O."
- "Isso foi uma decisão consciente: separar a **regra de negócio** da
  **infraestrutura**. O ganho é que consigo testar a lógica com dados sintéticos,
  sem depender da rede, e reaproveitá-la em outro contexto — um notebook, uma API."

---

## 4:15 – 5:15 | As regras de negócio (como defino cada movimentação)

**[TELA]** Continue em `movements.py`, mostrando as quatro máscaras.

**[FALA]**
- "O arquivo da CVM traz, por fundo, a **situação atual** e um conjunto de
  **datas**. Combinei situação + data pra classificar quatro tipos de movimentação
  dentro da janela:"
  - "**Novos**: `Data_Registro` dentro da semana."
  - "**Pré-operacional**: situação atual é 'Fase Pré-Operacional' **e** a data de
    início dessa situação caiu na semana."
  - "**Liquidação**: mesma ideia, com 'Em Liquidação'."
  - "**Cancelados**: situação 'Cancelado' **e** `Data_Cancelamento` na semana."
- "Um detalhe honesto: um fundo que **nasce** já em pré-operacional aparece em duas
  seções — novo e pré-operacional. São recortes diferentes da mesma semana, e eu
  deixo isso explícito no relatório em vez de esconder."

---

## 5:15 – 6:15 | A parte mais difícil (snapshot vs. log de eventos)

> Este é o bloco que mais mostra maturidade. O enunciado pede explicitamente:
> "qual foi a parte mais difícil? como contornou?".

**[TELA]** README, seção "Limitação conhecida e solução teórica mais robusta".

**[FALA]**
- "A parte que mais me fez pensar foi entender a **natureza do dado**. O
  `registro_fundo.csv` é um **retrato do estado atual** de cada fundo — não é um
  **log de eventos**."
- "Ou seja: ele guarda a situação de **hoje** e a data em que ela começou. Com isso
  eu reconstruo bem a **transição mais recente** de cada fundo. E, como a CVM
  atualiza esse arquivo **diariamente**, para um relatório semanal essa abordagem é
  correta e suficiente."
- "**Mas** eu preciso ser transparente sobre o limite: se um fundo passou por mais
  de uma transição, eu só enxergo a última. Um fundo que foi pré-operacional e já
  virou 'normal' apareceria hoje só como normal."
- "A solução teoricamente mais robusta — que documentei no README — é **guardar um
  snapshot por dia** e **comparar** o estado de cada CNPJ no início e no fim da
  semana. Aí qualquer mudança de situação vira um evento, inclusive as
  intermediárias."
- "E o ponto importante de engenharia: como as regras são uma **função pura que
  recebe um DataFrame**, migrar pra essa estratégia de diff significa trocar só a
  **camada de dados** — a lógica de relatório continua igual. A arquitetura já está
  preparada pra essa evolução."

---

## 6:15 – 7:15 | Testes e robustez

**[TELA]** Rode os testes:
```bash
pytest -q
```
Depois abra `tests/test_movements.py`.

**[FALA]**
- "Escrevi **testes unitários** com dados sintéticos, sem rede. Eles cobrem cada
  categoria, o **comportamento de fronteira** da janela — datas exatamente no
  limite —, janela vazia e janela invertida."
- "Testar a fronteira foi proposital: é exatamente a parte que precisa
  **generalizar** para qualquer semana que vocês usarem na avaliação."
- Decisões de robustez (cite 2–3):
  - "No download usei só a **biblioteca padrão** (`urllib`), sem `requests`, pra
    reduzir dependências e o risco de não rodar na máquina de vocês. Mesmo assim
    tem **retry com backoff**, timeout e **cache**."
  - "O `loader` **falha cedo** com mensagem clara se a CVM mudar o layout das
    colunas."
  - "Forcei a saída do console pra **UTF-8**, porque o terminal do Windows usa
    cp1252 e quebraria com os acentos."

---

## 7:15 – 8:00 | Fechamento

**[FALA]**
- "Resumindo: um pipeline automatizado de ponta a ponta — **baixa** o dado da CVM,
  **detecta** as movimentações de uma semana parametrizável e **gera** um relatório
  gerencial legível, com estatísticas agregadas de bônus."
- "As decisões que eu mais defendo: **isolar a regra de negócio em uma função
  pura** — que viabilizou os testes e deixa a evolução pra diff de snapshots
  natural — e ser **transparente sobre a limitação do dado** em vez de fingir que
  ela não existe."
- "Obrigado(a)!"

---

## Checklist antes de gravar

- [ ] `pip install -r requirements.txt` já rodado no ambiente.
- [ ] Rodar `python run.py --force-download` uma vez para popular o cache.
- [ ] Terminal com fonte legível (aumente o zoom).
- [ ] Ter o HTML de exemplo pronto pra abrir no navegador.
- [ ] Fechar abas/notificações que possam aparecer na gravação.
- [ ] Testar áudio antes.

## Comandos que aparecem no vídeo (cola rápida)

```bash
# demo principal
python run.py --start 2026-07-14 --end 2026-07-20

# segunda janela (mostra que não é hardcoded)
python run.py --offline --start 2026-06-30 --end 2026-07-06

# sem argumentos = última semana completa
python run.py

# ajuda
python run.py --help

# testes
pytest -q
```
