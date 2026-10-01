import importlib.util
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from forager_modes import (  # noqa: E402
    pane_modes,
    resolve_config_modes,
    resolve_mode,
    stops_new_symbols,
)


@pytest.mark.parametrize(
    "value,code",
    [
        ("n", "n"),
        ("normal", "n"),
        ("gs", "gs"),
        ("graceful_stop", "gs"),
        ("graceful-stop", "gs"),
        ("m", "m"),
        ("manual", "m"),
        ("p", "p"),
        ("panic", "p"),
        ("t", "t"),
        ("tp_only", "t"),
        ("tp-only", "t"),
        ("  Graceful_Stop ", "gs"),
        ("NORMAL", "n"),
    ],
)
def test_resolve_mode_aliases(value, code):
    assert resolve_mode(value, "long_mode") == code


@pytest.mark.parametrize("value", [None, "", "   "])
def test_resolve_mode_absent(value):
    assert resolve_mode(value, "long_mode") is None


@pytest.mark.parametrize("value", ["stop", "graceful", "apagar", "x", "normal_mode"])
def test_resolve_mode_unknown_raises(value):
    with pytest.raises(ValueError) as exc:
        resolve_mode(value, "short_mode")
    assert "short_mode" in str(exc.value)
    assert "graceful_stop" in str(exc.value)


@pytest.mark.parametrize("value", [1, True, ["n"], {"m": 1}])
def test_resolve_mode_wrong_type_raises(value):
    with pytest.raises(ValueError):
        resolve_mode(value, "long_mode")


def test_resolve_config_modes():
    assert resolve_config_modes({}) == (None, None)
    assert resolve_config_modes({"long_mode": "normal", "short_mode": "graceful_stop"}) == ("n", "gs")
    with pytest.raises(ValueError):
        resolve_config_modes({"long_mode": "normal", "short_mode": "bogus"})


def test_stops_new_symbols():
    assert not stops_new_symbols(None)
    assert not stops_new_symbols("n")
    for code in ("gs", "m", "p", "t"):
        assert stops_new_symbols(code)


@pytest.mark.parametrize("long_enabled", [True, False])
@pytest.mark.parametrize("short_enabled", [True, False])
@pytest.mark.parametrize("lw,sw", [(0.4, 0.4), (0.0, 0.4), (0.4, 0.0)])
@pytest.mark.parametrize("override", [None, "n", "gs"])
def test_pane_modes_identity_without_forced_override(long_enabled, short_enabled, lw, sw, override):
    # regla historica de forager.py
    expected = (
        "n" if long_enabled and lw > 0.0 else "gs",
        "n" if short_enabled and sw > 0.0 else "gs",
    )
    assert pane_modes(long_enabled, short_enabled, lw, sw, override, override) == expected


@pytest.mark.parametrize("code", ["m", "p", "t"])
def test_pane_modes_forced(code):
    assert pane_modes(True, True, 0.4, 0.4, code, None) == (code, "n")
    assert pane_modes(False, False, 0.4, 0.4, None, code) == ("gs", code)
    assert pane_modes(False, True, 0.4, 0.4, code, code) == (code, code)


# ---------------------------------------------------------------------------
# generate_yaml de forager.py con dependencias pesadas stubbeadas
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def forager(monkeypatch_module):
    stubs = {}
    ccxt = types.ModuleType("ccxt")
    ccxt_async = types.ModuleType("ccxt.async_support")
    ccxt_async.__version__ = "stub"
    ccxt.async_support = ccxt_async
    stubs["ccxt"] = ccxt
    stubs["ccxt.async_support"] = ccxt_async
    procedures = types.ModuleType("procedures")
    procedures.load_ccxt_version = lambda: "stub"
    for name in (
        "load_exchange_key_secret_passphrase",
        "utc_ms",
        "make_get_filepath",
        "get_first_ohlcv_timestamps",
    ):
        setattr(procedures, name, lambda *a, **k: None)
    stubs["procedures"] = procedures
    njit_funcs = types.ModuleType("njit_funcs")
    njit_funcs.calc_emas = lambda *a, **k: None
    stubs["njit_funcs"] = njit_funcs
    pure_funcs = types.ModuleType("pure_funcs")
    pure_funcs.determine_pos_side_ccxt = lambda *a, **k: None
    pure_funcs.date_to_ts2 = lambda *a, **k: None
    stubs["pure_funcs"] = pure_funcs
    for name, mod in stubs.items():
        monkeypatch_module.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("forager_under_test", os.path.join(ROOT, "forager.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def base_config(**extra):
    cfg = {
        "user": "binance_01",
        "twe_long": 1.6,
        "twe_short": 0.8,
        "n_longs": 2,
        "n_shorts": 1,
        "before_command": "",
        "approved_symbols_long": [],
        "approved_symbols_short": [],
        "live_configs_map": {},
        "live_configs_map_long": {},
        "live_configs_map_short": {"ETHUSDT": "configs/live/short.json"},
        "default_config_path": "configs/live/default.json",
        "leverage": 10,
        "price_distance_threshold": 0.5,
        "max_n_panes": 8,
        "passivbot_root_dir": "~/passivbot",
        "sleep_interval": 5,
        "graceful_stop": False,
        "graceful_stop_long": False,
        "graceful_stop_short": False,
    }
    cfg.update(extra)
    return cfg


def apply_modes(cfg):
    """Replica lo que hace main() con long_mode/short_mode."""
    long_code, short_code = resolve_config_modes(cfg)
    cfg["long_mode_code"] = long_code
    cfg["short_mode_code"] = short_code
    cfg["graceful_stop_long"] = cfg["graceful_stop_long"] or stops_new_symbols(long_code)
    cfg["graceful_stop_short"] = cfg["graceful_stop_short"] or stops_new_symbols(short_code)
    return cfg


SORTED = [(0, s) for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]]
# posiciones actuales: XRP long (rotado fuera), ETH short (config short distinta)
POSITIONS = (["XRPUSDT"], ["ETHUSDT"], [], [])


def gen(forager, cfg):
    return forager.generate_yaml(SORTED, cfg, *[list(x) for x in POSITIONS])


def pane_lines(yaml):
    return [line for line in yaml.splitlines() if "passivbot.py" in line]


def test_yaml_identical_absent_vs_normal(forager):
    absent = gen(forager, apply_modes(base_config()))
    normal = gen(forager, apply_modes(base_config(long_mode="normal", short_mode="n")))
    assert absent == normal
    # sanity: hay paneles normales y en gs
    assert "-lm n" in absent and "-lm gs" in absent


def test_yaml_graceful_stop_matches_cli_flags(forager):
    via_config = gen(
        forager, apply_modes(base_config(long_mode="graceful_stop", short_mode="graceful_stop"))
    )
    via_cli = gen(forager, base_config(graceful_stop_long=True, graceful_stop_short=True))
    assert via_config == via_cli
    lines = pane_lines(via_config)
    # solo los simbolos con posicion, ningun simbolo nuevo
    assert len(lines) > 0
    assert all("BTCUSDT" not in l and "SOLUSDT" not in l for l in lines)
    assert all(" -lm n " not in l and " -sm n " not in l for l in lines)


@pytest.mark.parametrize("value,code", [("panic", "p"), ("tp_only", "t"), ("manual", "m")])
def test_yaml_forced_mode_all_panes(forager, value, code):
    yaml = gen(forager, apply_modes(base_config(long_mode=value)))
    lines = pane_lines(yaml)
    assert lines
    for line in lines:
        # el lado long recibe el modo en todos los paneles, salvo la instancia que
        # corre solo el short de un simbolo con config separada (que va con -lm m)
        assert f"-lm {code} " in line or "-lm m " in line
    # no se abren simbolos long nuevos
    assert not any("BTCUSDT" in l or "SOLUSDT" in l for l in lines)


def test_yaml_long_normal_short_graceful(forager):
    yaml = gen(forager, apply_modes(base_config(long_mode="normal", short_mode="gs")))
    lines = pane_lines(yaml)
    assert any("BTCUSDT" in l and "-lm n" in l for l in lines)
    assert all(" -sm n " not in l for l in lines)
