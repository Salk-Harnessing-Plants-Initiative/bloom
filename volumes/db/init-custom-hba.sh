#!/bin/bash
set -e

HBA_PATH="/var/lib/postgresql/data/pg_hba.conf"

echo ">>> [INIT] Customizing pg_hba.conf to use scram-sha-256..."

# Overwrite pg_hba.conf with your preferred authentication rules
cat > "$HBA_PATH" <<'EOF'
# TYPE  DATABASE        USER            ADDRESS                 METHOD

# Require password-based auth everywhere (no peer/trust)
local   all             all                                     scram-sha-256
host    all             all             0.0.0.0/0               scram-sha-256
host    all             all             ::/0                    scram-sha-256

# Replication connections
local   replication     all                                     scram-sha-256
host    replication     all             127.0.0.1/32            scram-sha-256
host    replication     all             ::1/128                 scram-sha-256
EOF

# Ensure permissions are correct
chown postgres:postgres "$HBA_PATH"
chmod 600 "$HBA_PATH"

echo ">>> [INIT] Custom pg_hba.conf applied successfully!"

