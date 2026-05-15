from __future__ import annotations

from ngo_homesuite.ai.semantic_memory import SemanticMemoryLayer
from ngo_homesuite.domain import DomainRegistry, DonorEntity, ProgramEntity


def test_semantic_memory_retrieves_relevant_entities():
    registry = DomainRegistry()
    registry.upsert(DonorEntity(entity_id="donor:1", name="Acme Foundation Donor", donor_type="foundation"))
    registry.upsert(ProgramEntity(entity_id="program:1", name="Youth Mentorship Program"))

    memory = SemanticMemoryLayer()
    memory.index_registry(registry)

    context = memory.assemble_context("foundation donor follow-up", limit=3)

    assert context["entity_count"] >= 1
    top_names = [item.get("name", "") for item in context["retrieved_entities"]]
    assert any("Donor" in name for name in top_names)
