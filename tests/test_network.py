import pytest
import uvicorn
import yaml
from typer.testing import CliRunner

import litresearch.cli as cli
from litresearch.config import load_config
from litresearch.web.auth import hash_password
from litresearch.web.network import Interface, NetworkError, interfaces, resolve

runner = CliRunner()

PI = [
    Interface("lo", "127.0.0.1"),
    Interface("eth0", "192.168.50.2"),
    Interface("wlan0", "10.100.7.134"),
]


# --- resolving web.host --------------------------------------------------------------


@pytest.mark.parametrize("spec", ["local", "localhost", "127.0.0.1", "", None, "lo"])
def test_local(spec):
    binding = resolve(spec, 8080, PI)
    assert binding.host == "127.0.0.1" and binding.local_only
    assert binding.urls == ("http://127.0.0.1:8080/",)


@pytest.mark.parametrize("spec", ["all", "0.0.0.0", "ALL"])
def test_all_networks(spec):
    binding = resolve(spec, 8080, PI)
    assert binding.host == "0.0.0.0" and not binding.local_only
    assert binding.urls == ("http://192.168.50.2:8080/", "http://10.100.7.134:8080/")
    assert binding.description == "all networks (eth0, wlan0)"


@pytest.mark.parametrize(
    "spec, host, kind",
    [
        ("eth0", "192.168.50.2", "cable (Ethernet)"),
        ("wlan0", "10.100.7.134", "WLAN"),
        ("10.100.7.134", "10.100.7.134", "WLAN"),
    ],
)
def test_one_network(spec, host, kind):
    binding = resolve(spec, 9000, PI)
    assert binding.host == host and not binding.local_only
    assert binding.urls == (f"http://{host}:9000/",) and kind in binding.description


@pytest.mark.parametrize(
    "spec, message",
    [
        ("wlan1", "Unknown network 'wlan1'"),
        ("192.168.1.99", "not an address of this computer"),
        ("not an address", "Unknown network"),
    ],
)
def test_unknown_networks_list_the_choices(spec, message):
    with pytest.raises(NetworkError, match=message) as error:
        resolve(spec, 8080, PI)
    assert "eth0 (192.168.50.2)" in str(error.value) and "wlan0" in str(error.value)


def test_interface_kinds():
    kinds = {
        i.name: i.kind
        for i in [
            *PI,
            Interface("tailscale0", "100.64.0.1"),
            Interface("enp3s0", "10.0.0.2"),
            Interface("br0", "1.2.3.4"),
        ]
    }
    assert kinds["lo"] == "this computer only" and kinds["tailscale0"] == "VPN"
    assert kinds["enp3s0"] == "cable (Ethernet)" and kinds["br0"] == "network"


def test_real_interfaces_include_loopback():
    found = interfaces()
    assert found and found[0].address.startswith("127.")
    assert all(i.address.count(".") == 3 for i in found)


# --- the serve command -----------------------------------------------------------------


@pytest.fixture
def server(monkeypatch):
    calls = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.update(kwargs))
    monkeypatch.setattr("litresearch.web.network.interfaces", lambda: list(PI))
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: pytest.fail("no browser"))
    return calls


@pytest.fixture
def plain_console(monkeypatch):
    from rich.console import Console

    monkeypatch.setattr(cli, "console", Console(color_system=None, width=300, highlight=False))


def write_config(tmp_path, **web):
    path = tmp_path / "config.yaml"
    path.write_text(
        "# my settings\n"
        + yaml.safe_dump(
            {
                "paths": {"bibtex": "x.bib", "database": str(tmp_path / "db.sqlite")},
                "web": {"port": 8080, **web},
            }
        )
    )
    return str(path)


def serve(*args):
    return runner.invoke(cli.app, ["serve", *args])


def test_default_is_local_and_never_opens_a_browser(tmp_path, server, plain_console):
    result = serve("-c", write_config(tmp_path))
    assert result.exit_code == 0, result.stdout
    assert server == {"host": "127.0.0.1", "port": 8080, "log_level": "warning"}
    assert "this computer only" in result.stdout and "open http://127.0.0.1:8080/" in result.stdout


def test_one_network_with_password(tmp_path, server, plain_console, isolated):
    config = write_config(tmp_path, host="wlan0", password_hash=hash_password("lab-meeting-42"))
    result = serve("-c", config)
    assert result.exit_code == 0, result.stdout
    assert server["host"] == "10.100.7.134"
    assert "wlan0, WLAN" in result.stdout and "open http://10.100.7.134:8080/" in result.stdout
    assert "192.168.50.2" not in result.stdout and "login required" in result.stdout
    assert len(isolated) == 1  # backup thread started


def test_command_line_overrides_config(tmp_path, server, plain_console):
    config = write_config(tmp_path, host="wlan0", password_hash=hash_password("lab-meeting-42"))
    for option in ("--network", "-n", "--host"):
        result = serve(option, "eth0", "--port", "9001", "-c", config)
        assert result.exit_code == 0 and server == {
            "host": "192.168.50.2",
            "port": 9001,
            "log_level": "warning",
        }
    result = serve("--network", "all", "-c", config)
    assert server["host"] == "0.0.0.0"
    assert (
        "http://192.168.50.2:8080/" in result.stdout
        and "http://10.100.7.134:8080/" in result.stdout
    )


def test_network_needs_a_password(tmp_path, server, plain_console):
    config = write_config(tmp_path, host="eth0")
    result = serve("-c", config)
    assert result.exit_code == 1 and "without a password" in result.stdout and server == {}
    result = serve("--no-login", "-c", config)
    assert result.exit_code == 0 and "without a login" in result.stdout


def test_old_config_values_still_work(tmp_path, server, plain_console):
    hashed = hash_password("lab-meeting-42")
    assert serve("-c", write_config(tmp_path, host="0.0.0.0", password_hash=hashed)).exit_code == 0
    assert server["host"] == "0.0.0.0"
    assert serve("-c", write_config(tmp_path, host="127.0.0.1")).exit_code == 0
    assert server["host"] == "127.0.0.1"


def test_unknown_network_is_explained(tmp_path, server, plain_console):
    result = serve("--network", "wlan9", "-c", write_config(tmp_path))
    assert result.exit_code == 1 and "Unknown network 'wlan9'" in result.stdout
    assert "eth0 (192.168.50.2)" in result.stdout and server == {}


def test_list_networks(tmp_path, server, plain_console):
    result = serve("--list-networks", "-c", write_config(tmp_path))
    assert result.exit_code == 0 and server == {}
    assert "eth0     cable (Ethernet): 192.168.50.2" in result.stdout
    assert "wlan0    WLAN: 10.100.7.134" in result.stdout and "local" in result.stdout


def test_choose_and_save(tmp_path, server, plain_console, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    config = write_config(tmp_path, password_hash=hash_password("lab-meeting-42"))
    # serve asks by default; options: 1 local, 2 all, 3 eth0, 4 wlan0
    result = runner.invoke(cli.app, ["serve", "-c", config], input="4\ny\n")
    assert result.exit_code == 0, result.stdout
    assert server["host"] == "10.100.7.134" and "Saved" in result.stdout
    assert load_config(tmp_path / "config.yaml").web.host == "wlan0"
    assert "# my settings" in (tmp_path / "config.yaml").read_text()
    result = runner.invoke(cli.app, ["serve", "-c", config], input="1\nn\n")
    assert server["host"] == "127.0.0.1"
    assert load_config(tmp_path / "config.yaml").web.host == "wlan0"  # not saved
    # Enter keeps the default and asks nothing more
    result = runner.invoke(cli.app, ["serve", "-c", config], input="\n")
    assert result.exit_code == 0 and server["host"] == "10.100.7.134"
    assert "wlan0    WLAN: 10.100.7.134 ← default" in result.stdout
    assert "Make" not in result.stdout


def test_no_question_with_network_option_or_without_terminal(
    tmp_path, server, plain_console, monkeypatch
):
    config = write_config(tmp_path)
    monkeypatch.setattr(cli, "interactive", lambda: True)
    result = runner.invoke(cli.app, ["serve", "--network", "local", "-c", config])
    assert result.exit_code == 0 and "Where should" not in result.stdout
    result = runner.invoke(cli.app, ["serve", "--no-choose", "-c", config])
    assert result.exit_code == 0 and "Where should" not in result.stdout
    monkeypatch.setattr(cli, "interactive", lambda: False)  # e.g. systemd
    result = runner.invoke(cli.app, ["serve", "-c", config])
    assert result.exit_code == 0 and "Where should" not in result.stdout


def test_menu_marks_networks_that_need_a_password(tmp_path, server, plain_console, monkeypatch):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    result = runner.invoke(cli.app, ["serve", "-c", write_config(tmp_path)], input="\n")
    assert "eth0     cable (Ethernet): 192.168.50.2 (needs a password)" in result.stdout
    assert "litresearch set-password" in result.stdout
