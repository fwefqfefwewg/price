"""Envoi des alertes Discord via webhook (avec gestion du rate-limit)."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import aiohttp

from .config import Config
from .parsing import store_label

log = logging.getLogger("notifier")
SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "NGN": "₦", "KES": "KSh", "EGP": "E£",
           "MAD": "MAD", "GHS": "GH₵", "XOF": "FCFA", "ZAR": "R", "AED": "AED", "SAR": "SAR",
           "CAD": "C$", "AUD": "A$", "CHF": "CHF"}
CONF_LABEL = {"high": "🟢 Élevée", "medium": "🟡 Moyenne", "low": "🔴 Faible"}


def money(value: float, currency: str | None) -> str:
    sym = SYMBOLS.get((currency or "EUR").upper(), currency or "")
    txt = f"{value:,.2f}".replace(",", "\u202f").replace(".", ",")
    return f"{txt} {sym}".rstrip()


def build_payload(brand: str, *, url, name, price, currency, ref, drop, image, reason,
                  discovered=False, seller=None, confidence="low", samples=0) -> dict:
    """Construit le message Discord (fonction pure, testable)."""
    store, flag = store_label(url)
    pct = round((drop or 0) * 100)
    color = 0xE74C3C if pct >= 80 else 0xE67E22 if pct >= 65 else 0xF1C40F

    price_line = f"💶 **{money(price, currency)}**"
    lines = []
    if ref:
        price_line += f"   ~~{money(ref, currency)}~~"
        lines.append(price_line)
        lines.append(f"📉 **-{pct} %**  •  économie ≈ **{money(max(ref - price, 0), currency)}**")
    else:
        lines.append(price_line)
    if confidence == "low":
        lines.append("⚠️ Peu de recul sur ce prix : vérifie la fiche avant d'acheter.")

    seller_txt = seller or store
    if seller and store.lower() not in seller.lower():
        seller_txt += " · vendeur tiers"
    reliability = CONF_LABEL.get(confidence, CONF_LABEL["low"])
    if samples:
        reliability += f" ({samples} relevés)"

    embed = {
        "author": {"name": f"{brand} • ERREUR DE PRIX POSSIBLE"
                   + (" • DÉCOUVERTE" if discovered else "")},
        "title": (name or url)[:250],
        "url": url,
        "color": color,
        "description": "\n".join(lines),
        "fields": [
            {"name": "🏪 Boutique", "value": f"{store} {flag}".strip(), "inline": True},
            {"name": "🛒 Vendeur", "value": seller_txt[:100], "inline": True},
            {"name": "✅ Fiabilité", "value": reliability, "inline": True},
            {"name": "🔎 Détection", "value": reason or "—", "inline": False},
            {"name": "🔗 Lien", "value": f"[Ouvrir l'offre]({url})", "inline": False},
        ],
        "footer": {"text": f"Powered by {brand}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if image and image.startswith("http"):
        embed["thumbnail"] = {"url": image}

    # le texte hors embed sert d'aperçu dans la notification du téléphone
    preview = "🚨 **ALERTE & DÉTECTION PAR NOTRE BOT**"
    if ref:
        preview += f" • -{pct} %"
    preview += f" • {(name or store)[:60]} • {money(price, currency)}"
    return {"content": preview, "embeds": [embed]}


class Notifier:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._session: aiohttp.ClientSession | None = None

    async def _post(self, url: str, payload: dict) -> bool:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        for _ in range(3):
            try:
                async with self._session.post(url, json=payload) as r:
                    if r.status == 429:
                        retry = (await r.json()).get("retry_after", 2)
                        await asyncio.sleep(float(retry))
                        continue
                    if r.status >= 400:
                        log.error("Discord HTTP %s: %s", r.status, (await r.text())[:200])
                        return False
                    return True
            except aiohttp.ClientError as e:
                log.error("Discord erreur réseau: %s", e)
                await asyncio.sleep(2)
        return False

    async def send_alert(self, **kw) -> bool:
        return await self._post(self.cfg.webhook, build_payload(self.cfg.brand, **kw))

    async def send_admin(self, text: str):
        url = self.cfg.admin_webhook
        if url:
            await self._post(url, {"content": text[:1900]})
        else:
            log.info("[admin] %s", text)

    async def close(self):
        if self._session:
            await self._session.close()
