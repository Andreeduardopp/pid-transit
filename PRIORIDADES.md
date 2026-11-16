# Prioridades

Análise da secção *4. O que falta para vender* do `MERCADO.md`, verificada contra
o estado real do repositório, e a ordem de trabalho que dela decorre.

*pid-transit 0.7.0 · 9 de setembro de 2026*

---

## Verificação

Verifiquei as afirmações da secção 4 contra o repositório antes de priorizar.
Resumo do que se confirma e do que já está desatualizado:

| Afirmação da secção 4 | Estado real |
|---|---|
| Não há CI, CHANGELOG, CONTRIBUTING, docs | **Confirmado** — não existe sequer `.github/`; a raiz tem 4 `.md` e nenhum é changelog |
| `NETEX_EXPORT.md` por commitar | **Confirmado** — e também `NETEX_IMPORT_PERF.md` e `netex_profiles.py` (374 linhas) |
| Argumento `profile` do exportador inerte | **Confirmado** — `netex_exporter.py:163-174`, o docstring assume-o |
| Sem Fares v2 / Flex / SIRI / GeoJSON | **Confirmado** — zero ocorrências em `src/` |
| 318 testes, 7 357 linhas | **Errado** — 312 recolhidos, 7 972 linhas |

Dois problemas que a secção não regista:

**Os números do dossiê estão a derivar do código.** 318 vs 312 testes é irrelevante
em si; o que não é irrelevante é que um documento cujo argumento central é "publica
a prova" contenha números que o avaliador desmente com um `pytest --collect-only`.
Estes valores têm de sair de um comando, não da memória.

**O nome já se partiu em dois.** A secção trata "PID" como decisão adiável, mas o
`pyproject.toml` publica `pid-transit` e aponta para
`github.com/Andreeduardopp/PID-Toolkit`. Já não é risco futuro de descoberta — é
uma incoerência atual entre o pacote e o repositório.

---

## Prioridades

### P0 — dias, esta semana

1. **Commitar o que já está escrito.** `NETEX_EXPORT.md`, `NETEX_IMPORT_PERF.md`,
   `netex_profiles.py`, as alterações ao README. Está tudo por versionar. Risco
   zero, e é literalmente o item "publicar a prova que já tens" a custo nulo.
2. **CI em GitHub Actions.** Matriz 3.10–3.14, suite com e sem a extra `rt` (a
   regra "importa sem `gtfs-realtime-bindings`" não está a ser testada por
   ninguém). Meio dia, e protege tudo o que vier a seguir.
3. **Números gerados por comando.** Contagem de testes, LOC e métricas de
   desempenho no README a partir de script, não escritas à mão.

### P1 — semanas

4. **`to_dataframe()` / `to_geojson()` como extra `[geo]`.** Concordo com o
   documento: dias de trabalho, maior alcance por unidade de esforço, e o núcleo
   mantém-se em `pydantic` + `openpyxl`.
5. **Resolver o nome.** Mesmo mantendo `pid-transit`, alinhar o repositório e
   reescrever `description` e `keywords` em torno de "GTFS ⇄ NeTEx". Agora custa
   uma tarde.
6. **1.0 + CHANGELOG + política de depreciação.** Depois de existir CI — sem
   suite a correr automaticamente, a promessa de estabilidade não tem quem a faça
   cumprir.

### P2 — a aposta grande: perfis de exportação, feitos ao mesmo tempo que o streaming

Aqui divirjo da ordem da secção 4. O documento lista perfis em segundo e
exportadores em streaming em quarto, como trabalhos independentes. São a mesma
cirurgia no mesmo ficheiro:

- Os perfis já têm arquitetura do lado da importação (`netex_profiles.py` com
  `ProfileAdapter` e seams inertes por omissão), o parâmetro já está na
  assinatura, e existem dois feeds reais — suíço e português — para validar.
  Fares v2 não tem nada disto.
- Os 5,2 GB de pico não são só dívida técnica: enquanto existirem, "entrega no
  NAP" é uma demonstração, não um produto. Ninguém converte um feed nacional numa
  instância de 8 GB dedicada.
- Fazer os dois em passagens separadas significa reescrever o mesmo exportador
  duas vezes.

Portanto: **um rework do exportador que entrega streaming e `--profile epip` em
conjunto**, com EPIP primeiro e o perfil nacional a seguir.

### Adiar

Fares v2, GTFS-Flex, SIRI, `pid-transit-server`, Action e Docker. Todos
defensáveis, nenhum urgente antes de existir CI e alguém a usar a biblioteca.

A exceção barata é a **exportação para folha de cálculo**: dias, `openpyxl` já é
dependência, fecha o ciclo do planeador — bom trabalho de intervalo enquanto o
exportador está em rework.

---

## Uma dependência que a secção 4 não declara

"Publicar o benchmark comparativo" (Nível 3) exige decidir o que sai de
`research/`, que está gitignored por regra do `CLAUDE.md`. Não é uma tarefa de
publicação, é uma decisão sobre o limite do repositório — e tem de ser tomada
antes, não durante.
