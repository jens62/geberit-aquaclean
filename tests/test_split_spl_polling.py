import ast
from pathlib import Path


CLIENT_PATH = (
    Path(__file__).resolve().parents[1]
    / "aquaclean_console_app"
    / "aquaclean_core"
    / "Clients"
    / "AquaCleanClient.py"
)
SOURCE = CLIENT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _assignment_value(name: str):
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"assignment {name!r} not found")


def _method_source(class_name: str, method_name: str) -> str:
    cls = next(
        node
        for node in TREE.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and node.name == method_name
    )
    return ast.get_source_segment(SOURCE, method) or ""


def test_mera_spl_batches_stay_within_safe_boundary():
    assert _assignment_value("SPL_PARAMS_MERA_COMFORT_STATE") == list(range(8))
    assert _assignment_value("SPL_PARAMS_MERA_COMFORT_AUX") == [12, 13]
    assert len(_assignment_value("SPL_PARAMS_MERA_COMFORT_STATE")) <= 8
    assert len(_assignment_value("SPL_PARAMS_MERA_COMFORT_AUX")) <= 8


def test_state_poll_uses_two_getspl_requests_and_maps_aux_from_second_result():
    source = _method_source("AquaCleanClient", "_state_changed_timer_elapsed")

    assert source.count("get_system_parameter_list_async(") == 2
    assert "SPL_PARAMS_MERA_COMFORT_STATE" in source
    assert "SPL_PARAMS_MERA_COMFORT_AUX" in source

    assert "IsUserSitting=state_result.data_array[0] != 0" in source
    assert "IsAnalShowerRunning=state_result.data_array[3] != 0" in source
    assert "IsLadyShowerRunning=state_result.data_array[2] != 0" in source
    assert "IsDryerRunning=state_result.data_array[1] != 0" in source

    assert "LidOffsetPosition=aux_result.data_array[0]" in source
    assert "ShowerArmOffsetPosition=aux_result.data_array[1]" in source

    # Regression guard: never reintroduce the known-destructive combined batch.
    assert "[0, 1, 2, 3, 4, 5, 6, 7, 12, 13]" not in source




def test_main_does_not_import_removed_combined_spl_constant():
    main_path = Path(__file__).resolve().parents[1] / "aquaclean_console_app" / "main.py"
    main_source = main_path.read_text(encoding="utf-8")
    main_tree = ast.parse(main_source)

    ac_imports = [
        node
        for node in main_tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module
        == "aquaclean_console_app.aquaclean_core.Clients.AquaCleanClient"
    ]
    assert len(ac_imports) == 1
    imported_names = {alias.name for alias in ac_imports[0].names}
    assert "AquaCleanClient" in imported_names
    assert "SPL_PARAMS_MERA_COMFORT" not in imported_names



def test_ondemand_fetch_state_uses_same_safe_split():
    main_path = Path(__file__).resolve().parents[1] / "aquaclean_console_app" / "main.py"
    main_source = main_path.read_text(encoding="utf-8")
    main_tree = ast.parse(main_source)

    api_mode = next(
        node for node in main_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ApiMode"
    )
    fetch_state = next(
        node for node in api_mode.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_fetch_state"
    )
    source = ast.get_source_segment(main_source, fetch_state) or ""

    assert source.count("get_system_parameter_list_async(") == 2
    assert "SPL_PARAMS_MERA_COMFORT_STATE" in source
    assert "SPL_PARAMS_MERA_COMFORT_AUX" in source

    assert 'state_result.data_array[0]' in source
    assert 'state_result.data_array[1]' in source
    assert 'state_result.data_array[2]' in source
    assert 'state_result.data_array[3]' in source
    assert 'state_result.data_array[4]' in source
    assert 'state_result.data_array[5]' in source
    assert 'state_result.data_array[6]' in source
    assert 'aux_result.data_array[0]' in source
    assert 'aux_result.data_array[1]' in source

    assert "result.data_array[8]" not in source
    assert "result.data_array[9]" not in source


def test_diagnostic_runtime_shim_is_removed():
    init_path = Path(__file__).resolve().parents[1] / "aquaclean_console_app" / "__init__.py"
    assert init_path.read_text(encoding="utf-8") == ""


def test_no_runtime_reference_to_removed_combined_spl_symbol():
    repo = Path(__file__).resolve().parents[1]
    offenders = []

    for path in repo.rglob("*.py"):
        # Ignore generated caches/virtual envs if present in a developer checkout.
        if any(part in {".git", ".venv", "venv", "__pycache__"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "SPL_PARAMS_MERA_COMFORT":
                offenders.append(f"{path.relative_to(repo)}:{getattr(node, 'lineno', '?')}")
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "SPL_PARAMS_MERA_COMFORT":
                        offenders.append(f"{path.relative_to(repo)}:{getattr(node, 'lineno', '?')}")

    assert offenders == [], "stale SPL_PARAMS_MERA_COMFORT runtime refs: " + ", ".join(offenders)

if __name__ == "__main__":
    test_mera_spl_batches_stay_within_safe_boundary()
    test_state_poll_uses_two_getspl_requests_and_maps_aux_from_second_result()
    test_main_does_not_import_removed_combined_spl_constant()
    test_ondemand_fetch_state_uses_same_safe_split()
    test_diagnostic_runtime_shim_is_removed()
    test_no_runtime_reference_to_removed_combined_spl_symbol()
    print("split SPL regression checks: OK")
