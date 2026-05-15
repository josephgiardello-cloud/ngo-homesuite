from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StepNode:
    name: str
    terminal: bool = False


@dataclass(frozen=True)
class TransitionRule:
    from_step: str
    event_type: str
    to_step: str


@dataclass(frozen=True)
class WorkflowDefinition:
    workflow_type: str
    initial_step: str
    steps: dict[str, StepNode]
    transitions: list[TransitionRule] = field(default_factory=list)

    def transition_for(self, current_step: str, event_type: str) -> TransitionRule | None:
        for rule in self.transitions:
            if rule.from_step == current_step and rule.event_type == event_type:
                return rule
        return None
