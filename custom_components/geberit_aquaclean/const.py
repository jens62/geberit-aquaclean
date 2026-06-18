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


# ── Alba DpId coverage ────────────────────────────────────────────────────────
# DpIds that have at least one wired HA entity (sensor, binary_sensor, number,
# or button).  Used by AquaCleanAlbaDpIdCoverageSensor to mark each inventory
# DpId as "wired" or "not wired".  Update this set whenever a new Alba entity
# is added to any entity file.
ALBA_WIRED_DPIDS: frozenset[int] = frozenset({
    15,   # RTC_TIME                            → sensor
    93,   # POWER_SUPPLY_ERROR_STATUS           → binary_sensor
    148,  # OPERATION_TIME_TOTAL                → sensor
    149,  # OPERATION_TIME_SINCE_POWER_UP       → sensor
    153,  # RESTART                             → button
    270,  # SET_RTC_TIME                        → button
    563,  # START_STOP_ANAL_SHOWER              → button
    565,  # ANAL_SHOWER_PROGRESS                → sensor (instanced)
    566,  # START_STOP_SPRAY_ARM_CLEANING       → button
    567,  # SPRAY_ARM_CLEANING_STATUS           → sensor
    568,  # SPRAY_ARM_CLEANING_PROGRESS         → sensor (instanced)
    570,  # SET_ACTIVE_ANAL_SPRAY_INTENSITY     → number (write)
    571,  # ACTIVE_ANAL_SPRAY_INTENSITY_STATUS  → number (read) + sensor
    572,  # SET_ACTIVE_ANAL_SPRAY_ARM_POSITION  → number (write)
    573,  # ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS → number (read) + sensor
    574,  # SET_ACTIVE_SHOWER_WATER_TEMPERATURE → number (write)
    575,  # ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS → number (read) + sensor
    576,  # SET_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION → number (write)
    577,  # ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS → binary_sensor + number
    585,  # DESCALING_STATUS                    → sensor
    586,  # DESCALING_PROGRESS                  → sensor (instanced)
    588,  # UNACCOUNTED_SHOWER_CYCLES           → sensor
    589,  # DAYS_UNTIL_NEXT_DESCALING           → sensor
    590,  # TIMESTAMP_OF_LAST_DESCALING         → sensor
    591,  # TIMESTAMP_OF_LAST_DESCALING_REQUEST → sensor
    592,  # DESCALING_CYCLES                    → sensor
    689,  # STATISTIC_COUNTER_TOTAL             → sensor (instanced)
    764,  # WATER_HEATER_ERROR_STATUS           → binary_sensor
    765,  # LEVEL_CONTROL_ERROR_STATUS          → binary_sensor
    766,  # USER_DETECTION_ERROR_STATUS         → binary_sensor
    781,  # CREDITS_UNTIL_NEXT_DESCALING        → sensor
    785,  # FUS_VERSION                         → sensor (instanced)
    786,  # GEBERIT_LOADER_VERSION              → sensor (instanced)
    787,  # WIRELESS_STACK_VERSION              → sensor (instanced)
    789,  # WATER_PUMP_ERROR_STATUS             → binary_sensor
    790,  # SPRAY_ARM_DRIVE_ERROR_STATUS        → binary_sensor
    795,  # DEMO_MODE                           → binary_sensor
    796,  # PRODUCT_REGISTRATION_LEVEL          → sensor
    803,  # SHOWROOM_MODE                       → binary_sensor
    810,  # DRY_RUN_MODE                        → binary_sensor
    820,  # MAINTENANCE_REQUEST_STATUS          → binary_sensor
    977,  # DESCALING_DEVICE_LOCK_REMAINING_DAYS     → sensor
    979,  # DESCALING_DEVICE_RELOCK_REMAINING_CYCLES → sensor
    982,  # DESCALING_ERROR_STATUS              → binary_sensor
    983,  # DESCALING_DEVICE_LOCK_STATUS        → sensor
})


def get_feature_sets(device_model: str | None) -> frozenset:
    """Return the feature sets for a model key.

    Falls back to the full set when model is unknown so all entities are
    created — same as the previous unconditional behaviour.
    """
    if not device_model:
        return _FS_FULL
    return DEVICE_MODEL_FEATURE_SETS.get(device_model, _FS_FULL)
