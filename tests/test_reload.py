"""Reload a changed directory plugin through Hermes' real module loader."""
import shutil
from pathlib import Path

from hermes_cli import plugins


def test_directory_reload_replaces_business_modules(tmp_path):
    source = Path(__file__).resolve().parents[1]
    target = tmp_path / "plugins" / "tokenwatch"
    target.mkdir(parents=True)
    for name in ("__init__.py", "plugin.yaml"):
        shutil.copy2(source / name, target / name)
    shutil.copytree(source / "token_monitor", target / "token_monitor",
                    ignore=shutil.ignore_patterns("__pycache__"))
    manifest = plugins.parse_manifest_file(target / "plugin.yaml", target, "user", "")
    assert manifest is not None
    manager = plugins.PluginManager()
    first = manager._load_directory_module(manifest)
    assert first.get_hooks().config.limit_total_tokens == 20_000_000
    config = target / "token_monitor" / "config.py"
    config.write_text(config.read_text().replace("20_000_000", "123_456"))
    second = manager._load_directory_module(manifest)
    assert second.get_hooks().config.limit_total_tokens == 123_456
    assert type(first.get_hooks()) is not type(second.get_hooks())
