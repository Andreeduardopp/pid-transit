# PID-Transit no mercado

Vinte e três usos reais em projetos de trânsito, mapeados contra o que a biblioteca
já faz hoje — e o roteiro do que falta para que alguém dependa dela em produção.

*Dossiê de posicionamento · pid-transit 0.7.0 · 8 de setembro de 2026*

| | |
|---|---|
| Código | 7 357 linhas em `src/` |
| Modelo | 20 entidades Pydantic, 11 modos de transporte |
| Cobertura | 17 ficheiros GTFS, NeTEx 1.3 nos dois sentidos |
| Testes | 318 (300 sem a extra `rt`) |
| CLI | 9 comandos |
| Licença | MIT |

---

## 1. O que já está construído

Não é um protótipo. O importador NeTEx corre o horário nacional suíço — 355 ficheiros,
~4 GB de XML — em cerca de 48 min com 1,25 GB de RSS de pico, produzindo 26,5 M de
passing times. O exportador passou de 2 771 erros de esquema CEN 1.3 para zero.

Isto é material de venda que ainda não está publicado em lado nenhum.

| Número | O que representa |
|---|---|
| 26,5 M | passing times importados do feed nacional suíço |
| 12× | redução de RSS depois do rework de streaming (1 040 MB → 87 MB) |
| 0 | erros de XSD NeTEx 1.3 na exportação |
| 163 k | linhas/s de escrita em bulk session |
| 5,2 GB | pico do exportador para 1,18 GB de saída — **a dívida em aberto** |

---

## 2. Onde isto se usa

A biblioteca não se vende a "programadores de transportes" em abstrato. Vende-se a cinco
compradores com problemas diferentes, e cada um reconhece um subconjunto distinto destas
capacidades. Cada entrada indica o módulo que a sustenta e se funciona hoje.

### 2.1 Autoridades e operadores

*AMP, CP, STCP, Carris, Entur, SBB…*

**Conformidade com o Ponto de Acesso Nacional** — Funciona hoje · `import -f gtfs → export -f netex`
O operador produz GTFS; o regulador exige NeTEx no NAP (Regulamento Delegado UE 2017/1926).
Hoje isso resolve-se com consultoria ad hoc ou ferramentas Java. Aqui é um pipeline
reprodutível em dois comandos.

**Caminho inverso: NAP → mapas de consumo** — Funciona hoje · `netex_importer → gtfs_exporter`
Uma autoridade que só publica NeTEx é invisível no Google Maps, na Apple e na Transit.
Converter o feed nacional para GTFS é o passo que a torna visível — e é a direção que quase
nenhuma ferramenta faz bem.

**Consolidação metropolitana de vários operadores** — Funciona hoje · `merge --namespace --dedupe-stops --distance 50`
Uma área metropolitana recebe feeds de N operadores com IDs em conflito e paragens duplicadas
a 20 m de distância. O merge faz namespacing dos IDs e deduplica por distância Haversine.

**Portão de qualidade antes da publicação** — Funciona hoje · `validate --min-severity error --json`
Validação semântica em CI que bloqueia o release quando há erros: ordem dos horários de
passagem, integridade referencial, velocidades implícitas impossíveis por modo (V16),
paragens duplicadas a menos de 10 m (V18).

**Fiscalização de contrato de concessão** — Funciona hoje · `statistics.headways()` · `vehicle_hours()`
O contrato diz "headway máximo de 12 min no pico, 18 horas de amplitude". As estatísticas
respondem com números defensáveis por linha e por tipo de dia — veículos-hora, amplitude de
serviço, headway médio de pico e fora de pico.

**Autoria de horários para operadores pequenos** — Funciona hoje · `adapters.spreadsheet_importer`
Um operador rural com 12 autocarros planeia em Excel e nunca vai comprar Trapeze ou Optibus.
O importador de folha de cálculo transforma esse ficheiro em GTFS e NeTEx válidos. Segmento
genuinamente mal servido.

**Higiene de GTFS-Realtime** — Funciona hoje · `rt-snapshot --db --min-match-rate 0.95`
A falha número um em RT de produção: o feed em tempo real refere `trip_id`s que já não
existem no estático. O snapshot cruza os dois e devolve taxa de correspondência, com limiar
que faz falhar o build.

**Auditoria de acessibilidade** — *Parcial* · modelado, falta o relatório dedicado
Inventário do que está por preencher: `wheelchair_boarding`, pathways, levels, transfers.
Um relatório de conformidade que hoje se faz à mão em folhas de cálculo.

**Arquivo histórico consultável** — Funciona hoje · SQLite + `diff`
Uma `.db` por versão de feed transforma um arquivo de zips opacos numa série temporal em
SQL: como evoluiu a oferta em cinco anos, quando desapareceu uma linha, que paragens
mudaram de sítio.

### 2.2 Consultoria e planeamento

*gabinetes de mobilidade, estudos de rede*

**Antes e depois de uma alteração de rede** — Funciona hoje · `diff base.db target.db -o relatorio.md`
O diff entre duas bases responde ao que o cliente pergunta sempre: o que mudou exatamente, e
quanta oferta foi acrescentada ou retirada. Saída em Markdown para colar no relatório, ou
JSON para tratar.

**Due diligence sobre o incumbente num concurso** — Funciona hoje · `stats --json` + SQL direto
Antes de apresentar proposta, quantificar o serviço que o operador atual presta a partir do
feed público — sem depender dos números que ele declara.

**Comparação de cenários de redesenho** — *Parcial* · falta `to_geojson()` / `to_dataframe()`
Cada cenário é uma base independente, e a comparação passa a ser sistemática em vez de
narrativa. O que falta aqui é exportar para GeoPandas, para o mapa que o cliente quer ver.

### 2.3 Equipas de produto e MaaS

*journey planners, apps, integradores*

**Camada de normalização antes do motor de rotas** — Funciona hoje · `normalize --patterns` · `validate`
O OpenTripPlanner lê GTFS e NeTEx, mas cada fonte chega com defeitos diferentes. Normalizar
tudo para um modelo único, validar, e só depois alimentar o motor — em vez de descobrir o
problema já dentro do grafo.

**SQLite como artefacto de deployment** — Funciona hoje · `TransmodelDatabase` · WAL
Um ficheiro por versão de feed: imutável, versionável, replicável, sem servidor de base de
dados para manter. É o modelo de distribuição que torna um feed operável, e não apenas
transferível.

**Backend de API de horários** — *Falta* · ver Nível 2, proposta `pid-transit-server`
Os repositórios já dão consultas filtradas, ordenação e contagem sobre tabelas indexadas.
Falta a camada HTTP — que deve ser um pacote separado, para não engordar as dependências do
núcleo.

**Testes de regressão sobre dados de terceiros** — Funciona hoje · `diff` + `rt-check`
Um produto que consome feeds alheios quebra quando o fornecedor muda alguma coisa em
silêncio. O diff em CI, corrido a cada atualização, avisa antes dos utilizadores.

**Preparação de dados para simulação e otimização** — Funciona hoje · `frequencies --expand` / `--compress`
Modelos de simulação querem viagens materializadas, não headways. A expansão de frequências
gera as viagens; a compressão inversa encolhe o feed antes de publicar.

### 2.4 Investigação e ensino

*universidades, teses, laboratórios de mobilidade*

**Análise reprodutível com proveniência** — Funciona hoje · `research/benchmark`
O caso que já conheces em primeira mão. A base passa a ser o anexo do artigo: quem quiser
reproduzir corre o mesmo comando sobre o mesmo ficheiro.

**Métricas de acessibilidade e equidade** — *Parcial* · `stop_coverage()`, falta GeoJSON
Cobertura de paragens por raio, cruzada com headways e amplitude de serviço, é a base de
qualquer estudo de equidade de acesso. Falta o passo de juntar dados de população, que pede
saída geoespacial.

**Ensinar o modelo Transmodel** — Funciona hoje · `core/schemas.py` · `core-entity-model.html`
Transmodel aprende-se normalmente a ler PDFs do CEN. Um esquema relacional navegável, com
entidades tipadas e um feed real lá dentro, é um recurso didático que praticamente não existe.

### 2.5 Cívico e dados abertos

*municípios, portais de dados, jornalismo*

**O primeiro GTFS de uma cidade pequena** — Funciona hoje · `import -f spreadsheet → export -f gtfs`
Do Excel do município a um feed válido, sem contratar ninguém. É o caminho de entrada mais
provável para novos utilizadores — e o que gera as histórias que fazem a biblioteca circular.

**Validação num portal de dados abertos** — Funciona hoje · `dataset.validate()`
Um portal que aceita feeds de terceiros precisa de rejeitar lixo à entrada. Validar em
processo, sem JVM, é bastante mais fácil de integrar do que a alternativa Java.

---

## 3. A lacuna de cobertura

Esta tabela é ao mesmo tempo a página de documentação mais útil que podes publicar e a lista
de trabalho por fazer. Publicá-la tal como está, com as ausências à vista, ganha credibilidade
junto de quem avalia; escondê-la perde negócios mais tarde, já na integração.

| Formato | Importa | Exporta | Estado e consequência de mercado |
|---|---|---|---|
| GTFS estático | sim | sim | 17 ficheiros, incluindo pathways, levels, translations e attributions. Cobertura acima da média das bibliotecas Python. |
| GTFS Fares v1 | sim | sim | `fare_attributes` e `fare_rules`. É o modelo antigo, em desuso. |
| GTFS Fares v2 | **não** | **não** | Sem `fare_products`, `fare_leg_rules`, `areas`, `networks`, `timeframes`. É para aqui que o mercado está a migrar — a lacuna mais cara. |
| GTFS-Flex | **não** | **não** | Sem `locations.geojson` nem `booking_rules`. Fecha a porta ao transporte a pedido e ao rural, que é onde há financiamento. |
| GTFS-Realtime | sim | **não** | Snapshot e correspondência com o estático. Só leitura — não produz feeds RT. |
| SIRI (ET / VM / SX) | **não** | **não** | É o par em tempo real do NeTEx na Europa. Sem ele, um cliente europeu tem de juntar duas ferramentas — e escolhe a que faz as duas. |
| NeTEx (XSD 1.3) | sim | sim | Zero erros de esquema e round-trip sem perda. O ativo técnico mais forte do projeto. |
| Perfis NeTEx | parcial | **não** | Importa dialetos reais (SKI suíço, `AvailabilityCondition`, `calls/Call`) mas o argumento `profile` do exportador está inerte. Válido no XSD não significa aceite pelo NAP. |
| Folha de cálculo | sim | **não** | Excel e pastas de CSV. Falta o retorno: exportar para Excel é o que os planeadores pedem logo a seguir. |
| GeoJSON / DataFrame | **não** | **não** | Custo de implementação baixo, retorno de adoção alto. É a ponte para quem já vive em pandas e QGIS. |

---

## 4. O que falta para vender

Os níveis estão numerados porque a ordem importa mesmo: construir capacidades novas (Nível 1)
antes de resolver a credibilidade (Nível 0) produz uma biblioteca poderosa que ninguém
experimenta, porque o avaliador desiste na página do GitHub antes de chegar ao código.

### Nível 0 — Credibilidade

*semanas · sem funcionalidade nova*

**Não há CI, CHANGELOG, CONTRIBUTING nem site de documentação** — baixo esforço, bloqueia tudo
Um comprador institucional avalia estes sinais *antes* de olhar para o código. Uma biblioteca
de dados de transportes sem CI visível não entra num pipeline público, por melhor que seja.
É a correção com melhor relação custo-benefício que tens em aberto.

**Sair do 0.x com uma promessa de estabilidade** — baixo esforço, alto sinal
A versão 0.7.0 com `Development Status :: 4 - Beta` comunica "não construas em cima de mim".
Fixar a superfície pública, escrever a política de depreciação e lançar 1.0 é sobretudo uma
decisão — o código já está mais estável do que a versão sugere.

**Publicar a prova de conformidade** — já escrito, por commitar
`NETEX_EXPORT.md` — 2 771 erros de esquema levados a zero, com a análise de por que razão a
suíte de testes não os via — é o melhor material de marketing do projeto e ainda está por
versionar. O mesmo para os números do feed suíço e para a passagem no gtfs-validator da
MobilityData. Ninguém acredita em "conforme"; toda a gente acredita numa contagem de erros
com o comando ao lado.

**O README subvaloriza o que existe** — corrigido a 2026-09-08
Anunciava 13 tipos de entidade quando o esquema tem 20, 152 testes quando são 318, 7 modos de
transporte quando são 11, e documentava uma flag de CLI (`--include-shapes`) que não existe.
Também apresentava `profile="EPIP"` como se funcionasse, quando está inerte. Tudo corrigido,
mais uma secção *Scale* com os números de desempenho, que não estavam em lado nenhum.

**O nome trabalha contra a descoberta** — decidir agora, caro depois
"PID" colide com *process ID* em qualquer contexto Python e, dentro do domínio dos
transportes, é a sigla da autoridade de Praga (Pražská integrovaná doprava) — o que confunde
e traz risco de marca. Além disso, quem procura no PyPI por "gtfs netex python" não chega a
`pid-transit`. Renomear com zero utilizadores custa uma tarde; com cem utilizadores é uma
migração. Se decidires manter o nome, alarga pelo menos os `keywords` e reescreve o
`description` em torno de "GTFS ⇄ NeTEx".

### Nível 1 — Lacunas que perdem negócios

*meses · por ordem de valor*

**GTFS-Fares v2 com ponte para o modelo tarifário NeTEx** — esforço alto, diferenciador
Ninguém faz bem a tradução entre Fares v2 e `Tariff` / `FareStructureElement` do NeTEx, e
toda a autoridade europeia que publica tarifas precisa dela. É a funcionalidade mais
defensável que podes construir: difícil o suficiente para ser barreira à entrada, procurada o
suficiente para justificar contactos comerciais.

**Perfis NeTEx na exportação** — esforço médio, abre a UE
XSD 1.3 válido não é o que o NAP aceita: cada país exige um perfil (o EPIP europeu e, depois,
FR, Nordic, SKI suíço, entre outros) com restrições e obrigatoriedades próprias. Um
`--profile epip` com validação de perfil transforma "converte para NeTEx" em "entrega no
NAP", que é a frase que liberta orçamento. Começa pelo EPIP e por um perfil nacional de que
já tenhas feed real. O parâmetro `profile` já existe na assinatura do exportador e está
inerte — é a tarefa 9 do roteiro.

**SIRI: fechar o par estático + tempo real** — esforço alto, completa a pilha
Tens NeTEx estático e GTFS-RT, mas falta o lado em tempo real do mundo europeu. Sem
SIRI-ET/VM/SX, o cliente europeu junta duas ferramentas — e da próxima vez escolhe a que faz
as duas coisas. A Fase 3 já está identificada no roteiro; o que falta é pô-la à frente de
trabalho mais confortável.

**Exportadores em streaming** — esforço médio, bloqueia deploy
5,2 GB de pico para produzir 1,18 GB de NeTEx significa uma instância de 8 GB para converter
um ficheiro, quando o importador já faz o percurso inverso a ~1,17 GB constantes. Enquanto a
assimetria existir, a biblioteca não entra em função serverless nem em contentor pequeno, que
é como estas conversões correm em produção. É a dívida técnica mais cara em aberto, e já está
identificada no `CLAUDE.md`.

**GTFS-Flex para transporte a pedido** — esforço médio, segmento em crescimento
`locations.geojson` e `booking_rules` são a porta para o rural e o a-pedido — precisamente
onde há programas de financiamento público ativos e poucas ferramentas maduras.

### Nível 2 — Ergonomia de adoção

*esforço baixo por item · efeito composto*

**`to_dataframe()`, `to_geojson()`, Parquet** — dias, maior retorno
A maior comunidade de utilizadores de dados de transportes vive em pandas, GeoPandas, QGIS e
DuckDB. Hoje, para essas pessoas, a biblioteca é um beco sem saída. Como extras opcionais
(`pip install pid-transit[geo]`) mantém-se a regra de dependências magras do núcleo e ganha-se
a comunidade inteira. Se só fizeres uma coisa deste nível, é esta.

**GitHub Action e imagem Docker** — dias, canal de distribuição
Uma Action de marketplace que faz falhar o PR quando o feed regride é o canal mais barato que
existe para chegar a agências de transportes: instala-se em cinco minutos, sem processo de
compras. A imagem Docker cobre quem não tem Python no pipeline.

**`pid-transit-server` como pacote separado** — semanas, biblioteca → produto
Uma API HTTP só de leitura sobre a base, com OpenAPI. Separado por duas razões: mantém o
núcleo com apenas `pydantic` e `openpyxl`, e é o artefacto demonstrável que se mostra numa
reunião. As chamadas `on_progress` e `cancel_event` que já existem nos importadores servem
diretamente uma interface web com SSE.

**Exportação para folha de cálculo** — dias
Já importas de Excel; o retorno fecha o ciclo de trabalho real do planeador, que é editar
horários fora do sistema e voltar a carregá-los. O `openpyxl` já é dependência.

### Nível 3 — Modelo de sustentação

*quando houver utilizadores a sério*

**Núcleo aberto, serviços pagos** — decisão de negócio
O padrão que funciona neste setor: MIT no núcleo, que é o que faz uma agência experimentar
sem processo de compras, e receita em validação alojada, packs de perfis nacionais mantidos,
suporte com SLA e consultoria de migração para o NAP. As autoridades pagam por certeza de
conformidade e por alguém a quem telefonar, não por bytes de software.

**Publicar o benchmark comparativo** — números já existem
Os resultados já estão em `research/benchmark/`. Uma comparação pública e reprodutível contra
as alternativas Java e Go, com o método à vista, é o tipo de conteúdo que circula sozinho na
comunidade de dados de transportes — e a via mais rápida para os primeiros utilizadores
institucionais.

---

## 5. O nicho que está realmente vazio

> Python, conversão GTFS ⇄ NeTEx nos dois sentidos, memória baixa em feeds nacionais, com um
> armazenamento relacional consultável e licença MIT. Nenhuma outra ferramenta ocupa as cinco
> posições ao mesmo tempo.

| Alternativa | Linguagem | GTFS | NeTEx | O que a distingue — e o que deixa de fora |
|---|---|---|---|---|
| **pid-transit** | Python | ida e volta | ida e volta | Modelo Transmodel em SQLite, validação, estatísticas, merge, diff. Falta-lhe tarifas v2, perfis e SIRI. |
| Entur netex-gtfs-converter | Java | saída | entrada | Conversão de qualidade e apoio institucional norueguês, mas assimétrica e presa à JVM. |
| transitland-lib | Go | ida e volta | não | Muito sólido em GTFS e base de dados, sem o mundo europeu do NeTEx. |
| gtfs-kit · partridge | Python | leitura | não | Excelentes para análise em pandas; não são modelo de dados nem escrevem NeTEx. |
| gtfs-validator (MobilityData) | Java | valida | não | A referência de validação — e o padrão contra o qual te deves medir, não competir. |
| OpenTripPlanner 2 | Java | leitura | leitura | Lê ambos para calcular rotas, mas não é ferramenta de dados nem exporta. |

**Panorama a confirmar antes de entrar numa proposta comercial:** estes projetos mexem-se, e
uma afirmação comparativa desatualizada custa mais credibilidade do que ganha.

---

## 6. Se só fizeres três coisas

**01 · Publicar a prova que já tens.**
CI, CHANGELOG, site de documentação, `NETEX_EXPORT.md` versionado e os números do feed suíço
em destaque no README. Zero funcionalidade nova, e é o que separa "projeto pessoal" de
"biblioteca em que se pode confiar".

**02 · Acrescentar `to_dataframe()` e `to_geojson()`.**
Dias de trabalho, como extras opcionais, e a biblioteca deixa de ser um beco sem saída para a
maior comunidade de utilizadores de dados de transportes que existe.

**03 · Escolher uma aposta grande e ir até ao fim.**
Tarifas v2 com ponte para NeTEx, ou perfis de exportação para o NAP. Ambas são defensáveis;
fazer metade das duas não é. A escolha depende de a quem queres vender primeiro — a quem
publica tarifas, ou a quem tem um prazo regulatório em cima.

O padrão que atravessa este documento: o trabalho difícil já está feito e sub-comunicado,
enquanto o trabalho fácil de comunicação está por fazer. Corrigir essa inversão é mais barato
do que qualquer funcionalidade nova desta lista.

---

*Elaborado a partir da leitura do código em `src/pid_transit/` (0.7.0), do `CLAUDE.md`, do
`NETEX_EXPORT.md` e do `research/IMPROVEMENTS.md`. Os números de desempenho vêm dos benchmarks
já registados no repositório; as afirmações sobre ferramentas de terceiros e sobre requisitos
regulamentares devem ser reconfirmadas antes de irem para uma proposta.*
