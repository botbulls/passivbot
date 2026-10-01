"""Modos long/short de forager leidos desde la config HJSON.

Sin dependencias pesadas (ccxt/numba) para poder testearlo aislado.

Claves opcionales en la config de forager:
    long_mode:  normal | graceful_stop | manual | panic | tp_only  (o n | gs | m | p | t)
    short_mode: idem

Si la clave no esta (o vale null / ""), forager se comporta exactamente igual que antes.
"""

from typing import Optional, Tuple

# alias aceptado -> codigo corto de passivbot.py (-lm / -sm)
# Coinciden con los aliases que acepta passivbot.py.
MODE_ALIASES = {
    "n": "n",
    "normal": "n",
    "gs": "gs",
    "graceful_stop": "gs",
    "graceful-stop": "gs",
    "m": "m",
    "manual": "m",
    "p": "p",
    "panic": "p",
    "t": "t",
    "tp_only": "t",
    "tp-only": "t",
}

# Modos que fuerzan el flag en todos los paneles del lado.
FORCED_CODES = ("m", "p", "t")
# Modos en los que forager no abre simbolos nuevos para ese lado (mismo efecto que -gsl/-gss).
NO_NEW_SYMBOLS_CODES = ("gs", "m", "p", "t")

ACCEPTED_VALUES = (
    "normal (n), graceful_stop (gs, graceful-stop), manual (m), panic (p), tp_only (t, tp-only)"
)


def resolve_mode(value, key: str = "mode") -> Optional[str]:
    """Traduce el valor de long_mode/short_mode al codigo corto de passivbot.py.

    None o "" -> None (clave ausente: sin cambios de comportamiento).
    Valor desconocido o de tipo invalido -> ValueError.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"forager config: {key}={value!r} invalido (tipo {type(value).__name__}). "
            f"Valores aceptados: {ACCEPTED_VALUES}"
        )
    normalized = value.strip().lower()
    if normalized == "":
        return None
    if normalized not in MODE_ALIASES:
        raise ValueError(
            f"forager config: {key}={value!r} desconocido. Valores aceptados: {ACCEPTED_VALUES}"
        )
    return MODE_ALIASES[normalized]


def resolve_config_modes(config: dict) -> Tuple[Optional[str], Optional[str]]:
    """Valida y resuelve long_mode/short_mode de la config. Lanza ValueError si son invalidos."""
    return (
        resolve_mode(config.get("long_mode"), "long_mode"),
        resolve_mode(config.get("short_mode"), "short_mode"),
    )


def stops_new_symbols(code: Optional[str]) -> bool:
    """True si con ese modo forager no debe abrir simbolos nuevos en ese lado."""
    return code in NO_NEW_SYMBOLS_CODES


def pane_modes(
    long_enabled: bool,
    short_enabled: bool,
    lw: float,
    sw: float,
    long_code: Optional[str] = None,
    short_code: Optional[str] = None,
) -> Tuple[str, str]:
    """Devuelve (lm, sm) para un panel de passivbot.py.

    Sin override (None, "n" o "gs") es la regla historica de forager:
    "n" si el lado esta activo y su WE > 0, si no "gs".
    Con manual/panic/tp_only se fuerza ese codigo en todos los paneles del lado.
    """
    lm = "n" if long_enabled and lw > 0.0 else "gs"
    sm = "n" if short_enabled and sw > 0.0 else "gs"
    if long_code in FORCED_CODES:
        lm = long_code
    if short_code in FORCED_CODES:
        sm = short_code
    return lm, sm
