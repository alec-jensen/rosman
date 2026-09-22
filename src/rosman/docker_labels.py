"""Names of Docker labels used by rosman-managed objects.

This module intentionally imports no Docker SDK code so warm command dispatch
can validate one `docker inspect` response without paying docker-py's import
cost.
"""

MANAGED_LABEL = "rosman.managed"
WORKSPACE_LABEL = "rosman.workspace"
DISTRO_LABEL = "rosman.distro"
CONFIG_HASH_LABEL = "rosman.config_hash"
NETWORK_GROUP_LABEL = "rosman.network_group"
NETWORK_MODE_LABEL = "rosman.network_mode"
DOMAIN_ID_LABEL = "rosman.domain_id"
RESTART_POLICY_LABEL = "rosman.restart_policy"
REMOTE_PEERS_LABEL = "rosman.remote_peers"
