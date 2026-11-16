# Acelerar o import NeTEx — diagnóstico medido e alternativas

O import do feed nacional suíço (`prod_netex.zip`, 355 ficheiros / 24,5 GB de XML)
leva **6 501 s (108 min)** e produz uma base de dados de 8,64 GB. Este documento
mede onde esse tempo é gasto e enumera as alternativas, ordenadas por retorno
sobre esforço.

*Baseline: `research/benchmark/METRICS.md`, commit `e49665c`. Medições novas nesta
sessão em `c21e048`, mesma máquina (Core Ultra 7 265H, 31,5 GB, NVMe, Win 11,
CPython 3.12.1).*

> **Aviso de método.** As medições novas usam um subset próprio — 5 ficheiros de
> referência (RESOURCE, SITE, SERVICE, SERVICECALENDAR, COMMON) + 1 TIMETABLE de
> 223 MB, 624 MB no total — que **não** é o subset de `METRICS.md` (630 MB, outro
> ficheiro TIMETABLE, 293 820 passing times contra 254 468 aqui). Os números novos
> são comparáveis entre si, não com a tabela de Julho. Onde a cache de páginas do
> SO influencia o resultado, isso está dito explicitamente.

## Resumo

O gargalo não é o parser. É o caminho de escrita para o SQLite, e dentro dele o
factor dominante é uma coisa que não estava a ser olhada: **as linhas chegam em
ordem aleatória de chave primária**, porque os ids do feed suíço são strings
arbitrárias.

| Achado | Medido |
|---|---|
| Ordem aleatória de PK vs. ordenada, na mesma escrita de 3,7 M linhas | **3,0× mais lenta** |
| Carregar sem índice e criá-lo no fim, com linhas aleatórias | **3,64×** mais rápido |
| `journal_mode=OFF` + `synchronous=OFF` + `foreign_keys=OFF` na fase de referência | **2,66×**, CPU 80 % → 95 % |
| `foreign_keys=OFF` isolado | 1,42× |
| Buffer de exceções de calendário → gerador | **1 011 MB → 19 MB** de RSS |
| Tuplos em vez de dicionários na construção de linhas | 6,5× |
| Trocar `xml.etree` por `lxml` | 1,18–1,21× |
| `cache_size = 512 MB` | **sem efeito**, +445 MB de RSS |
| `wal_autocheckpoint=0` + `mmap_size` (o conjunto "óbvio" de bulk-load) | **6,6× pior** |

Trocar de biblioteca de XML é a alternativa com pior relação ganho/risco da lista.
Ordenar uma lista de chaves antes de a expandir é a melhor.

---

## 1. Onde vai o tempo

O baseline de `METRICS.md` dá a pista mais importante e é fácil de ignorar:

| Feed | Wall | CPU | CPU/wall | Peak RSS | DB |
|---|---:|---:|---:|---:|---:|
| Subset (630 MB XML) | 251,4 s | 195,9 s | **78 %** | 1 171 MB | 516,6 MB |
| Feed completo (24,5 GB XML) | 6 501 s | 2 751 s | **42 %** | 1 171 MB | 8,64 GB |

No subset o processo é *CPU-bound*. No feed completo **58 % do wall é tempo fora
do CPU deste processo** — a base de dados de 8,6 GB mais o WAL deixam de caber na
cache de páginas do SO e cada escrita passa a tocar o disco. São dois problemas
diferentes e exigem correcções diferentes; medir um no fixture do outro é o erro
clássico aqui.

O peak RSS ser **exatamente o mesmo** (1 171 MB) nos dois feeds, com uma diferença
de 40× no volume de entrada, é a assinatura do desenho em streaming a funcionar —
e, como se vê em §3.5, também a assinatura de um único buffer que não faz
streaming e domina o número.

### 1.1 Divisão por fase (subset, medido nesta sessão)

Instrumentei as linhas de log do próprio importer (`NeTEx pass 1` / `NeTEx pass 2`)
para separar as fases. `_flush_reference_data` corre entre os dois passes e conta
para o pass 1.

| Fase | Wall | CPU | Peak RSS | DB |
|---|---:|---:|---:|---:|
| Subset completo (6 ficheiros, 624 MB) | 150,2 s | 125,6 s (84 %) | 1 219 MB | 515,7 MB |
| ├─ pass 1 + flush de referências | **133,7 s** | | | |
| └─ pass 2 (journeys) | 16,6 s | | | |
| Só os 5 ficheiros de referência (400 MB) | 182,6 s | 145,1 s (80 %) | **1 219 MB** | 434,6 MB |

Duas leituras diretas:

1. **Os journeys não são o problema no subset.** 254 468 passing times + 12 680
   journeys custam 16,6 s. Todo o resto — 400 MB de XML de referência e,
   sobretudo, as 3 717 297 linhas de `operating_day_exception` — custa ~134 s.
2. **O pico de RSS é fixado pelo calendário, não pelo tamanho do feed.** A
   corrida só com ficheiros de referência importa **zero journeys** e ainda assim
   chega aos mesmos 1 219 MB. O pico não vem do XML nem dos passing times.

> As duas corridas não são subtraíveis uma da outra: 182,6 s (referência) é
> *maior* que 150,2 s (subset completo) porque na primeira corrida os 624 MB de
> XML tinham acabado de ser extraídos e estavam quentes na cache do SO. Serve
> como ordem de grandeza da fase de referência, não como aritmética exata.

### 1.2 O modelo de custo do feed completo

Extrapolando o pass 2 do subset (16,6 s por 223 MB de TIMETABLE) para os 24,1 GB
de ficheiros TIMETABLE, e o pass 1 à taxa de parse medida (§2.1):

| Componente | Estimativa | % de 6 501 s |
|---|---:|---:|
| Parse + escrita de referências e calendário (constante) | ~180 s | 3 % |
| Pass 1: re-varrimento dos ficheiros TIMETABLE | ~630 s | 10 % |
| Pass 2: parse + construção de linhas | ~1 800 s | 28 % |
| **Sobrecarga do caminho de escrita SQLite** | **~3 900 s** | **~59 %** |

O último termo é uma **subtração**, não uma medição — mas tem um mecanismo
identificado e medido à parte (§3.1): na fase de referência, o journal WAL mais a
verificação de chaves estrangeiras respondem por **113,9 s de 182,6 s (62 %)** de
uma fase que ainda estava 80 % CPU-bound. No feed completo, onde o CPU cai para
42 % do wall, é o mesmo mecanismo a atuar sobre uma base 20× maior. É por isso que
§3 vem antes de §2 na ordem de trabalhos.

---

## 2. Alternativas no parse do XML

### 2.1 Trocar de biblioteca de parsing — ganho real, mas modesto

Medido num ficheiro TIMETABLE real do feed suíço (57,3 MB, 4 709 journeys), média
do melhor de 3 corridas. A coluna "pass 1" só inspeciona filhos diretos do
`ServiceJourney`; a "pass 2" percorre a subárvore completa (`calls/Call/Arrival/Time`),
que é o que o importer faz hoje.

| Estratégia | pass 1 | pass 2 | vs. atual |
|---|---:|---:|---:|
| Leitura crua do ficheiro (piso de I/O) | 0,05 s | — | — |
| `xml.parsers.expat` sem construir árvore (piso do parser) | 0,89 s | — | **1,6×** |
| **`ET.iterparse` start+end (ATUAL)** | **1,44 s** | **1,60 s** | 1,0× |
| `ET.iterparse` só end-events | 1,24 s | — | 1,16× |
| `lxml.etree.iterparse` start+end | — | 1,90 s | 0,84× (pior) |
| `lxml.etree.iterparse` só end + filtro `tag=` | 1,22 s | 1,32 s | **1,18–1,21×** |

**Conclusão honesta: lxml não é a bala de prata.** O ganho é 18–21 %, não 2–3×.
Duas razões:

- O parser de C do `xml.etree` já é o mesmo expat por baixo. A diferença está na
  construção de objetos `Element`, e aí o lxml não é dramaticamente melhor.
- O filtro `tag=` do lxml elimina a iteração Python sobre os ~250 M de elementos
  não interessantes (o `_stream` atual itera **duas vezes** por elemento, start e
  end) — mas os handlers do pass 2 continuam a percorrer a subárvore de cada
  journey em Python, e é isso que domina. `lxml.etree.iterparse` sem o filtro é
  **mais lento** que a stdlib.

Se ainda assim se quiser: entra como extra opcional, no padrão já usado pelo
`gtfs-realtime-bindings`, nunca como dependência de runtime.

```toml
[project.optional-dependencies]
fast = ["lxml>=5.0"]
```

…com detecção em `_stream` e fallback para a stdlib, guardada por um teste no
estilo de `tests/test_lazy_imports.py` que garanta que os dois caminhos produzem
as mesmas linhas. Custo: um segundo caminho de código no sítio mais delicado do
importer, por 20 %.

**Recomendação: não fazer isto primeiro.** É a alternativa com pior relação
ganho/risco da lista.

### 2.2 Não parsear os ficheiros TIMETABLE duas vezes — ~10 %

Esta é a mudança estrutural que o parse permite. Hoje:

- **Pass 1** lê os 355 ficheiros (24,5 GB) e, dos TIMETABLE, extrai apenas:
  contadores de `ProfileEvidence`, o par `jp_to_line`, e a marca
  `files_with_journeys`.
- **Pass 2** volta a ler os mesmos 24,1 GB para o trabalho a sério.

Ou seja, 24,1 GB são descomprimidos e parseados **duas vezes** para que a segunda
passagem saiba coisas que quase sempre não existem nos ficheiros TIMETABLE (as
entidades de referência vivem em COMMON/SERVICE/SITE).

Desenho proposto — *passagem fundida sobre os ficheiros de journeys*:

1. Classificar cada ficheiro sem o parsear: varrimento de bytes à procura de
   `<ServiceJourney` / `<TemplateServiceJourney` (a leitura crua corre a
   ~1,2 GB/s, §2.1), ou pelas declarações de frames no cabeçalho.
2. Ler primeiro **só** os ficheiros de referência, exatamente como o pass 1 hoje.
3. Ler os ficheiros de journeys **uma vez**, fazendo o varrimento leve e o
   trabalho pesado na mesma visita.
4. Journeys cuja linha não resolve nesse momento vão para um buffer de diferidos
   (id + refs, ~100 B cada) e são reprocessados no fim. No feed suíço esse buffer
   fica vazio: os journeys trazem `calls` e não dependem de patterns de ficheiros
   posteriores.
5. A detecção de perfil (`ProfileEvidence.detect`) passa a decidir sobre os
   ficheiros de referência mais uma **amostra limitada** dos primeiros journeys —
   as regras em `netex_profiles.py` são rácios, estabilizam em poucos milhares de
   journeys e não precisam de contar 1,66 M.

Ganho: ~630 s (~10 %) no feed completo, mais o `inflate` de 24 GB que deixa de se
repetir. Risco: o buffer de diferidos pode crescer se um produtor puser as
entidades de referência *depois* dos journeys — é preciso um limite com derrame
para uma tabela de staging, senão troca-se tempo por RAM.

### 2.3 Baixar o custo por elemento no pass 1 — barato, sem dependências

O pass 2 já foi optimizado (`_qtags`, um único varrimento sobre filhos diretos).
O pass 1 não: tem **46 chamadas `find()`** com `ElementPath` e dicionário de
namespaces nos handlers, e em dois sítios constrói um dicionário de namespaces
novo *por elemento*:

```
netex_importer.py:651   pos = link.find(".//g:posList", {**self._ns, "g": _GML_NS})
netex_importer.py:699   pos = proj.find(".//g:posList", {**self._ns, "g": _GML_NS})
```

`on_day_type` faz ~6 `find()` × 69 652 elementos; `on_quay`/`on_stop_place` fazem
2 + 3 (`_centroid_coords`) + 2 (`_access_int`) × ~134 k elementos;
`on_day_type_assignment` faz 4 por elemento. Aplicar ao pass 1 o mesmo tratamento
que o pass 2 já recebeu — pré-qualificar tags uma vez por namespace e varrer
filhos diretos — é uma alteração local, sem dependências novas e sem mudança de
comportamento. Os dois dicionários construídos por elemento devem ser içados para
fora do handler em qualquer caso.

---

## 3. Alternativas no caminho de escrita — o maior ganho medido

### 3.1 Os pragmas: 2,66× medido, e um caminho que piora 6,6×

Corri o importer sobre os 5 ficheiros de referência (400 MB, 3,86 M linhas
escritas, zero journeys) com três conjuntos de pragmas em `bulk_session`, cada um
em processo próprio, e instrumentei `TransmodelDatabase.upsert` por tabela.

| Variante | Pragmas | Wall | CPU | CPU/wall | Peak RSS | `operating_day_exception` |
|---|---|---:|---:|---:|---:|---:|
| **base** (atual) | `WAL`, `synchronous=NORMAL`, `foreign_keys=ON` | **182,6 s** | 145,1 s | 80 % | 1 219 MB | — |
| **cache** | base + `cache_size=512 MB` | 171,1 s | 135,8 s | 79 % | 1 664 MB | 93,2 s / **39,9 k linhas/s** |
| **nowal** | `journal_mode=OFF`, `synchronous=OFF`, `foreign_keys=OFF`, `cache_size=512 MB`, `temp_store=MEMORY` | **68,7 s** | 65,6 s | **95 %** | 1 665 MB | 23,0 s / **161,5 k linhas/s** |

**2,66× no wall, e o processo deixa de ser I/O-bound (80 % → 95 % de CPU).** A
tabela grande passa de 39,9 k para 161,5 k linhas/s — 4,0×. Todas as outras
tabelas ganham na mesma proporção (`day_type` 42,6 → 114,3 k, `scheduled_stop_point`
66,0 → 169,5 k), o que confirma que o custo é do caminho de escrita e não da forma
de nenhuma tabela em particular.

Dois resultados negativos que valem tanto como o positivo:

- **`cache_size=512 MB` não faz nada** (182,6 → 171,1 s, dentro da variância) e
  **custa +445 MB de peak RSS**. Numa biblioteca cujo argumento é RSS plano, é um
  mau negócio. Não incluir.
- **O conjunto "óbvio" de bulk-load piora 6,6×.** Com
  `wal_autocheckpoint=0` + `mmap_size=1 GB` + WAL, o subset completo passou de
  **150,2 s para 993,3 s**, com CPU a cair para 10 % do wall. Razão: sem
  checkpoint o WAL cresce até ao tamanho de todo o conjunto de escritas (medi
  495 MB) e cada `INSERT OR REPLACE` — que sonda a chave antes de escrever — passa
  a ter de procurar páginas dentro desse WAL cada vez maior. É a patologia
  clássica do "nunca fazer checkpoint", e desaparece por completo quando não há
  WAL nenhum.

Descascando o caminho de escrita camada a camada, com a tabela real
(`operating_day_exception`, 3 717 297 linhas sobre 69 652 day types) num benchmark
isolado. **Estas camadas foram medidas com as linhas em ordem de chave primária**
— ou seja, são os fatores que se obtêm *depois* de corrigir §3.2, e é por isso que
a linha A já parte de 159,6 k linhas/s em vez das 39,9 k do import real:

| Camada | Wall | Taxa | vs. anterior |
|---|---:|---:|---:|
| A dicionários → `db.upsert`, FK on, `OR REPLACE`, WAL (caminho atual) | 23,3 s | 159,6 k/s | — |
| B tuplos → `executemany` direto | 18,9 s | 196,6 k/s | 1,23× |
| C  + `foreign_keys=OFF` (+ `foreign_key_check` no fim) | 13,3 s | 279,0 k/s | **1,42×** |
| D  + `INSERT` simples em vez de `OR REPLACE` | 13,9 s | 268,2 k/s | 0,96× (pior) |
| E  + sem PK durante a carga, índice único no fim | 15,3 s | 243,3 k/s | 0,91× (pior) |
| F  + `journal_mode=OFF` | 11,5 s | 322,7 k/s | **1,21×** |
| G  + uma só transação (sem commit por lote) | 11,2 s | 330,9 k/s | 1,03× |

Os dois maiores fatores isolados são **`foreign_keys=OFF` (1,42×)** e
**`journal_mode=OFF` (1,21×)**, nessa ordem — o que é a notícia boa, porque a FK
é o pragma com o perfil de risco mais benigno dos três (§3.3). Trocar o statement
(D) e adiar a chave primária (E) *pioram* — mas ver §3.2, porque a razão pela qual
(E) piora aqui deixa de valer no feed real.

### 3.2 A ordem de inserção: 3,0× medido, e ninguém está a olhar para isto

O benchmark isolado dava 159,6 k linhas/s para o caminho atual; o import real dá
**39,9 k** com a mesma configuração. A discrepância de 4× tinha de ser explicada
antes de recomendar qualquer coisa, e a explicação não está nos pragmas.

`operating_day_exception` tem `PRIMARY KEY (day_type_id, date)`. Os ids de day
type do feed suíço são **strings aleatórias de 5 caracteres**:

```
ch:1:AvailabilityCondition:kb400
ch:1:AvailabilityCondition:ral00
ch:1:AvailabilityCondition:uq420      (69 634 destas, em ordem nenhuma)
```

`_flush_reference_data` expande-as na ordem em que apareceram no documento, ou
seja **em ordem aleatória de chave primária**. Cada insert cai numa página
aleatória de uma árvore B de 3,7 M entradas.

| Ordem das linhas | Cache | Wall | Taxa |
|---|---|---:|---:|
| ordenada por PK | 2 MB (default) | **19,7 s** | 188,3 k/s |
| ordenada por PK | 512 MB | 20,7 s | 179,8 k/s |
| **aleatória (como no feed real)** | 2 MB (default) | **59,4 s** | **62,5 k/s** |
| aleatória | 512 MB | 59,7 s | 62,3 k/s |

**3,0× só pela ordem de inserção**, e é isto que faltava para reconciliar os dois
números. (As 62,5 k contra as 39,9 k medidas *in situ* fecham com a conversão
dict→tuplo de §3.4, ~7 s, mais as outras tabelas a partilhar a ligação.)

**O mesmo problema atinge `passing_time`, que é 26,5 M linhas.** A sua PK é
`(service_journey_id, "order")`, e os ids de journey do feed suíço chegam em ordem
igualmente arbitrária — numa amostra de 2 000 ids consecutivos de um ficheiro
TIMETABLE, **51 % são descidas** face ao anterior:

```
ch:1:ServiceJourney:ch:1:sjyid:100640:plan:020580507115049_92507A.j26_3537
ch:1:ServiceJourney:ch:1:sjyid:100640:plan:020580507115191_92507A.j26_3538
ch:1:ServiceJourney:ch:1:sjyid:100640:plan:020580507115190_92507A.j26_3539
```

Se o fator de 3,0× se mantiver à escala das 26,5 M linhas — e à escala tende a
piorar, porque a árvore fica mais funda e a cache cobre uma fração menor dela —
esta é a maior alavanca única do import, maior do que todos os pragmas juntos. É
também a hipótese mais forte para o termo de 59 % de §1.2.

Para as exceções de calendário a correcção é trivial: **ordenar as chaves antes de
expandir**, o que é uma linha em `_flush_reference_data` e não custa memória (os
69 652 ids já estão todos em RAM; as 3,7 M linhas é que não precisam de estar).

Para `passing_time` a ordem tem de sair de um stream que não se pode materializar,
o que exclui simplesmente ordenar. Medi as três saídas, sempre com as 3,7 M linhas
em ordem de chave aleatória:

| Estratégia | Wall | Taxa | DB final | vs. atual |
|---|---:|---:|---:|---:|
| 1 ordem aleatória, PK presente (atual) | 63,3 s | 58,7 k/s | 426 MB | — |
| 2 ordenar em Python antes de escrever | 18,8 s | 197,4 k/s | 415 MB | 3,37× |
| **3 carregar sem índice, criar o índice no fim** | **17,4 s** | **213,2 k/s** | **389 MB** | **3,64×** |
| 4 tabela de staging + `INSERT…SELECT…ORDER BY` | 20,2 s | 183,9 k/s | 608 MB | 3,13× |

**A opção 3 ganha nas três dimensões** — é a mais rápida, produz a base mais
pequena, e é a única que não exige segurar o conjunto todo em memória, logo é a
única compatível com a regra "stream, don't materialize" do `CLAUDE.md`. A opção 4
paga uma base 56 % maior pela cópia de staging. A opção 2 obriga a materializar
tudo, exactamente o que §3.5 quer eliminar.

Note-se que é a **mesma** estratégia que a camada (E) de §3.1 mediu como *pior*
(0,91×) — porque lá as linhas chegavam já ordenadas, e a manutenção incremental do
índice era barata. Com linhas aleatórias o sinal inverte-se por completo. É o
argumento mais forte deste documento a favor de medir no feed real e não no
fixture: a mesma alteração vale 0,91× ou 3,64× dependendo de um facto sobre os
dados que nenhum teste da suite observa.

Implementá-la exige um cuidado no `bulk_session`: hoje ele já faz *load-then-index*
para os **índices secundários** (`_INDEX_SCHEMAS`), mas não para os autoindexes que
o SQLite cria por causa das cláusulas `PRIMARY KEY` — e é nesses que estão as
26,5 M inserções aleatórias. Tirá-los da carga significa declarar as tabelas
grandes sem `PRIMARY KEY` no `CREATE TABLE` e criar um `UNIQUE INDEX` equivalente
em `_INDEX_SCHEMAS`. É uma migração de esquema, com o que isso implica para as
bases 3.2 já existentes.

### 3.3 O que isto custa em garantias — e como pagar só o necessário

Os três pragmas do `nowal` têm perfis de risco muito diferentes e **não devem ser
tratados como um bloco**:

| Pragma | O que se perde | Quando é aceitável |
|---|---|---|
| `foreign_keys=OFF` | Violações deixam de falhar o lote; passam a aparecer no fim | **Sempre**, desde que se corra `PRAGMA foreign_key_check` antes do commit final e se aborte se houver linhas. O importer já filtra proativamente (journeys sem linha resolúvel, calls com stops desconhecidos), pelo que a FK é hoje uma rede de segurança, não o mecanismo. |
| `journal_mode=OFF` | Não há rollback; uma falha a meio deixa a base corrompida | **Só quando o destino é uma base nova**. Um import interrompido reinicia-se; uma base existente que se corrompe não. |
| `synchronous=OFF` | Uma falha de energia pode corromper | Mesma condição. É o pragma com menos ganho isolado e mais risco — ver §3.1. |

Desenho recomendado: `bulk_session(fresh=...)` decide o modo em vez de o impor.

```python
@contextmanager
def bulk_session(self, *, unsafe_bulk_load: bool = False):
    # unsafe_bulk_load só é legítimo quando a base é nova: o rollback
    # deixa de existir, e um import interrompido tem de ser recomeçado.
    ...
```

`ImportCancelledError` e o `cancel_event` deixam de poder prometer rollback nesse
modo — é uma mudança de contrato visível, tem de ficar no docstring e num teste.

### 3.4 Construir tuplos em vez de dicionários — 6,5× na construção de linhas

`_Pass2.on_service_journey` acumula um dicionário de 5 chaves por passing time, e
`TransmodelDatabase.upsert(validate=False)` volta a lê-lo com um `.get` por coluna:

```python
rows = [tuple(raw.get(c, defaults[c]) for c in cols) for raw in records]
```

Para `passing_time` são 11 colunas × 26,5 M linhas ≈ 290 M lookups de dicionário,
mais 26,5 M dicionários alocados e destruídos.

| Caminho | 3 M linhas | Taxa |
|---|---:|---:|
| dict → tuplo via `.get` (atual) | 5,75 s | 0,52 M linhas/s |
| tuplo acrescentado diretamente | **0,89 s** | **3,36 M linhas/s** |

**6,5×** na construção de linhas, ~1,6 µs por linha. Nos ~32 M de linhas do feed
completo são ~52 s — pouco face a 6 501 s, mas é quase de graça e a proporção muda
depois de §3.1: das 23,0 s de `upsert` na variante `nowal`, ~7 s (30 %) são esta
conversão. Precisa de um caminho novo — `db.insert_rows(table, cols, tuples)` —
com os adapters a emitirem tuplos na ordem das colunas; o caminho de dicionários
fica para quem chama de fora.

### 3.5 Escoar o buffer de calendário — ~1 GB de RSS

`_flush_reference_data` materializa **todas** as `operating_day_exception` numa
lista Python antes de a primeira chegar ao SQLite (`netex_importer.py:818-870`).
No feed suíço são 3 717 297 dicionários. Reproduzido isoladamente, sem mais nada
no processo:

| Forma | Peak RSS |
|---|---:|
| Lista de dicionários (atual) | **1 011 MB** |
| Idem, reutilizando as strings de data | 772 MB |
| Gerador escoado em lotes de 5 000 | **19 MB** |

Isto explica todo o pico de 1 219 MB do import: ~1 011 MB são esta lista. A
corrida só com ficheiros de referência — **zero journeys importados** — chega ao
mesmo pico, o que fecha a questão.

A correcção é local: `_bits_to_exceptions` passa a ser um gerador, e o laço em
`_flush_reference_data` escoa lotes de `BATCH_SIZE` em vez de acumular. Há uma
ordem a respeitar: as `day_type` têm de estar escritas antes das exceções (FK), e
hoje o código escreve os day types *depois* de construir a lista de exceções
precisamente porque a lista existe. Com o gerador, os day types passam para antes.

Ganho de velocidade: indireto mas real no feed completo. Menos 1 GB de RSS é 1 GB
a mais de cache de páginas do SO disponível para uma base de 8,64 GB — que é
exactamente o recurso em falta no regime disk-bound de §1.

---

## 4. Paralelismo — a única via para mais de 3×

Com §2 e §3 aplicados o feed completo fica CPU-bound outra vez, e aí há 16 núcleos
parados. O import é estruturalmente paralelizável: 355 ficheiros independentes,
uma passagem de referência sequencial e barata, e o trabalho pesado todo em
ficheiros TIMETABLE que não se referenciam entre si.

Desenho: um processo leitor por ficheiro (`multiprocessing`), cada um a emitir
lotes de tuplos; um único escritor a consumir. Variante melhor no disco:

1. Passagem de referência sequencial → `reference.db`.
2. N workers, cada um com o seu `shard_k.db` (cópia das tabelas de referência,
   ou apenas as tabelas de journeys), a parsear e escrever em paralelo.
3. Merge sequencial por SQL: `ATTACH shard_k` + `INSERT INTO main.passing_time
   SELECT * FROM s.passing_time`. Sem Python no meio, e é o único momento em que
   se paga a manutenção de índices sobre o total.

Os ids são únicos por ficheiro no feed suíço, pelo que não há colisões de PK entre
shards. O merge de 26,5 M linhas em SQL puro deve correr bem acima das 300 k
linhas/s medidas para Python→SQLite, ou seja ~90 s.

Custo: é a alternativa mais invasiva da lista. `on_progress` e `cancel_event`
passam a ter de agregar N workers; erros de parse num worker têm de chegar ao
chamador com o ficheiro identificado; e o pico de RSS passa a ser N × o RSS de um
worker, o que só é aceitável depois de §3.5.

---

## 5. Alternativas que examinei e descartei

| Alternativa | Porquê não |
|---|---|
| `lxml` como dependência de runtime | 18–21 % (§2.1), e viola a regra de duas dependências. Só como extra opcional, e depois de tudo o resto. |
| `cache_size` grande | Medido: sem efeito, +445 MB de RSS (§3.1). |
| `wal_autocheckpoint=0`, `mmap_size` | Medido: **6,6× pior** (§3.1). |
| `INSERT` em vez de `INSERT OR REPLACE` | Medido: 0,96× (ligeiramente **pior**). E o import tem de poder correr sobre uma base já povoada, onde a semântica de replace é o comportamento pretendido. |
| Tabela de staging + `ORDER BY` para `passing_time` | 3,13× medido, mas produz uma base **56 % maior** (608 vs 389 MB) e é mais lenta que a alternativa sem índice (§3.2, opção 3). |
| `isal` / `zlib-ng` para o `inflate` do zip | O `inflate` de 24,5 GB é da ordem de 60–120 s em 6 501 s. Uma dependência C nova por ~1,5 %. Fica atrás de §2.2, que remove metade desse trabalho de graça. |
| Tabelas `WITHOUT ROWID` | Poupa a árvore duplicada em `passing_time` e `operating_day_exception`, mas com PKs `TEXT` longas o SQLite recomenda-o só para linhas pequenas. Merece medição antes de qualquer promessa. |
| `PRAGMA page_size = 8192` | No micro-benchmark de `passing_time` dava ~10 % (166,7 vs 149,8 k linhas/s), mas só se aplica a bases **novas** e não sobreviveu como fator isolado depois de `journal_mode=OFF`. Testar junto com §3.1, não antes. |

---

## 6. Ordem sugerida

| # | Alteração | Ganho medido | Onde | Esforço |
|---|---|---|---|---|
| 1 | Ordenar as chaves de day type antes de expandir as exceções | **3,4×** nessa tabela | `netex_importer.py:818-850` | 1 h |
| 2 | `foreign_keys=OFF` + `foreign_key_check` antes do commit | **1,42×** no caminho de escrita | `database.py` `bulk_session` | horas |
| 3 | Escoar as exceções de calendário por lotes | **−1 GB** de peak RSS | `netex_importer.py:818-870` | horas |
| 4 | `journal_mode=OFF` para base nova, atrás de flag | **1,21×** | `database.py` `bulk_session` | horas |
| 5 | Tuplos em vez de dicionários no caminho de escrita | **1,23×** no `executemany`, 6,5× na construção | `database.py` + adapters | 1 dia |
| 6 | `passing_time`/`point_in_journey_pattern` sem PK durante a carga, `UNIQUE INDEX` no fim | **3,64×** na tabela medida | `database.py` `_TABLE_SCHEMAS` + migração | 2–3 dias |
| 7 | Pré-qualificar tags no pass 1 (como o pass 2 já faz) | não medido isoladamente | `netex_importer.py:440-790` | 1 dia |
| 8 | Uma só passagem sobre os ficheiros TIMETABLE | ~10 % do feed completo | `netex_importer.py` `import_to_db` | 2–3 dias |
| 9 | Parse paralelo + shards + merge SQL | a via para >3× | novo módulo | ~1 semana |
| 10 | `lxml` como extra opcional | 18–21 % do parse | `_stream` | 1 dia |

**1 a 5 são independentes entre si, cabem numa tarde, e não mudam a arquitetura
nem o esquema.** A 1 é literalmente ordenar uma lista de chaves e vale 3,4× na
tabela mais pesada da fase de referência — é o melhor rácio da lista inteira.

A 6 é a de maior alavanca absoluta (afeta 26,5 M linhas) mas é a primeira que
mexe no esquema, e por isso vem depois. A 8 e a 9 mudam a arquitetura do importer.

Os fatores **não se multiplicam**: 1 e 6 atacam o mesmo mecanismo em tabelas
diferentes, e 2/4/5 compõem-se parcialmente. A composição real só se sabe medindo.

---

## 7. O que falta validar

Tudo acima foi medido no subset e nas suas fases. **Nenhuma destas medições
observa o regime disk-bound** que consome ~59 % do feed completo (§1.2), porque
uma base de 389–608 MB cabe folgadamente na cache do SO — a de 8,64 GB não,
sobretudo com 24,5 GB de XML a passar pela mesma cache e a desalojar-lhe as
páginas.

Só uma coisa fecha esta questão: **re-correr o feed completo** (`prod_netex.zip`,
355 ficheiros, ~108 min de baseline) com as alterações 1–5 aplicadas, e reportar
wall, CPU, peak RSS e tamanho da base separadamente, contra
`research/benchmark/METRICS.md`. Duas corridas, ~3,5 h. É a única medição que
justifica pôr um número no README.

Duas hipóteses que essa corrida também testa:

- Se o que desaloja as páginas da base é o *streaming* dos 24,5 GB de XML pela
  cache do SO, então §2.2 (metade do XML lido) vale mais no feed completo do que
  os ~10 % que a extrapolação lhe dá, e um `cache_size` grande — inútil no subset
  (§3.1) — passa a proteger as páginas de índice dentro do processo.
- O fator de inserção aleatória (§3.2) foi medido numa árvore de 3,7 M entradas.
  Numa de 26,5 M a árvore é mais funda e a fração coberta pela cache é menor, pelo
  que o fator deve ser **maior** que 3,6× — mas isso é uma expectativa, não uma
  medição, e é exactamente o tipo de expectativa que este documento já viu
  inverter-se uma vez (§3.2, camada E).

---

## Scripts

Os harnesses desta análise estão em **`research/benchmark/import_perf/`**
(gitignored, como todo o `research/`). Correr a partir da raiz do repositório, com
`.venv/Scripts/python.exe`; `mksubset.py` extrai primeiro o subset de
`research/feeds/prod_netex.zip`.

| Ficheiro | Mede |
|---|---|
| `importbench.py` | Import real, wall/CPU/RSS, split pass 1/pass 2, `upsert` por tabela, conjuntos de pragmas alternativos |
| `parsebench2.py` | Estratégias de parse na forma do pass 1 e do pass 2 |
| `odebench.py` | Caminho de escrita de `operating_day_exception`, camada a camada |
| `odebench2.py` | Ordem de inserção vs. `cache_size`, isolados |
| `odebench3.py` | As quatro estratégias para inserção em ordem aleatória de PK |
| `rowbench.py` | dict → tuplo vs tuplo direto |
| `membench.py` | Peak RSS do buffer de exceções, três formas |
| `dbbench.py` / `scalebench.py` | Caminho de escrita de `passing_time` |
| `mksubset.py` | Extrai o subset de 6 ficheiros de `prod_netex.zip` |

`importbench.py` é o único que precisa de manutenção para §7: aceita
`base` / `cache` / `nowal` como primeiro argumento e o conjunto de pragmas está
num dicionário no topo, pelo que acrescentar a variante que valida as alterações
1–5 é editar esse dicionário.

