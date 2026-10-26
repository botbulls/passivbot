"""Regla de forager para elegir config live y manifiesto (forager_live_configs.py). Sin red."""

import copy
import hashlib
import json
import os
import random
import stat
import sys

os.environ.setdefault("NOJIT", "true")

import pytest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import forager_live_configs as flc  # noqa: E402
from forager_live_configs import (  # noqa: E402
    LiveConfigsManifestWriter,
    build_manifest,
    fill_live_configs_map_from_dir,
    resolve_live_config_path,
)
from pure_funcs import get_template_live_config  # noqa: E402

LIVE_DIR = "configs/live/clock/"
DEFAULT = "configs/live/cargaydescarga.json"
CLOCK_FILES = {
    # como en la imagen: existe TUSDT.json, pero ANTUSDT.json va antes y contiene "TUSDT"
    "ANTUSDT.json": 0.011,
    "AXSUSDT.json": 0.012,
    "BTCSTUSDT.json": 0.013,
    "BTCUSDT.json": 0.0102,
    "ETHUSDT.json": 0.0123,
    "TUSDT.json": 0.014,
    "XRPUSDT.json": 0.01,
}


def _clock(name, qty):
    cfg = get_template_live_config("clock")
    cfg["config_name"] = name
    cfg["long"]["qty_pct_entry"] = qty
    return cfg


def _neat(name):
    cfg = get_template_live_config("neat_grid")
    cfg["config_name"] = name
    return cfg


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    live = tmp_path / LIVE_DIR
    live.mkdir(parents=True)
    for fname, qty in CLOCK_FILES.items():
        (live / fname).write_text(json.dumps(_clock(f"clock_{fname[:-5]}", qty)))
    (live / "NEATUSDT.json").write_text(json.dumps(_neat("neat_NEATUSDT")))
    (live / "BROKENUSDT.json").write_text("{esto no es json")
    (live / "notas.txt").write_text("BTCUSDT no es json")
    (tmp_path / DEFAULT).write_text(json.dumps(_neat("neat_grid_XTZUSDT_876days")))
    (tmp_path / "configs/forager").mkdir(parents=True)
    (tmp_path / "configs/forager/new.json").write_text("{}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(flc.REVISION_ENV, raising=False)
    return tmp_path


def _config(**over):
    cfg = {
        "live_configs_dir_long": LIVE_DIR,
        "live_configs_dir_short": LIVE_DIR,
        "live_configs_map": {},
        "live_configs_map_long": {},
        "live_configs_map_short": {},
        "default_config_path": DEFAULT,
    }
    cfg.update(over)
    return cfg


# --- oraculo: bloques copiados literal de forager.py @92dba71 -----------------------------


def legacy_fill(config, symbols_map_inv):
    """forager.py @92dba71 dump_yaml :427-443."""
    for side in ["long", "short"]:
        if config[f"live_configs_dir_{side}"] and os.path.exists(config[f"live_configs_dir_{side}"]):
            fnames = sorted(
                [f for f in os.listdir(config[f"live_configs_dir_{side}"]) if f.endswith(".json")]
            )
            if fnames:
                for symbol in symbols_map_inv:
                    fnamesf = [f for f in fnames if symbol in f]
                    if fnamesf and not any(
                        [
                            symbol in x
                            for x in [config[f"live_configs_map_{side}"], config["live_configs_map"]]
                        ]
                    ):
                        config[f"live_configs_map_{side}"][symbol] = os.path.join(
                            config[f"live_configs_dir_{side}"], fnamesf[0]
                        )


def legacy_conf_paths(config, sym, shorts):
    """forager.py @92dba71 generate_yaml :202-216 (shorts = shorts_on_gs + active_shorts)."""
    if sym in config["live_configs_map_long"]:
        conf_path_long = config["live_configs_map_long"][sym]
    elif sym in config["live_configs_map"]:
        conf_path_long = config["live_configs_map"][sym]
    else:
        conf_path_long = config["default_config_path"]

    if sym not in shorts:
        conf_path_short = conf_path_long
    elif sym in config["live_configs_map_short"]:
        conf_path_short = config["live_configs_map_short"][sym]
    elif sym in config["live_configs_map"]:
        conf_path_short = config["live_configs_map"][sym]
    else:
        conf_path_short = config["default_config_path"]
    return conf_path_long, conf_path_short


def new_conf_paths(config, sym, shorts):
    """Lo que hace generate_yaml ahora."""
    conf_path_long = resolve_live_config_path(config, sym, "long")
    if sym not in shorts:
        conf_path_short = conf_path_long
    else:
        conf_path_short = resolve_live_config_path(config, sym, "short")
    return conf_path_long, conf_path_short


def test_equivalencia_con_92dba71_aleatoria(tree):
    (tree / "configs/live/short").mkdir()
    for fname in ["AXSUSDT.json", "SUSDT.json", "TUSDT.json", "ZZZUSDT.json"]:
        (tree / "configs/live/short" / fname).write_text("{}")
    universe = [
        "TUSDT", "SUSDT", "ANTUSDT", "AXSUSDT", "BTCUSDT", "BTCSTUSDT", "ETHUSDT",
        "XRPUSDT", "SOLUSDT", "NEATUSDT", "BROKENUSDT", "USDT", "ZZZUSDT",
    ]
    rng = random.Random(20261025)
    for _ in range(300):
        def rmap():
            return {
                s: f"configs/manual/{s}-{rng.randint(0, 9)}.json"
                for s in rng.sample(universe, rng.randint(0, 3))
            }

        cfg = _config(
            live_configs_dir_long=rng.choice([LIVE_DIR, "configs/live/short", "", "no/existe/"]),
            live_configs_dir_short=rng.choice([LIVE_DIR, "configs/live/short", ""]),
            live_configs_map=rmap(),
            live_configs_map_long=rmap(),
            live_configs_map_short=rmap(),
        )
        legacy, new = copy.deepcopy(cfg), copy.deepcopy(cfg)
        for cycle in range(2):  # el 2.o ciclo corre sobre la config ya mutada
            syms = {s: f"{s[:-4]}/USDT:USDT" for s in rng.sample(universe, rng.randint(0, len(universe)))}
            legacy_fill(legacy, syms)
            for side in ["long", "short"]:
                fill_live_configs_map_from_dir(new, syms, side)
            assert new == legacy
            shorts = rng.sample(universe, rng.randint(0, 4))
            for sym in universe:
                assert new_conf_paths(new, sym, shorts) == legacy_conf_paths(legacy, sym, shorts)


# --- regla sobre el arbol de fixtures ---------------------------------------------------------


def test_colisiones_tusdt_y_susdt(tree):
    m = build_manifest(flc.snapshot_live_configs_config(_config()), ["TUSDT", "SUSDT", "BTCUSDT"])
    t, s = m["symbols"]["TUSDT"], m["symbols"]["SUSDT"]
    assert (t["file"], t["source"], t["own_file"], t["is_clock"]) == ("ANTUSDT.json", "dir", False, True)
    assert t["path"] == os.path.join(LIVE_DIR, "ANTUSDT.json")
    assert t["md5"] == _md5(os.path.join(LIVE_DIR, "ANTUSDT.json"))
    assert (s["file"], s["source"], s["own_file"], s["is_clock"]) == ("AXSUSDT.json", "dir", False, True)
    b = m["symbols"]["BTCUSDT"]
    assert (b["file"], b["source"], b["own_file"], b["is_clock"]) == ("BTCUSDT.json", "dir", True, True)
    assert b["qty_pct_entry_long"] == pytest.approx(0.0102)
    assert b["config_name"] == "clock_BTCUSDT"


def test_sin_config_propia_cae_al_default(tree):
    m = build_manifest(flc.snapshot_live_configs_config(_config()), ["SOLUSDT", "NEATUSDT"])
    sol = m["symbols"]["SOLUSDT"]
    assert sol["source"] == "default" and sol["path"] == DEFAULT
    assert (sol["own_file"], sol["is_clock"], sol["passivbot_mode"]) == (False, False, "neat_grid")
    assert sol["config_name"] == "neat_grid_XTZUSDT_876days"
    assert sol["qty_pct_entry_long"] is None
    neat = m["symbols"]["NEATUSDT"]  # propio pero no clock
    assert (neat["own_file"], neat["is_clock"], neat["passivbot_mode"]) == (True, False, "neat_grid")
    d = m["default_config"]
    assert (d["path"], d["md5"], d["config_name"]) == (DEFAULT, _md5(DEFAULT), "neat_grid_XTZUSDT_876days")


def test_map_long_y_map_tienen_prioridad(tree):
    cfg = _config(
        live_configs_map_long={"TUSDT": os.path.join(LIVE_DIR, "TUSDT.json"), "ETHUSDT": DEFAULT},
        live_configs_map={"SUSDT": "configs/manual/SUSDT.json", "ETHUSDT": "configs/manual/x.json"},
    )
    m = build_manifest(flc.snapshot_live_configs_config(cfg), ["TUSDT", "SUSDT", "ETHUSDT"])
    t, s, e = (m["symbols"][k] for k in ["TUSDT", "SUSDT", "ETHUSDT"])
    assert (t["source"], t["file"], t["own_file"], t["is_clock"]) == ("map_long", "TUSDT.json", True, True)
    assert (s["source"], s["path"], s["exists"], s["error"]) == ("map", "configs/manual/SUSDT.json", False, "no existe")
    assert (e["source"], e["path"]) == ("map_long", DEFAULT)  # map_long > map


def test_indice_de_archivos(tree):
    m = build_manifest(flc.snapshot_live_configs_config(_config()))
    files = m["files"]
    assert sorted(files) == sorted(list(CLOCK_FILES) + ["NEATUSDT.json", "BROKENUSDT.json"])
    for fname, qty in CLOCK_FILES.items():
        info = files[fname]
        assert info["md5"] == _md5(os.path.join(LIVE_DIR, fname))
        assert (info["is_clock"], info["config_name"]) == (True, f"clock_{fname[:-5]}")
        assert info["qty_pct_entry_long"] == pytest.approx(qty)
        assert info["error"] is None
    broken = files["BROKENUSDT.json"]
    assert broken["exists"] and broken["md5"] and broken["error"] and not broken["is_clock"]
    assert files["NEATUSDT.json"]["is_clock"] is False
    assert m["live_configs_dir_long"] == LIVE_DIR and m["live_configs_dir_long_exists"]


def test_sin_markets_igual_escribe_el_indice(tree):
    w = LiveConfigsManifestWriter(_config(), "configs/forager/new.json")
    assert w.write() is True
    m = json.loads((tree / "configs/forager" / flc.MANIFEST_FILENAME).read_text())
    assert m["schema_version"] == 1 and m["generated_at"].endswith("Z")
    assert m["markets"] == {
        "status": "not_loaded", "available": False, "exchange": None, "testnet": None, "count": 0
    }
    assert m["symbols"] == {} and len(m["files"]) == len(CLOCK_FILES) + 2
    assert m["default_config"]["config_name"] == "neat_grid_XTZUSDT_876days"
    assert m["image_revision"] is None
    assert m["forager_config_path"] == "configs/forager/new.json"


def test_markets_vacios(tree):
    m = build_manifest(flc.snapshot_live_configs_config(_config()), [])
    assert (m["markets"]["status"], m["markets"]["available"], m["symbols"]) == ("empty", False, {})


def test_directorio_inexistente_y_default_faltante(tree):
    cfg = _config(live_configs_dir_long="configs/live/no_existe/", default_config_path="no/hay.json")
    m = build_manifest(flc.snapshot_live_configs_config(cfg), ["BTCUSDT"])
    assert m["files"] == {} and m["live_configs_dir_long_exists"] is False
    b = m["symbols"]["BTCUSDT"]
    assert (b["source"], b["path"], b["exists"], b["is_clock"]) == ("default", "no/hay.json", False, False)
    assert m["default_config"]["exists"] is False


def test_falta_live_configs_map_no_rompe(tree):
    cfg = _config()
    del cfg["live_configs_map"]  # forager tambien fallaria; el manifiesto lo registra
    m = build_manifest(flc.snapshot_live_configs_config(cfg), ["BTCUSDT"])
    assert m["symbols"] == {} and "KeyError" in m["symbols_error"]
    assert len(m["files"]) == len(CLOCK_FILES) + 2


def test_snapshot_no_se_contamina_con_el_relleno_de_forager(tree):
    cfg = _config()
    w = LiveConfigsManifestWriter(cfg, "configs/forager/new.json")
    syms = {"TUSDT": "T/USDT:USDT", "BTCUSDT": "BTC/USDT:USDT", "SOLUSDT": "SOL/USDT:USDT"}
    for _ in range(2):  # como dump_yaml en dos ciclos
        for side in ["long", "short"]:
            fill_live_configs_map_from_dir(cfg, syms, side)
    w.on_markets(list(syms), "binanceusdm")
    m = json.loads((tree / "configs/forager" / flc.MANIFEST_FILENAME).read_text())
    for sym in syms:
        assert m["symbols"][sym]["path"] == resolve_live_config_path(cfg, sym, "long")
    assert m["symbols"]["TUSDT"]["source"] == "dir"  # no "map_long": viene del directorio
    assert m["symbols"]["SOLUSDT"]["source"] == "default"


def test_on_markets_reescribe_solo_si_cambian_los_simbolos(tree, monkeypatch):
    w = LiveConfigsManifestWriter(_config(), "configs/forager/new.json")
    calls = []
    real = flc.write_json_atomic
    monkeypatch.setattr(flc, "write_json_atomic", lambda o, p: (calls.append(o), real(o, p)))
    w.testnet = True
    w.on_markets(["BTCUSDT", "TUSDT"], "binanceusdm")
    w.on_markets(["TUSDT", "BTCUSDT"], "binanceusdm")
    assert len(calls) == 1
    assert calls[0]["markets"] == {
        "status": "loaded", "available": True, "exchange": "binanceusdm", "testnet": True, "count": 2
    }
    w.on_markets(["BTCUSDT", "TUSDT", "SUSDT"], "binanceusdm")
    assert len(calls) == 2


def test_escritura_atomica_permisos_y_sin_temporales(tree):
    target = tree / "configs/forager" / flc.MANIFEST_FILENAME
    target.write_text("viejo")
    os.chmod(target, 0o600)
    w = LiveConfigsManifestWriter(_config(), str(tree / "configs/forager/new.json"))
    assert w.write(["BTCUSDT"]) is True
    assert json.loads(target.read_text())["symbols"]["BTCUSDT"]["own_file"] is True
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644
    assert sorted(os.listdir(tree / "configs/forager")) == ["live_configs_manifest.json", "new.json"]


def test_fallo_al_escribir_no_lanza_y_se_loguea(tree, monkeypatch, capsys):
    w = LiveConfigsManifestWriter(_config(), "no/existe/new.json")
    assert w.write() is False
    w.on_markets(["BTCUSDT"], "binanceusdm")  # tampoco lanza
    assert "no se pudo escribir" in capsys.readouterr().out

    def boom(*a, **k):
        raise RuntimeError("disco lleno")

    w2 = LiveConfigsManifestWriter(_config(), "configs/forager/new.json")
    monkeypatch.setattr(flc, "build_manifest", boom)
    assert w2.write() is False
    w2.on_markets(["BTCUSDT"])
    assert "disco lleno" in capsys.readouterr().out
    assert not os.path.exists(tree / "configs/forager" / flc.MANIFEST_FILENAME)


def test_fallo_a_mitad_de_escritura_borra_el_temporal(tree, monkeypatch):
    def boom(src, dst):
        raise OSError("replace fallo")

    monkeypatch.setattr(flc.os, "replace", boom)
    w = LiveConfigsManifestWriter(_config(), "configs/forager/new.json")
    assert w.write() is False
    assert os.listdir(tree / "configs/forager") == ["new.json"]


def test_writer_con_config_invalida_no_lanza(tree):
    w = LiveConfigsManifestWriter(None, "configs/forager/new.json")
    assert w.write() is False
    w.on_markets(["BTCUSDT"])


def test_revision_de_imagen_desde_env(tree, monkeypatch):
    monkeypatch.setenv(flc.REVISION_ENV, "abc1234")
    assert build_manifest(flc.snapshot_live_configs_config(_config()))["image_revision"] == "abc1234"
