import ast
from pathlib import Path


MAIN_PATH = Path(__file__).resolve().parents[1] / "aquaclean_console_app" / "main.py"
SOURCE = MAIN_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _class(name: str) -> ast.ClassDef:
    return next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == name)


def _method(class_name: str, method_name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    cls = _class(class_name)
    return next(
        node
        for node in cls.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == method_name
    )


def _source(node: ast.AST) -> str:
    return ast.get_source_segment(SOURCE, node) or ""


def test_service_mode_direct_handlers_can_be_disabled_for_api_mode():
    init = _method("ServiceMode", "__init__")
    assert "register_direct_mqtt_handlers" in [arg.arg for arg in init.args.args]
    assert isinstance(init.args.defaults[-1], ast.Constant)
    assert init.args.defaults[-1].value is True

    run = _method("ServiceMode", "run")
    run_source = _source(run)
    assert "if self._register_direct_mqtt_handlers:" in run_source
    assert "self.mqtt_service.ToggleLidPosition += self.on_toggle_lid_message" in run_source
    assert "self.mqtt_service.ResetFilterCounter += self.on_reset_filter_counter_message" in run_source
    assert "self.mqtt_service.Connect += self.request_reconnect" in run_source


def test_api_mode_disables_direct_handlers_on_embedded_service_mode():
    init = _method("ApiMode", "__init__")
    service_call = next(
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ServiceMode"
    )
    keyword = next(kw for kw in service_call.keywords if kw.arg == "register_direct_mqtt_handlers")
    assert isinstance(keyword.value, ast.Constant)
    assert keyword.value.value is False


def test_api_mode_wires_direct_service_events_through_api_mode():
    run_source = _source(_method("ApiMode", "run"))
    assert (
        "self.service.mqtt_service.ToggleLidPosition          += self._on_mqtt_toggle_lid"
        in run_source
    )
    assert (
        "self.service.mqtt_service.ResetFilterCounter          += self._on_mqtt_reset_filter_counter"
        in run_source
    )
    assert "self.service.mqtt_service.Connect                     += self._on_mqtt_connect" in run_source


def test_api_mode_handlers_use_run_command_router():
    toggle_source = _source(_method("ApiMode", "_on_mqtt_toggle_lid"))
    reset_source = _source(_method("ApiMode", "_on_mqtt_reset_filter_counter"))
    connect_source = _source(_method("ApiMode", "_on_mqtt_connect"))
    assert 'await self.run_command("toggle-lid")' in toggle_source
    assert 'await self.run_command("reset-filter-counter")' in reset_source
    assert "await self.do_connect()" in connect_source
