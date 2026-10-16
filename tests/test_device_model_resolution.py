"""Tests for device model-name resolution in the HA integration.

No BLE hardware and no Home Assistant required — const.py has no imports, so
it is loaded straight from its file path.

Covers the three spellings devices use for their own model:
    proc 0x82 description   "AcMeraClassic"           (short form)
    proc 0x82 description   "AquaClean Mera Classic"  (long form, RS30.0 TS206)
    BLE advertisement       "Geberit Mera Classic"
"""

import importlib.util
import os
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_const_path = os.path.join(
    _repo_root, "custom_components", "geberit_aquaclean", "const.py"
)

_spec = importlib.util.spec_from_file_location("geberit_const", _const_path)
const = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(const)

failures: list[str] = []


def check(label: str, got, expected) -> None:
    if got == expected:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}: expected {expected!r}, got {got!r}")
        failures.append(label)


def test_every_model_has_a_unique_normalized_key() -> None:
    print("normalized keys are unique:")
    check(
        f"{len(const.DEVICE_MODEL_FEATURE_SETS)} models -> "
        f"{len(const.MODEL_BY_NORMALIZED_NAME)} keys",
        len(const.MODEL_BY_NORMALIZED_NAME),
        len(const.DEVICE_MODEL_FEATURE_SETS),
    )


def test_exact_tables_still_resolve() -> None:
    """Backward compatibility: every spelling the old tables handled must still work."""
    print("exact tables still resolve:")
    for table in (const.PROC82_DESCRIPTION_TO_MODEL, const.ADV_DEVICE_TYPE_TO_MODEL):
        for raw, expected in table.items():
            check(repr(raw), const.resolve_device_model(raw), expected)


def test_long_form_descriptions_resolve() -> None:
    """The regression: firmware RS30.0 TS206 reports the long form."""
    print("long-form spellings resolve:")
    for raw, expected in [
        ("AquaClean Mera Classic", "mera_classic"),  # observed on real hardware
        ("AquaClean Mera Comfort", "mera_comfort"),
        ("AquaClean Tuma Classic", "tuma_classic"),
        ("AquaClean Sela", "sela"),
        ("AquaClean Cama Testset", "cama_testset"),
        ("Geberit AquaClean Mera Classic", "mera_classic"),
        ("Geberit AquaClean Alba", "alba"),
        ("  AquaClean Mera Classic  ", "mera_classic"),
        ("AQUACLEAN MERA CLASSIC", "mera_classic"),
    ]:
        check(repr(raw), const.resolve_device_model(raw), expected)


def test_unknown_input_stays_unknown() -> None:
    """No false positives — an unmapped model must not silently pick a wrong one."""
    print("unknown input stays unknown:")
    for raw in ["", None, "AcFuturisticModel9000", "Totally Unrelated", "Geberit"]:
        check(repr(raw), const.resolve_device_model(raw), None)


def test_unknown_model_still_gets_the_full_entity_set() -> None:
    """get_feature_sets keeps its documented fallback."""
    print("unknown model falls back to the full feature set:")
    check(
        "get_feature_sets(None)",
        const.get_feature_sets(None),
        const.get_feature_sets("definitely_not_a_model"),
    )


def test_mera_classic_excludes_comfort_only_features() -> None:
    """The point of the fix: a resolved Mera Classic must not get Comfort entities."""
    print("mera_classic excludes Comfort/Alba-only features:")
    fs = const.get_feature_sets(const.resolve_device_model("AquaClean Mera Classic"))
    for feature in (
        const.FS_MERA_COMFORT_ONLY,
        const.FS_WITH_SEAT_HEATER,
        const.FS_WITH_WATER_HEATER,
        const.FS_ALBA_ONLY,
        const.FS_SELA_ONLY,
    ):
        check(f"{feature} not in feature sets", feature in fs, False)
    check(const.FS_WITH_ODOUR_EXTRACTION, const.FS_WITH_ODOUR_EXTRACTION in fs, True)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print()
    if failures:
        print(f"{len(failures)} failure(s): {failures}")
        sys.exit(1)
    print("all checks passed")
