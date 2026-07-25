"""Agent registry used by the LangGraph workflow."""

from .accessibility_agent import NAME as ACCESSIBILITY_NAME, create_node as create_accessibility_node
from .flight_agent import NAME as FLIGHT_NAME, create_node as create_flight_node
from .hotel_transport_agent import NAME as HOTEL_TRANSPORT_NAME, create_node as create_hotel_transport_node
from .orchestrator_agent import create_node as create_orchestrator_node
from .risk_advisory_agent import NAME as RISK_ADVISORY_NAME, create_node as create_risk_advisory_node
from .accessibility_agent.prompt import INSTRUCTION as ACCESSIBILITY_INSTRUCTION
from .flight_agent.prompt import INSTRUCTION as FLIGHT_INSTRUCTION
from .hotel_transport_agent.prompt import INSTRUCTION as HOTEL_TRANSPORT_INSTRUCTION
from .risk_advisory_agent.prompt import INSTRUCTION as RISK_ADVISORY_INSTRUCTION

SPECIALIST_NODE_FACTORIES = {
    FLIGHT_NAME: create_flight_node,
    HOTEL_TRANSPORT_NAME: create_hotel_transport_node,
    ACCESSIBILITY_NAME: create_accessibility_node,
    RISK_ADVISORY_NAME: create_risk_advisory_node,
}

SPECIALIST_INSTRUCTIONS = {
    FLIGHT_NAME: FLIGHT_INSTRUCTION,
    HOTEL_TRANSPORT_NAME: HOTEL_TRANSPORT_INSTRUCTION,
    ACCESSIBILITY_NAME: ACCESSIBILITY_INSTRUCTION,
    RISK_ADVISORY_NAME: RISK_ADVISORY_INSTRUCTION,
}

__all__ = [
    "SPECIALIST_INSTRUCTIONS",
    "SPECIALIST_NODE_FACTORIES",
    "create_orchestrator_node",
]
