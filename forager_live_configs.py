"""Regla de forager para elegir la config live de un simbolo y manifiesto de configs live.

Sin dependencias pesadas a nivel de modulo (ccxt/numba) para poder testearlo aislado,
igual que forager_modes.py.

Regla de forager v6.1 (extraida de forager.py sin cambiar el comportamiento):

1. dump_yaml rellena live_configs_map_<side> desde live_configs_dir_<side>
   (fill_live_configs_map_from_dir): para cada simbolo de los markets, el primer
   archivo *.json del directorio, en el orden de sorted(), cuyo nombre CONTIENE el
   simbolo, salvo que el simbolo ya este en live_configs_map_<side> o en
   live_configs_map. Por eso TUSDT elige ANTUSDT.json aunque exista TUSDT.json.
2. generate_yaml elige live_configs_map_<side> > live_configs_map > default_config_path
   (resolve_live_config_path).

Manifiesto: forager escribe live_configs_manifest.json en el directorio de su config
(configs/forager/, el que comparte con el dashboard), con escritura atomica. Una vez
al arrancar, solo con el indice (markets.status "not_loaded"), y de nuevo cada vez que
cambia el conjunto de simbolos de los markets (en la practica, en el primer ciclo).
Un fallo se loguea y nunca frena a forager.

Esquema (schema_version 1):

{
  "schema_version": 1,
  "generated_at": "2026-10-25T22:30:00Z",        # UTC
  "generator": "forager",
  "image_revision": "<sha git>" | null,          # env BOTBULLS_PASSIVBOT_REVISION
  "forager_config_path": "configs/forager/new.json",
  "cwd": "/passivbot",                            # base de las rutas relativas
  "side": "long",
  "live_configs_dir_long": "configs/live/single_symbol_optimizations/clock/",
  "live_configs_dir_long_exists": true,
  "default_config": <info de archivo>,            # default_config_path; null si no hay
  "files": {"BTCUSDT.json": <info de archivo>, ...},   # *.json de live_configs_dir_long
  "files_error": null | "texto",
  "markets": {
    "status": "not_loaded" | "loaded" | "empty",
    "available": true | false,                    # status == "loaded"
    "exchange": "binanceusdm" | null,             # cc.id de forager
    "testnet": true | false | null,               # Binance Demo
    "count": 528
  },
  "symbols": {                                    # {} si no hay markets
    "TUSDT": {
      "path": "configs/live/single_symbol_optimizations/clock/ANTUSDT.json",
      "file": "ANTUSDT.json",
      "source": "map_long" | "map" | "dir" | "default",
      "own_file": false,                          # file == "<SYM>.json"
      ...<info de archivo sin "path">
    }, ...
  },
  "symbols_error": null | "texto"
}

<info de archivo> = {
  "path": "...", "exists": true, "md5": "<hex>" | null,
  "config_name": "clock_0days" | null,
  "passivbot_mode": "clock" | "neat_grid" | "recursive_grid" | null,
  "is_clock": true | false,
  "qty_pct_entry_long": 0.0101 | null,            # valor efectivo (tras make_compatible)
  "error": null | "texto"
}

Simbolos: los mismos que usa forager en dump_yaml (markets no spot terminados en USDT
con ticker valido), con el nombre sin "/" ni ":USDT" (BTCUSDT).
"""

import copy
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone

MANIFEST_FILENAME = "live_configs_manifest.json"
SCHEMA_VERSION = 1
REVISION_ENV = "BOTBULLS_PASSIVBOT_REVISION"
SIDE = "long"
_SNAPSHOT_KEYS = (
    "live_configs_dir_long",
    "live_configs_map",
    "live_configs_map_long",
    "default_config_path",
)
_LOG_PREFIX = "live_configs_manifest:"


def list_live_config_files(live_configs_dir):
    return sorted([f for f in os.listdir(live_configs_dir) if f.endswith(".json")])


def fill_live_configs_map_from_dir(config, symbols, side):
    """Bloque de dump_yaml (forager.py @92dba71 :428-443). Muta config[live_configs_map_<side>]."""
    if config[f"live_configs_dir_{side}"] and os.path.exists(config[f"live_configs_dir_{side}"]):
        fnames = list_live_config_files(config[f"live_configs_dir_{side}"])
        if fnames:
            for symbol in symbols:
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


def resolve_live_config_path(config, sym, side):
    """Prioridad de generate_yaml (forager.py @92dba71 :202-216): map_<side> > map > default."""
    if sym in config[f"live_configs_map_{side}"]:
        return config[f"live_configs_map_{side}"][sym]
    elif sym in config["live_configs_map"]:
        return config["live_configs_map"][sym]
    else:
        return config["default_config_path"]


def snapshot_live_configs_config(config):
    """Copia de las claves de la regla tal como se cargaron, antes del primer relleno."""
    return {k: copy.deepcopy(config[k]) for k in _SNAPSHOT_KEYS if k in config}


def _log(msg):
    print(f"{_LOG_PREFIX} {msg}", flush=True)


def _err(e):
    return f"{type(e).__name__}: {e}"[:300]


def _finite_float(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def describe_config_file(path):
    """md5, config_name, modo y qty_pct_entry long, leidos como los lee passivbot.py."""
    info = {
        "path": path,
        "exists": False,
        "md5": None,
        "config_name": None,
        "passivbot_mode": None,
        "is_clock": False,
        "qty_pct_entry_long": None,
        "error": None,
    }
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        info["error"] = "no existe"
        return info
    except Exception as e:
        info["error"] = _err(e)
        return info
    info["exists"] = True
    info["md5"] = hashlib.md5(raw).hexdigest()
    try:
        # mismo camino que passivbot.py: load_live_config + determine_passivbot_mode
        from procedures import load_live_config
        from pure_funcs import determine_passivbot_mode

        cfg = load_live_config(path)
        if cfg.get("config_name") is not None:
            info["config_name"] = str(cfg["config_name"])
        mode = determine_passivbot_mode(cfg)
        info["passivbot_mode"] = mode
        info["is_clock"] = mode == "clock"
        info["qty_pct_entry_long"] = _finite_float(cfg.get("long", {}).get("qty_pct_entry"))
    except Exception as e:
        info["error"] = _err(e)
        try:
            name = json.loads(raw).get("config_name")
            if name is not None:
                info["config_name"] = str(name)
        except Exception:
            pass
    return info


def _utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_manifest(
    snapshot,
    symbols=None,
    exchange=None,
    testnet=None,
    forager_config_path=None,
    generated_at=None,
):
    """Arma el manifiesto. symbols=None: markets todavia no cargados (solo indice)."""
    cache = {}

    def describe(path):
        if path not in cache:
            cache[path] = describe_config_file(path)
        return cache[path]

    dir_long = snapshot.get(f"live_configs_dir_{SIDE}") or ""
    default_path = snapshot.get("default_config_path")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or _utc_now_iso(),
        "generator": "forager",
        "image_revision": os.environ.get(REVISION_ENV) or None,
        "forager_config_path": forager_config_path,
        "cwd": os.getcwd(),
        "side": SIDE,
        "live_configs_dir_long": dir_long,
        "live_configs_dir_long_exists": bool(dir_long) and os.path.exists(dir_long),
        "default_config": describe(default_path) if default_path else None,
        "files": {},
        "files_error": None,
        "markets": {
            "status": "not_loaded",
            "available": False,
            "exchange": exchange,
            "testnet": testnet,
            "count": 0,
        },
        "symbols": {},
        "symbols_error": None,
    }

    if manifest["live_configs_dir_long_exists"]:
        try:
            for fname in list_live_config_files(dir_long):
                manifest["files"][fname] = describe(os.path.join(dir_long, fname))
        except Exception as e:
            manifest["files_error"] = _err(e)

    if symbols is None:
        return manifest
    symbols = sorted(set(symbols))
    manifest["markets"]["count"] = len(symbols)
    manifest["markets"]["status"] = "loaded" if symbols else "empty"
    manifest["markets"]["available"] = bool(symbols)
    try:
        explicit_side = snapshot.get(f"live_configs_map_{SIDE}") or {}
        explicit_map = snapshot.get("live_configs_map") or {}
        work = copy.deepcopy(snapshot)
        fill_live_configs_map_from_dir(work, symbols, SIDE)
        for sym in symbols:
            path = resolve_live_config_path(work, sym, SIDE)
            if sym in explicit_side:
                source = "map_long"
            elif sym in explicit_map:
                source = "map"
            elif sym in work[f"live_configs_map_{SIDE}"]:
                source = "dir"
            else:
                source = "default"
            file_info = {k: v for k, v in describe(path).items() if k != "path"}
            fname = os.path.basename(str(path))
            manifest["symbols"][sym] = {
                "path": path,
                "file": fname,
                "source": source,
                "own_file": fname == f"{sym}.json",
                **file_info,
            }
    except Exception as e:
        manifest["symbols"] = {}
        manifest["symbols_error"] = _err(e)
    return manifest


def write_json_atomic(obj, path):
    """Temporal en el mismo directorio + fsync + chmod 0644 + os.replace."""
    data = json.dumps(obj, indent=1, sort_keys=True, allow_nan=False, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".", prefix=".live_configs_manifest.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class LiveConfigsManifestWriter:
    """Escribe el manifiesto junto a la config de forager. Ningun metodo lanza excepciones."""

    def __init__(self, config, forager_config_path):
        self.path = None
        self.snapshot = None
        self.forager_config_path = forager_config_path
        self.testnet = None
        self._written_symbols = None
        try:
            self.path = os.path.join(
                os.path.dirname(os.path.abspath(forager_config_path)), MANIFEST_FILENAME
            )
            self.snapshot = snapshot_live_configs_config(config)
        except Exception as e:
            _log(f"no se pudo preparar el manifiesto: {_err(e)}")

    def write(self, symbols=None, exchange=None):
        try:
            if self.path is None or self.snapshot is None:
                return False
            manifest = build_manifest(
                self.snapshot,
                symbols=symbols,
                exchange=exchange,
                testnet=self.testnet,
                forager_config_path=self.forager_config_path,
            )
            write_json_atomic(manifest, self.path)
            n_own_clock = sum(
                1 for s in manifest["symbols"].values() if s["own_file"] and s["is_clock"]
            )
            _log(
                f"escrito {self.path}: {len(manifest['files'])} archivos, "
                f"markets {manifest['markets']['status']} ({manifest['markets']['count']}), "
                f"{n_own_clock} con config clock propia"
            )
            return True
        except Exception as e:
            _log(f"no se pudo escribir {self.path}: {_err(e)}")
            return False

    def on_markets(self, symbols, exchange=None):
        """Hook de dump_yaml: reescribe solo si cambio el conjunto de simbolos."""
        try:
            symbols = sorted(set(symbols))
            if symbols == self._written_symbols:
                return
            if self.write(symbols=symbols, exchange=exchange):
                self._written_symbols = symbols
        except Exception as e:
            _log(f"error inesperado: {_err(e)}")
