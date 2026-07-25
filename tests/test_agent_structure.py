from flaskapp.travel_ai.agents import SPECIALIST_INSTRUCTIONS, SPECIALIST_NODE_FACTORIES


EXPECTED_SPECIALISTS = {
    "flight_agent",
    "hotel_transport_agent",
    "accessibility_agent",
    "risk_advisory_agent",
}


def test_every_specialist_has_an_execution_module_and_prompt():
    assert set(SPECIALIST_NODE_FACTORIES) == EXPECTED_SPECIALISTS
    assert set(SPECIALIST_INSTRUCTIONS) == EXPECTED_SPECIALISTS


def test_governance_prompts_express_veto_and_escalation_rules():
    assert "VETO" in SPECIALIST_INSTRUCTIONS["accessibility_agent"]
    assert "ESCALATE:" in SPECIALIST_INSTRUCTIONS["risk_advisory_agent"]


def test_each_agent_package_contains_agent_and_prompt_modules():
    for name in EXPECTED_SPECIALISTS | {"orchestrator_agent"}:
        package = __import__(f"flaskapp.travel_ai.agents.{name}", fromlist=["agent", "prompt"])
        assert package.agent
        assert package.prompt
