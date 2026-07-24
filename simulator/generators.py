"""Generateurs de valeurs fictives.

Chaque generateur est une classe avec une methode ``compute(t, dt, ctx)`` qui
renvoie la valeur brute du tag a l'instant ``t`` (secondes depuis le demarrage).

``ctx`` est un dict {nom_tag: valeur} contenant les valeurs deja calculees lors
du tick courant : il permet aux generateurs ``expression`` de se referer aux
autres tags.

Chaque classe declare aussi ses parametres (``PARAMS``) et le type de tag
auquel elle s'applique (``KIND``) : l'IHM web s'en sert pour construire le
formulaire d'ajout de variable.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List

REGISTRY: Dict[str, type] = {}


def num(name: str, label: str, default: Any) -> Dict[str, Any]:
    return {"name": name, "label": label, "type": "number", "default": default}


def register(name: str, label: str, kind: str, params=()) -> Callable[[type], type]:
    """kind : 'numeric', 'bool' ou 'both'."""

    def deco(cls: type) -> type:
        REGISTRY[name] = cls
        cls.type_name = name
        cls.LABEL = label
        cls.KIND = kind
        cls.PARAMS = list(params)
        return cls

    return deco


class Generator:
    """Classe de base. ``params`` vient directement du fichier de config."""

    type_name = "base"
    LABEL = ""
    KIND = "both"
    PARAMS: List[Dict[str, Any]] = []

    def __init__(self, params: Dict[str, Any]):
        self.params = params or {}

    def p(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def compute(self, t: float, dt: float, ctx: Dict[str, Any]) -> Any:
        raise NotImplementedError

    def describe(self) -> str:
        if not self.params:
            return self.type_name
        items = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.type_name}({items})"

    def to_spec(self) -> Dict[str, Any]:
        """Representation serialisable vers le fichier de configuration."""
        return {"type": self.type_name, **self.params}


# --------------------------------------------------------------------------
# Generateurs numeriques
# --------------------------------------------------------------------------


@register("constant", "Constante", "both", [num("value", "Valeur", 0)])
class Constant(Generator):
    def compute(self, t, dt, ctx):
        return self.p("value", 0)


@register("manual", "Manuel (pilote depuis l'IHM ou le reseau)", "both",
          [num("value", "Valeur initiale", 0)])
class Manual(Generator):
    def __init__(self, params):
        super().__init__(params)
        self.value = self.p("value", 0)

    def compute(self, t, dt, ctx):
        return self.value

    def set(self, value):
        self.value = value

    def to_spec(self):
        return {"type": "manual", "value": self.value}


@register("sine", "Sinusoide", "numeric", [
    num("amplitude", "Amplitude", 1.0),
    num("offset", "Offset (valeur moyenne)", 0.0),
    num("period", "Periode (s)", 10.0),
    num("phase", "Phase (deg)", 0.0),
])
class Sine(Generator):
    def compute(self, t, dt, ctx):
        period = float(self.p("period", 10.0)) or 10.0
        phase = math.radians(float(self.p("phase", 0.0)))
        return float(self.p("offset", 0.0)) + float(self.p("amplitude", 1.0)) * math.sin(
            2.0 * math.pi * t / period + phase
        )


@register("cosine", "Cosinusoide", "numeric", [
    num("amplitude", "Amplitude", 1.0),
    num("offset", "Offset (valeur moyenne)", 0.0),
    num("period", "Periode (s)", 10.0),
    num("phase", "Phase (deg)", 0.0),
])
class Cosine(Generator):
    def compute(self, t, dt, ctx):
        period = float(self.p("period", 10.0)) or 10.0
        phase = math.radians(float(self.p("phase", 0.0)))
        return float(self.p("offset", 0.0)) + float(self.p("amplitude", 1.0)) * math.cos(
            2.0 * math.pi * t / period + phase
        )


@register("triangle", "Triangle (monte puis descend)", "numeric", [
    num("min", "Minimum", 0.0), num("max", "Maximum", 100.0),
    num("period", "Periode (s)", 10.0),
])
class Triangle(Generator):
    def compute(self, t, dt, ctx):
        lo = float(self.p("min", 0.0))
        hi = float(self.p("max", 100.0))
        period = float(self.p("period", 10.0)) or 10.0
        x = (t % period) / period          # 0 -> 1
        y = 2.0 * x if x < 0.5 else 2.0 * (1.0 - x)
        return lo + (hi - lo) * y


@register("sawtooth", "Dent de scie (monte puis retombe)", "numeric", [
    num("min", "Minimum", 0.0), num("max", "Maximum", 100.0),
    num("period", "Periode (s)", 10.0),
])
class Sawtooth(Generator):
    def compute(self, t, dt, ctx):
        lo = float(self.p("min", 0.0))
        hi = float(self.p("max", 100.0))
        period = float(self.p("period", 10.0)) or 10.0
        return lo + (hi - lo) * ((t % period) / period)


@register("square", "Creneau", "numeric", [
    num("min", "Niveau bas", 0.0), num("max", "Niveau haut", 100.0),
    num("period", "Periode (s)", 10.0), num("duty", "Rapport cyclique (0..1)", 0.5),
])
class Square(Generator):
    def compute(self, t, dt, ctx):
        period = float(self.p("period", 10.0)) or 10.0
        duty = float(self.p("duty", 0.5))
        return float(self.p("max", 100.0)) if (t % period) / period < duty else float(self.p("min", 0.0))


@register("ramp", "Rampe (monte en continu)", "numeric", [
    num("start", "Depart", 0.0),
    num("rate", "Pente (unites/s)", 1.0),
    num("min", "Minimum", 0.0), num("max", "Maximum", 100.0),
    {"name": "mode", "label": "En butee", "type": "choice", "default": "wrap",
     "choices": ["wrap", "clamp", "bounce"]},
])
class Ramp(Generator):
    def __init__(self, params):
        super().__init__(params)
        self.value = float(self.p("start", self.p("min", 0.0)))
        self.direction = 1.0

    def compute(self, t, dt, ctx):
        lo = float(self.p("min", 0.0))
        hi = float(self.p("max", 100.0))
        rate = float(self.p("rate", 1.0))
        mode = str(self.p("mode", "wrap")).lower()

        self.value += rate * dt * self.direction

        if mode == "clamp":
            self.value = min(max(self.value, lo), hi)
        elif mode == "bounce":
            if self.value > hi:
                self.value = hi - (self.value - hi)
                self.direction = -self.direction
            elif self.value < lo:
                self.value = lo + (lo - self.value)
                self.direction = -self.direction
        else:  # wrap
            span = hi - lo
            if span > 0:
                self.value = lo + ((self.value - lo) % span)
        return self.value


@register("counter", "Compteur", "numeric", [
    num("start", "Depart", 0), num("step", "Pas", 1),
    num("period", "Periode entre 2 pas (s)", 1.0), num("max", "Rebouclage a", 65535),
])
class Counter(Generator):
    def __init__(self, params):
        super().__init__(params)
        self.value = float(self.p("start", 0))
        self._last = None

    def compute(self, t, dt, ctx):
        period = float(self.p("period", 1.0)) or 1.0
        tick = int(t // period)
        if self._last is None:
            self._last = tick
        while self._last < tick:
            self.value += float(self.p("step", 1))
            self._last += 1
        top = self.p("max")
        if top is not None:
            start = float(self.p("start", 0))
            span = float(top) - start
            if span > 0:
                self.value = start + ((self.value - start) % span)
        return self.value


@register("random", "Aleatoire uniforme", "numeric", [
    num("min", "Minimum", 0.0), num("max", "Maximum", 100.0),
    num("period", "Renouvelle toutes les (s)", 1.0),
])
class RandomUniform(Generator):
    def __init__(self, params):
        super().__init__(params)
        self.value = None
        self._last = None

    def compute(self, t, dt, ctx):
        period = float(self.p("period", 1.0)) or 1.0
        tick = int(t // period)
        if self.value is None or tick != self._last:
            self._last = tick
            self.value = random.uniform(float(self.p("min", 0.0)), float(self.p("max", 100.0)))
        return self.value


@register("gaussian", "Bruit gaussien", "numeric", [
    num("mean", "Moyenne", 0.0), num("sigma", "Ecart-type", 1.0),
    num("period", "Renouvelle toutes les (s)", 1.0),
])
class Gaussian(Generator):
    def __init__(self, params):
        super().__init__(params)
        self.value = None
        self._last = None

    def compute(self, t, dt, ctx):
        period = float(self.p("period", 1.0)) or 1.0
        tick = int(t // period)
        if self.value is None or tick != self._last:
            self._last = tick
            self.value = random.gauss(float(self.p("mean", 0.0)), float(self.p("sigma", 1.0)))
        return self.value


@register("random_walk", "Marche aleatoire", "numeric", [
    num("start", "Depart", 0.0), num("step", "Amplitude max par seconde", 1.0),
    num("min", "Minimum", 0.0), num("max", "Maximum", 100.0),
])
class RandomWalk(Generator):
    def __init__(self, params):
        super().__init__(params)
        self.value = float(self.p("start", 0.0))

    def compute(self, t, dt, ctx):
        step = float(self.p("step", 1.0)) * dt
        self.value += random.uniform(-step, step)
        self.value = min(max(self.value, float(self.p("min", -1e9))), float(self.p("max", 1e9)))
        return self.value


@register("sequence", "Suite de paliers", "both", [
    {"name": "steps", "label": "Paliers (une ligne : valeur, duree en s)",
     "type": "steps", "default": [{"value": 0, "duration": 5}, {"value": 100, "duration": 5}]},
    {"name": "loop", "label": "Boucler", "type": "bool", "default": True},
])
class Sequence(Generator):
    def compute(self, t, dt, ctx):
        steps = self.p("steps") or [{"value": 0, "duration": 1}]
        total = sum(float(s.get("duration", 1)) for s in steps) or 1.0
        pos = (t % total) if self.p("loop", True) else min(t, total - 1e-9)
        acc = 0.0
        for s in steps:
            acc += float(s.get("duration", 1))
            if pos < acc:
                return s.get("value", 0)
        return steps[-1].get("value", 0)


@register("expression", "Formule", "both", [
    {"name": "expr", "label": "Formule Python (t, math, autres tags)",
     "type": "text", "default": "0"},
])
class Expression(Generator):
    """Formule Python evaluee a chaque tick.

    Variables disponibles : ``t`` (secondes), ``dt``, le module ``math``, et le
    nom des tags declares AVANT celui-ci. Exemple : ``temperature * 1.8 + 32``
    """

    _SAFE = {
        "abs": abs, "min": min, "max": max, "round": round, "int": int,
        "float": float, "bool": bool, "pow": pow, "sum": sum, "len": len,
    }

    def compute(self, t, dt, ctx):
        expr = self.p("expr", "0")
        env = {"math": math, "t": t, "dt": dt, "random": random}
        env.update(ctx)
        return eval(expr, {"__builtins__": self._SAFE}, env)  # noqa: S307 - config de confiance


# --------------------------------------------------------------------------
# Generateurs booleens
# --------------------------------------------------------------------------


@register("toggle", "Allume / eteint periodiquement", "bool", [
    num("period", "Periode (s)", 2.0), num("duty", "Part allumee (0..1)", 0.5),
    {"name": "invert", "label": "Inverser", "type": "bool", "default": False},
])
class Toggle(Generator):
    def compute(self, t, dt, ctx):
        period = float(self.p("period", 2.0)) or 2.0
        duty = float(self.p("duty", 0.5))
        state = ((t % period) / period) < duty
        return (not state) if self.p("invert", False) else state


@register("pulse", "Impulsion breve", "bool", [
    num("period", "Periode (s)", 10.0), num("width", "Duree du niveau haut (s)", 0.5),
])
class Pulse(Generator):
    def compute(self, t, dt, ctx):
        period = float(self.p("period", 10.0)) or 10.0
        return (t % period) < float(self.p("width", 0.5))


@register("random_bool", "Booleen aleatoire", "bool", [
    num("probability", "Probabilite d'etre vrai (0..1)", 0.5),
    num("period", "Renouvelle toutes les (s)", 1.0),
])
class RandomBool(Generator):
    def __init__(self, params):
        super().__init__(params)
        self.value = False
        self._last = None

    def compute(self, t, dt, ctx):
        period = float(self.p("period", 1.0)) or 1.0
        tick = int(t // period)
        if tick != self._last:
            self._last = tick
            self.value = random.random() < float(self.p("probability", 0.5))
        return self.value


def catalog() -> List[Dict[str, Any]]:
    """Description des generateurs, consommee par l'IHM web."""
    out = []
    for name, cls in sorted(REGISTRY.items(), key=lambda kv: kv[1].LABEL):
        out.append({
            "type": name,
            "label": cls.LABEL,
            "kind": cls.KIND,
            "params": cls.PARAMS,
        })
    return out


def build(spec: Any) -> Generator:
    """Construit un generateur a partir de la config.

    Accepte ``{type: sine, period: 10}`` ou simplement la chaine ``"sine"``.
    """
    if isinstance(spec, str):
        spec = {"type": spec}
    spec = dict(spec or {})
    kind = spec.pop("type", "constant")
    if kind not in REGISTRY:
        raise ValueError(
            f"Generateur inconnu : '{kind}'. Disponibles : {', '.join(sorted(REGISTRY))}"
        )
    return REGISTRY[kind](spec)
