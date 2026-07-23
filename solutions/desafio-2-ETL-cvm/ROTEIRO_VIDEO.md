# Roteiro do vídeo — Desafio 2 (ETL de FIDCs / CVM)

**Duração-alvo:** 8 minutos · **Formato:** gravação de tela + narração.
Cada bloco tem **[TELA]** (o que mostrar) e **[FALA]** (o que dizer). A fala é um
guia — melhor soar natural do que ler ao pé da letra.

> Antes de gravar: deixe um terminal aberto na raiz do projeto, o `data/` já
> baixado (o download de 1,6 GB não cabe no vídeo), e o editor com os arquivos
> `sanitizer.py`, `loader.py` e `retornos.py` em abas.

---

## 0. Abertura — 0:00 a 0:40

**[TELA]** README aberto no topo.

**[FALA]**
"Olá, eu sou o Luis Henrique. Escolhi o Desafio 2: o ETL dos informes mensais de
FIDCs da CVM, que calcula o retorno de cada subclasse de cota nas janelas de mês
atual, 6, 12, 24 e 36 meses.

O que eu quero mostrar não é só que o código roda — é o *processo*. Esses dados
da CVM têm várias armadilhas silenciosas, e a maior parte das minhas decisões de
engenharia nasceu de patologias reais que fui encontrando ao inspecionar os
arquivos. Vou passar por elas."

---

## 1. Arquitetura e a escolha do DuckDB — 0:40 a 2:00

**[TELA]** Diagrama do fluxo no README (download → normalize → returns); depois a
tabela de módulos.

**[FALA]**
"A arquitetura são três etapas independentes, cada uma um subcomando de CLI:
baixar, normalizar e calcular retornos. Elas se comunicam por artefatos em disco
— os CSVs e um banco DuckDB — então eu posso rodar cada uma isolada e reprocessar
sem rebaixar tudo.

A decisão técnica mais estruturante foi usar **DuckDB em vez de pandas**. O
histórico completo são cerca de 1,6 GB de CSV, que viram 770 mil linhas de
painel. Carregar isso em memória seria lento e frágil. O DuckDB lê o CSV direto
do disco e executa as transformações como SQL vetorizado, com *streaming*.

E tem uma segunda razão, mais sutil: eu escrevi as **regras de limpeza como
macros SQL**, não como funções Python."

**[TELA]** Abrir `sanitizer.py`, rolar pelas macros (`norm_cnpj`, `norm_decimal`,
`norm_subclasse`, `tipo_subclasse`).

**[FALA]**
"Isso me dá uma fonte única de verdade. A mesma macro é usada pela normalização,
pelo cálculo de retorno e pelos testes. Não existe o risco de a regra do teste
divergir da regra de produção — é literalmente o mesmo SQL."

---

## 2. Rodando o pipeline — 2:00 a 3:30

**[TELA]** Terminal. Rodar a normalização:

```bash
uv run python src/__main__.py normalize
```

**[FALA]** (enquanto roda)
"O `download` eu já rodei antes com a flag `--hist`, que traz o histórico anual
necessário para a janela de 36 meses. Agora a normalização: ela lê todas as
competências, aplica as macros e materializa o painel `fato_subclasse_mensal`."

**[TELA]** Apontar a linha final do log: *162 meses (2013-01 a 2026-06), 772 mil
linhas, 5960 classes, 44 mil subclasses*.

**[FALA]**
"Pronto: 162 competências, de 2013 a 2026, sem nenhuma linha descartada. Agora os
retornos."

**[TELA]** Rodar:

```bash
uv run python src/__main__.py returns
```

**[FALA]** (enquanto roda)
"Isso atribui a chave de série estável, calcula as cinco janelas e exporta o
consolidado da data de referência mais recente em CSV e Parquet."

**[TELA]** Mostrar o bloco de cobertura no log (69% / 61% / 44% / 26% / 8%) e
depois abrir `output/retornos_subclasse_202606.csv` (ou o Parquet num viewer).

**[FALA]**
"O resultado: uma linha por subclasse na data de referência, com CNPJ, PL,
número de cotistas, os cinco retornos e — importante — a data-base que cada
retorno usou. Já volto na cobertura, porque ela conta uma história."

---

## 3. As decisões difíceis — 3:30 a 6:45

> Este é o coração do vídeo. Quatro patologias, cada uma com o problema e a
> solução.

### 3a. Encoding misto — 3:30 a 4:10

**[TELA]** `loader.py`, função `decode_mixed_encoding`.

**[FALA]**
"A primeira armadilha: encoding. Os arquivos são latin-1, tranquilo. Mas quatro
arquivos de abril de 2024, de um FIDC da Stone, quebravam o DuckDB inteiro. Ao
inspecionar byte a byte, descobri uma *dupla codificação*: na mesma linha, a
denominação social vinha em latin-1 correto, mas o rótulo da subclasse trazia
bytes UTF-8 crus. O byte `0x83` não existe em latin-1 e o parser abortava.

O detalhe é que trocar só o byte inválido produziria um nome corrompido e
quebraria a série daquela subclasse em duas. Então eu decodifico de verdade — onde
há UTF-8 válido ele vence, senão cai para latin-1 — e só nos arquivos que o DuckDB
de fato recusa, para não arriscar o resto do corpus."

### 3b. Schema drift da Resolução 175 — 4:10 a 5:10

**[TELA]** README, decisão 3 e 4. Se quiser, mostrar dois headers de CSV lado a
lado (`CNPJ_FUNDO` vs `CNPJ_FUNDO_CLASSE`).

**[FALA]**
"A segunda, e a mais séria: o layout dos informes mudou com a Resolução CVM 175,
em outubro de 2023. Antes, a chave era `CNPJ_FUNDO`; depois, `CNPJ_FUNDO_CLASSE`.
E os arquivos antigos, até 2018, são anuais em vez de mensais.

Aqui eu cometi — e corrigi — um bug instrutivo. Minha primeira versão escolhia
uma coluna de CNPJ para o corpus todo. Como a coluna nova existe nos arquivos
recentes, ela foi escolhida, e ficou nula em dez anos de arquivos antigos, que o
filtro então descartava *em silêncio*. Eu só percebi porque o painel tinha 33
meses quando eu esperava 90.

A correção foi fazer um `coalesce` das duas colunas, e — mais importante — eu
adicionei um log que reporta qualquer linha descartada na normalização. Perda de
dado nunca mais é muda.

Além disso, a Res. 175 *reescreveu os rótulos* das subclasses. Casando por texto,
só 136 de 9.500 séries sobreviviam à virada. Mas o CNPJ atravessa a fronteira, e
o tipo — sênior, mezanino, subordinada — continua legível dos dois lados. Então
construí uma ponte que casa CNPJ mais tipo, mas só quando o tipo é único na
classe. Isso subiu a continuidade de 136 para 3.365 séries."

### 3c. A chave de série estável — 5:10 a 6:10

**[TELA]** `retornos.py`, função `build_series_key` (a lógica `TIPO` vs `ROTULO`).

**[FALA]**
"A terceira decisão é a que mais afeta o resultado. Investigando por que as
janelas longas tinham cobertura baixa, descobri que o rótulo da subclasse **não é
estável nem dentro do layout novo**. A mesma cota sênior aparece como 'Série 1',
depois só 'Sênior', depois 'Subclasse 1' — o nome muda quando o administrador
deixa de reportar e volta.

Ou seja, o rótulo não serve como identificador da série temporal. A solução foi
usar como chave o par CNPJ mais tipo, colapsando as variações de nome. Isso só
funciona quando a classe tem uma única subclasse daquele tipo — que é metade dos
casos. Na outra metade, com múltiplas subclasses do mesmo tipo, colapsar seria um
chute, então caio de volta para o rótulo e marco isso numa coluna, para ser
transparente."

### 3d. Zero como ausente — 6:10 a 6:45

**[TELA]** README, decisão 6.

**[FALA]**
"E a quarta: cerca de 28% das linhas de valor de cota vêm zeradas. Isso não quer
dizer que a cota vale zero — quer dizer que o mês não foi reportado. Tratar como
valor produziria retornos de menos 100% e mais infinito.

Então, no cálculo, zero conta como ausente: a âncora de cada janela é a cota
válida mais próxima da data-alvo, com uma tolerância que cresce com a janela. E a
data que foi de fato usada vai no arquivo de saída, na coluna `data_base` — então
qualquer retorno é auditável."

---

## 4. Validação e limitações — 6:45 a 7:40

**[TELA]** Rodar a validação ao vivo (ou mostrar o número no README).

```bash
uv run pytest -q
```

**[FALA]**
"Como eu sei que os retornos estão certos? A CVM publica, numa outra tabela, a
rentabilidade mensal que o próprio administrador informa. Eu cruzei o meu cálculo
do mês atual com esse número de referência: 77,8% batem dentro de cinco
centésimos de ponto percentual, com erro mediano de três milésimos. Praticamente
exato.

Os 22% que divergem não são bug — são meses de amortização de cota, em que o
valor da cota cai sem que haja perda. Isso me leva às limitações, que eu faço
questão de deixar explícitas."

**[TELA]** README, seção de limitações.

**[FALA]**
"A cobertura de 36 meses é de só 8%. Mas isso é um limite *dos dados*, não do
método: apenas 2.281 subclasses têm três anos de histórico válido — a maioria dos
FIDCs é nova demais. Segundo: a amortização de cota ainda não é separada do
retorno; hoje eu *sinalizo* os casos estruturais numa flag, em vez de capar o
número silenciosamente. Separar de fato exigiria cruzar com as tabelas de
captação e resgate, e é a evolução natural do pipeline.

E os 44 testes cobrem cada uma dessas patologias com casos derivados dos dados
reais."

---

## 5. Fechamento — 7:40 a 8:00

**[TELA]** Voltar ao `output/` com o CSV final; ou o histórico de commits.

**[FALA]**
"Resumindo: o pipeline é totalmente automatizado, do download ao arquivo final,
tratando encoding, mudança de layout regulatório e a instabilidade dos rótulos —
e é honesto sobre o que não consegue resolver, com flags e logs em vez de números
escondidos. Toda a lógica de decisão está documentada no README. Obrigado."

---

## Checklist de gravação

- [ ] `data/` baixado (com `--hist`) antes de gravar
- [ ] Terminal com fonte grande e legível
- [ ] Abas abertas: `sanitizer.py`, `loader.py`, `retornos.py`, README
- [ ] `output/retornos_subclasse_202606.csv` já gerado, para abrir rápido
- [ ] Testar o áudio antes da tomada final
- [ ] Cronometrar: se passar de 10 min, cortar o bloco 3d ou encurtar o 1
