import json

from flaskapp.travel_ai.tracing import AuditTracer, verify_hash_chain


def test_verify_hash_chain_true_for_untampered_log(tmp_path):
    tracer = AuditTracer(tmp_path, "req-1")
    tracer.record("agent_started", "flight_agent")
    tracer.record("agent_completed", "flight_agent", {"option_count": 2})
    assert verify_hash_chain(tracer.path) is True


def test_verify_hash_chain_false_when_event_body_tampered(tmp_path):
    tracer = AuditTracer(tmp_path, "req-2")
    tracer.record("agent_started", "flight_agent")
    tracer.record("agent_completed", "flight_agent", {"option_count": 2})

    lines = tracer.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[-1])
    tampered["details"] = {"option_count": 999}  # hash no longer matches
    lines[-1] = json.dumps(tampered)
    tracer.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert verify_hash_chain(tracer.path) is False


def test_verify_hash_chain_false_when_reordered(tmp_path):
    tracer = AuditTracer(tmp_path, "req-3")
    tracer.record("agent_started", "flight_agent")
    tracer.record("agent_completed", "flight_agent")

    lines = tracer.path.read_text(encoding="utf-8").splitlines()
    reordered = "\n".join([lines[1], lines[0]]) + "\n"
    tracer.path.write_text(reordered, encoding="utf-8")

    assert verify_hash_chain(tracer.path) is False
