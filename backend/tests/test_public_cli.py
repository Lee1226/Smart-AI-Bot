"""Standalone CLI contracts; never connect to a production service."""
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace

path = Path(__file__).resolve().parents[2] / 'cli/sab'
spec = importlib.util.spec_from_loader('public_sab_cli', SourceFileLoader('public_sab_cli', str(path)))
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_pending_run_without_results_has_zero_rate():
    assert cli._fmt_run({'id': 'fixture', 'status': 'pending', 'total': 0})['pass_rate'] == 0
    assert cli._fmt_run({'passed': 2, 'total': 4})['pass_rate'] == 50


def test_config_permissions_and_default_output_hide_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, 'CONFIG_PATH', tmp_path / 'sab.conf')
    cli._cmd_config_set(SimpleNamespace(key='api_key', value='fixture-secret-only'))
    assert 'fixture-secret-only' not in capsys.readouterr().out
    assert cli.CONFIG_PATH.stat().st_mode & 0o777 == 0o600
    cli._cmd_config_get(SimpleNamespace(key=None))
    assert 'fixture-secret-only' not in capsys.readouterr().out


def test_defaults_and_device_resolution(monkeypatch):
    monkeypatch.delenv('SAB_SERVER', raising=False)
    monkeypatch.setattr(cli, '_load_config', lambda: {})
    assert cli._server() == 'http://localhost:8000'
    monkeypatch.setattr(cli, '_request', lambda *args: (200, [{'id': 'fixture-device', 'name': 'Test phone'}]))
    assert cli._resolve_device('Test phone') == 'fixture-device'
