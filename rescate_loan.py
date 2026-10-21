"""Lectura del estado del préstamo de rescate (colchón) para dimensionar sobre capital propio.

El servicio rescate escribe un JSON atómico (tmp + os.replace) con el préstamo vigente.
passivbot lo lee en cada actualización de balance y dimensiona sobre
``max(0, wallet_balance - loan_outstanding_usdt)``.

Contrato (versión 1)::

    {"version": 1, "loan_outstanding_usdt": <float >= 0>,
     "updated_at": "<ISO8601 UTC>", "mode": "real" | "dry_run"}

Reglas:

* Sin la variable de entorno ``PASSIVBOT_LOAN_STATE`` (o vacía) no se lee nada y el
  balance queda idéntico al de hoy.
* Archivo ausente => préstamo 0.
* Archivo inválido o ilegible => se conserva el último valor válido leído (o 0 si nunca
  hubo uno) y se loguea el error en cada lectura mientras siga inválido.

``sin_capital_propio`` queda en True cuando hay préstamo > 0 y el balance para dimensionar
da 0: passivbot lo usa para no calcular órdenes con balance 0 (ver Bot.cancel_and_create).

Este módulo no depende de nada del bot para poder testearse aislado.
"""

import json
import logging
import math
import os

ENV_VAR = "PASSIVBOT_LOAN_STATE"
CONTRACT_VERSION = 1
VALID_MODES = ("real", "dry_run")


class LoanStateError(ValueError):
    pass


def parse_loan_state(raw: str) -> dict:
    """Valida el contenido del archivo según el contrato. Devuelve el dict o levanta LoanStateError."""
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise LoanStateError(f"JSON inválido: {e}")
    if not isinstance(data, dict):
        raise LoanStateError("se esperaba un objeto JSON")
    version = data.get("version")
    if isinstance(version, bool) or version != CONTRACT_VERSION:
        raise LoanStateError(f"version no soportada: {version!r} (se espera {CONTRACT_VERSION})")
    loan = data.get("loan_outstanding_usdt")
    if isinstance(loan, bool) or not isinstance(loan, (int, float)):
        raise LoanStateError(f"loan_outstanding_usdt no es numérico: {loan!r}")
    try:
        loan = float(loan)
    except OverflowError:
        raise LoanStateError(f"loan_outstanding_usdt fuera de rango: {loan!r}")
    if not math.isfinite(loan) or loan < 0.0:
        raise LoanStateError(f"loan_outstanding_usdt fuera de rango: {loan!r}")
    mode = data.get("mode")
    if mode not in VALID_MODES:
        raise LoanStateError(f"mode inválido: {mode!r} (se espera real o dry_run)")
    updated_at = data.get("updated_at")
    if not isinstance(updated_at, str) or not updated_at:
        raise LoanStateError(f"updated_at inválido: {updated_at!r}")
    return {
        "version": CONTRACT_VERSION,
        "loan_outstanding_usdt": loan,
        "updated_at": updated_at,
        "mode": mode,
    }


class LoanState:
    """Lee el archivo del préstamo y calcula el balance para dimensionar."""

    def __init__(self, path: str):
        self.path = path
        self.last_valid = None  # dict del último estado válido (o None si nunca hubo uno)
        self.sin_capital_propio = False  # préstamo > 0 y balance para dimensionar 0
        self._last_error = None
        self._last_logged = None

    @classmethod
    def from_env(cls, environ=None):
        """Devuelve un LoanState si la env está seteada y no vacía; si no, None."""
        environ = os.environ if environ is None else environ
        path = environ.get(ENV_VAR)
        if not path:
            return None
        return cls(path)

    def read(self) -> dict:
        """Relee el archivo y devuelve el estado vigente según el contrato."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            state = {"loan_outstanding_usdt": 0.0, "mode": None, "updated_at": None}
            self.last_valid = state
            self._clear_error()
            return state
        except (OSError, UnicodeDecodeError) as e:
            return self._fallback(f"no se pudo leer {self.path}: {e}")
        try:
            state = parse_loan_state(raw)
        except LoanStateError as e:
            return self._fallback(f"estado del préstamo inválido en {self.path}: {e}")
        self.last_valid = state
        self._clear_error()
        return state

    def _fallback(self, error: str) -> dict:
        if self.last_valid is not None:
            state = self.last_valid
            detail = f"se conserva el último préstamo válido ({state['loan_outstanding_usdt']})"
        else:
            state = {"loan_outstanding_usdt": 0.0, "mode": None, "updated_at": None}
            detail = "nunca hubo un estado válido, se usa préstamo 0"
        # se loguea en cada lectura mientras siga inválido: un solo log por proceso se pierde
        # entre los reinicios de forager y el error queda silencioso.
        logging.error(f"rescate: {error}; {detail}")
        self._last_error = error
        return state

    def _clear_error(self):
        if self._last_error is not None:
            logging.info(f"rescate: estado del préstamo válido de nuevo en {self.path}")
            self._last_error = None

    def balance_para_dimensionar(self, wallet_balance: float) -> float:
        """max(0, wallet - préstamo). Con préstamo 0 devuelve el wallet sin tocar."""
        state = self.read()
        loan = state["loan_outstanding_usdt"]
        if loan <= 0.0:
            self._last_logged = None
            self.sin_capital_propio = False
            return wallet_balance
        sized = max(0.0, wallet_balance - loan)
        self.sin_capital_propio = sized <= 0.0
        key = (sized, wallet_balance, loan, state["mode"])
        if key != self._last_logged:
            logging.info(
                f"rescate: balance para dimensionar {sized} (wallet {wallet_balance} - préstamo {loan})"
                + f" modo {state['mode']}"
            )
            self._last_logged = key
        return sized
