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


def test_semantic_memory_organization_filter_blocks_cross_tenant_nodes():
    registry_org1 = DomainRegistry()
    registry_org1.upsert(DonorEntity(entity_id="donor:org1", name="Org1 Donor", donor_type="individual"))

    registry_org2 = DomainRegistry()
    registry_org2.upsert(DonorEntity(entity_id="donor:org2", name="Org2 Donor", donor_type="individual"))

    memory = SemanticMemoryLayer()
    memory.index_registry(registry_org1, organization_id=1)
    memory.index_registry(registry_org2, organization_id=2)

    org1_context = memory.assemble_context("donor", limit=10, organization_id=1)
    names = [item.get("name") for item in org1_context["retrieved_entities"]]

    assert "Org1 Donor" in names
    assert "Org2 Donor" not in names
