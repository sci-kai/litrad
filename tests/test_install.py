"""The installer's checks: problems end with the fix, never with a false "Done"."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install", ROOT / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


@pytest.fixture
def venv_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "VENV", tmp_path / ".venv")
    return tmp_path / ".venv"


def test_expected_version_matches_the_package():
    from litrad import __version__

    assert install.expected_version() == __version__


def test_missing_venv_support_names_the_package_to_install(venv_dir, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        install.importlib.util,
        "find_spec",
        lambda name, *a: None if name == "ensurepip" else real(name, *a),
    )
    with pytest.raises(install.InstallError, match="sudo apt install python3-venv"):
        install.check_system()


def test_old_python_is_refused(monkeypatch):
    monkeypatch.setattr(install, "MINIMUM", (sys.version_info.major, sys.version_info.minor + 1))
    with pytest.raises(install.InstallError, match="or newer"):
        install.check_system()


def fake_run(returncode, stdout="", stderr=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, stderr)


def test_a_broken_installation_is_not_done(venv_dir, monkeypatch):
    error = "ImportError: cannot import name 'Sentinel' from 'typing_extensions'"
    monkeypatch.setattr(install.subprocess, "run", fake_run(1, stderr=f"Traceback\n{error}"))
    with pytest.raises(install.InstallError) as raised:
        install.verify()
    assert "Sentinel" in str(raised.value) and "--repair" in str(raised.value)


def test_a_working_installation_reports_its_version(venv_dir, monkeypatch):
    monkeypatch.setattr(install.subprocess, "run", fake_run(0, install.expected_version() + "\n"))
    assert install.verify() == install.expected_version()


def test_repair_reinstalls_without_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(install, "run", lambda *command: calls.append(command))
    install.install_package(Path("python"), dev=False, repair=True)
    assert "--force-reinstall" in calls[-1] and "--no-cache-dir" in calls[-1]
    install.install_package(Path("python"), dev=False)
    assert "--force-reinstall" not in calls[-1]


def test_main_stops_with_the_fix_and_exit_code_1(monkeypatch, capsys):
    def broken():
        raise install.InstallError("do this")

    monkeypatch.setattr(install, "check_system", broken)
    monkeypatch.setattr(sys, "argv", ["install.py"])
    assert install.main() == 1
    output = capsys.readouterr()
    assert "Installation stopped: do this" in output.err and "Done" not in output.out


def test_config_is_created_once(tmp_path, monkeypatch):
    example, config = tmp_path / "example.yaml", tmp_path / "config.yaml"
    example.write_text("a: 1\n")
    monkeypatch.setattr(install, "EXAMPLE", example)
    monkeypatch.setattr(install, "CONFIG", config)
    install.create_config()
    config.write_text("a: 2\n")
    install.create_config()
    assert config.read_text() == "a: 2\n"


def test_remove_service(tmp_path, monkeypatch):
    service = tmp_path / "litrad.service"
    service.write_text("[Unit]\n")
    monkeypatch.setattr(install, "SERVICE", service)
    monkeypatch.setattr(install.subprocess, "run", fake_run(0))
    install.remove_service()
    assert not service.exists()
    install.remove_service()  # nothing installed: no error
