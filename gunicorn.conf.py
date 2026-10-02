"""Gunicorn settings for the web role that the command line cannot express.

Loaded explicitly by the Dockerfile's `--config`; the bind, worker and thread
settings stay on that command line, where they have always been.
"""

# Let running plans finish when the pod is asked to stop (a deploy, or the
# autoscaler removing a pod). On SIGTERM gunicorn stops accepting requests and
# waits this long before killing the worker; the worker's exit joins jobs.py's
# plan threads, so a plan in progress completes and records its result. A
# plan takes ~3.5 min on GKE (206 s and 247 s measured). Must stay below the
# pod's terminationGracePeriodSeconds minus its preStop sleep (web.yaml).
graceful_timeout = 300


def post_worker_init(worker):
    """Publish this worker's plans in flight for the autoscaler (metrics.py).

    In the worker, not the master: the count lives in the worker's jobs.py.
    """
    from flaskapp.metrics import start_metrics_server
    from flaskapp.travel_ai.jobs import plans_in_flight

    start_metrics_server({
        "travel_planner_plans_in_flight": (
            "Plans this pod is running or has queued", plans_in_flight,
        ),
    })
