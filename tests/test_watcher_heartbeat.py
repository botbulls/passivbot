"""Tests del dead-man del watcher de C3: sin latido en modo real, passivbot no abre entradas.

Contrato: PASSIVBOT_WATCHER_HEARTBEAT apunta a un JSON {"c3_mode": off|dry_run|real, "ts": ...}
(ts en segundos epoch o ISO8601) y PASSIVBOT_WATCHER_MAX_AGE_SECONDS fija la edad máxima
(default 120). Sin la env, el bot se comporta igual que hoy.
"""

import asyncio
import datetime as dt
import json
import logging
import os
import stat
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import watcher_heartbeat  # noqa: E402
from watcher_heartbeat import (  # noqa: E402
    DEFAULT_MAX_AGE_SECONDS,
    ENV_MAX_AGE,
    MIN_MAX_AGE_SECONDS,
    ENV_PATH,
    HeartbeatError,
    WatcherHeartbeat,
    parse_heartbeat,
    parse_max_age,
    parse_ts,
)

# fixtures e helpers del guard sin capital propio: el dead-man reutiliza el mismo mecanismo
from test_rescate_loan import (  # noqa: E402,F401
    CONFIGS_POR_MODO,
    POSICIONES,
    guard_bot,
    ids,
    make_bot,
    pb,
    rescate_records,
    write_state,
)

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z


def write_hb(path, c3_mode="real", ts=NOW, **extra):
    payload = {"c3_mode": c3_mode, "ts": ts}
    payload.update(extra)
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, str(path))


def watcher_records(caplog, level=None):
    return [
        r
        for r in caplog.records
        if r.getMessage().startswith("watcher:") and (level is None or r.levelno == level)
    ]


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def make_hb(path, max_age=120.0, now=NOW):
    return WatcherHeartbeat(str(path), max_age=max_age, clock=Clock(now))


# ---------------------------------------------------------------- lector del contrato


def test_from_env_sin_variable_es_none():
    assert WatcherHeartbeat.from_env({}) is None
    assert WatcherHeartbeat.from_env({ENV_PATH: ""}) is None
    # sin la ruta, la edad máxima sola no activa nada
    assert WatcherHeartbeat.from_env({ENV_MAX_AGE: "60"}) is None


def test_from_env_con_variable(tmp_path):
    path = str(tmp_path / "watcher_heartbeat.json")
    hb = WatcherHeartbeat.from_env({ENV_PATH: path})
    assert isinstance(hb, WatcherHeartbeat)
    assert hb.path == path
    assert hb.max_age == DEFAULT_MAX_AGE_SECONDS == 120.0
    assert WatcherHeartbeat.from_env({ENV_PATH: path, ENV_MAX_AGE: "300"}).max_age == 300.0
    assert WatcherHeartbeat.from_env({ENV_PATH: path, ENV_MAX_AGE: " 45.5 "}).max_age == 45.5


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "nan", "inf", "-inf", "1e999", "0.001", "44.9"])
def test_max_age_invalida_usa_default_y_loguea(tmp_path, caplog, raw):
    caplog.set_level(logging.INFO)
    hb = WatcherHeartbeat.from_env({ENV_PATH: str(tmp_path / "x.json"), ENV_MAX_AGE: raw})
    assert hb.max_age == 120.0
    errors = watcher_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert ENV_MAX_AGE in errors[0].getMessage()


def test_parse_max_age_vacia_es_default_sin_error():
    assert parse_max_age(None) == (120.0, None)
    assert parse_max_age("") == (120.0, None)
    assert parse_max_age("  ") == (120.0, None)


def test_max_age_tiene_piso():
    # 3 x 5 + 30: con el intervalo mínimo del servicio rescate, menos es un dead-man que salta
    # con el watcher sano. 0.001 antes se aceptaba y bloqueaba en cada ciclo.
    assert MIN_MAX_AGE_SECONDS == 45.0
    assert parse_max_age("45") == (45.0, None)
    value, error = parse_max_age("0.001")
    assert value == DEFAULT_MAX_AGE_SECONDS
    assert "mínimo 45 s" in error


@pytest.mark.parametrize(
    "ts, esperado",
    [
        (1_800_000_000, NOW),
        (1_800_000_000.5, NOW + 0.5),
        ("2027-01-15T08:00:00Z", NOW),
        ("2027-01-15T08:00:00z", NOW),
        ("2027-01-15T08:00:00+00:00", NOW),  # lo que emite now_iso() del dashboard
        ("2027-01-15T05:00:00-03:00", NOW),
        ("2027-01-15T08:00:00.250000+00:00", NOW + 0.25),
        ("2027-01-15T08:00:00", NOW),  # sin zona horaria: UTC
    ],
)
def test_parse_ts_acepta_epoch_e_iso8601(ts, esperado):
    assert parse_ts(ts) == pytest.approx(esperado)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{",
        '{"c3_mode": "real", "ts": ',  # escritura cortada
        "[]",
        '"real"',
        "null",
        '{"ts": 1800000000}',
        '{"c3_mode": "REAL", "ts": 1800000000}',
        '{"c3_mode": "on", "ts": 1800000000}',
        '{"c3_mode": null, "ts": 1800000000}',
        '{"c3_mode": "real"}',
        '{"c3_mode": "real", "ts": null}',
        '{"c3_mode": "real", "ts": true}',
        '{"c3_mode": "real", "ts": ""}',
        '{"c3_mode": "real", "ts": "ayer"}',
        '{"c3_mode": "real", "ts": NaN}',
        '{"c3_mode": "real", "ts": Infinity}',
        '{"c3_mode": "real", "ts": 1e999}',
        '{"c3_mode": "real", "ts": 0}',
        '{"c3_mode": "real", "ts": -1}',
        '{"c3_mode": "real", "ts": [1800000000]}',
        '{"c3_mode": "off", "ts": "ayer"}',  # el contrato se valida entero, en cualquier modo
    ],
)
def test_parse_rechaza_contenido_fuera_de_contrato(raw):
    with pytest.raises(HeartbeatError):
        parse_heartbeat(raw)


def test_parse_acepta_el_contrato_y_tolera_claves_de_mas():
    assert parse_heartbeat('{"c3_mode": "real", "ts": 1800000000}') == {"c3_mode": "real", "ts": NOW}
    raw = '{"c3_mode": "dry_run", "ts": "2027-01-15T08:00:00+00:00", "version": 3, "pid": 7}'
    assert parse_heartbeat(raw) == {"c3_mode": "dry_run", "ts": NOW}


# ---------------------------------------------------------------- check()


def test_real_fresco_no_bloquea(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 30)
    hb = make_hb(path)
    assert hb.check() is None
    assert hb.check() is None
    # solo el INFO del primer latido válido, una vez
    msgs = [(r.levelno, r.getMessage()) for r in watcher_records(caplog)]
    assert msgs == [
        (logging.INFO, f"watcher: primer latido válido en {path}: C3 en modo real (dead-man armado)")
    ]


def test_real_viejo_bloquea_en_la_primera_lectura(tmp_path):
    # proceso recién arrancado (forager reinicia los bots): no depende de lo leído antes
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 500)
    motivo = make_hb(path).check()
    assert motivo == "último latido hace 500 s, máximo 120 s"


def test_borde_de_la_edad_maxima(tmp_path):
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 120)
    assert make_hb(path).check() is None  # justo en el máximo: todavía vale
    write_hb(path, "real", NOW - 120.5)
    assert make_hb(path).check() is not None
    write_hb(path, "real", NOW - 59)
    assert make_hb(path, max_age=60).check() is None
    write_hb(path, "real", NOW - 61)
    assert make_hb(path, max_age=60).check() is not None


def test_iso8601_viejo_y_fresco(tmp_path):
    path = tmp_path / "hb.json"
    write_hb(path, "real", "2027-01-15T07:59:00+00:00")
    assert make_hb(path).check() is None
    write_hb(path, "real", "2027-01-15T07:50:00Z")
    assert make_hb(path).check() == "último latido hace 600 s, máximo 120 s"


def test_ts_en_el_futuro(tmp_path):
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW + 5)  # desfase chico de reloj: vale
    assert make_hb(path).check() is None
    write_hb(path, "real", NOW + 3600)  # dejaría el latido "fresco" para siempre
    assert make_hb(path).check() == "latido con ts 3600 s en el futuro, máximo 120 s"
    write_hb(path, "real", NOW * 1000)  # milisegundos por error: también bloquea
    assert make_hb(path).check() is not None


@pytest.mark.parametrize("c3_mode", ["off", "dry_run"])
@pytest.mark.parametrize("ts", [NOW - 30, NOW - 86400, NOW + 86400])
def test_off_y_dry_run_sin_efecto(tmp_path, c3_mode, ts):
    path = tmp_path / "hb.json"
    write_hb(path, c3_mode, ts)
    assert make_hb(path).check() is None


def test_ausente_sin_latido_valido_previo_no_bloquea_y_loguea(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    hb = make_hb(tmp_path / "no_existe.json")
    assert hb.check() is None
    errors = watcher_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "falta el archivo de latido" in errors[0].getMessage()
    assert "nunca hubo un latido válido en este proceso, sin efecto" in errors[0].getMessage()


def test_invalido_sin_latido_valido_previo_no_bloquea_y_loguea(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    path.write_text('{"c3_mode": "real", "ts": ')
    assert make_hb(path).check() is None
    errors = watcher_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "latido inválido" in errors[0].getMessage()


@pytest.mark.parametrize("rotura", ["ausente", "invalido", "ilegible"])
def test_ausente_o_invalido_despues_de_real_valido_bloquea(tmp_path, caplog, rotura):
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 10)
    hb = make_hb(path)
    assert hb.check() is None
    if rotura == "ausente":
        path.unlink()
    elif rotura == "invalido":
        path.write_text('{"c3_mode": "real", "ts": "ayer"}')
    else:
        path.unlink()
        path.mkdir()  # open() de un directorio: IsADirectoryError (OSError)
    motivo = hb.check()
    assert motivo is not None
    errors = watcher_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "el último latido válido estaba en modo real" in errors[0].getMessage()


JSON_ANIDADO_DE_MAS = "[" * 200_000  # json.loads levanta RecursionError, no ValueError


def test_parse_json_anidado_de_mas_es_invalido():
    with pytest.raises(HeartbeatError):
        parse_heartbeat(JSON_ANIDADO_DE_MAS)


def test_json_anidado_de_mas_despues_de_real_bloquea_sin_levantar(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 10)
    hb = make_hb(path)
    assert hb.check() is None
    path.write_text(JSON_ANIDADO_DE_MAS)
    assert hb.check() is not None
    errors = watcher_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "latido inválido" in errors[0].getMessage()


def test_json_anidado_de_mas_sin_real_previo_no_bloquea(tmp_path):
    path = tmp_path / "hb.json"
    path.write_text(JSON_ANIDADO_DE_MAS)
    assert make_hb(path).check() is None


@pytest.mark.parametrize("donde", ["parse", "lectura"])
def test_cualquier_excepcion_cuenta_como_archivo_invalido(tmp_path, monkeypatch, caplog, donde):
    # check() no levanta nunca: una excepción que escapara cortaría el ciclo de órdenes sin
    # cancelar las entradas que ya están en el exchange
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 10)
    hb = make_hb(path)
    assert hb.check() is None

    def explota(*a, **k):
        raise TypeError("inesperado")

    if donde == "parse":
        monkeypatch.setattr(watcher_heartbeat, "parse_heartbeat", explota)
    else:
        monkeypatch.setattr(watcher_heartbeat, "open", explota, raising=False)
    assert hb.check() is not None
    errors = watcher_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "TypeError('inesperado')" in errors[0].getMessage()


def test_real_luego_off_luego_ausente_no_bloquea(tmp_path):
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 10)
    hb = make_hb(path)
    assert hb.check() is None
    write_hb(path, "off", NOW - 10)
    assert hb.check() is None
    path.unlink()
    assert hb.check() is None  # off = sin efecto, aunque después falte el archivo
    write_hb(path, "dry_run", NOW - 10)
    assert hb.check() is None
    path.write_text("roto")
    assert hb.check() is None


def test_vuelve_a_latir_y_desbloquea(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    write_hb(path, "real", NOW - 10)
    hb = make_hb(path)
    assert hb.check() is None
    path.write_text("roto")
    assert hb.check() is not None
    write_hb(path, "real", NOW - 400)
    assert hb.check() is not None  # válido pero viejo
    write_hb(path, "real", NOW - 1)
    assert hb.check() is None
    infos = [r.getMessage() for r in watcher_records(caplog, logging.INFO)]
    assert infos == [
        f"watcher: primer latido válido en {path}: C3 en modo real (dead-man armado)",
        f"watcher: latido válido de nuevo en {path}",
    ]


def test_info_con_el_primer_latido_y_en_cada_cambio_de_modo(tmp_path, caplog):
    # para verificar un deploy: sin esto, "dead-man activo" solo prueba que llegó la env
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    hb = make_hb(path)
    write_hb(path, "off", NOW - 1)
    hb.check()
    hb.check()  # mismo modo: no repite
    write_hb(path, "real", NOW - 1)
    hb.check()
    hb.check()
    write_hb(path, "dry_run", NOW - 1)
    hb.check()
    write_hb(path, "real", NOW - 500)  # cambio de modo con latido viejo: también se loguea
    assert hb.check() is not None
    infos = [r.getMessage() for r in watcher_records(caplog, logging.INFO)]
    assert infos == [
        f"watcher: primer latido válido en {path}: C3 en modo off (dead-man sin efecto)",
        "watcher: C3 pasó de modo off a real (dead-man armado)",
        "watcher: C3 pasó de modo real a dry_run (dead-man sin efecto)",
        "watcher: C3 pasó de modo dry_run a real (dead-man armado)",
    ]


def test_error_de_lectura_rate_limited(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "hb.json"
    path.write_text("roto")
    hb = make_hb(path)
    for _ in range(5):
        hb.check()
    assert len(watcher_records(caplog, logging.ERROR)) == 1  # se lee en cada ciclo: no spamea
    hb.clock.now += hb.log_interval
    hb.check()
    assert len(watcher_records(caplog, logging.ERROR)) == 2
    path.unlink()  # otro error: se loguea enseguida
    hb.check()
    assert len(watcher_records(caplog, logging.ERROR)) == 3


def test_relee_el_archivo_en_cada_check(tmp_path):
    path = tmp_path / "hb.json"
    hb = make_hb(path)
    write_hb(path, "real", NOW - 10)
    assert hb.check() is None
    hb.clock.now += 200  # el watcher dejó de escribir
    assert hb.check() is not None
    write_hb(path, "real", hb.clock.now)
    assert hb.check() is None


# ---------------------------------------------------------------- contrato con el watcher del dashboard

# Lo que escribe futuresboard/c3.py (dashboard): heartbeat_payload() arma
# {"version": 1, "ts": now_iso(ts), "c3_mode": ..., "c3_parado": bool} y write_heartbeat() lo
# escribe con rescate.write_json_atomic(..., mode=0o644): json.dumps(indent=1,
# ensure_ascii=False) + "\n", tmp + fsync + chmod + os.replace. now_iso() es
# datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").
C3_HEARTBEAT_REAL = '{\n "version": 1,\n "ts": "2027-01-15T08:00:00+00:00",\n "c3_mode": "real",\n "c3_parado": false\n}\n'


def now_iso_dashboard(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def test_contrato_formato_de_now_iso_del_dashboard():
    assert now_iso_dashboard(NOW) == "2027-01-15T08:00:00+00:00"
    assert parse_ts(now_iso_dashboard(NOW - 37)) == NOW - 37


def test_contrato_lo_que_escribe_c3_del_dashboard(tmp_path):
    assert parse_heartbeat(C3_HEARTBEAT_REAL) == {"c3_mode": "real", "ts": NOW}
    path = tmp_path / "watcher_heartbeat.json"
    for c3_mode in ("off", "dry_run", "real"):
        for parado in (False, True):
            payload = {"version": 1, "ts": now_iso_dashboard(NOW - 20), "c3_mode": c3_mode, "c3_parado": parado}
            tmp = tmp_path / ".watcher_heartbeat.json.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
            hb = make_hb(path)
            assert hb.check() is None
            assert hb.last_valid_mode == c3_mode  # se leyó como válido: el dead-man se arma con real
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644


DASHBOARD_SRC = os.environ.get("BOTBULLS_DASHBOARD_SRC")


@pytest.mark.skipif(not DASHBOARD_SRC, reason="opt-in: BOTBULLS_DASHBOARD_SRC=<dashboard>/src")
def test_contrato_con_el_writer_real_del_dashboard(tmp_path, monkeypatch):
    # cruza los dos repos: escribe con futuresboard.c3.write_heartbeat y lee con parse_heartbeat
    monkeypatch.syspath_prepend(DASHBOARD_SRC)
    import pathlib

    from futuresboard import c3

    path = tmp_path / "watcher_heartbeat.json"
    c3.write_heartbeat(pathlib.Path(path), "real", False, NOW)
    with open(path, encoding="utf-8") as f:
        assert f.read() == C3_HEARTBEAT_REAL  # el golden de arriba es lo que escribe hoy
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644  # passivbot lo lee con otro uid
    for c3_mode in ("off", "dry_run", "real"):
        for parado in (False, True):
            c3.write_heartbeat(pathlib.Path(path), c3_mode, parado, NOW - 20)
            with open(path, encoding="utf-8") as f:
                assert parse_heartbeat(f.read()) == {"c3_mode": c3_mode, "ts": NOW - 20}
            hb = make_hb(path)
            assert hb.check() is None
            assert hb.last_valid_mode == c3_mode


# ---------------------------------------------------------------- integración con passivbot.Bot


@pytest.fixture
def wbot(guard_bot, monkeypatch, tmp_path):
    """Bot con exchange stubeado (guard_bot) y el dead-man según `hb`.

    hb: None (sin env), "fresco", "viejo", "ausente" o "invalido". El préstamo es 0 salvo
    que se pida otro, así el guard sin capital propio no interviene.
    """
    hb_path = tmp_path / "watcher_heartbeat.json"

    def _make(hb="fresco", c3_mode="real", loan=0.0, **kw):
        if hb is None:
            monkeypatch.delenv(ENV_PATH, raising=False)
            monkeypatch.delenv(ENV_MAX_AGE, raising=False)
        else:
            monkeypatch.setenv(ENV_PATH, str(hb_path))
            monkeypatch.setenv(ENV_MAX_AGE, "120")
            if hb_path.exists():
                hb_path.unlink()
            if hb == "fresco":
                write_hb(hb_path, c3_mode, NOW - 5)
            elif hb == "viejo":
                write_hb(hb_path, c3_mode, NOW - 500)
            elif hb == "invalido":
                hb_path.write_text('{"c3_mode": "real", "ts": ')
        bot = guard_bot(loan=loan, **kw)
        bot.hb_path = hb_path
        if bot.watcher is not None:
            bot.watcher.clock = Clock()
        return bot

    return _make


def run_cycle(bot):
    asyncio.run(bot.cancel_and_create())
    return bot.calls


def snapshot(calls):
    return (calls["calc_orders"], list(calls["cancel"]), list(calls["create"]))


def test_bot_lee_la_env(wbot, caplog):
    caplog.set_level(logging.INFO)
    bot = wbot("fresco")
    assert isinstance(bot.watcher, WatcherHeartbeat)
    assert bot.watcher.path == str(bot.hb_path)
    assert bot.watcher.max_age == 120.0
    msgs = [r.getMessage() for r in watcher_records(caplog, logging.INFO)]
    assert msgs == [f"watcher: dead-man activo, latido leído de {bot.hb_path} (máximo 120 s)"]


@pytest.mark.parametrize("pos_name", list(POSICIONES))
@pytest.mark.parametrize("mode", list(CONFIGS_POR_MODO))
def test_bot_sin_env_identico_y_no_lee_nada(wbot, monkeypatch, caplog, mode, pos_name):
    caplog.set_level(logging.INFO)

    def check_prohibido(self):
        raise AssertionError("sin la env no se lee el latido")

    monkeypatch.setattr(WatcherHeartbeat, "check", check_prohibido)
    bot = wbot(None, mode=mode, **POSICIONES[pos_name])
    assert bot.watcher is None
    assert bot.watcher_sin_latido() is False
    calls = run_cycle(bot)
    assert calls["calc_orders"] == 1  # el flujo normal de hoy
    assert watcher_records(caplog) == []


@pytest.mark.parametrize("pos_name", list(POSICIONES))
@pytest.mark.parametrize("mode", list(CONFIGS_POR_MODO))
@pytest.mark.parametrize(
    "hb, c3_mode",
    [
        ("fresco", "real"),
        ("fresco", "off"),
        ("viejo", "off"),
        ("fresco", "dry_run"),
        ("viejo", "dry_run"),
        ("ausente", "real"),  # nunca hubo uno válido en este proceso
    ],
)
def test_bot_sin_bloqueo_identico_a_sin_env(wbot, mode, pos_name, hb, c3_mode):
    ref = snapshot(run_cycle(wbot(None, mode=mode, **POSICIONES[pos_name])))
    assert ref[0] == 1
    bot = wbot(hb, c3_mode=c3_mode, mode=mode, **POSICIONES[pos_name])
    assert bot.watcher is not None
    assert snapshot(run_cycle(bot)) == ref


@pytest.mark.parametrize("pos_name", list(POSICIONES))
@pytest.mark.parametrize("mode", list(CONFIGS_POR_MODO))
def test_bot_latido_viejo_en_real_cancela_entradas_y_conserva_cierres(wbot, caplog, mode, pos_name):
    caplog.set_level(logging.INFO)
    bot = wbot("viejo", mode=mode, **POSICIONES[pos_name])
    assert bot.passivbot_mode == mode
    assert bot.position["wallet_balance"] == 1000.0  # con capital propio: solo el dead-man
    calls = run_cycle(bot)
    assert calls["calc_orders"] == 0
    assert ids(calls["cancel"]) == [1, 3]  # solo las entradas
    assert calls["create"] == []
    criticals = watcher_records(caplog, logging.CRITICAL)
    assert len(criticals) == 1
    msg = criticals[0].getMessage()
    assert "C3 en modo real sin latido del watcher" in msg
    assert "último latido hace 500 s, máximo 120 s" in msg
    assert rescate_records(caplog, logging.CRITICAL) == []


@pytest.mark.parametrize("rotura", ["ausente", "invalido"])
def test_bot_archivo_roto_despues_de_real_valido_bloquea(wbot, rotura):
    bot = wbot("fresco")
    assert run_cycle(bot)["calc_orders"] == 1
    if rotura == "ausente":
        bot.hb_path.unlink()
    else:
        bot.hb_path.write_text('{"c3_mode": "real", "ts": ')
    bot.calls.update(calc_orders=0, cancel=[], create=[])
    calls = run_cycle(bot)
    assert calls["calc_orders"] == 0
    assert ids(calls["cancel"]) == [1, 3]
    assert calls["create"] == []


def test_bot_json_anidado_de_mas_despues_de_real_cancela_entradas(wbot):
    # antes, el RecursionError salía de cancel_and_create: no se cancelaba nada y las entradas
    # que ya estaban en el exchange seguían vivas
    bot = wbot("fresco")
    assert run_cycle(bot)["calc_orders"] == 1
    bot.hb_path.write_text(JSON_ANIDADO_DE_MAS)
    bot.calls.update(calc_orders=0, cancel=[], create=[])
    calls = run_cycle(bot)
    assert calls["calc_orders"] == 0
    assert ids(calls["cancel"]) == [1, 3]
    assert calls["create"] == []


@pytest.mark.parametrize("rotura", ["ausente", "invalido"])
def test_bot_archivo_roto_sin_real_previo_no_bloquea(wbot, rotura):
    bot = wbot(rotura)
    assert run_cycle(bot)["calc_orders"] == 1


def test_bot_reanuda_cuando_vuelve_a_latir(wbot, caplog):
    caplog.set_level(logging.INFO)
    bot = wbot("viejo", psize_long=0.01, pprice_long=30500.0)
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0
    write_hb(bot.hb_path, "real", NOW - 1)  # el watcher vuelve a latir
    bot.open_orders = []
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 1
    assert any(o["side"] == "buy" and o["position_side"] == "long" for o in bot.calls["create"])
    msgs = [r.getMessage() for r in watcher_records(caplog, logging.INFO)]
    assert (
        "watcher: el watcher late de nuevo (o C3 ya no está en real), se vuelven a calcular las órdenes"
        in msgs
    )


def test_bot_reanuda_si_c3_pasa_a_off(wbot):
    bot = wbot("viejo")
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0
    write_hb(bot.hb_path, "off", NOW - 500)
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 1


def test_bot_critical_rate_limited(wbot, caplog):
    caplog.set_level(logging.INFO)
    bot = wbot("viejo")
    for _ in range(3):
        run_cycle(bot)
    assert len(watcher_records(caplog, logging.CRITICAL)) == 1  # no spamea en cada tick
    bot.watcher_guard_log_interval = 0.0
    for _ in range(2):
        run_cycle(bot)
    assert len(watcher_records(caplog, logging.CRITICAL)) == 3


def test_bot_respeta_manual(wbot):
    bot = wbot("viejo", psize_long=0.01, pprice_long=30500.0)
    bot.long_mode = "manual"
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0
    assert ids(bot.calls["cancel"]) == [3]  # el lado long no se toca
    assert bot.calls["create"] == []


def test_bot_respeta_tp_only(wbot):
    bot = wbot("viejo", psize_long=0.01, pprice_long=30500.0)
    bot.long_mode = "tp_only"
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0
    assert ids(bot.calls["cancel"]) == [3]  # solo la entrada short
    assert bot.calls["create"] == []


def test_bot_panic_crea_el_cierre_de_panico(wbot):
    bot = wbot("viejo", psize_long=0.01, pprice_long=30500.0)
    bot.long_mode = "panic"
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0
    assert ids(bot.calls["cancel"]) == [1, 2, 3]
    assert bot.calls["create"] == bot.rescate_panic_closes("long")
    assert [o["custom_id"] for o in bot.calls["create"]] == ["long_panic_close"]


def test_bot_una_lectura_por_ciclo_aunque_relea_la_posicion(wbot):
    # modo websocket: ante una ientry cerca del precio, cancel_and_create relee la posición por
    # REST y vuelve a llamar a calc_orders. El latido se lee una sola vez por ciclo.
    bot = wbot("fresco", mode="neat_grid", ohlcv=False, open_orders=[])
    bot.price = bot.ob[0] = 30101.0
    bot.ob[1] = 30102.0
    lecturas = []
    real_check = bot.watcher.check

    def check_spy():
        lecturas.append(1)
        return real_check()

    bot.watcher.check = check_spy
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 2  # precondición: pasó por la relectura REST
    assert len(lecturas) == 1
    run_cycle(bot)
    assert len(lecturas) == 2


# ---------------------------------------------------------------- combinado con el guard del préstamo


def test_bot_los_dos_guards_activos_cancelan_una_vez(wbot, caplog):
    caplog.set_level(logging.INFO)
    bot = wbot("viejo", loan=1500.0)
    assert bot.position["wallet_balance"] == 0.0
    calls = run_cycle(bot)
    assert calls["calc_orders"] == 0
    assert ids(calls["cancel"]) == [1, 3]  # sin cancelaciones duplicadas
    assert calls["create"] == []
    assert len(rescate_records(caplog, logging.CRITICAL)) == 1
    assert len(watcher_records(caplog, logging.CRITICAL)) == 1


def test_bot_se_liberan_por_separado(wbot, caplog):
    caplog.set_level(logging.INFO)
    bot = wbot("viejo", loan=1500.0, psize_long=0.01, pprice_long=30500.0)
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0

    # vuelve el capital propio, pero el watcher sigue sin latir: sigue bloqueado
    write_state(bot.loan_path, 300.0, mode="real")
    assert asyncio.run(bot.update_position()) is True
    assert bot.position["wallet_balance"] == 700.0
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0
    rescate_infos = [r.getMessage() for r in rescate_records(caplog, logging.INFO)]
    assert "rescate: hay capital propio de nuevo, se vuelven a calcular las órdenes" in rescate_infos

    # el préstamo vuelve a cubrir todo y el watcher late: sigue bloqueado por el préstamo
    write_state(bot.loan_path, 1500.0, mode="real")
    assert asyncio.run(bot.update_position()) is True
    write_hb(bot.hb_path, "real", NOW - 1)
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 0
    watcher_infos = [r.getMessage() for r in watcher_records(caplog, logging.INFO)]
    assert any("el watcher late de nuevo" in m for m in watcher_infos)

    # los dos liberados: vuelve el flujo normal
    write_state(bot.loan_path, 300.0, mode="real")
    assert asyncio.run(bot.update_position()) is True
    bot.open_orders = []
    run_cycle(bot)
    assert bot.calls["calc_orders"] == 1
    assert any(o["side"] == "buy" and o["position_side"] == "long" for o in bot.calls["create"])


def test_bot_guard_del_prestamo_sin_cambios_con_latido_fresco(wbot, caplog):
    # el guard del préstamo se comporta igual con el dead-man activo y latiendo
    caplog.set_level(logging.INFO)
    bot = wbot("fresco", loan=1500.0)
    calls = run_cycle(bot)
    assert calls["calc_orders"] == 0
    assert ids(calls["cancel"]) == [1, 3]
    assert len(rescate_records(caplog, logging.CRITICAL)) == 1
    assert watcher_records(caplog, logging.CRITICAL) == []

