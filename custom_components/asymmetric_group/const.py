"""Constants for the Asymmetric Group integration."""

DOMAIN = "asymmetric_group"

# Config / options keys
CONF_NAME = "name"
CONF_BASE_MEMBERS = "base_members"
CONF_POWER_ON_MEMBERS = "power_on_members"
CONF_POWER_OFF_MEMBERS = "power_off_members"
CONF_ATTRIBUTE_MEMBERS = "attribute_members"
CONF_STATE_MEMBERS = "state_members"
CONF_HIDE_MEMBERS = "hide_members"
CONF_ALL_ENTITIES = "all_entities"

# Config keys holding the *default* (seed) value of each runtime toggle.
CONF_ASYMMETRIC_POWER = "asymmetric_power"
CONF_ASYMMETRIC_ATTRIBUTES = "asymmetric_attributes"
CONF_ASYMMETRIC_STATE = "asymmetric_state"
CONF_ENFORCE_POWER_SCOPE = "enforce_power_scope"

# Runtime toggle keys (engine level).
TOGGLE_POWER = "power"
TOGGLE_ATTRIBUTES = "attributes"
TOGGLE_STATE = "state"
TOGGLE_ENFORCE = "enforce"
TOGGLES = (TOGGLE_POWER, TOGGLE_ATTRIBUTES, TOGGLE_STATE, TOGGLE_ENFORCE)

# Map runtime toggle key -> config/preset key.
TOGGLE_TO_PRESET_KEY = {
    TOGGLE_POWER: CONF_ASYMMETRIC_POWER,
    TOGGLE_ATTRIBUTES: CONF_ASYMMETRIC_ATTRIBUTES,
    TOGGLE_STATE: CONF_ASYMMETRIC_STATE,
    TOGGLE_ENFORCE: CONF_ENFORCE_POWER_SCOPE,
}

# Subgroup member-list keys, in config / preset / runtime dicts alike.
MEMBER_KEYS = (
    CONF_POWER_ON_MEMBERS,
    CONF_POWER_OFF_MEMBERS,
    CONF_ATTRIBUTE_MEMBERS,
    CONF_STATE_MEMBERS,
)

# Services
SERVICE_APPLY_PRESET = "apply_preset"
SERVICE_SAVE_PRESET = "save_preset"
SERVICE_DELETE_PRESET = "delete_preset"
SERVICE_LIST_PRESETS = "list_presets"
SERVICE_SET_MEMBERS = "set_members"

ATTR_PRESET = "preset"
ATTR_OVERWRITE = "overwrite"
ATTR_LAST_APPLIED_PRESET = "last_applied_preset"

# Storage
PRESET_STORE_KEY = "asymmetric_group.presets"
PRESET_STORE_VERSION = 1
ENTRY_STORE_VERSION = 1


def entry_store_key(entry_id: str) -> str:
    """Return the per-entry runtime store key."""
    return f"asymmetric_group.entry_{entry_id}"


PLATFORMS = ["light", "switch"]
