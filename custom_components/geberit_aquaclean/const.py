DOMAIN = "geberit_aquaclean"

CONF_DEVICE_ID = "device_id"
CONF_DEVICE_TYPE = "device_type"
CONF_ESPHOME_HOST = "esphome_host"
CONF_ESPHOME_PORT = "esphome_port"
CONF_NOISE_PSK = "noise_psk"
CONF_POLL_INTERVAL = "poll_interval"
CONF_USE_HA_BLUETOOTH = "use_ha_bluetooth"

DEFAULT_ESPHOME_PORT = 6053
DEFAULT_POLL_INTERVAL = 30

# ── Feature set names ──────────────────────────────────────────────────────
# FS_ALL: identification, poll timing, BLE status — every model including Alba.
# FS_AQUACLEAN_OLD: entities present on all AquacleanOld (non-Alba) models:
#   descaling, filter, lid toggle, flush/stop, calibration buttons, etc.
# Others: sub-capabilities present only on certain model variants.
FS_ALL                  = "ALL"
FS_AQUACLEAN_OLD        = "AQUACLEAN_OLD"
FS_WITH_LADY_SHOWER     = "WITH_LADY_SHOWER"
FS_WITH_DRYER           = "WITH_DRYER"
FS_WITH_DRYER_FAN       = "WITH_DRYER_FAN"
FS_WITH_ODOUR_EXTRACTION = "WITH_ODOUR_EXTRACTION"
FS_WITH_SEAT_HEATER     = "WITH_SEAT_HEATER"
FS_WITH_WATER_HEATER    = "WITH_WATER_HEATER"   # placeholder — no entities wired yet
FS_MERA_COMFORT_ONLY    = "MERA_COMFORT_ONLY"
FS_SELA_ONLY            = "SELA_ONLY"           # placeholder — no entities wired yet
FS_CAMA_ONLY            = "CAMA_ONLY"           # placeholder — no entities wired yet
FS_ALBA_ONLY            = "ALBA_ONLY"

# Fallback when device model is unknown: create all entities (backward-compatible).
_FS_FULL = frozenset({
    FS_ALL, FS_AQUACLEAN_OLD, FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_WITH_DRYER_FAN,
    FS_WITH_ODOUR_EXTRACTION, FS_WITH_SEAT_HEATER, FS_WITH_WATER_HEATER,
    FS_MERA_COMFORT_ONLY, FS_SELA_ONLY, FS_CAMA_ONLY, FS_ALBA_ONLY,
})

# ── Model key → feature sets ───────────────────────────────────────────────
DEVICE_MODEL_FEATURE_SETS: dict = {
    "mera_comfort":       frozenset({FS_ALL, FS_AQUACLEAN_OLD, FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_WITH_DRYER_FAN, FS_WITH_ODOUR_EXTRACTION, FS_WITH_SEAT_HEATER, FS_WITH_WATER_HEATER, FS_MERA_COMFORT_ONLY}),
    "mera_classic":       frozenset({FS_ALL, FS_AQUACLEAN_OLD, FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_WITH_DRYER_FAN, FS_WITH_ODOUR_EXTRACTION}),
    "mera_floorstanding": frozenset({FS_ALL, FS_AQUACLEAN_OLD, FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_WITH_DRYER_FAN, FS_WITH_ODOUR_EXTRACTION}),
    "tuma_comfort":       frozenset({FS_ALL, FS_AQUACLEAN_OLD, FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_WITH_DRYER_FAN, FS_WITH_ODOUR_EXTRACTION, FS_WITH_SEAT_HEATER, FS_WITH_WATER_HEATER}),
    "tuma_classic":       frozenset({FS_ALL, FS_AQUACLEAN_OLD}),
    "sela":               frozenset({FS_ALL, FS_AQUACLEAN_OLD, FS_WITH_LADY_SHOWER, FS_SELA_ONLY}),
    "cama":               frozenset({FS_ALL, FS_AQUACLEAN_OLD, FS_CAMA_ONLY}),
    "cama_testset":       frozenset({FS_ALL, FS_AQUACLEAN_OLD, FS_CAMA_ONLY}),
    "alba":               frozenset({FS_ALL, FS_ALBA_ONLY}),
}

# ── BLE advertisement device_type string → model key ──────────────────────
# Returned by parse_geberit_adv_info() / parse_geberit_adv_info_bleak().
ADV_DEVICE_TYPE_TO_MODEL: dict = {
    "Geberit Mera Comfort":      "mera_comfort",
    "Geberit Mera Classic":      "mera_classic",
    "Geberit Mera Floorstanding": "mera_floorstanding",
    "Geberit Tuma Comfort":      "tuma_comfort",
    "Geberit Tuma Classic":      "tuma_classic",
    "Geberit Sela":              "sela",
    "Geberit Cama":              "cama",
    "Geberit Cama Testset":      "cama_testset",
    "Geberit Alba":              "alba",
}

# ── Proc 0x82 GetDeviceIdentification description → model key ─────────────
# Used on first poll to refine _device_model for manual-MAC-entry installs.
PROC82_DESCRIPTION_TO_MODEL: dict = {
    "AcMeraComfort":       "mera_comfort",
    "AcMeraClassic":       "mera_classic",
    "AcMeraFloorstanding": "mera_floorstanding",
    "AcTumaComfort":       "tuma_comfort",
    "AcTumaClassic":       "tuma_classic",
    "AcSela":              "sela",
    "AcCama":              "cama",
    "AcCamaTestset":       "cama_testset",
}


def get_feature_sets(device_model: str | None) -> frozenset:
    """Return the feature sets for a model key.

    Falls back to the full set when model is unknown so all entities are
    created — same as the previous unconditional behaviour.
    """
    if not device_model:
        return _FS_FULL
    return DEVICE_MODEL_FEATURE_SETS.get(device_model, _FS_FULL)
