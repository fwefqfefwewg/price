"""Logique de détection d'erreur de prix (pure, testable)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from .parsing import is_placeholder_price
from .scheduling import volatility

MAX_CV = 0.35        # au-delà, l'historique est trop instable pour servir de référence
CONF_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class Verdict:
    alert: bool
    reason: str = ""
    ref: float | None = None
    drop: float | None = None        # 0.6 = -60 %
    confidence: str = "low"          # low / medium / high
    samples: int = 0                 # nb de relevés utilisés pour la référence


def clean_history(history: list[float]) -> list[float]:
    """Retire les prix invalides et les prix factices (9999, 99999...)."""
    return [h for h in history if h and h > 0 and not is_placeholder_price(h)]


def reliable_reference(history: list[float], min_history: int) -> float | None:
    """Prix habituel (médiane) si l'historique est assez long ET stable, sinon None."""
    hist = clean_history(history)
    if len(hist) < min_history:
        return None
    cv = volatility(hist)
    if cv is not None and cv > MAX_CV:
        return None
    return statistics.median(hist)


def confidence(history: list[float], ref: float | None, old_price: float | None = None) -> str:
    """Fiabilité de la référence : nombre de relevés, stabilité, accord avec le prix barré."""
    n, pts = len(history), 0
    if n >= 6:
        pts += 2
    elif n >= 3:
        pts += 1
    cv = volatility(history)
    if cv is not None:
        if cv <= 0.05:
            pts += 2
        elif cv <= 0.15:
            pts += 1
    if old_price and ref and abs(old_price - ref) / ref <= 0.25:
        pts += 1
    return "high" if pts >= 4 else "medium" if pts >= 2 else "low"


def evaluate(price: float | None, history: list[float], threshold: float,
             max_price: float | None = None, min_history: int = 3) -> Verdict:
    if price is None or price <= 0 or is_placeholder_price(price):
        return Verdict(False, "prix invalide")

    hist = clean_history(history)
    ref = reliable_reference(hist, min_history)
    drop = (1 - price / ref) if ref else None
    conf = confidence(hist, ref) if ref else "low"

    if max_price is not None and price <= max_price:
        # seuil choisi par l'utilisateur : au moins « moyenne »
        return Verdict(True, "sous le prix max", ref, drop,
                       "medium" if conf == "low" else conf, len(hist))
    if ref and price <= ref * (1 - threshold):
        return Verdict(True, "chute de prix", ref, drop, conf, len(hist))
    return Verdict(False, "normal", ref, drop, conf, len(hist))


def evaluate_listing(price: float | None, old_price: float | None, history: list[float],
                     threshold: float, min_discount: float, min_ref_price: float,
                     use_old: bool = True, min_history: int = 2) -> Verdict:
    """Détection sur une page liste : chute vs scans précédents OU prix barré annoncé."""
    if price is None or price <= 0 or is_placeholder_price(price):
        return Verdict(False, "prix invalide")

    hist = clean_history(history)
    ref = reliable_reference(hist, min_history)

    if ref and ref >= min_ref_price and price <= ref * (1 - threshold):
        return Verdict(True, "chute vs scans précédents", ref, 1 - price / ref,
                       confidence(hist, ref, old_price), len(hist))

    if (use_old and old_price and not is_placeholder_price(old_price)
            and old_price >= min_ref_price and price <= old_price * (1 - min_discount)):
        conf = confidence(hist, ref, old_price) if ref else "low"
        return Verdict(True, "remise annoncée (prix barré)", old_price,
                       1 - price / old_price, conf, len(hist))
    return Verdict(False, "normal")
