# Physical architecture — GKE, as deployed

Where the code actually runs, as of **2026-09-19**. This is the deployed
reality, not a target state: every box below exists and can be listed with
`kubectl`. The Render deployment is unchanged and still live; the two share a
Supabase project but not a schema.

Slots into [`architecture.md`](architecture.md) as a third physical view,
between §4 (Render, one process) and §5 (target state). Icons are deliberately
left out — the component table at the end has a blank column for them.

---

## 1. The diagram

```mermaid
graph TB
    subgraph USERS["People"]
        DEV["Developer browser<br/>6 allow-listed Google accounts"]
    end

    subgraph GOOGLE["Google Cloud — configured project"]

        subgraph EDGE["Global edge"]
            IP["Static IP 8.232.99.251<br/><i>travel-planner-ip</i>"]
            LB["External HTTPS Load Balancer<br/><i>Ingress: gce</i>"]
            CERT["Managed certificate<br/><i>8-232-99-251.sslip.io</i>"]
            IAP["<b>Identity-Aware Proxy</b><br/>Google sign-in +<br/>roles/iap.httpsResourceAccessor"]
        end

        subgraph AR["Artifact Registry — us-west1"]
            IMG["travel-planner/app:v3<br/><i>one image, two roles</i>"]
        end

        subgraph GKE["GKE Autopilot cluster: travel-planner · us-west1"]
            subgraph NS["Namespace: travel-planner"]

                SVCW["Service/web<br/><i>ClusterIP :80 → :8000</i><br/>NEG, BackendConfig"]

                subgraph DW["Deployment/web · replicas 1 · Recreate"]
                    WEB["<b>Pod: web</b><br/>gunicorn · Flask<br/>REST API · job manager<br/>orchestrator synthesis<br/>L2 guardrails<br/><br/>⚠ _jobs dict in memory<br/>⚠ TRACE_DIR on ephemeral disk"]
                end

                NP["<b>NetworkPolicy</b><br/>agents-allow-web-only<br/><i>app=web → :8000 only</i>"]

                SVCA["Service/agents<br/><i>ClusterIP :8000</i><br/>internal only"]

                subgraph DA["Deployment/agents · replicas 1 · Recreate"]
                    AGT["<b>Pod: agents</b><br/>uvicorn · A2A 1.x JSON-RPC<br/>flight · hotel_transport<br/>accessibility · risk_advisory<br/><br/>⚠ InMemoryTaskStore"]
                end

                CM["ConfigMap app-config"]
                SEC["Secret app-secrets<br/>Secret iap-oauth"]
                PDB["PodDisruptionBudgets<br/><i>minAvailable 1 each</i>"]
            end

            NODES["Autopilot nodes<br/><i>2 pools, auto-provisioned</i>"]
        end

        subgraph OPS["Operations"]
            MON["Cloud Monitoring<br/>4 alert policies → email"]
            LOG["Cloud Logging<br/><i>pod stdout</i>"]
            BUD["Billing budget<br/>SGD 100 · 25/50/90/100%"]
        end
    end

    subgraph EXT["Outside Google Cloud"]
        PGB["Supabase session pooler<br/><i>IPv4 · us-west-2 Oregon</i>"]
        PG[("Postgres<br/>schema travelplanner_gke<br/><i>Render's schema untouched</i>")]
        OAI["OpenAI<br/><i>gpt-5 · gpt-5-mini</i><br/>separate key, hard cap"]
        WEBS["Serper / Duffel<br/><i>optional</i>"]
    end

    DEV -->|"HTTPS 443"| IP --> LB
    CERT -.->|terminates TLS| LB
    LB --> IAP
    IAP -->|"only if signed in AND allow-listed"| SVCW
    SVCW --> WEB
    IMG -.->|"image pull"| WEB
    IMG -.->|"image pull"| AGT
    CM -.-> WEB
    CM -.-> AGT
    SEC -.-> WEB
    SEC -.-> AGT
    WEB -->|"A2A JSON-RPC<br/>http://agents:8000"| SVCA
    NP -.->|"enforced by Dataplane V2"| SVCA
    SVCA --> AGT
    WEB -->|SQL| PGB --> PG
    AGT -->|SQL| PGB
    WEB -->|HTTPS| OAI
    AGT -->|HTTPS| OAI
    AGT -.-> WEBS
    WEB -.-> LOG
    AGT -.-> LOG
    LOG --> MON
    NODES -.-> DW
    NODES -.-> DA
    PDB -.->|"blocks autoscaler eviction"| DW
    PDB -.-> DA

    style IAP fill:#1e5f3a,color:#fff
    style WEB fill:#1e3a5f,color:#fff
    style AGT fill:#1e3a5f,color:#fff
    style NP fill:#4a3f6b,color:#fff
    style IMG fill:#5f4a1e,color:#fff
```

---

## 2. The same thing as a box drawing

For slides, where mermaid is inconvenient and icons go on top.

```
                         Developer browser  (6 allow-listed Google accounts)
                                     |
                                     |  HTTPS 443
                                     v
   ============================ GOOGLE CLOUD =================================
   |                                                                         |
   |   8.232.99.251  ->  External HTTPS LB  ->  [ IAP: Google sign-in ]      |
   |   (static IP)       (managed cert,          + IAM allow-list            |
   |                      HTTP->HTTPS)           ------------------          |
   |                                                    |                    |
   |   +--------------------- GKE Autopilot · us-west1 --|-----------------+  |
   |   |          namespace: travel-planner              v                 |  |
   |   |                                        Service/web  :80           |  |
   |   |                                               |                   |  |
   |   |   Artifact Registry                   +-------v--------+          |  |
   |   |   app:v3  -- pull -->                 |   POD: web     |          |  |
   |   |       |                               |   gunicorn     |          |  |
   |   |       |                               |   Flask API    |          |  |
   |   |       |                               |   job manager  |          |  |
   |   |       |                               |   orchestrator |          |  |
   |   |       |                               +-------+--------+          |  |
   |   |       |                                       |                   |  |
   |   |       |                     A2A JSON-RPC over http://agents:8000  |  |
   |   |       |                            [ NetworkPolicy: web only ]    |  |
   |   |       |                                       |                   |  |
   |   |       |                               Service/agents :8000        |  |
   |   |       |                                       |                   |  |
   |   |       |                               +-------v--------+          |  |
   |   |       +----------- pull ------------> |  POD: agents   |          |  |
   |   |                                       |  uvicorn       |          |  |
   |   |                                       |  4 specialists |          |  |
   |   |                                       +-------+--------+          |  |
   |   +-----------------------------------------------|-----------------+ |  |
   |                                                    |                   |
   |   Cloud Monitoring (4 alerts)  ·  Cloud Logging  ·  Budget SGD 100     |
   ==========================================|===============================
                                             |
              +------------------------------+-----------------------------+
              |                              |                             |
              v                              v                             v
   Supabase session pooler          OpenAI  gpt-5 / gpt-5-mini      Serper / Duffel
   schema travelplanner_gke         (separate key, hard cap)        (optional)
   (Render's schema untouched)
```

---

## 3. Components

The icon column is left blank on purpose.

| Icon | Component | Kind | Where | Count |
|---|---|---|---|---|
| | Static IP `travel-planner-ip` | Google Cloud | global | 1 |
| | External HTTPS load balancer | `Ingress` (gce) | global | 1 |
| | Managed certificate | `ManagedCertificate` | global | 1 |
| | HTTP → HTTPS redirect | `FrontendConfig` | global | 1 |
| | Identity-Aware Proxy | `BackendConfig` + IAM | global | 1 |
| | Cluster `travel-planner` | GKE Autopilot | `us-west1` | 1 |
| | Nodes | auto-provisioned | 2 pools | 4 |
| | `web` | `Deployment` + `Service` | namespace | 1 pod |
| | `agents` | `Deployment` + `Service` | namespace | 1 pod |
| | `agents-allow-web-only` | `NetworkPolicy` | namespace | 1 |
| | `app-config` | `ConfigMap` | namespace | 1 |
| | `app-secrets`, `iap-oauth` | `Secret` | namespace | 2 |
| | PodDisruptionBudgets | `PDB` | namespace | 2 |
| | `travel-planner/app:v3` | container image | Artifact Registry `us-west1` | 1 image, 2 roles |
| | Alert policies | Cloud Monitoring | project | 4 |
| | Budget | Cloud Billing | account | 1 |
| | Postgres | Supabase (external) | `us-west-2` | schema `travelplanner_gke` |
| | Model provider | OpenAI (external) | — | `gpt-5`, `gpt-5-mini` |

---

## 4. Reading the diagram

**One image, two roles.** `web` and `agents` are the same bytes from Artifact
Registry started with different commands. That is ADR-0013's decision, and it is
why a version can never be half-deployed: both roles move together or neither
does.

**The only public door is IAP.** There is no route to `agents` from outside the
cluster and no second Ingress. A request reaches Flask only after Google sign-in
*and* an IAM check — including `/register` and the login page, which is what
makes an open sign-up form acceptable here for now.

**The NetworkPolicy is load-bearing, not decoration.** The A2A endpoints have no
authentication of their own, and every call spends the provider key. The policy
is what makes `A2A_TRUST_CALLER_REQUEST_ID=true` safe on the agents role, since
a trusted request id is a write key into another plan's audit trail.

**Two warnings are drawn in, not hidden.** `_jobs` in pod memory and
`InMemoryTaskStore` are why both Deployments say `replicas: 1`. See
[ADR-0017](adr/0017-fix-data-location-before-adding-replicas.md).

**Both roles talk to Postgres and to OpenAI.** The agents pod is not a pure
compute worker — specialists write their own audit events and make their own
model calls. That is why the connection-per-call problem is counted across both
pods, not just `web`.

---

## 5. What is proved, and by what

Every arrow above has something that checks it, so the diagram is not a drawing
of intent.

| Claim in the diagram | Proved by |
|---|---|
| Two roles, one image, two commands | `python deploy/prove_topology.py` |
| Separate pods, IPs, resource accounting | same |
| `agents` has no public door | same (Ingress backends) |
| NetworkPolicy admits `web` only | same |
| `web` is wired to the Service, not localhost | same |
| Specialists really answer over the network | `python deploy/smoke_test.py` |
| Restarting `agents` does not touch `web` | `prove_topology.py --restart-agents` |
| The platform will run two pods | `prove_topology.py --scale web=2` |
| A session works on either pod | `prove_topology.py --scale web=2 --session-portability` |
| Plans do **not** yet survive two pods | `deploy/load_test.py --plans 2` at 2 replicas — expected to FAIL today |
