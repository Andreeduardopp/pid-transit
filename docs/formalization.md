# Mathematical Formalization of `pid_transit`

This document gives a formal, math-notation account of the key design decisions
in `pid_transit`. It is the companion to [`architecture.md`](architecture.md):
where that document argues *why* each decision was made in prose, this one states
*what* each decision is as a set, function, relation, or constraint.

Notation conventions:

- Sets are upper-case ($G$, $T$, $L$); elements are lower-case ($r$, $s$, $jp$).
- $f \colon A \to B$ is a total function; $A \times B$ is a Cartesian product.
- $\langle s_1, \dots, s_n \rangle$ is an ordered sequence (a tuple).
- $A / {\sim}$ is the quotient set of $A$ under the equivalence relation $\sim$.
- $\lvert A \rvert$ is the cardinality of $A$.

---

## 1. The Format-Translation Mapping (Set Theory)

The library does not treat GTFS and NeTEx as peers. Both are mapped onto a single
canonical Transmodel model $T$, which is the database (Decision 1 in
`architecture.md`). Let:

- $G$ — the set of entities in a GTFS feed,
- $T$ — the set of entities in the Transmodel database,
- $N$ — the set of entities in a NeTEx document.

The adapters are the translation functions between these sets:

$$
\text{import}_{G} \colon G \to T, \qquad
\text{export}_{G} \colon T \to G, \qquad
\text{import}_{N} \colon N \to T, \qquad
\text{export}_{N} \colon T \to N.
$$

The architectural goal of **lossless round-tripping** is the statement that import
followed by export is the identity on the originating format (up to entity
isomorphism $\cong$):

$$
\text{export}_{G} \circ \text{import}_{G} \cong \mathrm{id}_{G},
\qquad
\text{export}_{N} \circ \text{import}_{N} \cong \mathrm{id}_{N}.
$$

### 1.1 Per-entity mappings

Restricting to specific entity families gives the concrete maps the adapters
implement. Let $R_{\text{GTFS}}$ be the set of GTFS routes and $L_{\text{TM}}$ the
set of Transmodel `Line`s:

$$
f_{\text{line}} \colon R_{\text{GTFS}} \to L_{\text{TM}}, \qquad
f_{\text{agency}} \colon \text{Agency}_{\text{GTFS}} \to \text{Operator}_{\text{TM}}.
$$

The semantically interesting map is for trips. A single GTFS `trips.txt` record is
the source of **both** a `ServiceJourney` and a synthetic `JourneyPattern`
(Decision 4); the GTFS importer emits the pattern with id `JP_{trip_id}`:

$$
f_{\text{trip}} \colon \text{Trip}_{\text{GTFS}}
\longrightarrow
\text{ServiceJourney}_{\text{TM}} \times \text{JourneyPattern}_{\text{TM}}.
$$

This map is injective on the pattern component but **not** surjective onto distinct
patterns: many trips produce structurally identical `JourneyPattern`s. That
redundancy is exactly what §2 removes at export time.

### 1.2 Stop duality

GTFS conflates platforms and stations in one `stops.txt` table, discriminated by
`location_type`. The importer splits this set (Decision 3). Writing
$S_{\text{GTFS}}$ for the GTFS stop set:

$$
f_{\text{stop}}(s) =
\begin{cases}
\text{ScheduledStopPoint} & \text{if } \texttt{location\_type}(s) = 0, \\
\text{StopArea} & \text{if } \texttt{location\_type}(s) \ge 1.
\end{cases}
$$

This is a partition of $S_{\text{GTFS}}$ into two disjoint sets whose union is the
whole; the GTFS exporter is the inverse merge that reattaches the correct
`location_type`.

---

## 2. Journey-Pattern Deduplication (Equivalence Relations & Quotients)

A `JourneyPattern` is modelled as an ordered sequence of scheduled stop points,
together with the `Line` it serves and its travel direction. Because each GTFS
trip generates its own pattern, the database holds large numbers of patterns that
are identical in structure. The NeTEx exporter's `deduplicate_patterns` flag
(default on) collapses these.

Represent a journey pattern's **stop signature** as the ordered tuple of its
stop-point ids in `order`:

$$
\sigma(jp) = \langle s_1, s_2, \dots, s_n \rangle .
$$

The dedup key is **not** the stop sequence alone — the implementation groups on
the triple of line, direction, and stop signature:

$$
\kappa(jp) = \bigl(\, \texttt{line\_id}(jp),\ \texttt{direction}(jp),\ \sigma(jp) \,\bigr).
$$

Define the equivalence relation $\sim$ on the set of patterns $\mathit{JP}$:

$$
jp_i \sim jp_j \iff \kappa(jp_i) = \kappa(jp_j).
$$

$\sim$ is reflexive, symmetric, and transitive (it is the kernel of $\kappa$), so
it partitions $\mathit{JP}$ into equivalence classes. Deduplication computes the
**quotient set** $\mathit{JP} / {\sim}$ and picks one **canonical representative**
per class — the implementation chooses the first pattern encountered for each key:

$$
\text{canon} \colon \mathit{JP} \to \mathit{JP}, \qquad
\text{canon}(jp) \in [jp]_{\sim},
$$

where $[jp]_{\sim}$ is the class of $jp$. Every `ServiceJourney` that referenced
$jp$ is remapped to reference $\text{canon}(jp)$, and only the representatives
$\{\, \text{canon}(jp) : jp \in \mathit{JP} \,\}$ are serialized.

The data-volume reduction is the drop from the full set to the quotient:

$$
\lvert \mathit{JP} \rvert \ \longrightarrow\ \lvert \mathit{JP} / {\sim} \rvert,
\qquad\text{e.g. } 12\,722 \longrightarrow 220 .
$$

This happens before NeTEx XML generation, so the serializer never emits duplicate
`<JourneyPattern>` elements. Import remains lossless (every trip's exact sequence
is stored); only the export is compacted.

---

## 3. Operational & Spatial Metrics (Algebraic Equations)

These are the equations behind `analytics/statistics.py` and
`analytics/merge.py`. All times are first mapped to seconds-since-midnight,
allowing values $\ge 24\!\cdot\!3600$ for overnight service (Decision 5):

$$
\tau(\texttt{"HH:MM:SS"}) = 3600 \cdot \mathrm{HH} + 60 \cdot \mathrm{MM} + \mathrm{SS}.
$$

### 3.1 Headways

For a line $\ell$ on day type $d$, let the sorted departure times of its service
journeys be $t_1 \le t_2 \le \dots \le t_m$. The headway between consecutive
departures is

$$
H_k = t_{k+1} - t_k, \qquad k \in [1,\, m-1].
$$

Defined only when $m \ge 2$. The reported statistics are

$$
\bar H = \frac{1}{m-1}\sum_{k=1}^{m-1} H_k, \qquad
H_{\min} = \min_k H_k, \qquad
H_{\max} = \max_k H_k.
$$

Peak and off-peak averages partition the gaps by whether the **midpoint** of the
gap falls inside a configured peak window $P = \{[a_1,b_1], [a_2,b_2], \dots\}$
(default morning/evening peaks):

$$
m_k = \left\lfloor \frac{t_k + t_{k+1}}{2} \right\rfloor, \qquad
\text{peak}(k) \iff \exists\, [a,b] \in P : a \le m_k < b,
$$

$$
\bar H_{\text{peak}} = \operatorname*{avg}_{\,k\,:\,\text{peak}(k)} H_k,
\qquad
\bar H_{\text{off}} = \operatorname*{avg}_{\,k\,:\,\neg\text{peak}(k)} H_k.
$$

### 3.2 Vehicle-hours

For each service journey $j$, let $\Pi_j$ be its set of passing times, each with a
time $t = \texttt{departure\_time}$ or, if absent, $\texttt{arrival\_time}$. A
journey contributes only when $\lvert \Pi_j \rvert \ge 2$. Total vehicle-hours over
the active set $J$ (optionally filtered by day type) is the sum of each journey's
span, converted to hours:

$$
VH = \frac{1}{3600} \sum_{j \in J}
\Bigl( \max_{p \in \Pi_j} \tau(t_p) \;-\; \min_{p \in \Pi_j} \tau(t_p) \Bigr).
$$

(The implementation uses first-to-last span per journey, which equals
arrival$-$departure for monotone journeys but is robust to ordering.)

### 3.3 Service span

For each line $\ell$ and day type $d$, the span is the extremal departure times:

$$
\text{span}(\ell, d) = \Bigl( \min_{j \in J_{\ell,d}} \tau(t_j^{\text{dep}}),\ \
\max_{j \in J_{\ell,d}} \tau(t_j^{\text{dep}}) \Bigr).
$$

### 3.4 Stop coverage and service balance

Stop coverage counts departures observed at each stop $s$ over the active journey
set:

$$
\text{cov}(s) = \bigl\lvert \{\, p \in \Pi : \texttt{stop}(p) = s \ \wedge\ \texttt{departure}(p) \neq \varnothing \,\} \bigr\rvert .
$$

Service balance reports, per day type $d$, the number of distinct journeys and
distinct lines:

$$
\text{balance}(d) = \bigl( \lvert \{\, j : \texttt{day\_type}(j) = d \,\} \rvert,\ \
\lvert \{\, \texttt{line}(j) : \texttt{day\_type}(j) = d \,\} \rvert \bigr).
$$

### 3.5 Proximity merging (Haversine)

`FeedMerger` can deduplicate stops that are physically close across operators. The
great-circle distance between stops $s_1 = (\varphi_1, \lambda_1)$ and
$s_2 = (\varphi_2, \lambda_2)$ (latitude $\varphi$, longitude $\lambda$, in
radians) is the Haversine formula with Earth radius $R = 6\,371\,000\ \text{m}$:

$$
a = \sin^2\!\Bigl(\frac{\varphi_2 - \varphi_1}{2}\Bigr)
  + \cos\varphi_1 \cos\varphi_2 \, \sin^2\!\Bigl(\frac{\lambda_2 - \lambda_1}{2}\Bigr),
$$

$$
d(s_1, s_2) = 2R \cdot \arcsin\!\bigl(\sqrt{a}\,\bigr).
$$

A source stop $s$ is merged into the **nearest** target stop within a threshold
$\delta$ rather than added as a duplicate:

$$
\text{merge}(s) =
\operatorname*{arg\,min}_{\,t \in T_{\text{stops}}} \ d(s, t)
\quad\text{subject to}\quad
\min_{t} d(s, t) < \delta .
$$

If no target lies within $\delta$, $s$ is kept as a new (namespaced) stop.

### 3.6 ID namespacing on merge

To prevent collisions when merging feeds, every id from a source feed with
namespace $\nu$ is rewritten by prefixing, and all foreign keys are remapped
through the same function:

$$
g_\nu(\text{id}) = \nu \,\Vert\, \texttt{":"} \,\Vert\, \text{id}, \qquad
\text{e.g. } g_{\text{metro}}(\texttt{L1}) = \texttt{metro:L1}.
$$

Proximity-merged stops are the exception: their references resolve via
$\text{merge}(\cdot)$ instead of $g_\nu(\cdot)$.

---

## 4. Validation Business Rules (Logical Constraints)

`core/validator.py` audits logical consistency beyond what the Pydantic schema
enforces. Each rule is a constraint the data must satisfy; a violation produces a
`ValidationIssue` graded `ERROR` / `WARNING` / `INFO`. The principal constraints,
in formal terms:

### V01 — Passing-time existence
Every service journey has at least one passing time:

$$
\forall\, j \in J,\ \exists\, p \in \Pi : \texttt{service\_journey}(p) = j .
$$

### V02 — Order uniqueness
Within a journey, no two passing times share an `order` value (the `order` map is
injective on each journey's passing times):

$$
\forall\, j \in J,\ \forall\, p, q \in \Pi_j :\ \texttt{order}(p) = \texttt{order}(q) \implies p = q .
$$

### V04 — Time monotonicity
Along a journey's passing times sorted by `order`, the sequence of times
$\langle t_1, \dots, t_n \rangle$ is **non-decreasing** (not strictly — equal
consecutive times are allowed, e.g. a brief dwell recorded identically):

$$
\forall\, k \in [1,\, n-1] :\ t_k \le t_{k+1} .
$$

### V03 — Pattern completeness
Every line has at least one associated journey pattern (this is the
`no_journey_patterns` issue):

$$
\forall\, \ell \in L,\ \exists\, jp \in \mathit{JP} : \texttt{line}(jp) = \ell .
$$

### V11 — Journey completeness *(warning)*
Every line has at least one service journey:

$$
\forall\, \ell \in L,\ \exists\, j \in J : \texttt{line}(j) = \ell .
$$

### V05 / V06 / V08 / V13 / V14 — Referential integrity
Foreign keys point at existing entities. Writing $\text{ref}(x) \in S$ for "the
reference held by $x$ resolves into set $S$":

$$
\forall\, p \in \Pi :\ \texttt{stop}(p) \in S_{\text{stops}},
\qquad
\forall\, j \in J :\ \texttt{day\_type}(j) \in D,
$$
$$
\forall\, e \in E :\ \texttt{day\_type}(e) \in D,
\qquad
\forall\, f \in F :\ \texttt{service\_journey}(f) \in J,
$$

and for pathways, both endpoints resolve into the union of stop points and stop
areas: $\forall\, w :\ \texttt{from}(w), \texttt{to}(w) \in S_{\text{stops}} \cup S_{\text{areas}}$.

### V07 — Valid date range
Every day type's validity interval is well-formed:

$$
\forall\, d \in D :\ \texttt{start\_date}(d) \le \texttt{end\_date}(d) .
$$

### V09 — Time format *(warning)*
Departure times match the GTFS time grammar (note 2- or 3-digit hours to permit
$\ge 24\text{:}00\text{:}00$):

$$
\forall\, j \in J :\ \texttt{departure\_time}(j) \in \mathcal{L}(\,\texttt{\textbackslash d\{2,3\}:\textbackslash d\{2\}:\textbackslash d\{2\}}\,).
$$

### V10 — Coordinate plausibility *(warning)*
Stop coordinates are neither at Null Island nor out of range:

$$
\forall\, s \in S_{\text{stops}} :\ \neg\bigl(\varphi_s = 0 \wedge \lambda_s = 0\bigr)
\ \wedge\ \lvert \varphi_s \rvert \le 90 \ \wedge\ \lvert \lambda_s \rvert \le 180 .
$$

### V12 — Feed date coverage *(info)*
At least one day type's validity interval overlaps the feed's declared range
$[\texttt{fi}_{\text{start}}, \texttt{fi}_{\text{end}}]$:

$$
\exists\, d \in D :\ \texttt{start\_date}(d) \le \texttt{fi}_{\text{end}}
\ \wedge\ \texttt{end\_date}(d) \ge \texttt{fi}_{\text{start}} .
$$

---

## Symbol Reference

| Symbol | Meaning |
|---|---|
| $G,\ T,\ N$ | GTFS / Transmodel / NeTEx entity sets |
| $L,\ J,\ \mathit{JP}$ | Lines, service journeys, journey patterns |
| $\Pi,\ \Pi_j$ | All passing times / passing times of journey $j$ |
| $S_{\text{stops}},\ S_{\text{areas}},\ D,\ E,\ F$ | Stop points, stop areas, day types, exceptions, frequencies |
| $\tau(\cdot)$ | Time string → seconds since midnight |
| $\sigma(jp),\ \kappa(jp)$ | Stop signature / full dedup key of a pattern |
| $\sim,\ [jp]_\sim,\ \mathit{JP}/{\sim}$ | Pattern equivalence, class, quotient set |
| $H_k,\ \bar H$ | Headway between departures $k, k{+}1$ / mean headway |
| $VH$ | Total vehicle-hours |
| $d(s_1,s_2),\ \delta$ | Haversine distance / merge threshold |
| $g_\nu(\cdot)$ | Namespacing function for feed $\nu$ |

> **Rendering note.** The equations use LaTeX in `$ … $` / `$$ … $$` delimiters.
> They render on GitHub and in most Markdown viewers with MathJax/KaTeX support.
