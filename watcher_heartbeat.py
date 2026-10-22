"""Dead-man del watcher de C3 (stop/reinicio): sin latido en modo real, no se abren entradas.

El watcher de C3 escribe un JSON atómico (tmp + os.replace) con su modo y la hora del
último latido. passivbot lo lee una vez por ciclo de órdenes (Bot.cancel_and_create).
Si C3 está en modo real y el watcher dejó de latir, passivbot no abre entradas nuevas:
cancela las entradas abiertas y conserva los cierres, con el mismo mecanismo que el guard
sin capital propio del rescate.

Contrato::

    {"c3_mode": "off" | "dry_run" | "real", "ts": <latido>}

* ``ts`` es la hora del último latido: segundos epoch (int o float) o un texto ISO8601
  (``2026-10-22T12:00:00+00:00``, ``2026-10-22T12:00:00Z``). Un ISO8601 sin zona horaria
  se toma como UTC.
* Se toleran claves de más (se ignoran). El watcher del dashboard (futuresboard/c3.py)
  escribe además ``version`` y ``c3_parado``; passivbot hoy no usa ninguna de las dos.
* Qué significa ``ts`` lo define el watcher, y de eso depende lo que cubre el dead-man: si
  se refresca en cada tick, también en los que no pudieron leer la cuenta de futuros, el
  dead-man cubre "el proceso del watcher vive"; si se refresca solo en los ticks en que C3
  leyó el capital y evaluó el piso sin error, cubre "C3 está protegiendo".

Variables de entorno:

* ``PASSIVBOT_WATCHER_HEARTBEAT``: ruta del archivo. Sin la env (o vacía) no se lee nada
  y el bot se comporta igual que hoy.
* ``PASSIVBOT_WATCHER_MAX_AGE_SECONDS``: edad máxima del latido en segundos (default 120,
  mínimo 45). Un valor no numérico, menor al mínimo, NaN o infinito usa el default y se
  loguea el error. En el deploy tiene que ser >= 3 x intervalo del watcher + 30, el mismo
  criterio con el que el dashboard da por vivo al servicio (con el intervalo default de
  15 s alcanza 75; el mínimo 45 sale del intervalo mínimo de 5 s).

Reglas (``check`` devuelve el motivo para no abrir entradas, o None):

* Archivo válido con ``c3_mode`` off o dry_run: sin efecto.
* Archivo válido con ``c3_mode`` real: bloquea si el latido tiene más de MAX_AGE segundos,
  o si está más de MAX_AGE segundos en el futuro (un ts futuro dejaría el latido "fresco"
  para siempre). No depende de lo leído antes: un proceso recién arrancado que encuentra un
  latido viejo en real bloquea en la primera lectura.
* Archivo ausente, ilegible o inválido (incluido cualquier error inesperado al leerlo o
  parsearlo): bloquea solo si el último archivo válido leído por este proceso estaba en
  real. Si nunca hubo uno válido, o el último era off/dry_run, no tiene efecto (y el
  archivo inválido se loguea).

Logs: INFO con el primer latido válido de cada proceso y en cada cambio de ``c3_mode``.
Para verificar un deploy hay que ver esa línea ("primer latido válido ... modo real") y
que NO aparezca el ERROR "nunca hubo un latido válido".

Residual conocido: ese último modo vive en memoria y forager mata y relanza todos los
procesos de símbolo en cada ciclo (``update_interval_minutes``). Si el archivo falta o es
inválido, después del siguiente ciclo de forager ningún símbolo bloquea (sí loguean
ERROR). El caso principal, un watcher muerto que deja el archivo con un latido viejo,
bloquea siempre, también después de un ciclo de forager.

Este módulo no depende de nada del bot para poder testearse aislado.
"""

import datetime as dt
import json
import logging
import math
import os
import time

ENV_PATH = "PASSIVBOT_WATCHER_HEARTBEAT"
ENV_MAX_AGE = "PASSIVBOT_WATCHER_MAX_AGE_SECONDS"
DEFAULT_MAX_AGE_SECONDS = 120.0
# 3 x 5 + 30: con el intervalo mínimo del servicio rescate (5 s), el criterio con el que el
# dashboard da por vivo al servicio (3 x intervalo + 30). Menos que esto es un dead-man que
# salta con el watcher sano.
MIN_MAX_AGE_SECONDS = 45.0
C3_MODES = ("off", "dry_run", "real")
LOG_INTERVAL_SECONDS = 50.0


class HeartbeatError(ValueError):
    pass


def parse_ts(ts) -> float:
    """Convierte el ts del contrato a segundos epoch. Levanta HeartbeatError si no sirve."""
    if isinstance(ts, bool):
        raise HeartbeatError(f"ts inválido: {ts!r}")
    if isinstance(ts, (int, float)):
        try:
            value = float(ts)
        except OverflowError:
            raise HeartbeatError(f"ts fuera de rango: {ts!r}")
    elif isinstance(ts, str) and ts.strip():
        text = ts.strip()
        if text[-1] in "zZ":
            text = text[:-1] + "+00:00"  # fromisoformat de Python 3.9 no acepta la Z
        try:
            moment = dt.datetime.fromisoformat(text)
        except ValueError:
            raise HeartbeatError(f"ts no es ISO8601: {ts!r}")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=dt.timezone.utc)
        try:
            value = moment.timestamp()
        except (OverflowError, ValueError, OSError):
            raise HeartbeatError(f"ts fuera de rango: {ts!r}")
    else:
        raise HeartbeatError(f"ts inválido: {ts!r}")
    if not math.isfinite(value) or value <= 0.0:
        raise HeartbeatError(f"ts fuera de rango: {ts!r}")
    return value


def parse_heartbeat(raw: str) -> dict:
    """Valida el contenido del archivo según el contrato. Devuelve {"c3_mode", "ts"} o levanta."""
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:  # RecursionError: JSON anidado de más
        raise HeartbeatError(f"JSON inválido: {e}")
    if not isinstance(data, dict):
        raise HeartbeatError("se esperaba un objeto JSON")
    mode = data.get("c3_mode")
    if mode not in C3_MODES:
        raise HeartbeatError(f"c3_mode inválido: {mode!r} (se espera off, dry_run o real)")
    return {"c3_mode": mode, "ts": parse_ts(data.get("ts"))}


def parse_max_age(raw):
    """Devuelve (max_age, error). Sin valor usa el default sin error."""
    if raw is None or str(raw).strip() == "":
        return DEFAULT_MAX_AGE_SECONDS, None
    try:
        value = float(str(raw).strip())
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or value < MIN_MAX_AGE_SECONDS:
        return DEFAULT_MAX_AGE_SECONDS, (
            f"{ENV_MAX_AGE}={raw!r} inválido (mínimo {MIN_MAX_AGE_SECONDS:g} s); "
            + f"se usa {DEFAULT_MAX_AGE_SECONDS:g} s"
        )
    return value, None


class WatcherHeartbeat:
    """Lee el latido del watcher y decide si hay que dejar de abrir entradas."""

    def __init__(self, path: str, max_age: float = DEFAULT_MAX_AGE_SECONDS, clock=time.time):
        self.path = path
        self.max_age = float(max_age)
        self.clock = clock
        self.last_valid_mode = None  # c3_mode del último archivo válido leído por este proceso
        self.log_interval = LOG_INTERVAL_SECONDS
        self._error_log_ts = 0.0
        self._last_error = None

    @classmethod
    def from_env(cls, environ=None, clock=time.time):
        """Devuelve un WatcherHeartbeat si la env de la ruta está seteada y no vacía; si no, None."""
        environ = os.environ if environ is None else environ
        path = environ.get(ENV_PATH)
        if not path:
            return None
        max_age, error = parse_max_age(environ.get(ENV_MAX_AGE))
        if error:
            logging.error(f"watcher: {error}")
        return cls(path, max_age=max_age, clock=clock)

    def check(self):
        """Lee el archivo una vez y devuelve el motivo para no abrir entradas, o None.

        No levanta excepciones: cualquier error al leer o parsear cuenta como archivo inválido
        (si escapara, el ciclo de órdenes se cortaría sin cancelar las entradas).
        """
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            return self._sin_archivo_valido(f"falta el archivo de latido {self.path}")
        except Exception as e:  # OSError, UnicodeDecodeError, MemoryError, ...
            return self._sin_archivo_valido(f"no se pudo leer {self.path}: {e!r}")
        try:
            hb = parse_heartbeat(raw)
        except HeartbeatError as e:
            return self._sin_archivo_valido(f"latido inválido en {self.path}: {e}")
        except Exception as e:
            return self._sin_archivo_valido(f"latido inválido en {self.path}: error inesperado {e!r}")
        self._log_modo(hb["c3_mode"])
        self.last_valid_mode = hb["c3_mode"]
        if self._last_error is not None:
            logging.info(f"watcher: latido válido de nuevo en {self.path}")
            self._last_error = None
        if hb["c3_mode"] != "real":
            return None
        age = self.clock() - hb["ts"]
        if age > self.max_age:
            return f"último latido hace {age:.0f} s, máximo {self.max_age:g} s"
        if age < -self.max_age:
            return f"latido con ts {-age:.0f} s en el futuro, máximo {self.max_age:g} s"
        return None

    def _log_modo(self, mode: str):
        """INFO con el primer latido válido del proceso y en cada cambio de c3_mode."""
        if mode == self.last_valid_mode:
            return
        efecto = "dead-man armado" if mode == "real" else "dead-man sin efecto"
        if self.last_valid_mode is None:
            logging.info(f"watcher: primer latido válido en {self.path}: C3 en modo {mode} ({efecto})")
        else:
            logging.info(f"watcher: C3 pasó de modo {self.last_valid_mode} a {mode} ({efecto})")

    def _sin_archivo_valido(self, error: str):
        armado = self.last_valid_mode == "real"
        now = self.clock()
        if error != self._last_error or now - self._error_log_ts >= self.log_interval:
            # rate-limited: se lee en cada ciclo y en websocket hay varios por minuto
            if armado:
                detail = "el último latido válido estaba en modo real"
            elif self.last_valid_mode is None:
                detail = "nunca hubo un latido válido en este proceso, sin efecto"
            else:
                detail = f"el último latido válido estaba en modo {self.last_valid_mode}, sin efecto"
            logging.error(f"watcher: {error}; {detail}")
            self._error_log_ts = now
            self._last_error = error
        return error if armado else None
