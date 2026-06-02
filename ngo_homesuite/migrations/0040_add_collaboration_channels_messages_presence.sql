-- 0040_add_collaboration_channels_messages_presence.sql
-- Adds collaboration channels, channel members, messages, and user presence snapshots.

CREATE TABLE IF NOT EXISTS collaboration_channels (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    channel_type TEXT NOT NULL DEFAULT 'team', -- team, direct
    name TEXT,
    created_by_user_id INTEGER,
    is_archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (created_by_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_collab_channels_org ON collaboration_channels(organization_id);
CREATE INDEX IF NOT EXISTS idx_collab_channels_type ON collaboration_channels(channel_type);
CREATE INDEX IF NOT EXISTS idx_collab_channels_archived ON collaboration_channels(is_archived);

CREATE TABLE IF NOT EXISTS collaboration_channel_members (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'member', -- owner, member
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_read_at TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (channel_id) REFERENCES collaboration_channels(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE (organization_id, channel_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_collab_members_org_user ON collaboration_channel_members(organization_id, user_id);
CREATE INDEX IF NOT EXISTS idx_collab_members_org_channel ON collaboration_channel_members(organization_id, channel_id);

CREATE TABLE IF NOT EXISTS collaboration_messages (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    sender_user_id INTEGER NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    edited_at TEXT,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (channel_id) REFERENCES collaboration_channels(id),
    FOREIGN KEY (sender_user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_collab_messages_org_channel ON collaboration_messages(organization_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_collab_messages_created_at ON collaboration_messages(created_at);

CREATE TABLE IF NOT EXISTS collaboration_presence (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'offline', -- online, away, dnd, offline
    status_message TEXT,
    last_seen_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE (organization_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_collab_presence_org_user ON collaboration_presence(organization_id, user_id);
CREATE INDEX IF NOT EXISTS idx_collab_presence_status ON collaboration_presence(status);
