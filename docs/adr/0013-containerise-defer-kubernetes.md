# ADR 0013: Containerise now, defer Kubernetes

**Status:** Proposed — the Dockerfile does not exist yet

## Context

The application deploys today as a Render Python service: `pip install -r
requirements.txt`, then Gunicorn. Render builds the environment from a
buildpack, so the runtime is described by `render.yaml` rather than by an image.

A common misreading is worth correcting first: **all five agents run in one
process.** A container image is the unit of deployment for the *application*,
not for an agent. There is no sense in which four agents "lack" a Dockerfile
that a fifth has. One image covers all five.

## Options

1. **Stay on the buildpack.** Zero work.
2. **Add a Dockerfile.** One image, two possible start commands.
3. **Dockerfile + Kubernetes manifests**, one deployment per agent.

## Decision

Option 2 now. Option 3 documented as target state, not built.

### Why one image serves both topologies

`FLIGHT_AGENT_TRANSPORT` already selects in-process or remote at composition
time ([ADR 0004](0004-composition-time-binding.md)). The same image therefore
runs either shape, chosen by command and environment:

```
# Monolith (today)
CMD gunicorn "flaskapp:create_app()" --workers 1 --threads 8

# Decomposed: the planner, with the flight agent remote
ENV FLIGHT_AGENT_TRANSPORT=a2a
ENV FLIGHT_AGENT_A2A_URL=http://flight-agent:8000/a2a/flight_agent
CMD gunicorn "flaskapp:create_app()" ...

# Decomposed: the flight agent as a service — same image
CMD python scripts/a2a_server.py
```

**Not a per-agent image. One image, three roles.** That is a direct consequence
of the composition-time seam already being built and tested.

### Why not Kubernetes

Kubernetes buys horizontal pod autoscaling, self-healing, rolling deploys and
DNS service discovery. The first two are the point — and
[ADR 0005](0005-single-worker-in-process-queue.md) says this application
**cannot horizontally scale today**, because job state lives in module-level
dicts. Kubernetes would faithfully schedule replicas that answer 404 for each
other's jobs.

Scheduling more replicas of a thing that cannot be replicated is not
scalability. **The prerequisite is a data-location fix, not an orchestrator.**

## Consequences

- The build becomes reproducible and independent of Render's buildpack; the
  Python version stops being an environment variable in a YAML file.
- The K8s argument gains credibility: the image exists, the transport seam is
  exercised, and the blocker is named precisely.
- Discovery needs no new component. `FLIGHT_AGENT_A2A_URL` becomes
  `http://flight-agent.default.svc.cluster.local:8000/...` — **a value change,
  not a code change** ([ADR 0004](0004-composition-time-binding.md)).
- A Dockerfile is one more file to keep in step with `requirements.txt`.

**Revisit Kubernetes when:** (1) job state has moved into the `planning_jobs`
table, (2) more than one node is genuinely needed, and (3) someone owns cluster
operations. All three, not any one.
