# Autoscaling proof — 2026-10-02

**Result: PASS.** Both roles scaled out 1 → 3 pods under load and back in to 1
afterwards. All 18 plans completed; **no status poll returned 404**, which
before 2026-10-02 (PR #48) was exactly how a second web pod broke plans.

## What was tested

`deploy/scaling_demo.py`, run against the production GKE cluster in a throwaway
namespace (`travel-planner-loadtest`, deleted afterwards). It runs
**production's own manifests** — the same Deployments, Services, autoscalers
(`deploy/k8s/hpa.yaml`), NetworkPolicy and PodMonitoring, referenced rather
than copied (`deploy/k8s-loadtest/kustomization.yaml`; a test enforces this).
Only three things differ: a disposable Postgres, config pointing at it, and a
**fake model** (`scripts/ci_stub_provider.py`) that waits 30 s per reply so a
plan takes ~3 minutes, like a real one, at zero model cost.

The load generator ran **inside the cluster** and went through the web
Service, so new pods could receive traffic (`kubectl port-forward` would pin
everything to one pod). Plans arrived in **3 waves of 6, 150 s apart**,
because a plan queued on a pod stays there; only later arrivals can use pods
the autoscaler adds.

Rerun it with `python deploy/scaling_demo.py --report <file>` (~25 min).

## What it shows

| Question | Answer from this run |
|---|---|
| Does it scale out under load? | Yes: web 1 → 3, agents 1 → 2 → 3 |
| On what signal? | **Both designed triggers fired.** The login burst at the start of each wave is CPU-bound (scrypt password hashing on a 100m pod), and CPU scaled web to 3 and agents to 2 within 40 s. Then the **A2A calls-in-flight gauge** scaled agents 2 → 3 (16:25:10, "pods metric … above target") |
| Did any plan break across pods? | No: 18/18 completed, 0 × 404 on status, plans spread over three web pods |
| Does it scale back in? | Yes, slowly as designed: after the 10-minute window, one pod at a time, 3 → 2 → 1 for both roles |
| Does scale-down cut plans off? | None was running by then; the drain itself is covered by tests (PR #49) |

## Honest caveats

- **The web plans gauge never had to act on its own**, because CPU had already
  scaled web to its maximum of 3 during the first login burst. The gauge was
  live and correct throughout, and did read above target (3.67 per pod at
  179 s, against 3) while web was already at the ceiling. A run that logs
  every session in first, then submits, would isolate it.
- **Uneven spread across web pods (8 / 2 / 8).** The Service balances per
  *connection*, and each simulated traveller keeps one connection, so where a
  traveller lands depends on when its connection opened.
- **`poll: ConnectionError` lines** are the client reusing a keep-alive
  connection gunicorn had just closed (its 2 s idle timeout equals the poll
  interval). The next poll succeeded every time; browsers retry these
  silently. No plan was affected.
- **Latency p95 321 s vs median 172 s**: plans queued behind a full pod
  (4 at a time) wait for a slot. That is the cost scaling out is meant to cut.
- A fake model removes model-side variance and cost; it does not exercise
  OpenAI rate limits, which a real burst would also meet.

---

## Raw report, as generated

Image `app:ef49e72` · 18 plans in 3 waves 150s apart · fake model 30s per reply · namespace `travel-planner-loadtest`
**PASS** · peak pods: web 3, agents 3 · after cooldown: web 1, agents 1


## What the autoscalers saw and did (every 15 s)

```
    2s  |  web:     ? per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
   20s  |  web:     0 per pod, desired 3, pods 1  |  agents:     0 per pod, desired 1, pods 1
   37s  |  web:     0 per pod, desired 3, pods 3  |  agents:    12 per pod, desired 2, pods 1
   55s  |  web:     2 per pod, desired 3, pods 3  |  agents:     6 per pod, desired 2, pods 2
   73s  |  web:     2 per pod, desired 3, pods 3  |  agents:     6 per pod, desired 2, pods 2
   91s  |  web:     2 per pod, desired 3, pods 3  |  agents:     6 per pod, desired 2, pods 2
  108s  |  web:     2 per pod, desired 3, pods 3  |  agents:     2 per pod, desired 2, pods 2
  126s  |  web:     2 per pod, desired 3, pods 3  |  agents:     1 per pod, desired 2, pods 2
  144s  |  web:     2 per pod, desired 3, pods 3  |  agents:     2 per pod, desired 2, pods 2
  162s  |  web:     2 per pod, desired 3, pods 3  |  agents:     1 per pod, desired 2, pods 2
  179s  |  web: 3.67 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 2, pods 2
  197s  |  web: 3.67 per pod, desired 3, pods 3  |  agents:     2 per pod, desired 2, pods 2
  215s  |  web: 2.67 per pod, desired 3, pods 3  |  agents:    12 per pod, desired 3, pods 2
  232s  |  web: 2.67 per pod, desired 3, pods 3  |  agents:    12 per pod, desired 3, pods 3
  251s  |  web: 2.67 per pod, desired 3, pods 3  |  agents: 11.5 per pod, desired 3, pods 3
  268s  |  web: 2.67 per pod, desired 3, pods 3  |  agents: 5.33 per pod, desired 3, pods 3
  287s  |  web: 2.67 per pod, desired 3, pods 3  |  agents: 3.33 per pod, desired 3, pods 3
  305s  |  web: 2.67 per pod, desired 3, pods 3  |  agents: 3.33 per pod, desired 3, pods 3
  322s  |  web: 4.33 per pod, desired 3, pods 3  |  agents:     3 per pod, desired 3, pods 3
  340s  |  web:     4 per pod, desired 3, pods 3  |  agents: 2.33 per pod, desired 3, pods 3
  358s  |  web: 2.33 per pod, desired 3, pods 3  |  agents: 3.67 per pod, desired 3, pods 3
  375s  |  web:     2 per pod, desired 3, pods 3  |  agents:     5 per pod, desired 3, pods 3
  393s  |  web:     2 per pod, desired 3, pods 3  |  agents:     4 per pod, desired 3, pods 3
  410s  |  web:     2 per pod, desired 3, pods 3  |  agents:     4 per pod, desired 3, pods 3
  428s  |  web:     2 per pod, desired 3, pods 3  |  agents: 1.67 per pod, desired 3, pods 3
  446s  |  web:     2 per pod, desired 3, pods 3  |  agents:     1 per pod, desired 3, pods 3
  464s  |  web:     2 per pod, desired 3, pods 3  |  agents:     1 per pod, desired 3, pods 3
  481s  |  web:     2 per pod, desired 3, pods 3  |  agents:  0.67 per pod, desired 3, pods 3
  499s  |  web: 1.67 per pod, desired 3, pods 3  |  agents:  0.67 per pod, desired 3, pods 3
  517s  |  web:  0.33 per pod, desired 3, pods 3  |  agents:     1 per pod, desired 3, pods 3
  535s  |  web:  0.33 per pod, desired 3, pods 3  |  agents:  0.33 per pod, desired 3, pods 3
  552s  |  web:  0.33 per pod, desired 3, pods 3  |  agents:  0.33 per pod, desired 3, pods 3
  570s  |  web:  0.33 per pod, desired 3, pods 3  |  agents:  0.33 per pod, desired 3, pods 3
  588s  |  web:  0.33 per pod, desired 3, pods 3  |  agents:  0.33 per pod, desired 3, pods 3
  606s  |  web:  0.33 per pod, desired 3, pods 3  |  agents:  0.33 per pod, desired 3, pods 3
  624s  |  web:  0.33 per pod, desired 3, pods 3  |  agents:  0.33 per pod, desired 3, pods 3
  642s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  659s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  677s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  695s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  713s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  730s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  748s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  766s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  784s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  803s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  820s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  838s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  856s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 3, pods 3
  874s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 2, pods 3
  892s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 2, pods 2
  910s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 2, pods 2
  927s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 2, pods 2
  945s  |  web:     0 per pod, desired 3, pods 3  |  agents:     0 per pod, desired 2, pods 2
  963s  |  web:     0 per pod, desired 2, pods 3  |  agents:     0 per pod, desired 2, pods 2
  981s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
  998s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1016s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1034s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1051s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1069s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1087s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1105s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1122s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1140s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1158s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 2, pods 2
 1176s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 1, pods 2
 1194s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 1, pods 1
 1211s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 1, pods 1
 1229s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 1, pods 1
 1247s  |  web:     0 per pod, desired 2, pods 2  |  agents:     0 per pod, desired 1, pods 1
 1264s  |  web:     0 per pod, desired 1, pods 2  |  agents:     0 per pod, desired 1, pods 1
 1282s  |  web:     0 per pod, desired 1, pods 2  |  agents:     0 per pod, desired 1, pods 1
 1300s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1318s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1336s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1354s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1372s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1389s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1407s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1425s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1443s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1460s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
 1478s  |  web:     0 per pod, desired 1, pods 1  |  agents:     0 per pod, desired 1, pods 1
```

## Scaling decisions (Kubernetes events)

```
2026-10-02T16:22:04Z  web     New size: 3; reason: cpu resource utilization (percentage of request) above target
2026-10-02T16:22:19Z  agents  New size: 2; reason: cpu resource utilization (percentage of request) above target
2026-10-02T16:25:10Z  agents  New size: 3; reason: pods metric prometheus.googleapis.com|travel_planner_a2a_calls_in_flight|gauge above target
2026-10-02T16:36:18Z  agents  New size: 2; reason: cpu resource utilization (percentage of request) below target
2026-10-02T16:37:51Z  web     New size: 2; reason: cpu resource utilization (percentage of request) below target
2026-10-02T16:41:23Z  agents  New size: 1; reason: cpu resource utilization (percentage of request) below target
2026-10-02T16:42:55Z  web     New size: 1; reason: cpu resource utilization (percentage of request) below target
```

## Which web pod ran which plans

```
web_pod        |  status   | plans 
----------------------+-----------+-------
 web-68dccb6568-4c7c7 | completed |     8
 web-68dccb6568-87727 | completed |     2
 web-68dccb6568-b6ltb | completed |     8
(3 rows)
```

## The load generator's verdict

```
submitting 18 plans in 3 wave(s) of [6, 6, 6], 150s apart, against http://web

[    0s] wave 1: 6 plans
[  150s] wave 2: 6 plans
[  300s] wave 3: 6 plans

plan  wave  status        submit    total  polls  404s  request_id
0     1     completed       0.2s   164.5s     79     0  94b99fa1-cacc-4b0a-bd56-9f6908bfae31
        ! poll: ConnectionError
        ! poll: ConnectionError
1     1     completed       0.3s   350.5s    168     0  04267d29-517a-4d65-a06b-db602173dfe7
        ! poll: ConnectionError
2     1     completed       0.2s   320.6s    154     0  4e5a49db-966f-43cb-8831-e68bcde1fc61
        ! poll: ConnectionError
        ! poll: ConnectionError
3     1     completed       0.2s   163.7s     78     0  6e76c0a5-7501-4f96-be29-7add24e770b3
        ! poll: ConnectionError
4     1     completed       0.1s   162.6s     78     0  6b82ff14-83ec-4dc4-8aab-c2952b5f9be0
        ! poll: ConnectionError
5     1     completed       0.2s   162.6s     78     0  df4fd97f-38db-4690-aa55-7a513a2db33c
        ! poll: ConnectionError
6     2     completed       0.2s   169.7s     81     0  09ae6e3e-41a8-44e5-afbe-c5e0c14afee9
        ! poll: ConnectionError
7     2     completed       0.2s   172.0s     82     0  2590813b-4fce-4751-9d0d-f3905ca11326
        ! poll: ConnectionError
8     2     completed       0.1s   171.8s     81     0  fb69f0d7-bb3e-4685-9e3f-05932aacd6fa
9     2     completed       0.1s   172.0s     82     0  8e1d7dd6-7821-4372-82d5-8e5fc998f74f
10    2     completed       0.2s   171.5s     81     0  40164fba-e5ec-4c41-99b3-b7f794991a63
11    2     completed       0.2s   169.7s     80     0  ddf5755e-49df-4541-bda3-69e78d3067e8
12    3     completed       0.2s   177.1s     86     0  d14a1da8-2ea9-4f77-a303-50fa9af6b386
        ! poll: ConnectionError
13    3     completed       0.3s   176.5s     86     0  78dd5f67-04c3-47f0-a2fc-8db35222d5f1
        ! poll: ConnectionError
14    3     completed       0.2s   306.3s    149     0  edd80767-a4e5-4269-8e0f-018db683b4a8
15    3     completed       0.2s   174.5s     85     0  3d9646d9-6df4-4a53-b4a2-26e90f2c5487
16    3     completed       0.1s   153.8s     75     0  ec061f35-3341-4382-b64c-a8aad362010e
17    3     completed       0.2s   155.0s     75     0  4dbac62d-d6a5-4cf4-8126-1bab8ec528fd

completed      18/18
latency        min 153.8s | median 171.6s | p95 320.6s | max 350.5s
submit p95     0.3s
wall clock     608.5s for all 18
404 on status  0   <- must be 0; anything else is ADR-0005 biting

PASS
```
