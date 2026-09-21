# Architecture

Five views of the same system, each answering a different question. Decisions
referenced as **ADR-nnnn** are recorded in [`docs/adr/`](adr/README.md).

| View | Question it answers |
|---|---|
| [Context](#1-context) | Who uses it, what it depends on |
| [Logical](#2-logical-view) | How responsibility is divided |
| [Process](#3-process-view) | What happens during one plan |
| [Physical](#4-physical-view--as-deployed-today) | Where the code actually runs |
| [Degradation](#6-degradation-ladder) | What happens when something breaks |

**Logical vs physical, since the distinction does most of the work here:** the
logical view shows five agents. The physical view shows *one process*. Five
components, one deployable — and the gap between those two numbers is the
scalability discussion (ADR-0013).

---

## 1. Context

```mermaid
graph TB
    T["Traveller<br/><i>browser</i>"]
    A["Administrator<br/><i>ADMIN_EMAILS</i>"]
    S["<b>Agentic Travel Planner</b><br/>Flask + LangGraph"]
    LLM["LLM Provider<br/><i>OpenAI / Anthropic / Google / …</i>"]
    DUF["Duffel<br/><i>live flight inventory, opt-in</i>"]
    SER["Serper / Tavily<br/><i>accessibility evidence</i>"]
    PG["Supabase Postgres<br/><i>session pooler</i>"]

    T -->|"plan a trip, refine, view"| S
    A -->|"/admin monitoring"| S
    S -->|"HTTPS, max_retries=2"| LLM
    S -.->|"opt-in, degrades to seed"| DUF
    S -.->|"domain-allowlisted search"| SER
    S -->|"SQL"| PG

    style S fill:#1e3a5f,color:#fff
    style LLM fill:#4a3f6b,color:#fff
```

Dotted edges are **optional dependencies**: absence degrades a feature, it does
not stop a plan (ADR-0011). The LLM provider is the one hard external
dependency.

---

## 2. Logical view

How responsibility divides, independent of where code runs.

```mermaid
graph TB
    subgraph PRES["Presentation"]
        UI["Jinja templates + app.js"]
        API["REST API<br/><i>travel_ai/api.py</i>"]
    end

    subgraph APP["Application"]
        SVC["TravelPlanningService"]
        JOBS["Job manager<br/><i>jobs.py — ThreadPoolExecutor</i>"]
        INTAKE["Conversational intake"]
    end

    subgraph ORCH["Orchestration"]
        GRAPH["LangGraph workflow<br/><i>graph.py</i>"]
        BARRIER(["fan-in barrier"])
    end

    subgraph AGENTS["Specialist agents"]
        FL["Flight"]
        HT["Hotel &amp; Transport"]
        AC["Accessibility"]
        RA["Risk &amp; Advisory"]
        OR["Orchestrator<br/><i>synthesis</i>"]
    end

    subgraph CROSS["Cross-cutting"]
        A2A["A2A envelope<br/><i>a2a.py</i>"]
        GR["Guardrails L0/L1/L2"]
        TR["AuditTracer<br/><i>hash-chained</i>"]
        SG["safeguards.py"]
    end

    subgraph DATA["Data & providers"]
        DB[("12 tables")]
        SEED["Seed inventory<br/><i>1568 rows</i>"]
        DUFFEL["Duffel adapter"]
        RET["Web retrieval"]
    end

    UI --> API --> SVC --> JOBS --> GRAPH
    INTAKE --> SVC
    GRAPH --> FL & HT & AC & RA
    FL & HT & AC & RA --> BARRIER --> OR
    FL --> SEED & DUFFEL
    HT --> SEED
    AC --> RET
    AGENTS -.-> A2A & GR & TR
    OR --> SG
    SVC --> DB
    TR --> DB

    style ORCH fill:#1e3a5f,color:#fff
    style CROSS fill:#4a3f6b,color:#fff
```

### The layering rule that matters

Inside Flight and Hotel & Transport there is a further split that is the
system's central design decision (ADR-0002):

```mermaid
graph LR
    REQ["Request"] --> ADPT["adapter.py<br/><i>shared → agent schema</i>"]
    ADPT --> DOM["<b>domain.py</b><br/>pure functions<br/>no I/O, no langchain<br/><i>filters · ranks · decides</i>"]
    DOM --> PROP["Proposal<br/><i>candidates</i>"]
    PROP --> LLM["Model<br/><i>writes prose only</i>"]
    LLM --> VAL["validate_grounded_ids"]
    VAL --> OPT["Options built from<br/><b>candidates</b>, never from<br/>the model's text"]

    style DOM fill:#1e5f3a,color:#fff
    style LLM fill:#4a3f6b,color:#fff
    style OPT fill:#1e5f3a,color:#fff
```

Green is deterministic and reviewable; purple is probabilistic. **Facts only
ever flow through green.** Remove the model and the wording degrades, not the
candidate list.

---

## 3. Process view

One plan, end to end.

```mermaid
sequenceDiagram
    participant C as Browser
    participant A as API
    participant J as Job manager
    participant G as LangGraph
    participant S as 4 specialists
    participant O as Orchestrator
    participant D as Database

    C->>A: POST /api/v1/travel-plans
    A->>A: L0/L1 screening · injection, PII, bias
    A->>J: submit_plan()
    J->>D: INSERT planning_jobs (queued)
    A-->>C: 202 { request_id }

    par Polling
        loop every 2s
            C->>A: GET /status
            A-->>C: processing
        end
    and Planning
        J->>G: invoke(state)
        par Four in parallel
            G->>S: flight
            G->>S: hotel & transport
            G->>S: accessibility
            G->>S: risk & advisory
        end
        Note over S: each: screen input → domain search →<br/>model explains → ground → screen output
        S-->>G: 4 × AgentFinding
        Note over G: barrier — all four required
        G->>O: synthesise
        O->>O: L2 output guardrail + safeguards
        O-->>J: PlanResponse
        J->>D: UPDATE planning_jobs (completed)
    end

    C->>A: GET /status
    A-->>C: completed + plan + trace_url
```

Two things worth reading off this diagram:

- **The client is never blocked.** `202` returns immediately; the work runs on a
  thread and is polled. That is asynchronous request-reply — already built, and
  the reason an asyncio rewrite would add nothing (ADR-0014).
- **The barrier is a data dependency.** The orchestrator needs all four findings
  to synthesise. No concurrency model removes that wait.

---

## 4. Physical view — as deployed today

```mermaid
graph TB
    subgraph CLIENT["Traveller device"]
        BR["Browser"]
    end

    subgraph RENDER["Render — free tier, single region"]
        subgraph SVC["Web service · 1 instance"]
            subgraph PROC["<b>ONE Gunicorn process</b> — workers=1, threads=8"]
                FLASK["Flask app"]
                POOL["ThreadPoolExecutor<br/>max_workers=4"]
                MEM["<b>Module-level dicts</b><br/>_jobs · _cancel_events · _futures<br/><i>⚠ the scaling ceiling</i>"]
                AG["All 5 agents<br/><i>in-process</i>"]
                CACHE["Verdict cache<br/><i>LRU 512</i>"]
            end
            DISK["Ephemeral disk<br/>TRACE_DIR<br/><i>⚠ lost on redeploy</i>"]
        end
    end

    subgraph SUPA["Supabase"]
        PGB["Session pooler<br/><i>IPv4 — required</i>"]
        PG[("Postgres<br/>Travelplanner_schema")]
    end

    EXT["LLM provider · Duffel · Serper"]

    BR -->|HTTPS| FLASK
    FLASK --> POOL --> AG
    POOL <--> MEM
    AG --> CACHE
    AG --> EXT
    AG --> DISK
    FLASK --> PGB --> PG

    style PROC fill:#1e3a5f,color:#fff
    style MEM fill:#5f1e1e,color:#fff
    style DISK fill:#5f4a1e,color:#fff
```

**Read this diagram as the honest one.** Five agents, one process, one instance.
Two constraints are marked in red and amber because they are the two facts an
examiner should hear from you before they find them:

1. **Job state in module-level dicts** caps the system at one worker. A second
   worker answers `GET /status` for jobs it never ran → 404 while the plan runs
   fine in its sibling (ADR-0005).
2. **`TRACE_DIR` is ephemeral.** Trace URLs 404 after a redeploy, though
   `audit_events` still holds the same events (ADR-0006).

The direct Supabase host resolves only to IPv6 and Render cannot reach it — the
session pooler is not a preference, it is the only route that works.

---

## 5. Physical view — target state

What the system becomes **after** the prerequisite is fixed. The prerequisite is
not Kubernetes; it is moving job state into the `planning_jobs` table.

```mermaid
graph TB
    subgraph K8S["Kubernetes cluster"]
        ING["Ingress / Gateway<br/><i>TLS · routing · <b>rate limiting</b></i>"]

        subgraph WEB["Deployment: web · N replicas"]
            W1["Pod: Flask API"]
            W2["Pod: Flask API"]
        end

        subgraph WORK["Deployment: planner · M replicas"]
            K1["Pod: planning worker"]
            K2["Pod: planning worker"]
        end

        subgraph FLT["Deployment: flight-agent"]
            F1["Pod: A2A server<br/><i>scripts/a2a_server.py</i>"]
        end

        SVCD["Service DNS<br/><i>flight-agent.default.svc.cluster.local</i>"]
        Q[["Broker<br/><i>Redis / RabbitMQ</i>"]]
        RC[("Redis<br/><i>shared verdict cache</i>")]
    end

    PG[("Postgres<br/><i>+ planning_jobs as queue</i>")]
    OBJ["Object storage<br/><i>traces — durable</i>"]
    EXT["LLM provider"]

    ING --> W1 & W2
    W1 & W2 -->|enqueue| Q
    Q --> K1 & K2
    K1 & K2 --> SVCD --> F1
    K1 & K2 --> RC
    W1 & W2 & K1 & K2 --> PG
    K1 & K2 --> OBJ
    K1 & K2 & F1 --> EXT

    style ING fill:#1e5f3a,color:#fff
    style SVCD fill:#4a3f6b,color:#fff
```

### What this diagram is claiming, and what it isn't

| Element | Status | Note |
|---|---|---|
| Separate flight-agent service | **Seam already built** | `FLIGHT_AGENT_TRANSPORT=a2a` routes that one agent; `A2A_INTERNAL_ENABLED=true` routes all four, which is what the GKE deployment uses. Same tests pass either way (ADR-0004) |
| One image, three roles | **Follows from the above** | Web, planner and flight-agent are the same image with different commands (ADR-0013) |
| Service discovery via DNS | **Config change only** | `FLIGHT_AGENT_A2A_URL` is already externalised — no discovery client needed |
| Broker | **Not built** | The DB job table is already a queue; a broker earns its place past one node (ADR-0005) |
| Shared Redis cache | **Not built** | Becomes correct the moment workers > 1 (ADR-0010) |
| Rate limiting at ingress | **Not built anywhere** | A real gap today — the first thing a gateway buys |
| Service mesh | **Deliberately absent** | Cardinality: 5 services, one language, one team (ADR-0012) |

The honest summary for a reviewer: **the decomposition boundary is exercised
code, not a hopeful arrow.** Everything else on this diagram is a documented
migration path with a named trigger condition.

---

## 6. Degradation ladder

Every partial failure has a defined, *visible* fallback (ADR-0011).

```mermaid
graph TD
    START["Planning request"] --> Q1{"Guardrail<br/>reachable?"}
    Q1 -->|no| FC["<b>FAIL CLOSED</b><br/>refuse, visibly"]
    Q1 -->|yes| Q2{"Input<br/>clean?"}
    Q2 -->|blocked| BL["Refuse with reason"]
    Q2 -->|ok| Q3{"Inventory<br/>covers route?"}

    Q3 -->|no| P2["<b>Path 2</b><br/>prompt-only guidance<br/><b>all options stripped</b><br/>+ ESTIMATE_WARNING"]
    Q3 -->|yes| Q4{"Provider<br/>credential?"}
    Q4 -->|missing| SEED["Seed inventory<br/>+ visible note"]
    Q4 -->|present| LIVE["Live inventory"]

    SEED & LIVE --> Q5{"Empty leg?"}
    Q5 -->|yes| LOOP["Escalate to tool loop"]
    Q5 -->|no| ONE["Single-shot"]
    LOOP --> Q6{"Budget<br/>exhausted?"}
    Q6 -->|yes| BEST["Best candidates so far<br/><i>agent_budget_exhausted</i>"]
    Q6 -->|no| ONE
    BEST --> ONE

    ONE --> Q7{"Model output<br/>grounded &amp; clean?"}
    Q7 -->|no| RETRY["Retry once"]
    RETRY --> Q8{"Now ok?"}
    Q8 -->|no| DET["<b>Deterministic response</b><br/>no narrative, options intact"]
    Q8 -->|yes| OK
    Q7 -->|yes| OK["Grounded response"]

    DET & OK & P2 --> SYN["Orchestrator synthesis"]
    SYN --> PROV["enforce_provenance_disclosure()"]
    PROV --> PLAN["Plan + limitations"]

    style FC fill:#5f1e1e,color:#fff
    style P2 fill:#5f4a1e,color:#fff
    style DET fill:#1e5f3a,color:#fff
    style PLAN fill:#1e3a5f,color:#fff
```

**Two principles, and the asymmetry between them is the point.**

Everything degrades **open** — a worse answer beats no answer — *except* the
guardrail, which degrades **closed**. A safety control that waves traffic
through when it breaks is not a safety control.

And no degradation is silent. `enforce_provenance_disclosure()` exists because a
warning *was once* paraphrased into something milder during synthesis, and no
unit test caught it — each tested one agent's output, never what the
orchestrator did with it downstream. The discipline is now enforced
structurally, because prose alone demonstrably failed.

---

## 7. Data view

Twelve tables, dual DDL for SQLite and Postgres (ADR-0006).

```mermaid
erDiagram
    users ||--o{ travel_requests : makes
    travel_requests ||--o| travel_plans : produces
    travel_requests ||--o{ planning_jobs : tracked_by
    travel_requests ||--o{ intake_messages : conversation
    travel_plans ||--o{ agent_findings : contains
    agent_findings ||--o{ options : proposes
    travel_requests ||--o{ a2a_messages : exchanges
    travel_requests ||--o{ audit_events : traces
    travel_requests ||--o{ agent_runs : executes
    travel_plans ||--o{ plan_feedback : receives
```

The shape is relational because the assessed question is relational: *which
agent produced this option, in which run, under which request, and what did the
audit chain say at the time.* That is a join, not a document fetch — and it is
why there is no vector store (ADR-0007): grounding here is **exact identifier
membership**, which is decidable, not semantic similarity, which is not.

`audit_events` is the durable record. The JSONL trace files are a debugging
convenience on ephemeral disk, and any aggregation must read the table instead
(ADR-0015).
