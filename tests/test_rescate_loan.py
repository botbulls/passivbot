"""Tests del rescate como colchón: passivbot dimensiona sobre capital propio.

Contrato: PASSIVBOT_LOAN_STATE apunta a un JSON
{"version": 1, "loan_outstanding_usdt": >= 0, "updated_at": ISO8601, "mode": real|dry_run}
y el balance para dimensionar es max(0, wallet - préstamo).
"""

import asyncio
import json
import logging
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from rescate_loan import ENV_VAR, LoanState, LoanStateError, parse_loan_state  # noqa: E402


def write_state(path, loan, mode="dry_run", version=1, updated_at="2026-10-21T12:00:00Z"):
    payload = {
        "version": version,
        "loan_outstanding_usdt": loan,
        "updated_at": updated_at,
        "mode": mode,
    }
    tmp = str(path) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, str(path))


def rescate_records(caplog, level=None):
    return [
        r
        for r in caplog.records
        if r.getMessage().startswith("rescate:") and (level is None or r.levelno == level)
    ]


# ---------------------------------------------------------------- lector del contrato


def test_from_env_sin_variable_es_none():
    assert LoanState.from_env({}) is None
    assert LoanState.from_env({ENV_VAR: ""}) is None


def test_from_env_con_variable(tmp_path):
    path = str(tmp_path / "loan_state.json")
    state = LoanState.from_env({ENV_VAR: path})
    assert isinstance(state, LoanState)
    assert state.path == path


def test_archivo_ausente_es_prestamo_cero(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    loan = LoanState(str(tmp_path / "no_existe.json"))
    assert loan.read()["loan_outstanding_usdt"] == 0.0
    wallet = 1234.5678
    assert loan.balance_para_dimensionar(wallet) is wallet
    assert rescate_records(caplog) == []


def test_archivo_valido_resta_el_prestamo(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0, mode="real")
    loan = LoanState(str(path))
    sized = loan.balance_para_dimensionar(1000.0)
    assert sized == 700.0
    assert isinstance(sized, float)
    msgs = [r.getMessage() for r in rescate_records(caplog, logging.INFO)]
    assert msgs == [
        "rescate: balance para dimensionar 700.0 (wallet 1000.0 - préstamo 300.0) modo real"
    ]


def test_log_no_se_repite_si_no_cambia(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    loan = LoanState(str(path))
    for _ in range(5):
        loan.balance_para_dimensionar(1000.0)
    assert len(rescate_records(caplog, logging.INFO)) == 1
    loan.balance_para_dimensionar(1100.0)
    assert len(rescate_records(caplog, logging.INFO)) == 2


def test_prestamo_entero_se_acepta(tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 300)
    assert LoanState(str(path)).balance_para_dimensionar(1000.0) == 700.0


def test_prestamo_cero_no_toca_el_balance(tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 0.0)
    loan = LoanState(str(path))
    for wallet in (1000.0, 0.0, -5.0):
        assert loan.balance_para_dimensionar(wallet) is wallet


def test_prestamo_mayor_que_wallet_da_cero(tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 1500.0)
    sized = LoanState(str(path)).balance_para_dimensionar(1000.0)
    assert sized == 0.0
    assert isinstance(sized, float)


def test_se_relee_en_cada_actualizacion(tmp_path):
    path = tmp_path / "loan_state.json"
    loan = LoanState(str(path))
    assert loan.balance_para_dimensionar(1000.0) == 1000.0
    write_state(path, 250.0)
    assert loan.balance_para_dimensionar(1000.0) == 750.0
    write_state(path, 100.0)
    assert loan.balance_para_dimensionar(1000.0) == 900.0
    write_state(path, 0.0)
    assert loan.balance_para_dimensionar(1000.0) == 1000.0


def test_invalido_despues_de_valido_conserva_el_ultimo(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    loan = LoanState(str(path))
    assert loan.balance_para_dimensionar(1000.0) == 700.0
    path.write_text('{"version": 1, "loan_outstanding_usdt": ')  # escritura cortada
    assert loan.balance_para_dimensionar(1000.0) == 700.0
    assert loan.balance_para_dimensionar(1000.0) == 700.0
    errors = rescate_records(caplog, logging.ERROR)
    assert len(errors) == 2  # un ERROR por lectura mientras siga inválido
    for e in errors:
        assert "se conserva el último préstamo válido (300.0)" in e.getMessage()
    write_state(path, 200.0)
    assert loan.balance_para_dimensionar(1000.0) == 800.0
    assert any("válido de nuevo" in r.getMessage() for r in rescate_records(caplog, logging.INFO))


def test_invalido_sin_valido_previo_usa_cero(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "loan_state.json"
    path.write_text("no es json")
    loan = LoanState(str(path))
    assert loan.balance_para_dimensionar(1000.0) == 1000.0
    errors = rescate_records(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "nunca hubo un estado válido, se usa préstamo 0" in errors[0].getMessage()


def test_invalido_loguea_error_en_cada_lectura(tmp_path, caplog):
    # forager relanza los panes cada hora: un solo ERROR por proceso se pierde y el préstamo 0
    # (o el último válido) queda aplicado sin ruido. Mientras el archivo siga inválido, cada
    # lectura tiene que dejar un ERROR.
    caplog.set_level(logging.INFO)
    path = tmp_path / "loan_state.json"
    path.write_text(
        '{"version": 1, "loan_outstanding_usdt": "300.00", "updated_at": "x", "mode": "real"}'
    )
    loan = LoanState(str(path))
    for _ in range(3):
        assert loan.balance_para_dimensionar(1000.0) == 1000.0
    errors = rescate_records(caplog, logging.ERROR)
    assert len(errors) == 3
    assert all("loan_outstanding_usdt no es numérico" in e.getMessage() for e in errors)
    write_state(path, 300.0, mode="real")
    assert loan.balance_para_dimensionar(1000.0) == 700.0
    assert len(rescate_records(caplog, logging.ERROR)) == 3


def test_dry_run_resta_igual_que_real_contrato_v1(tmp_path):
    # El contrato v1 no distingue por mode: dry_run también se resta. Si se decide cambiarlo,
    # tiene que cambiar a la vez en el servicio rescate y acá; este test lo deja explícito.
    path = tmp_path / "loan_state.json"
    for mode in ("real", "dry_run"):
        write_state(path, 300.0, mode=mode)
        assert LoanState(str(path)).balance_para_dimensionar(1000.0) == 700.0


def test_sin_capital_propio_solo_con_prestamo(tmp_path):
    path = tmp_path / "loan_state.json"
    loan = LoanState(str(path))
    assert loan.balance_para_dimensionar(0.0) == 0.0  # archivo ausente: préstamo 0
    assert loan.sin_capital_propio is False
    write_state(path, 1000.0)
    assert loan.balance_para_dimensionar(1000.0) == 0.0
    assert loan.sin_capital_propio is True
    assert loan.balance_para_dimensionar(1500.0) == 500.0
    assert loan.sin_capital_propio is False
    write_state(path, 0.0)
    loan.balance_para_dimensionar(1000.0)
    write_state(path, 2000.0)
    loan.balance_para_dimensionar(1000.0)
    assert loan.sin_capital_propio is True
    write_state(path, 0.0)
    assert loan.balance_para_dimensionar(0.0) == 0.0
    assert loan.sin_capital_propio is False


def test_ilegible_conserva_el_ultimo(tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    loan = LoanState(str(path))
    assert loan.balance_para_dimensionar(1000.0) == 700.0
    os.remove(path)
    os.mkdir(path)  # un directorio en lugar del archivo: no se puede leer
    assert loan.balance_para_dimensionar(1000.0) == 700.0


def test_ausente_despues_de_valido_es_cero(tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    loan = LoanState(str(path))
    assert loan.balance_para_dimensionar(1000.0) == 700.0
    os.remove(path)
    assert loan.balance_para_dimensionar(1000.0) == 1000.0


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        "null",
        '{"version": 1, "loan_outstanding_usdt": NaN, "updated_at": "x", "mode": "real"}',
        '{"version": 1, "loan_outstanding_usdt": Infinity, "updated_at": "x", "mode": "real"}',
        '{"version": 1, "loan_outstanding_usdt": -1, "updated_at": "x", "mode": "real"}',
        '{"version": 1, "loan_outstanding_usdt": true, "updated_at": "x", "mode": "real"}',
        '{"version": 1, "loan_outstanding_usdt": "300", "updated_at": "x", "mode": "real"}',
        '{"version": 1, "loan_outstanding_usdt": 1' + "0" * 400 + ', "updated_at": "x", "mode": "real"}',
        '{"version": 2, "loan_outstanding_usdt": 1, "updated_at": "x", "mode": "real"}',
        '{"version": true, "loan_outstanding_usdt": 1, "updated_at": "x", "mode": "real"}',
        '{"loan_outstanding_usdt": 1, "updated_at": "x", "mode": "real"}',
        '{"version": 1, "updated_at": "x", "mode": "real"}',
        '{"version": 1, "loan_outstanding_usdt": 1, "updated_at": "x", "mode": "live"}',
        '{"version": 1, "loan_outstanding_usdt": 1, "updated_at": "x"}',
        '{"version": 1, "loan_outstanding_usdt": 1, "updated_at": "", "mode": "real"}',
        '{"version": 1, "loan_outstanding_usdt": 1, "mode": "real"}',
    ],
)
def test_parse_rechaza_contenido_fuera_de_contrato(raw):
    with pytest.raises(LoanStateError):
        parse_loan_state(raw)


def test_parse_acepta_el_contrato():
    state = parse_loan_state(
        '{"version": 1, "loan_outstanding_usdt": 12.5, "updated_at": "2026-10-21T12:00:00Z", "mode": "dry_run"}'
    )
    assert state["loan_outstanding_usdt"] == 12.5
    assert state["mode"] == "dry_run"


# ---------------------------------------------------------------- integración con passivbot.Bot


@pytest.fixture(scope="module")
def pb():
    import passivbot

    return passivbot


CONFIGS_POR_MODO = {
    "clock": "configs/live/clock_mode.example.json",
    "neat_grid": "configs/live/neat_grid_mode.example.json",
    "recursive_grid": "configs/live/recursive_grid_mode.example.json",
}


@pytest.fixture
def make_bot(pb, monkeypatch):
    from procedures import load_live_config

    monkeypatch.setattr(pb, "load_exchange_key_secret_passphrase", lambda *a, **k: ("binance", "k", "s", "p"))
    monkeypatch.setattr(pb, "make_get_filepath", lambda p: p)
    monkeypatch.setattr(pb, "load_broker_code", lambda *a, **k: "")
    monkeypatch.delenv("PASSIVBOT_WATCHER_HEARTBEAT", raising=False)  # dead-man apagado acá

    def _make(assigned_balance=None, cross_wallet_pct=1.0, mode="clock"):
        cfg = load_live_config(os.path.join(ROOT, CONFIGS_POR_MODO[mode]))
        cfg.update(
            user="u",
            exchange="binance",
            symbol="BTCUSDT",
            market_type="futures",
            config_name="test",
            test_mode=False,
            inverse=False,
            qty_step=0.00001,
            price_step=0.1,
            min_qty=0.00001,
            min_cost=0.1,
            c_mult=1.0,
            assigned_balance=assigned_balance,
            cross_wallet_pct=cross_wallet_pct,
        )
        cfg["long"]["enabled"] = True
        cfg["short"]["enabled"] = True
        bot = pb.Bot(cfg)
        # lo que hace Bot._init en modo clock, sin red
        bot.xk = pb.create_xk(bot.config)
        for k in (
            "auto_unstuck_ema_dist",
            "auto_unstuck_wallet_exposure_threshold",
            "auto_unstuck_delay_minutes",
            "auto_unstuck_qty_pct",
            "delay_between_fills_ms_entry",
            "delay_between_fills_ms_close",
        ):
            bot.xk[k] = (0.0, 0.0)
        bot.long_mode = bot.short_mode = None
        bot.price = 30000.0
        bot.ob = [29999.0, 30001.0]
        bot.emas_long = np.array([30100.0, 30200.0, 30300.0])
        bot.emas_short = np.array([29900.0, 29800.0, 29700.0])
        bot.server_time = 10**13
        bot.position = position(0.0)
        bot.dump_log = lambda *a, **k: None
        return bot

    return _make


def position(wallet, psize_long=0.0, pprice_long=0.0):
    return {
        "long": {"size": psize_long, "price": pprice_long, "liquidation_price": 0.0},
        "short": {"size": 0.0, "price": 0.0, "liquidation_price": 0.0},
        "wallet_balance": wallet,
        "equity": wallet,
    }


def adjust_wallet_balance_original(balance, assigned_balance, cross_wallet_pct):
    """Copia literal del cálculo de hoy (antes del rescate)."""
    return (balance if assigned_balance is None else assigned_balance) * cross_wallet_pct


CASOS_BALANCE = [1000.0, 1234.56789, 0.0, -5.0, 1e-12, 987654.321]


@pytest.mark.parametrize("assigned_balance", [None, 500.0])
@pytest.mark.parametrize("cross_wallet_pct", [1.0, 0.5, 0.3])
def test_bot_sin_env_identico_a_hoy(make_bot, monkeypatch, caplog, assigned_balance, cross_wallet_pct):
    caplog.set_level(logging.INFO)
    monkeypatch.delenv(ENV_VAR, raising=False)
    bot = make_bot(assigned_balance, cross_wallet_pct)
    assert bot.rescate_loan is None
    for wallet in CASOS_BALANCE:
        got = bot.adjust_wallet_balance(wallet)
        expected = adjust_wallet_balance_original(wallet, assigned_balance, cross_wallet_pct)
        assert got == expected
        assert type(got) is type(expected)
        assert repr(got) == repr(expected)
    assert rescate_records(caplog) == []


def test_bot_env_vacia_es_como_sin_env(make_bot, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "")
    assert make_bot().rescate_loan is None


@pytest.mark.parametrize("cross_wallet_pct", [1.0, 0.5])
def test_bot_archivo_ausente_identico_a_hoy(make_bot, monkeypatch, tmp_path, cross_wallet_pct):
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "loan_state.json"))
    bot = make_bot(None, cross_wallet_pct)
    assert bot.rescate_loan is not None
    for wallet in CASOS_BALANCE:
        assert repr(bot.adjust_wallet_balance(wallet)) == repr(
            adjust_wallet_balance_original(wallet, None, cross_wallet_pct)
        )


def test_bot_resta_prestamo_antes_de_cross_wallet_pct(make_bot, monkeypatch, tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    monkeypatch.setenv(ENV_VAR, str(path))
    bot = make_bot(None, 0.5)
    assert bot.adjust_wallet_balance(1000.0) == 350.0  # (1000 - 300) * 0.5


def test_bot_assigned_balance_no_se_ve_afectado(make_bot, monkeypatch, tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    monkeypatch.setenv(ENV_VAR, str(path))
    bot = make_bot(500.0, 1.0)
    assert bot.adjust_wallet_balance(1000.0) == 500.0


def test_bot_prestamo_mayor_que_wallet(make_bot, monkeypatch, tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 5000.0)
    monkeypatch.setenv(ENV_VAR, str(path))
    assert make_bot().adjust_wallet_balance(1000.0) == 0.0


def test_update_position_rest_usa_capital_propio_y_relee(make_bot, monkeypatch, tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    monkeypatch.setenv(ENV_VAR, str(path))
    bot = make_bot()

    async def fetch_position():
        return position(1000.0)

    bot.fetch_position = fetch_position
    assert asyncio.run(bot.update_position()) is True
    assert bot.position["wallet_balance"] == 700.0
    write_state(path, 400.0)  # el servicio rescate actualiza el préstamo, sin reiniciar el bot
    assert asyncio.run(bot.update_position()) is True
    assert bot.position["wallet_balance"] == 600.0


def test_evento_websocket_usa_capital_propio(make_bot, monkeypatch, tmp_path):
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    monkeypatch.setenv(ENV_VAR, str(path))
    bot = make_bot()
    bot.position = position(1000.0)

    async def noop():
        return None

    bot.cancel_and_create = noop
    asyncio.run(bot.on_user_stream_event({"wallet_balance": 1000.0}))
    assert bot.position["wallet_balance"] == 700.0


def test_funciones_de_ordenes_reciben_el_balance_propio(pb, make_bot, monkeypatch, tmp_path):
    seen = []
    real = pb.calc_clock_entry_long

    def spy(balance, *args):
        seen.append(balance)
        return real(balance, *args)

    monkeypatch.setattr(pb, "calc_clock_entry_long", spy)

    async def fetch_position():
        return position(1000.0)

    def entry_long_qty(bot):
        return [o["qty"] for o in bot.calc_orders() if o["custom_id"] == "clock_entry_long"][0]

    # sin env: dimensiona sobre el wallet completo
    monkeypatch.delenv(ENV_VAR, raising=False)
    bot = make_bot()
    bot.fetch_position = fetch_position
    asyncio.run(bot.update_position())
    qty_sin_prestamo = entry_long_qty(bot)
    assert seen[-1] == 1000.0

    # con préstamo de 300: las funciones de órdenes ven 700
    path = tmp_path / "loan_state.json"
    write_state(path, 300.0)
    monkeypatch.setenv(ENV_VAR, str(path))
    bot = make_bot()
    bot.fetch_position = fetch_position
    asyncio.run(bot.update_position())
    qty_con_prestamo = entry_long_qty(bot)
    assert seen[-1] == 700.0
    assert qty_con_prestamo < qty_sin_prestamo


@pytest.mark.xfail(
    strict=True,
    raises=ZeroDivisionError,
    reason="limitación de v6.1: calc_orders llamado directo con balance 0 divide por cero (NOJIT). "
    "Con rescate el bot no llega acá: cancel_and_create corta antes (ver tests del guard).",
)
def test_calc_orders_con_balance_cero(make_bot):
    bot = make_bot()
    bot.position = position(0.0, psize_long=0.01, pprice_long=30500.0)
    bot.calc_orders()


# ---------------------------------------------------------------- guard sin capital propio


def oo(order_id, side, pside, qty=0.001, price=30000.0):
    return {
        "order_id": order_id,
        "symbol": "BTCUSDT",
        "price": price,
        "qty": qty,
        "type": "limit",
        "side": side,
        "position_side": pside,
        "timestamp": 0,
    }


OPEN_ORDERS = [
    oo(1, "buy", "long", price=29900.0),  # entrada long
    oo(2, "sell", "long", price=30600.0),  # cierre long
    oo(3, "sell", "short", price=30100.0),  # entrada short
    oo(4, "buy", "short", price=29400.0),  # cierre short
]

POSICIONES = {
    "sin_pos": {},
    "long": {"psize_long": 0.01, "pprice_long": 30500.0},
    "short": {"psize_short": -0.01, "pprice_short": 29500.0},
}


def position_full(wallet, psize_long=0.0, pprice_long=0.0, psize_short=0.0, pprice_short=0.0):
    pos = position(wallet, psize_long, pprice_long)
    pos["short"].update(size=psize_short, price=pprice_short)
    return pos


@pytest.fixture
def guard_bot(make_bot, monkeypatch, tmp_path):
    """Bot con rescate activo, exchange stubeado y registro de cancelaciones y creaciones."""

    def _make(mode="clock", wallet=1000.0, loan=1500.0, open_orders=OPEN_ORDERS, ohlcv=True, **pos):
        path = tmp_path / "loan_state.json"
        if loan is not None:
            write_state(path, loan, mode="real")
        monkeypatch.setenv(ENV_VAR, str(path))
        bot = make_bot(mode=mode)
        bot.ohlcv = ohlcv
        bot.delay_between_executions = 0
        bot.max_n_orders_per_batch = 5
        bot.max_n_cancellations_per_batch = 10
        bot.open_orders = [dict(o) for o in open_orders]
        bot.loan_path = path
        bot.wallet = wallet
        bot.pos_kwargs = pos

        async def fetch_position():
            return position_full(bot.wallet, **bot.pos_kwargs)

        bot.fetch_position = fetch_position
        bot.calls = {"calc_orders": 0, "cancel": [], "create": []}
        real_calc_orders = bot.calc_orders

        def calc_orders_spy():
            bot.calls["calc_orders"] += 1
            return real_calc_orders()

        bot.calc_orders = calc_orders_spy

        async def cancel_orders(orders):
            bot.calls["cancel"].extend(orders)
            return orders

        async def create_orders(orders):
            bot.calls["create"].extend(orders)
            return orders

        bot.cancel_orders = cancel_orders
        bot.create_orders = create_orders
        assert asyncio.run(bot.update_position()) is True
        return bot

    return _make


def ids(orders):
    return sorted(o["order_id"] for o in orders)


@pytest.mark.parametrize("pos_name", list(POSICIONES))
@pytest.mark.parametrize("mode", list(CONFIGS_POR_MODO))
def test_guard_sin_capital_cancela_entradas_y_conserva_cierres(guard_bot, caplog, mode, pos_name):
    caplog.set_level(logging.INFO)
    bot = guard_bot(mode=mode, **POSICIONES[pos_name])
    assert bot.passivbot_mode == mode
    assert bot.position["wallet_balance"] == 0.0
    asyncio.run(bot.cancel_and_create())  # sin el guard: ZeroDivisionError o entradas a min_qty
    assert bot.calls["calc_orders"] == 0
    assert ids(bot.calls["cancel"]) == [1, 3]  # solo las entradas
    assert bot.calls["create"] == []
    criticals = rescate_records(caplog, logging.CRITICAL)
    assert len(criticals) == 1
    assert "sin capital propio" in criticals[0].getMessage()


def test_guard_critical_cada_intervalo(guard_bot, caplog):
    caplog.set_level(logging.INFO)
    bot = guard_bot()
    for _ in range(3):
        asyncio.run(bot.cancel_and_create())
    assert len(rescate_records(caplog, logging.CRITICAL)) == 1  # no spamea en cada tick
    bot.rescate_guard_log_interval = 0.0
    for _ in range(2):
        asyncio.run(bot.cancel_and_create())
    assert len(rescate_records(caplog, logging.CRITICAL)) == 3


def test_guard_se_libera_cuando_baja_el_prestamo(guard_bot, caplog):
    caplog.set_level(logging.INFO)
    bot = guard_bot(psize_long=0.01, pprice_long=30500.0)
    asyncio.run(bot.cancel_and_create())
    assert bot.calls["calc_orders"] == 0
    write_state(bot.loan_path, 300.0, mode="real")  # devolución parcial
    assert asyncio.run(bot.update_position()) is True
    assert bot.position["wallet_balance"] == 700.0
    bot.open_orders = []
    asyncio.run(bot.cancel_and_create())
    assert bot.calls["calc_orders"] == 1
    assert any(o["side"] == "buy" and o["position_side"] == "long" for o in bot.calls["create"])
    msgs = [r.getMessage() for r in rescate_records(caplog, logging.INFO)]
    assert "rescate: hay capital propio de nuevo, se vuelven a calcular las órdenes" in msgs


@pytest.mark.parametrize(
    "env, assigned_balance, loan",
    [
        ("sin_env", None, None),
        ("archivo_ausente", None, None),
        ("prestamo_cero", None, 0.0),
        ("assigned_balance", 500.0, 1500.0),
    ],
)
def test_guard_no_se_activa_sin_prestamo_vigente(make_bot, monkeypatch, tmp_path, env, assigned_balance, loan):
    path = tmp_path / "loan_state.json"
    if env == "sin_env":
        monkeypatch.delenv(ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(ENV_VAR, str(path))
    if loan is not None:
        write_state(path, loan, mode="real")
    bot = make_bot(assigned_balance=assigned_balance)
    bot.adjust_wallet_balance(0.0 if assigned_balance is None else 1000.0)
    assert bot.rescate_sin_capital_propio() is False


def test_guard_respeta_manual(guard_bot):
    bot = guard_bot(psize_long=0.01, pprice_long=30500.0)
    bot.long_mode = "manual"
    asyncio.run(bot.cancel_and_create())
    assert ids(bot.calls["cancel"]) == [3]  # el lado long no se toca
    assert bot.calls["create"] == []


def test_guard_respeta_tp_only(guard_bot):
    # en tp_only el flujo normal nunca cancela entradas de ese lado: el guard tampoco
    bot = guard_bot(psize_long=0.01, pprice_long=30500.0)
    bot.long_mode = "tp_only"
    asyncio.run(bot.cancel_and_create())
    assert bot.calls["calc_orders"] == 0
    assert ids(bot.calls["cancel"]) == [3]  # solo la entrada short
    assert bot.calls["create"] == []


def test_guard_panic_crea_el_cierre_de_panico(guard_bot, make_bot, monkeypatch):
    bot = guard_bot(psize_long=0.01, pprice_long=30500.0)
    bot.long_mode = "panic"
    asyncio.run(bot.cancel_and_create())
    assert bot.calls["calc_orders"] == 0
    # panic cancela todo lo del lado long que no sea el cierre de pánico; short: solo la entrada
    assert ids(bot.calls["cancel"]) == [1, 2, 3]
    assert bot.calls["create"] == [
        {
            "side": "sell",
            "position_side": "long",
            "qty": 0.01,
            "price": 30001.0,
            "type": "limit",
            "reduce_only": True,
            "custom_id": "long_panic_close",
        }
    ]
    # el cierre del guard es el mismo que arma calc_orders con capital propio
    monkeypatch.delenv(ENV_VAR, raising=False)
    ref = make_bot()
    ref.long_mode = "panic"
    ref.position = position_full(1000.0, psize_long=0.01, pprice_long=30500.0)
    assert [o for o in ref.calc_orders() if o["custom_id"] == "long_panic_close"] == bot.calls["create"]
    ref.short_mode = "panic"
    ref.long_mode = None
    ref.position = position_full(1000.0, psize_short=-0.01, pprice_short=29500.0)
    bot.position = ref.position
    assert [o for o in ref.calc_orders() if o["custom_id"] == "short_panic_close"] == (
        bot.rescate_panic_closes("short")
    )


def test_guard_recheck_despues_del_update_rest_de_ientry(guard_bot):
    # modo websocket (no ohlcv): ante una ientry cerca del precio, cancel_and_create relee la
    # posición por REST y vuelve a llamar a calc_orders. Si en esa relectura el préstamo pasó a
    # cubrir todo el wallet, no tiene que calcular órdenes con balance 0.
    bot = guard_bot(mode="neat_grid", loan=0.0, ohlcv=False, open_orders=[])
    bot.price = bot.ob[0] = 30101.0
    bot.ob[1] = 30102.0
    near = [
        o
        for o in bot.calc_orders()
        if "ientry" in o["custom_id"] and abs(o["price"] - bot.price) / bot.price < 0.002
    ]
    assert near, "precondición: tiene que haber una ientry a menos de 0.2% del precio"
    bot.calls["calc_orders"] = 0
    write_state(bot.loan_path, 1500.0, mode="real")  # se inyecta mientras tanto
    asyncio.run(bot.cancel_and_create())
    assert bot.calls["calc_orders"] == 1  # solo la primera, antes de releer
    assert bot.position["wallet_balance"] == 0.0
    assert bot.calls["create"] == []
