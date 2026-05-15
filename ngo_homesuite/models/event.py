from typing import Optional

class Event:
    def __init__(self, name: str, event_type: str, date: str, location: str, description: Optional[str] = None):
        self.name = name
        self.event_type = event_type
        self.date = date
        self.location = location
        self.description = description

class Registration:
    def __init__(self, event_id: int, donor_id: int, registered_at: str):
        self.event_id = event_id
        self.donor_id = donor_id
        self.registered_at = registered_at
