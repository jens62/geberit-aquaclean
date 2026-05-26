from enum import IntEnum


class DeviceSeries(IntEnum):
    AQUACLEAN = 250
    AQUACLEAN_OLD = 248
    WC_FLUSH = 249
    CONVERTER = 251
    GAM = 252
    LAVATORY_TAP = 253
    URINAL = 254
    PLATFORM = 255
    SANITARY_FLUSH_UNIT = 247
    SMART_SENSOR = 246
    GATEWAY = 245
    MIRROR_CABINET = 244
    ILLUMINATED_MIRROR = 243
    WASHBASIN_CABINET = 242
    SHELF_UNIT = 241
    REMOTE_CONTROL = 240
    MONOLITH = 239
    SANITARY_FLUSH_UNIT_OLD = 238
    AQUACLEAN_NODE = 237


class AquacleanVariant(IntEnum):
    ALBA = 0
    MERA_COMFORT = 1
    MERA_CLASSIC = 2
    ALBA_WIFI = 3


class AquacleanOldVariant(IntEnum):
    UNKNOWN = 0
    AC_MERA_FLOORSTANDING = 1
    AC_MERA_CLASSIC = 2
    AC_MERA_COMFORT = 3
    AC_TUMA_CLASSIC = 4
    AC_TUMA_COMFORT = 5
    AC_SELA = 6
    AC_CAMA_TESTSET = 7
    AC_CAMA = 8
    AC_SSM_12V = 9


_AQUACLEAN_VARIANT_NAMES = {
    AquacleanVariant.ALBA: "Alba",
    AquacleanVariant.MERA_COMFORT: "Mera Comfort",
    AquacleanVariant.MERA_CLASSIC: "Mera Classic",
    AquacleanVariant.ALBA_WIFI: "Alba Wifi",
}

_AQUACLEAN_OLD_VARIANT_NAMES = {
    AquacleanOldVariant.UNKNOWN: "Unknown",
    AquacleanOldVariant.AC_MERA_FLOORSTANDING: "Mera Floorstanding",
    AquacleanOldVariant.AC_MERA_CLASSIC: "Mera Classic",
    AquacleanOldVariant.AC_MERA_COMFORT: "Mera Comfort",
    AquacleanOldVariant.AC_TUMA_CLASSIC: "Tuma Classic",
    AquacleanOldVariant.AC_TUMA_COMFORT: "Tuma Comfort",
    AquacleanOldVariant.AC_SELA: "Sela",
    AquacleanOldVariant.AC_CAMA_TESTSET: "Cama Testset",
    AquacleanOldVariant.AC_CAMA: "Cama",
    AquacleanOldVariant.AC_SSM_12V: "SSM 12V",
}

_SERIES_NAMES = {
    DeviceSeries.AQUACLEAN: "Aquaclean",
    DeviceSeries.AQUACLEAN_OLD: "AqC",
    DeviceSeries.WC_FLUSH: "WC Flush",
    DeviceSeries.CONVERTER: "Converter",
    DeviceSeries.GAM: "GAM",
    DeviceSeries.LAVATORY_TAP: "Lavatory Tap",
    DeviceSeries.URINAL: "Urinal",
    DeviceSeries.PLATFORM: "Platform",
    DeviceSeries.SANITARY_FLUSH_UNIT: "Sanitary Flush Unit",
    DeviceSeries.SMART_SENSOR: "Smart Sensor",
    DeviceSeries.GATEWAY: "Gateway",
    DeviceSeries.MIRROR_CABINET: "Mirror Cabinet",
    DeviceSeries.ILLUMINATED_MIRROR: "Illuminated Mirror",
    DeviceSeries.WASHBASIN_CABINET: "Washbasin Cabinet",
    DeviceSeries.SHELF_UNIT: "Shelf Unit",
    DeviceSeries.REMOTE_CONTROL: "Remote Control",
    DeviceSeries.MONOLITH: "Monolith",
    DeviceSeries.SANITARY_FLUSH_UNIT_OLD: "Sanitary Flush Unit",
    DeviceSeries.AQUACLEAN_NODE: "AquaClean Node",
}


def get_series_name(series: int) -> str:
    try:
        return _SERIES_NAMES[DeviceSeries(series)]
    except (ValueError, KeyError):
        return f"Unknown({series})"


def get_full_name(series: int, variant: int) -> str:
    series_name = get_series_name(series)
    try:
        s = DeviceSeries(series)
    except ValueError:
        return f"{series_name} {variant}"

    if s == DeviceSeries.AQUACLEAN:
        try:
            variant_name = _AQUACLEAN_VARIANT_NAMES[AquacleanVariant(variant)]
        except (ValueError, KeyError):
            variant_name = f"Variant({variant})"
        return f"{series_name} {variant_name}"

    if s == DeviceSeries.AQUACLEAN_OLD:
        try:
            variant_name = _AQUACLEAN_OLD_VARIANT_NAMES[AquacleanOldVariant(variant)]
        except (ValueError, KeyError):
            variant_name = f"Variant({variant})"
        return f"{series_name} {variant_name}"

    return series_name
