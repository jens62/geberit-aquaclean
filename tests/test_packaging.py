import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def dependency_names(requirements: list[str]) -> set[str]:
    return {
        re.split(r"[<>=!~\[ @]", requirement, maxsplit=1)[0].lower()
        for requirement in requirements
    }


def test_home_assistant_keeps_aioesphomeapi_core_managed() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    with (ROOT / "custom_components/geberit_aquaclean/manifest.json").open(encoding="utf-8") as file:
        manifest = json.load(file)

    assert "aioesphomeapi" not in dependency_names(project["dependencies"])
    assert "aioesphomeapi" in dependency_names(project["optional-dependencies"]["esphome"])
    assert all("[esphome]" not in requirement for requirement in manifest["requirements"])
