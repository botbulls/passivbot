"""forager.py con la regla extraida vs forager.py @92dba71, y arranque con manifiesto. Sin red.

Necesita ccxt (la version de requirements) y el historial git para leer 92dba71; si falta
alguno, los tests de equivalencia se saltean.
"""

import asyncio
import copy
import importlib.util
import json
import os
import subprocess
import sys

os.environ.setdefault("NOJIT", "true")

import pytest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("ccxt")
try:
    import forager  # noqa: E402
except AssertionError as e:  # version de ccxt distinta de la de requirements
    pytest.skip(f"forager no importa: {e}", allow_module_level=True)

import forager_live_configs as flc  # noqa: E402
from pure_funcs import get_template_live_config  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_forager_live_configs import legacy_fill  # noqa: E402  oraculo literal de 92dba71

LEGACY_REV = "92dba71"
LIVE_DIR = "configs/live/clock/"
DEFAULT = "configs/live/cargaydescarga.json"
SYMS = ["ANTUSDT", "AXSUSDT", "BNBUSDT", "BTCUSDT", "ETHUSDT", "SUSDT", "TUSDT", "XRPUSDT", "SOLUSDT"]
CLOCK_FILES = ["ANTUSDT.json", "AXSUSDT.json", "BNBUSDT.json", "BTCUSDT.json", "ETHUSDT.json", "TUSDT.json", "XRPUSDT.json"]


@pytest.fixture(scope="module")
def legacy(tmp_path_factory):
    try:
        src = subprocess.run(
            ["git", "-C", ROOT, "show", f"{LEGACY_REV}:forager.py"],
            capture_output=True, check=True, text=True,
        ).stdout
    except Exception as e:
        pytest.skip(f"sin git o sin {LEGACY_REV}: {e}")
    path = tmp_path_factory.mktemp("legacy") / "forager_legacy_92dba71.py"
    path.write_text(src)
    spec = importlib.util.spec_from_file_location("forager_legacy_92dba71", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "fill_live_configs_map_from_dir" not in src  # de verdad es la version vieja
    return mod


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / LIVE_DIR).mkdir(parents=True)
    (tmp_path / "configs/short").mkdir(parents=True)
    for fname in CLOCK_FILES:
        cfg = get_template_live_config("clock")
        cfg["config_name"] = f"clock_{fname[:-5]}"
        (tmp_path / LIVE_DIR / fname).write_text(json.dumps(cfg))
    for fname in ["SUSDT.json", "BTCUSDT_short.json"]:
        (tmp_path / "configs/short" / fname).write_text("{}")
    neat = get_template_live_config("neat_grid")
    neat["config_name"] = "neat_grid_XTZUSDT_876days"
    (tmp_path / DEFAULT).write_text(json.dumps(neat))
    (tmp_path / "configs/forager").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def base_config(**over):
    cfg = {
        "user": "binance_01",
        "twe_long": 4,
        "twe_short": 1,
        "n_longs": 4,
        "n_shorts": 0,
        "max_min_cost": 150,
        "approved_symbols_long": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"],
        "approved_symbols_short": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"],
        "live_configs_dir_long": LIVE_DIR,
        "live_configs_dir_short": LIVE_DIR,
        "live_configs_map": {},
        "live_configs_map_long": {},
        "live_configs_map_short": {},
        "default_config_path": DEFAULT,
        "volume_clip_threshold": 0.51,
        "unilateralness_clip_threshold": 0.49,
        "noisiness_clip_threshold": 0.52,
        "price_distance_threshold": 0.07,
        "max_n_panes": 8,
        "ohlcv_interval": "15m",
        "leverage": 10,
        "symbols_to_ignore": [],
        "market_age_threshold": 0,
        "passivbot_root_dir": "~/passivbot",
        "sleep_interval": 5,
        "before_command": "",
        "graceful_stop": False,
        "graceful_stop_long": False,
        "graceful_stop_short": False,
        "long_mode_code": "n",
        "short_mode_code": "n",
    }
    cfg.update(over)
    return cfg


GEN_SCENARIOS = [
    dict(),  # dev: solo long, directorio clock
    dict(n_shorts=2, live_configs_dir_short="configs/short",
         live_configs_map_short={"ETHUSDT": "configs/short/SUSDT.json"},
         live_configs_map={"XRPUSDT": "configs/manual/XRP.json"}),
    dict(n_longs=2, graceful_stop_long=True, long_mode_code="gs",
         live_configs_map_long={"BTCUSDT": DEFAULT}),
    dict(graceful_stop=True, n_shorts=1),
    dict(approved_symbols_long=[], approved_symbols_short=[], n_longs=6, n_shorts=3,
         live_configs_dir_long="", live_configs_map={"TUSDT": "configs/manual/T.json"}),
]


@pytest.mark.parametrize("over", GEN_SCENARIOS)
def test_generate_yaml_identico_a_92dba71(legacy, tree, over):
    cfg = base_config(**over)
    sorted_syms = [(float(i), s) for i, s in enumerate(SYMS)]
    pos = (["SOLUSDT", "BTCUSDT"], ["ETHUSDT"], ["TUSDT"], ["SUSDT", "XRPUSDT"])
    legacy_cfg, new_cfg = copy.deepcopy(cfg), copy.deepcopy(cfg)
    syms_inv = {s: f"{s[:-4]}/USDT:USDT" for s in SYMS}
    for side in ["long", "short"]:  # como dump_yaml antes de generate_yaml
        forager.fill_live_configs_map_from_dir(new_cfg, syms_inv, side)
    legacy_fill(legacy_cfg, syms_inv)
    assert new_cfg == legacy_cfg
    y_new = forager.generate_yaml(sorted_syms, new_cfg, *copy.deepcopy(pos))
    y_old = legacy.generate_yaml(sorted_syms, legacy_cfg, *copy.deepcopy(pos))
    assert y_new == y_old


class FakeCC:
    """ccxt binanceusdm de mentira: markets, tickers, posiciones, ordenes y velas fijas."""

    id = "binanceusdm"

    def __init__(self, positions=(), open_orders=()):
        self.options = {}
        self.closed = False
        self.markets = {}
        self.prices = {}
        for i, s in enumerate(SYMS + ["NOTICKERUSDT"]):
            ccxt_sym = f"{s[:-4]}/USDT:USDT"
            self.markets[ccxt_sym] = {
                "symbol": ccxt_sym, "type": "swap", "contractSize": 1.0,
                "limits": {"cost": {"min": 5.0}, "amount": {"min": 0.001}},
            }
            if s != "NOTICKERUSDT":
                self.prices[ccxt_sym] = 1.0 + i
        self.markets["BTC/USDT"] = {"symbol": "BTC/USDT", "type": "spot", "contractSize": None,
                                    "limits": {"cost": {"min": 5.0}, "amount": {"min": 0.001}}}
        self.positions = list(positions)
        self.open_orders = list(open_orders)

    async def load_markets(self):
        return self.markets

    async def fetch_tickers(self):
        return {k: {"last": v} for k, v in self.prices.items()}

    async def fetch_positions(self):
        return [{"symbol": s, "contracts": 1.0, "side": "long"} for s in self.positions]

    async def fetch_open_orders(self):
        return [{"symbol": s, "info": {"positionSide": "LONG"}} for s in self.open_orders]

    async def fetch_ohlcv(self, symbol, timeframe="15m", **kw):
        seed = sum(map(ord, symbol))
        out = []
        for i in range(120):
            c = self.prices.get(symbol, 1.0) * (1 + 0.01 * ((seed * (i + 3)) % 17 - 8) / 8)
            out.append([i * 900000, c, c * 1.01, c * 0.99, c, 1000.0 + (seed * i) % 97])
        return out

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("over", GEN_SCENARIOS[:3])
def test_dump_yaml_identico_a_92dba71_y_hook(legacy, tree, over):
    results = {}
    seen = []
    for name, mod in [("old", legacy), ("new", forager)]:
        cfg = base_config(yaml_filepath=f"{name}.yaml", **over)
        cc = FakeCC(positions=["SOL/USDT:USDT"], open_orders=["BNB/USDT:USDT"])
        if name == "new":
            asyncio.run(mod.dump_yaml(cc, cfg, on_markets=lambda s, ex: seen.append((sorted(s), ex))))
        else:
            asyncio.run(mod.dump_yaml(cc, cfg))
        results[name] = (open(f"{name}.yaml").read(), cfg)
    assert results["new"][0] == results["old"][0]
    assert results["new"][1]["live_configs_map_long"] == results["old"][1]["live_configs_map_long"]
    assert results["new"][1]["live_configs_map_short"] == results["old"][1]["live_configs_map_short"]
    assert seen == [(sorted(SYMS), "binanceusdm")]  # ni spot ni sin ticker


def test_dump_yaml_sigue_si_el_hook_falla(tree, capsys):
    def boom(symbols, exchange):
        raise RuntimeError("hook roto")

    cfg = base_config(yaml_filepath="x.yaml")
    asyncio.run(forager.dump_yaml(FakeCC(), cfg, on_markets=boom))
    assert os.path.getsize("x.yaml") > 0
    assert "hook roto" in capsys.readouterr().out


def _run_main(monkeypatch, cfg_path, keys=("binance", "k", "s", "")):
    cc = FakeCC()
    launched = []

    def load_keys(user):
        if isinstance(keys, Exception):
            raise keys
        return keys

    monkeypatch.setattr(forager, "load_exchange_key_secret_passphrase", load_keys)
    monkeypatch.setattr(forager, "is_binance_testnet", lambda user: True)
    monkeypatch.setattr(forager, "apply_binance_testnet_ccxt", lambda c: None)
    monkeypatch.setattr(forager.ccxt, "binanceusdm", lambda params: cc, raising=False)
    monkeypatch.setattr(forager.time, "sleep", lambda s: None)
    monkeypatch.setattr(forager.subprocess, "run", lambda args, **kw: launched.append(args))
    monkeypatch.setattr(sys, "argv", ["forager.py", cfg_path, "-n"])
    asyncio.run(forager.main())
    return cc, launched


def _write_forager_config(tree):
    cfg = {k: v for k, v in base_config().items() if not k.startswith("graceful") and not k.endswith("_code")}
    cfg["long_mode"] = "normal"
    path = tree / "configs/forager/new.json"
    path.write_text(json.dumps(cfg))
    return "configs/forager/new.json"


def test_main_escribe_el_manifiesto_y_arranca_igual(tree, monkeypatch):
    cfg_path = _write_forager_config(tree)
    cc, launched = _run_main(monkeypatch, cfg_path)
    assert cc.closed and ["tmuxp", "load", "-d", "binance_01.yaml"] in launched
    m = json.loads((tree / "configs/forager" / flc.MANIFEST_FILENAME).read_text())
    assert m["markets"] == {
        "status": "loaded", "available": True, "exchange": "binanceusdm", "testnet": True,
        "count": len(SYMS),
    }
    assert (m["symbols"]["TUSDT"]["file"], m["symbols"]["SUSDT"]["file"]) == ("ANTUSDT.json", "AXSUSDT.json")
    yaml = open("binance_01.yaml").read()
    for sym in ["BNBUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT"]:
        assert f"python3 passivbot.py binance_01 {sym} {m['symbols'][sym]['path']} " in yaml
        assert m["symbols"][sym]["own_file"] and m["symbols"][sym]["is_clock"]


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignora permisos")
def test_main_sigue_si_no_puede_escribir_el_manifiesto(tree, monkeypatch, capsys):
    cfg_path = _write_forager_config(tree)
    os.chmod(tree / "configs/forager", 0o555)
    try:
        cc, launched = _run_main(monkeypatch, cfg_path)
    finally:
        os.chmod(tree / "configs/forager", 0o755)
    assert ["tmuxp", "load", "-d", "binance_01.yaml"] in launched
    assert os.path.getsize("binance_01.yaml") > 0
    assert "no se pudo escribir" in capsys.readouterr().out
    assert not (tree / "configs/forager" / flc.MANIFEST_FILENAME).exists()


def test_main_deja_el_indice_aunque_fallen_las_keys(tree, monkeypatch):
    cfg_path = _write_forager_config(tree)
    with pytest.raises(Exception, match="sin api-keys"):
        _run_main(monkeypatch, cfg_path, keys=Exception("sin api-keys"))
    m = json.loads((tree / "configs/forager" / flc.MANIFEST_FILENAME).read_text())
    assert m["markets"]["status"] == "not_loaded" and len(m["files"]) == len(CLOCK_FILES)
