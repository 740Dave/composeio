"""ComposeIO — Software-Defined I/O for Hardware Projects.

Bus-oriented composition model using namespace-based pub/sub.
Behavior defined in YAML configuration, not firmware.
"""

__version__ = "0.1.0"

from composeio.bus import ComposeIOBus
from composeio.namespace import Namespace
from composeio.state_engine import (
    StateEngine,
    BinaryEngine,
    TriLevelEngine,
    StopLightEngine,
    ContinuousEngine,
    EnumEngine,
    JsonEngine,
)
from composeio.module import Module
from composeio.app import ComposeApp, AppModule
from composeio.web import WebBusModule
from composeio.transport_replay import ReplayTransport
from composeio.transport_synthetic import SyntheticTransport
from composeio.capture import BusCapture
from composeio.generate import generate_trace

__all__ = [
    "ComposeIOBus",
    "ComposeApp",
    "AppModule",
    "WebBusModule",
    "Namespace",
    "StateEngine",
    "BinaryEngine",
    "TriLevelEngine",
    "StopLightEngine",
    "ContinuousEngine",
    "EnumEngine",
    "JsonEngine",
    "Module",
    "ReplayTransport",
    "SyntheticTransport",
    "BusCapture",
    "generate_trace",
]
