# Roteiro — vídeo explicativo do PID-Transit

Vídeo de demonstração da biblioteca `pid-transit`, para publicar no repositório, no
PyPI e para enviar a autoridades e operadores.

*pid-transit 0.7.0 · roteiro escrito a 24 de outubro de 2026*

**Todos os comandos deste roteiro foram corridos antes de ele ser escrito.** Os tempos
e as saídas que aparecem em cada cena são reais, medidos nesta máquina com o feed de
demonstração da secção 2. Nada aqui é uma estimativa do que o terminal *deve* mostrar.

---

## 1. Decisões de formato

**Recomendação: um vídeo principal de ~9 minutos, e três cortes curtos tirados dele.**

Não gravar quatro vídeos. Gravar um, com cenas separadas e tomadas limpas, e cortar o
resto na edição — é o mesmo trabalho de gravação e dá quatro peças.

| Peça | Duração | Onde vive | Público |
|---|---|---|---|
| **Principal — demo guiada** | 9 min | README, PyPI, email a clientes | avaliador técnico: quem decide se adota |
| Corte A — *teaser* | 75 s | topo do README, LinkedIn | quem decide em 90 s se vê os 9 min |
| Corte B — perfis nacionais | 2 min | enviado a NAPs e ao IMT | autoridade que já tem NeTEx e desconfia dele |
| Corte C — prova e escala | 2 min | anexo a propostas | quem pergunta "isto aguenta o nosso feed?" |

**Língua:** locução em português europeu, legendas em inglês. O comprador imediato é
ibérico; o público do PyPI é internacional. Os slides já estão em português — manter.

**Tom:** demonstração, não anúncio. A biblioteca tem uma dívida conhecida (os 5,2 GB de
pico na exportação) e o argumento de venda inteiro do dossiê é publicar as ausências.
Um vídeo que as esconda contradiz o material escrito. A cena 8 diz o que falta, em voz
alta.

**O que o vídeo *não* é:** um tutorial de GTFS nem uma aula de Transmodel. Assume-se que
quem vê sabe o que é um feed. Se for preciso explicar o que é um `service_journey`,
perdeu-se o tempo de quem decide.

---

## 2. Antes de gravar — preparação verificada

### 2.1 Ambiente

```bash
# 1. UTF-8 no terminal, obrigatório.
#    Sem isto, o `stats` imprime "S?BADOS" e o vídeo mostra um bug que não existe.
export PYTHONUTF8=1          # PowerShell: $env:PYTHONUTF8 = "1"

# 2. Instalar a partir do PyPI, não do repositório — o espectador vai fazer o mesmo.
python -m venv .venv-demo
.venv-demo/Scripts/pip install "pid-transit[rt]"
```

Gravar o `pip install` **é** uma cena (cena 3): são poucos segundos, duas dependências,
e mostra que não há JVM nem servidor. Vale o tempo que ocupa.

### 2.2 Ficheiros de demonstração

O feed completo do Porto demora 41 s a converter. Isso não se grava ao vivo. A demo
corre sobre um corte de 4 linhas do mesmo feed, que faz tudo em 1 a 4 segundos.

```bash
# Gera o feed reduzido: 4 linhas, 1 026 viagens, 27 035 passing times, 265 KB
python research/scripts/make_demo_feed.py \
       "research/feeds/gtfs_feed (1).zip" demo/porto_demo.zip
```

Preparar na pasta de gravação, **antes** de gravar:

| Ficheiro | Como se obtém | Para que cena |
|---|---|---|
| `porto_demo.zip` | o script acima | 4, 5, 6 |
| `porto_full.db` | `pid-transit import` do feed completo (12,9 s) | 5 — é aqui que há avisos reais |
| `netex_1.xml` | extrair `research/feeds/nap_pt/netex_carris.zip` | 1 e 6 — 173,6 MB, o caso Carris |
| `demo.py` | apêndice B deste roteiro | 4b |

Apagar `porto.db`, `porto.xml` e `porto_pt.xml` antes de cada tomada: a demo tem de
criar os ficheiros em câmara, não encontrá-los já feitos.

### 2.3 Três armadilhas encontradas a preparar este roteiro

1. **`netex-check` não aceita `.zip`.** A ajuda do comando diz "Path to the NeTEx .xml
   (or .zip)", mas `check_netex` passa o caminho direto ao `iterparse` e um zip rebenta
   com `ParseError: not well-formed (invalid token): line 1, column 2`. **Nas cenas 1 e
   6, usar o `.xml` extraído.** É um defeito real, não um problema de gravação —
   convém corrigi-lo antes de publicar o vídeo, porque alguém vai tentar.
2. **Sem `PYTHONUTF8=1`** os nomes dos tipos de dia saem corrompidos no Windows
   (`S?BADOS`).
3. **O `validate` do feed reduzido não tem nada a dizer** (`PASSED - no issues found`),
   porque as paragens duplicadas estão noutras linhas. A cena da qualidade corre sobre
   o `porto_full.db`, onde há 41 avisos verdadeiros.

### 2.4 Ecrã

- 1920×1080, 30 fps. Gravar o ecrã inteiro e enquadrar na edição.
- Terminal com tipo de letra a **18–20 pt** e tema claro. O vídeo vai ser visto num
  telefone; a 14 pt não se lê nada.
- Janela a ~100 colunas. Mais do que isso e as tabelas do `netex-check` quebram.
- **Prompt limpo:** `PS1='$ '`. Um prompt com ramo de git e caminho longo rouba metade
  da linha.
- Esconder notificações, abas do browser e qualquer caminho com nomes de clientes.

---

## 3. Roteiro cena a cena

Cada cena indica o que está no ecrã, o que se diz, o que se escreve e o que aparece. A
locução está escrita para ser lida em voz alta, a ~145 palavras por minuto.

### Cena 1 — Gancho (0:00–0:40)

**Ecrã:** terminal, com o `netex_1.xml` já na pasta. Um único comando a correr.

> Este ficheiro tem 173 megabytes de NeTEx. Foi publicado pelo Instituto da Mobilidade
> e dos Transportes, é o horário da Carris, e é NeTEx válido.
>
> Lido por uma ferramenta genérica, a rede entra — as linhas, as paragens — e o horário
> desaparece. Sem erro. Sem aviso. Com um ficheiro à saída que parece bom.
>
> Dezoito segundos depois, esta biblioteca diz-te exatamente porquê.

**Comando:**

```bash
pid-transit netex-check netex_1.xml --profile pt --read
```

**Saída real** (18,0 s — acelerar 4× na edição, com o relógio visível):

```
| Rule                             | Layer       | Severity | Instances | Finding
| PT-ID-Call                       | profile     | error    |   296,603 | <Call> is a versioned object and must carry id and version; missing id, version
| PT-ID-ServiceJourney             | profile     | error    |    10,238 | <ServiceJourney> is a versioned object and must carry id and version; missing id, version
| PT-CHILD-ServiceJourney-dayTypes | profile     | error    |    10,238 | VehicleJourney/dayTypes is 1:* — the journey must name its DayType
| READ-profile                     | readability | info     |         1 | the importer read this file as the 'portuguese' dialect
```

**Notas de edição:** cortar para o cartão de título logo a seguir à última linha. Não
explicar nada aqui — a explicação é a cena 6. Esta cena só promete.

---

### Cena 2 — O problema (0:40–1:45)

**Ecrã:** slide `02 · Problema` (`research/slides/Problema.dc.html`).

> O Regulamento Delegado da União Europeia 2017/1926 obriga cada Estado-Membro a
> publicar os horários do transporte público no seu Ponto de Acesso Nacional. O formato
> que o Ponto de Acesso recebe é NeTEx.
>
> O operador, esse, produz GTFS. É o que as ferramentas de planeamento exportam e o que
> as equipas sabem manter.
>
> E o passageiro só vê GTFS: o Google Maps, a Apple e a Transit leem GTFS. Uma
> autoridade que cumpre a lei publicando apenas NeTEx fica invisível no telemóvel de
> quem apanha o autocarro.
>
> São dois formatos, uma obrigação legal, e nenhuma ponte que se possa repetir. Hoje
> essa ponte faz-se com consultoria *ad hoc* ou com ferramentas Java pesadas, uma vez
> por publicação. Ninguém reproduz a conversão na versão seguinte do feed, ninguém
> explica o que mudou entre duas versões, e o erro aparece depois de o ficheiro estar
> publicado.
>
> A conversão é tratada como um acontecimento. Devia ser um sistema.

**Notas de edição:** a última frase é o pivô do vídeo. Pausa de um segundo antes dela.

---

### Cena 3 — O que é (1:45–2:30)

**Ecrã:** slide `04 · Arquitetura`, e depois corte para o terminal com o `pip install`.

> O PID-Transit é uma biblioteca Python com um modelo canónico Transmodel ao centro e
> adaptadores à volta: GTFS, NeTEx, GTFS-Realtime, folha de cálculo. Nenhum vocabulário
> de formato entra no núcleo — acrescentar um formato é escrever um adaptador, não é
> mexer no modelo.
>
> A base de dados é SQLite. Um ficheiro por versão do feed: imutável, versionável,
> replicável. Sem servidor para manter.
>
> E instala-se assim.

**Comando:**

```bash
pip install "pid-transit[rt]"
```

> Duas dependências de runtime: Pydantic e openpyxl. Vinte e sete milissegundos a
> importar. Sem JVM, sem Docker, sem serviço.

**Notas de edição:** deixar o `pip install` à velocidade real se demorar menos de 10 s.
A rapidez é o argumento; acelerar estraga-o.

---

### Cena 4 — Demo: de GTFS a NeTEx (2:30–4:15)

**Ecrã:** terminal, pasta vazia tirando o `porto_demo.zip`.

> Vamos pegar num feed GTFS real — quatro linhas do Porto, mil e vinte e seis viagens —
> e levá-lo até NeTEx. São quatro comandos.

**Comando 1 — importar** (1,3 s):

```bash
pid-transit import porto_demo.zip --format gtfs --db porto.db --timezone Europe/Lisbon
```

```
Import complete:
  day_type: 3
  journey_pattern: 1,026
  line: 4
  operating_day_exception: 365
  passing_time: 27,035
  point_in_journey_pattern: 27,035
  scheduled_stop_point: 185
  service_journey: 1,026
  shape_point: 3,738
```

> O importador diz o que leu, entidade por entidade. E o que **não** conseguiu ler
> aparece aqui também, com um número ao lado — não só no log. Aqui não há nada, e
> portanto não há secção.

**Comando 2 — ver o que entrou** (0,7 s):

```bash
pid-transit stats porto.db
```

```
--- Dataset Summary ---
  lines: 4
  stops: 185
  service_journeys: 1,026
  passing_times: 27,035
  shapes: 14

--- Service Balance ---
  DIAS UTEIS: 482 journeys, 4 lines
  DOMINGOS|FERIADOS: 257 journeys, 4 lines
  SÁBADOS: 287 journeys, 4 lines
```

**Comando 3 — exportar NeTEx** (1,5 s):

```bash
pid-transit export --db porto.db -f netex -o porto.xml
```

```
Profile epip could not carry 27035 unsupported_field_passing_time_timepoint
Profile epip could not carry 1026 unsupported_field_service_journey_headsign
Profile epip could not carry 185 unsupported_field_scheduled_stop_point_zone_id
Exported to porto.xml
Not representable in this profile:
  unsupported_field_passing_time_timepoint: 27,035
  unsupported_field_service_journey_headsign: 1,026
  unsupported_field_scheduled_stop_point_zone_id: 185
  ...
```

> Repara no que ele faz antes de dizer que exportou: declara o que o perfil **não
> consegue** dizer. O `timepoint` do GTFS não tem equivalente em EPIP, e portanto ficam
> vinte e sete mil campos por escrever. Isso não é um aviso perdido num log — vem no
> valor de retorno, com contagem, campo a campo.
>
> Uma biblioteca que converte sem declarar perdas está a pedir-te para confiar nela.
> Esta diz-te do que é que estás a abdicar.

**Comando 4 — verificar o próprio ficheiro** (0,6 s):

```bash
pid-transit netex-check porto.xml --profile epip
```

```
# NeTEx check — porto.xml
Profile: **epip**

| Element               | Count  |
| JourneyPattern        |     14 |
| Line                  |      4 |
| ScheduledStopPoint    |    185 |
| ServiceJourney        |  1,026 |
| TimetabledPassingTime | 27,035 |

No findings.
```

> `No findings`. Mil e vinte e seis viagens de GTFS, em NeTEx, em menos de cinco
> segundos no total — e verificadas contra o perfil.

**Notas de edição:** não cortar entre comandos. A sequência corrida de ponta a ponta,
sem cortes, é a prova de que são mesmo 5 segundos. Com cortes, o espectador assume que
houve espera.

---

### Cena 4b — É uma biblioteca, não um script (4:15–4:45)

**Ecrã:** editor com `demo.py` (apêndice B), e a saída ao lado.

> O terminal é a parte visível. Por baixo está uma API tipada: vinte entidades
> Transmodel em Pydantic, com consultas filtradas.

```python
from pid_transit import TransitDataset

ds = TransitDataset("porto.db")

for linha in ds.lines.get_all():
    print(linha.id, linha.name, linha.transport_mode)

viagens = ds.service_journeys.query(
    where={"line_id": "200"}, order_by="departure_time", limit=3)
```

```
200 Bolhão - Cast.queijo TransportMode.BUS
201 Aliados-viso TransportMode.BUS
202 Aliados-passeio Alegre (via Av. Bessa) TransportMode.BUS
203 Estádio Do Dragão - Casa Da Música TransportMode.BUS
```

> Tudo validado à entrada, com tipos, autocompletar no editor, e erros no sítio onde os
> dados estão errados — não três passos à frente.

**Notas de edição:** 30 segundos, não mais. Mostrar o autocompletar do editor a sugerir
`transport_mode` vale mais do que qualquer frase sobre tipagem.

---

### Cena 5 — Qualidade: o feed está bom? (4:45–5:45)

**Ecrã:** terminal, agora com o `porto_full.db` — o feed completo do Porto, 71 linhas.

> Um ficheiro passar o esquema diz que abre. Não diz que os dados fazem sentido. São
> vinte regras sobre a base importada.

**Comando 1 — validação semântica** (12,0 s, acelerar 3×):

```bash
pid-transit validate porto_full.db
```

```
Validation PASSED - 41 issue(s) (0 error(s)):
  [WARNING] [scheduled_stop_point] 0COR3: Stop 0COR3 duplicates 0COR1 (same name within 0 m).
  [WARNING] [scheduled_stop_point] BARC2: Stop BARC2 duplicates BARC1 (same name within 10 m).
  ...
```

> Quarenta e uma paragens com o mesmo nome a menos de dez metros uma da outra, num feed
> que passa o validador oficial da MobilityData com zero erros. Nenhuma delas é um erro
> de esquema. Todas elas duplicam uma paragem no mapa do passageiro.
>
> São avisos, não erros, e por isso isto passa. Com `--min-severity error`, isto é um
> portão de integração contínua que falha nos erros e tolera os avisos.

**Comando 2 — higiene de tempo real** (0,5 s):

```bash
pid-transit rt-readiness porto_full.db
```

```
**Status:** NOT READY

## Issues (1026)
| unsafe_id | service_journey | `202_0_2|228|D1|T3|N2` | ID contains URL-unsafe or whitespace characters |
```

> Mil e vinte e seis identificadores de viagem com barras verticais. Em estático
> ninguém nota. No dia em que este operador ligar um feed de tempo real, estes
> identificadores vão para dentro de um URL — e a falha número um em GTFS-Realtime em
> produção é precisamente o feed em tempo real referir viagens que o estático já não
> casa.

**Comando 3 — a ida e volta perde dados?** (3,8 s):

```bash
pid-transit roundtrip porto_demo.zip --format gtfs --timezone Europe/Lisbon
```

```
Round trip is LOSSLESS - re-imported database is identical.
```

> GTFS, para NeTEx, e outra vez para GTFS. Base idêntica. E isto não é uma afirmação do
> vídeo: é um teste que corre em cada *commit*.

---

### Cena 6 — Perfis nacionais (5:45–7:15)

**Ecrã:** slide `05 · Perfis nacionais`, e depois volta ao terminal da cena 1.

> Agora voltemos ao ficheiro do início — e ao que lhe faltava.
>
> NeTEx válido não significa NeTEx legível. O padrão é enorme, e cada perfil nacional
> usa um canto diferente dele. Dois ficheiros podem ser os dois CEN NeTEx válido e
> precisar de leitores diferentes.
>
> Esta biblioteca identifica o dialeto pela **estrutura do documento** — nunca pelo
> *codespace* nem pelo nome do ficheiro, que são etiquetas que o produtor escolheu e
> pode mudar amanhã. Quatro dialetos reconhecidos: o genérico CEN, o suíço, a indireção
> europeia, e o português.

**Ecrã:** o relatório da cena 1, agora lido com calma, com destaque nas linhas citadas.

> O dataset da Carris publicado pelo IMT escreve elementos `ServiceJourney` sem atributo
> nenhum — sem identificador —, um `JourneyPatternView` em linha em vez de um
> `JourneyPattern`, e um calendário por viagem sem referência nenhuma entre os dois.
>
> Lido como NeTEx genérico, a rede importa e o horário desaparece em silêncio. Lido como
> perfil português: dez mil duzentas e trinta e oito viagens, duzentos e noventa e seis
> mil seiscentos e três passing times, com calendários reais. Vinte e dois segundos,
> cento e quarenta e nove megabytes de memória de pico.
>
> E aqui está a parte que importa mais do que a contagem: onde o perfil obriga a inferir
> em vez de ler, o importador diz que o está a fazer e recusa adivinhar. O emparelhamento
> entre viagem e calendário é posicional, porque é a única leitura que o ficheiro
> permite — e só se aplica quando as duas contagens batem exatamente. Se não baterem,
> avisa e importa sem calendário, em vez de colar um calendário plausível e errado.

**Comando — e escrever o perfil, não só lê-lo** (2,7 s no feed reduzido):

```bash
pid-transit convert porto_demo.zip -o porto_pt.xml --profile pt
```

```
Profile pt could not carry 1026 unsupported_journey_pattern
Profile pt could not carry 14 unsupported_shape
Converted porto_demo.zip (gtfs) -> porto_pt.xml (netex)
  service_journey: 1,026
  passing_time: 27,035
Not representable in this profile:
  unsupported_journey_pattern: 1,026
  unsupported_point_in_journey_pattern: 27,035
  unsupported_shape: 14
```

> Os perfis funcionam nos dois sentidos: ler o dialeto do IMT, e escrever para ele. E
> outra vez — o perfil português não tem padrões de viagem nem geometria, e a exportação
> diz-te isso com números antes de escrever o ficheiro.

**Notas de edição:** esta é a cena que vende, e é também o corte B. Gravar com margem de
2 segundos antes e depois, para a poder exportar isolada.

---

### Cena 7 — Prova e escala (7:15–8:15)

**Ecrã:** slide `06 · Prova`.

> Três afirmações diferentes, mantidas separadas de propósito, porque um avaliador
> competente vai perguntar por elas uma a uma.
>
> O ficheiro é válido? Validadores externos, não a nossa opinião: o GTFS exportado passa
> o `gtfs-validator` da MobilityData com zero erros; o NeTEx passa o XSD do CEN 1.3 com
> zero erros. E — isto é mais interessante do que a contagem de erros — a exportação tem
> cinco mil seiscentos e três avisos contra cinco mil quinhentos e noventa e nove no
> feed de origem. O ficheiro exportado preserva o que o operador entregou, em vez de o
> normalizar em silêncio. Um sistema de registo não deve "melhorar" aquilo que lhe deram.
>
> A ida e volta perde dados? As dez contagens de entidades batem certo, incluindo noventa
> e um mil oitocentos e cinquenta shape points reconstruídos a partir da geometria troço
> a troço. Duas exceções documentadas, não escondidas.
>
> E escala? O horário nacional suíço: trezentos e cinquenta e cinco ficheiros, vinte e
> quatro vírgula cinco gigabytes de XML, vinte e seis vírgula cinco milhões de passing
> times. Mil cento e setenta e um megabytes de memória de pico.
>
> O número que interessa nessa tabela é o que **não** muda: o subconjunto de seiscentos e
> trinta megabytes importa com os mesmos mil cento e setenta e um megabytes. Trinta e
> nove vezes mais entrada, exatamente a mesma memória. Os importadores fazem streaming —
> a memória segue os mapas de referência, não o tamanho do feed.

**Notas de edição:** mostrar a tabela de escala com as duas linhas em destaque e a coluna
do RSS de pico realçada. É a única coisa no vídeo que não tem rival óbvio.

---

### Cena 8 — O que falta, dito à frente (8:15–8:45)

**Ecrã:** slide `08 · Estado e roteiro`.

> E o que é que não faz.
>
> Os exportadores ainda tamponam toda a saída antes de a escrever: cinco vírgula dois
> gigabytes de pico para produzir um gigabyte e cento e oitenta megabytes de NeTEx,
> contra um gigabyte e cento e setenta constantes para importar os mesmos dados.
> Importar à escala nacional funciona hoje. Exportar à mesma escala exige orçamento de
> memória. Está no roteiro, e é o próximo item de desempenho.
>
> Não há Fares v2, que é para onde o mercado está a migrar. Não há GTFS-Flex, nem SIRI.
> Está tudo na lista, por esta ordem.
>
> Publicar as ausências à vista ganha credibilidade com quem avalia. Escondê-las perde o
> negócio mais tarde, já na integração — que é a altura cara.

**Notas de edição:** não acelerar esta cena nem a tratar como letra pequena. Lê-se à mesma
velocidade que a cena 7. É isso que a faz valer.

---

### Cena 9 — Fecho (8:45–9:00)

**Ecrã:** slide `01 · Main` com os quatro números, e por cima o comando de instalação.

> Licença MIT, Python 3.10 a 3.14, duas dependências.
>
> `pip install pid-transit`. O repositório está na descrição.
>
> Se tens um feed real e queres saber o que esta biblioteca diz dele — é literalmente um
> comando. Fala comigo.

---

## 4. Os três cortes curtos

Tirados do material da gravação principal, sem gravar nada de novo.

### Corte A — *teaser* (75 s)

| Trecho | Vem de |
|---|---|
| 0:00–0:20 | cena 1 (gancho, sem a última frase) |
| 0:20–0:35 | cena 2, só "a conversão é tratada como um acontecimento; devia ser um sistema" |
| 0:35–1:00 | cena 4, comandos 1 e 3 acelerados 2×, com os números à vista |
| 1:00–1:15 | cena 9 |

Sem locução nova. Legendas em inglês queimadas na imagem — o LinkedIn toca sem som.

### Corte B — perfis nacionais (2 min)

Cena 6 inteira, com 10 s de contexto da cena 2 à frente e a cena 9 atrás. É o corte que
se envia ao IMT e aos NAPs: mostra um defeito no ficheiro **deles** e uma ferramenta que
o lê de qualquer maneira. Enviar com o relatório do `netex-check` em anexo, em JSON
(`--json`), para quem quiser conferir.

### Corte C — prova e escala (2 min)

Cenas 7 e 8, por esta ordem, sem alterações. A cena 8 é obrigatória neste corte: a prova
sem as ausências lê-se como brochura.

---

## 5. Pós-produção

- **Cortes de espera:** qualquer comando acima de 5 s é acelerado, com o relógio ou a
  barra de progresso visível para que a aceleração seja honesta. Não cortar o meio de um
  comando e fingir que foi instantâneo.
- **Destaques:** retângulo animado nas linhas da saída que a locução está a citar. Nunca
  mais de uma por vez.
- **Legendas:** em inglês, geradas e **revistas** — "passing times", "journey pattern",
  "codespace" e "NeTEx" saem todos errados em transcrição automática.
- **Sem música** nas cenas de terminal. Música só nos cartões de título, se houver.
- **Miniatura:** o número `26,5 M` grande, com `GTFS ⇄ NeTEx` por baixo. Não usar
  "PID-Transit" na miniatura — ninguém procura o nome.

### Descrição para publicação (pronta a colar)

```
PID-Transit — uma base de dados Transmodel que fala GTFS e NeTEx nos dois sentidos.
Sem servidor, sem JVM, sem perder o horário pelo caminho.

Biblioteca Python, licença MIT, duas dependências de runtime.
  pip install pid-transit

O que se vê neste vídeo:
00:00  Um ficheiro NeTEx válido onde o horário desaparece
00:40  O problema: o regulador quer NeTEx, o operador produz GTFS
01:45  O que é: modelo Transmodel em SQLite, adaptadores à volta
02:30  Demo: de GTFS a NeTEx em quatro comandos
04:15  A API tipada
04:45  Qualidade: 20 regras, higiene de tempo real, ida e volta sem perdas
05:45  Perfis nacionais: o caso Carris / IMT
07:15  Prova: validadores externos e 24,5 GB de XML a memória constante
08:15  O que ainda não faz
08:45  Fecho

Repositório: https://github.com/Andreeduardopp/pid-transit
PyPI: https://pypi.org/project/pid-transit/

Os feeds usados são públicos: GTFS da STCP (Porto) e o dataset NeTEx da Carris
publicado pelo IMT no Ponto de Acesso Nacional português.
```

---

## 6. Apêndice A — `make_demo_feed.py`

Escrito em `research/scripts/make_demo_feed.py`. Corta um feed GTFS a N linhas,
arrastando viagens, horários, paragens, geometria e calendário. Só biblioteca padrão.

```bash
python research/scripts/make_demo_feed.py feed.zip demo.zip          # 4 primeiras linhas
python research/scripts/make_demo_feed.py feed.zip demo.zip 200 500  # linhas escolhidas
```

## 7. Apêndice B — `demo.py` para a cena 4b

```python
from pid_transit import TransitDataset

ds = TransitDataset("porto.db")

for linha in ds.lines.get_all():
    print(linha.id, "|", linha.name, "|", linha.transport_mode)

viagens = ds.service_journeys.query(
    where={"line_id": "200"},
    order_by="departure_time",
    limit=3,
)
for v in viagens:
    print(v.id, v.departure_time, v.day_type_id)

print("paragens:", ds.scheduled_stop_points.count())
print("passing times:", ds.passing_times.count())
```

Corrido, com esta saída:

```
200 | Bolhão - Cast.queijo | TransportMode.BUS
201 | Aliados-viso | TransportMode.BUS
202 | Aliados-passeio Alegre (via Av. Bessa) | TransportMode.BUS
203 | Estádio Do Dragão - Casa Da Música | TransportMode.BUS

200_0_1|228|D2|T2|N2 06:00:00 SÁBADOS
200_0_1|228|D3|T2|N2 06:00:00 DOMINGOS|FERIADOS
200_0_1|228|D1|T6|N2 06:00:00 DIAS UTEIS

paragens: 185
passing times: 27035
```

---

## 8. Apêndice C — números a corrigir antes de gravar

Os slides e o README têm valores que já não descrevem o código. Se algum destes aparecer
em câmara, o avaliador desmente-o com um comando — e o argumento inteiro do dossiê é
"publica a prova".

| Onde | Diz | Está | Verificado com |
|---|---|---|---|
| slide 08, README | 330 testes (312 sem `rt`) | **509** | `pytest --collect-only -q` |
| slide 03, MERCADO.md | CLI de 9 comandos | **13** | `pid-transit --help` |
| slide 08 | 7 616 linhas em `src/` | **10 728** | `wc -l` em `src/**/*.py` |
| slide 08 | "ainda não: exportar com perfil" | **já faz** — `epip` e `pt` | `pid-transit export --help` |
| README, `NETEX_EXPORT.md` §1 | "`profile` é aceite mas inerte" | **falso** desde o commit *Perfil PT* | `netex_export_profiles.py` |
| MERCADO.md | 318 testes, 7 357 linhas | desatualizado | idem |

A tabela de desempenho do README também está conservadora para o feed do Porto: diz
32,1 s de importação, e hoje faz **12,9 s** nesta máquina. Vale remedir antes de gravar
a cena 7, porque é um número que se diz em voz alta.

**Defeito a corrigir antes de publicar:** o `netex-check` anuncia aceitar `.zip` e
rebenta com um. Ver secção 2.3.

---

## 9. Lista de verificação final

Antes de gravar:

- [ ] `PYTHONUTF8=1` exportado e confirmado com `pid-transit stats` (ver "SÁBADOS")
- [ ] `pid-transit` instalado do PyPI num venv limpo, não o repositório em modo editável
- [ ] `porto_demo.zip`, `porto_full.db` e `netex_1.xml` na pasta de gravação
- [ ] `porto.db`, `porto.xml`, `porto_pt.xml` **apagados**
- [ ] tipo de letra a 18 pt, prompt reduzido a `$`, janela a 100 colunas
- [ ] notificações desligadas, nada de clientes no ecrã
- [ ] números da secção 8 corrigidos nos slides que vão aparecer

Depois de gravar, confirmar que existem tomadas limpas de: cena 1 isolada (corte A),
cena 6 completa com margem (corte B), cenas 7 e 8 seguidas (corte C).
