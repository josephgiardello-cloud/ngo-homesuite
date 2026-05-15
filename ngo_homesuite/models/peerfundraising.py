class PeerFundraisingPage:
    def __init__(self, id, owner_donor_id, title, goal_cents, description, created_at, status):
        self.id = id
        self.owner_donor_id = owner_donor_id
        self.title = title
        self.goal_cents = goal_cents
        self.description = description
        self.created_at = created_at
        self.status = status

class PeerFundraisingDonation:
    def __init__(self, id, page_id, donor_id, amount_cents, donated_at):
        self.id = id
        self.page_id = page_id
        self.donor_id = donor_id
        self.amount_cents = amount_cents
        self.donated_at = donated_at
