# `pid_transit` — visão geral do pacote

`pid_transit` é uma biblioteca Python para **importar, modelar, validar, analisar e exportar** dados de transporte público, com compatibilidade bidirecional entre **GTFS** (CSV achatado dentro de um `.zip`) e **NeTEx** (XML profundamente aninhado).

A decisão de arquitetura que sustenta tudo: em vez de eleger um dos dois formatos como modelo interno — o que tornaria os *round-trips* na outra direção lossy —, o modelo canônico interno é o **Transmodel** (EN 12896), a referência conceitual europeia de transporte público da qual o NeTEx é a serialização XML e o GTFS um subconjunto bem definido. Por isso as entidades usam **nomes Transmodel**, não GTFS:

| Transmodel | GTFS | NeTEx |
|---|---|---|
| `Operator` | `agency` | `Operator` |
| `Line` | `route` | `Line` |
| `ServiceJourney` | `trip` | `ServiceJourney` |
| `PassingTime` | `stop_time` | `TimetabledPassingTime` |

> O banco "fala" Transmodel; toda a tradução de/para formato vive nos adapters. O `core` nunca enxerga um nome de campo do GTFS nem um elemento XML.

A documentação completa de uso (API, exemplos, CLI) está no [`README.md` da raiz](../../README.md), e as decisões de arquitetura em detalhe estão em [`docs/architecture.md`](../../docs/architecture.md). Este documento descreve **como o pacote é organizado por dentro**.

---

## As três camadas

```
                 ┌──────────────────────────────────────────────────────┐
   GTFS .zip ←→  │  Gtfs Importer / Exporter                            │
   NeTEx  XML ←→ │  Netex Importer / Exporter   ──►  core (TransitDataset│  ◄── analytics
   CSV / XLSX →  │  Spreadsheet Importer            + TransmodelDatabase)│      (lê o dataset)
                 └──────────────────────────────────────────────────────┘
                          adapters                       core
```

Os **adapters** traduzem cada formato externo para/dos modelos Transmodel do **core**; a **analytics** opera por cima de um dataset já carregado, sem conhecer formato algum.

---

## `core/` — modelo de domínio e persistência

O coração agnóstico de formato. Define o que os dados *são* e como são guardados, validados e acessados.

| Arquivo | Responsabilidade |
|---|---|
| **`schemas.py`** | As entidades Transmodel como modelos **Pydantic v2** — `Operator`, `Line`, `ScheduledStopPoint`, `StopArea`, `Level`, `Pathway`, `DayType`, `OperatingDayException`, `JourneyPattern`, `PointInJourneyPattern`, `ServiceJourney`, `PassingTime`, `Frequency`, `Transfer`, `ShapePoint`, `FeedInfo`, `Attribution`, `FareAttribute`, `FareRule`, `Translation` — mais os enums `TransportMode` e `DirectionType`. É aqui que a validação forte acontece (tipos, campos obrigatórios, faixas de valores). |
| **`database.py`** | `TransmodelDatabase`: wrapper SQLite com **integridade referencial** (`PRAGMA foreign_keys = ON`), modo em-arquivo ou em-memória, e versionamento de schema (`SCHEMA_VERSION = "3.0.0-transmodel"`) com auto-migração na abertura. As tabelas são definidas como DDL puro aqui. |
| **`repositories.py`** | Camada de acesso a dados no padrão **Repository**, uma classe por entidade sobre uma `BaseRepository` genérica. Faz a ponte entre as linhas cruas do SQLite e os modelos Pydantic validados: `get_all`, `get_by_id`, `query(where, order_by, limit)`, `count`, `add` / `add_many`, `update` (com re-validação), `delete` / `delete_many`, além de métodos especializados (`lines.get_by_operator`, `passing_times.get_by_journey`, etc.). |
| **`dataset.py`** | `TransitDataset`: a **fachada principal** da biblioteca e o ponto de entrada que você normalmente usa. Junta o banco e todos os repositórios numa API única e expõe `import_data(importer, source)`, `export_data(exporter, target)` e `validate()`. |
| **`validator.py`** | Validação de **consistência lógica** que vai além do schema: toda `ServiceJourney` tem ao menos um `PassingTime`, não há ordem de passing time duplicada, toda `Line` tem ao menos um `JourneyPattern`, integridade de FK, etc. Reporta `ValidationIssue`s classificadas por `Severity` (`ERROR` / `WARNING` / `INFO`). |
| **`exceptions.py`** | Exceções de domínio — `ImportFailedError`, `EntityNotFoundError`, `ValidationError`. |

---

## `adapters/` — tradução entre formatos

Isolam **toda** a lógica específica de cada formato, para que o core permaneça limpo. As idiossincrasias que vivem aqui (e não no modelo) incluem:

- `route_type` inteiro do GTFS (`0=tram`, `3=bus`) ↔ modo string do NeTEx (`"tram"`, `"bus"`);
- `stops.txt` do GTFS, que mistura plataformas e estações via `location_type`, ↔ a separação Transmodel `ScheduledStopPoint` / `StopArea`;
- tempos: o GTFS usa `HH:MM:SS` linear (permitindo `≥ 24:00:00` para serviço noturno), o NeTEx usa `xs:time` + `DayOffset`;
- a lacuna semântica central: o GTFS é *trip-cêntrico* (cada trip lista sua sequência de paradas), o NeTEx é *pattern-cêntrico* (trips referenciam `JourneyPattern`s reutilizáveis).

| Arquivo | Direção | Notas |
|---|---|---|
| **`gtfs_importer.py`** | GTFS `.zip` → banco | Divide `stops.txt` por `location_type`; gera um `JourneyPattern` sintético por trip (`JP_{trip_id}`); popula `PointInJourneyPattern` (sequência) e `PassingTime` (horários). |
| **`gtfs_exporter.py`** | banco → GTFS `.zip` | Reúne `ScheduledStopPoint` + `StopArea` de volta em `stops.txt` com os `location_type` corretos; escreve todos os arquivos GTFS padrão, incluindo `shapes.txt`, `frequencies.txt`, `transfers.txt`. |
| **`netex_importer.py`** | NeTEx XML → banco | Detecta o namespace automaticamente; reconstrói tempos a partir de `DayOffset`; preserva `JourneyPattern`s nativos diretamente (sem síntese). |
| **`netex_exporter.py`** | banco → NeTEx XML | Monta a estrutura multi-frame (Resource / Site / Service / ServiceCalendar / Timetable); flag opcional `deduplicate_patterns` colapsa patterns idênticos em canônicos; normaliza `25:30:00 → (01:30:00, DayOffset=1)`. |
| **`spreadsheet_importer.py`** | CSV / XLSX → banco | Entrada via planilha — uma workbook Excel (uma aba por tabela) ou uma pasta de CSVs. |

**Contrato de adapter:** importers implementam `import_to_db(db, source) -> dict[str, int]` (contagem de registros por tabela); exporters implementam `export_from_db(db, target)`. Adapters próprios (formato proprietário, API interna) só precisam seguir esse contrato.

---

## `analytics/` — operações sobre datasets

Ferramentas que recebem um `TransitDataset` já carregado e produzem relatórios ou novos datasets. **Independentes de formato** — operam sobre o modelo Transmodel.

| Arquivo | O que faz |
|---|---|
| **`statistics.py`** | `TransitStatistics`: métricas operacionais — `summary()`, `service_span()` (primeira/última partida por linha e tipo de dia), `headways()` (intervalos, com suporte a horários de pico configuráveis), `vehicle_hours()`, `stop_coverage()`, `service_balance()`. |
| **`diff.py`** | `FeedDiffer` → `FeedDiffReport`: compara dois feeds entidade a entidade (adicionadas / removidas / modificadas) e detecta mudanças de janela de serviço e de frequência. Saída em `to_dict()` (estruturada) ou `to_markdown()`. |
| **`merge.py`** | `FeedMerger`: funde múltiplos feeds num só, processando tabelas em ordem segura de FK, fazendo **namespacing de IDs** para evitar colisões (`stcp:L1`, `metro:L1`) e remapeando todas as referências; opcionalmente deduplica paradas próximas por distância **haversine**. |
| **`rt_readiness.py`** | `check_rt_readiness` → `RTReadinessReport`: verifica se o dataset está pronto para um feed **GTFS-Realtime** — IDs únicos, não vazios e *URL-safe*, sequências de paradas consistentes, sem referências ambíguas. |

---

## `cli.py` — interface de linha de comando

Expõe as operações principais no terminal via o entry point `pid-transit` (definido no `pyproject.toml`):

```bash
pid-transit import feed.zip --format gtfs --db city.db
pid-transit export --db city.db --format netex --output feed.xml
pid-transit validate city.db
pid-transit stats city.db --line L_10 --day-type WEEKDAY
pid-transit diff january.db february.db --output report.md
```

---

## Caminho rápido pelo código

1. Comece por **`core/dataset.py`** (`TransitDataset`) — é a fachada e a porta de entrada de praticamente tudo.
2. Veja **`core/schemas.py`** para entender o vocabulário de domínio (as entidades Transmodel).
3. Siga um fluxo concreto: **`adapters/gtfs_importer.py`** (entrada) → banco → **`adapters/netex_exporter.py`** (saída) mostra a tradução de formato de ponta a ponta.
4. **`analytics/`** e **`core/validator.py`** são consumidores do dataset — leia depois de entender o modelo.
