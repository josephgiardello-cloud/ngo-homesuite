from ngo_homesuite.db.audit_log import log_event

# Service layer for donor operations

class DonorService:
    def get_donor(self, donor_id, actor=None):
        log_event('ngo_data.db', actor or 'system', 'get', 'donor', {'donor_id': donor_id})
        # TODO: Implement donor retrieval logic
        pass

    def create_donor(self, donor_data, actor=None):
        log_event('ngo_data.db', actor or 'system', 'create', 'donor', {'donor_data': donor_data})
        # TODO: Implement donor creation logic
        pass


    def update_donor(self, donor_id, donor_data, actor=None):
        # Prevent direct updates for critical entities (immutable/audited)
        raise NotImplementedError("Direct UPDATE is not allowed for critical entities. Use append-only or soft-delete patterns.")


    def delete_donor(self, donor_id, actor=None):
        # Prevent hard deletes for critical entities (use soft delete only)
        raise NotImplementedError("Direct DELETE is not allowed for critical entities. Use soft-delete pattern.")
