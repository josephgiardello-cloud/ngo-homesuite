from __future__ import annotations

from ngo_homesuite.domain import CampaignEntity, DomainRegistry, DonorEntity, LifecycleState


def test_domain_entity_lifecycle_and_links():
    donor = DonorEntity(entity_id="donor:1", name="Alice")
    donor.transition(LifecycleState.active, actor="tester", reason="onboarded")
    donor.link("supports_campaign", "campaign:1", actor="tester")

    assert donor.lifecycle_state == LifecycleState.active
    assert donor.relationships["supports_campaign"] == ["campaign:1"]
    assert len(donor.audit_trail) == 2


def test_domain_registry_snapshot_contains_entities():
    registry = DomainRegistry()
    donor = DonorEntity(entity_id="donor:9", name="Donor Nine")
    campaign = CampaignEntity(entity_id="campaign:1", name="Year End Drive")
    registry.upsert(donor)
    registry.upsert(campaign)
    linked = registry.link("donor:9", "supports_campaign", "campaign:1", actor="tester")

    snapshot = registry.snapshot()
    assert linked is True
    assert "donor:9" in snapshot
    assert "campaign:1" in snapshot
    assert snapshot["donor:9"]["relationships"]["supports_campaign"] == ["campaign:1"]
