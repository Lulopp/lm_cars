import requests
import statistics
import os
import re
from datetime import datetime
from collections import defaultdict

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "TU_CHAT_ID_AQUI")

DESCUENTO_MINIMO_PCT = 15
KM_PERCENTIL         = 30
RESULTADOS_POR_AUTO  = 50

MODELOS = [
    ("VW Gol / Gol Trend",     "volkswagen gol"),
    ("Toyota Hilux",           "toyota hilux"),
    ("Chevrolet Corsa/Classic", "chevrolet corsa"),
    ("VW Amarok",              "volkswagen amarok"),
    ("Ford Ranger",            "ford ranger"),
    ("Ford EcoSport",          "ford ecosport"),
    ("Toyota Corolla",         "toyota corolla"),
    ("Peugeot 208",            "peugeot 208"),
    ("Fiat Palio",             "fiat palio"),
    ("Ford Ka",                "ford ka"),
    ("Mercedes C200",          "mercedes benz c200"),
    ("Mercedes C250",          "mercedes benz c250"),
    ("VW Vento",               "volkswagen vento"),
    ("VW Golf",                "volkswagen golf"),
    ("VW Golf GTI",            "volkswagen golf gti"),
]
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}


def fetch_model(query: str, limit: int = 50) -> list[dict]:
    url    = "https://api.mercadolibre.com/sites/MLA/search"
    params = {"q": query, "category": "MLA1744", "limit": limit, "sort": "date_desc"}
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"  ⚠️ Error {resp.status_code} para '{query}'")
            return []
        return resp.json().get("results", [])
    except Exception as e:
        print(f"  ⚠️ Excepción para '{query}': {e}")
        return []


def extract_km(attributes: list) -> int | None:
    for attr in attributes:
        if attr.get("id") in ("KILOMETERS", "ODOMETER"):
            try:
                val = re.sub(r"[^\d]", "", attr.get("value_name", ""))
                return int(val) if val else None
            except (ValueError, AttributeError):
                pass
    return None


def extract_year(attributes: list) -> int | None:
    for attr in attributes:
        if attr.get("id") == "VEHICLE_YEAR":
            try:
                return int(attr.get("value_name", ""))
            except (ValueError, AttributeError):
                pass
    return None


def collect_all() -> list[dict]:
    all_listings = []
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando modelos...\n")
    for nombre, query in MODELOS:
        items = fetch_model(query, RESULTADOS_POR_AUTO)
        count = 0
        for item in items:
            price = item.get("price")
            if not price:
                continue
            attrs = item.get("attributes", [])
            km    = extract_km(attrs)
            year  = extract_year(attrs)
            all_listings.append({
                "id":     item.get("id"),
                "title":  item.get("title", nombre),
                "price":  price,
                "km":     km,
                "year":   year,
                "modelo": nombre,
                "url":    item.get("permalink", ""),
            })
            count += 1
        print(f"  ✓ {nombre}: {count} publicaciones")
    print(f"\n[✓] Total: {len(all_listings)} autos recolectados.")
    return all_listings


# ══════════════════════════════════════════════
# MENSAJE 1 — Oportunidades del día
# ══════════════════════════════════════════════
def find_opportunities(listings: list[dict]) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for item in listings:
        key = f"{item['modelo']} | {item['year'] or 'S/A'}"
        item["group_key"] = key
        groups[key].append(item)

    group_stats = {}
    for key, items in groups.items():
        prices = [i["price"] for i in items]
        if len(prices) < 3:
            continue
        group_stats[key] = {"mean": statistics.mean(prices)}

    km_ratios = [i["km"] / i["price"] for i in listings if i["km"] and i["price"]]
    km_threshold = None
    if km_ratios:
        km_sorted    = sorted(km_ratios)
        idx          = int(len(km_sorted) * KM_PERCENTIL / 100)
        km_threshold = km_sorted[idx]

    opportunities = []
    for item in listings:
        stats = group_stats.get(item.get("group_key", ""))
        if not stats:
            continue
        pct_bajo  = ((stats["mean"] - item["price"]) / stats["mean"]) * 100
        es_barato = pct_bajo >= DESCUENTO_MINIMO_PCT
        pocos_km  = False
        if km_threshold and item["km"] and item["price"]:
            pocos_km = (item["km"] / item["price"]) <= km_threshold
        if es_barato or pocos_km:
            opportunities.append({
                **item,
                "precio_pct_bajo": round(pct_bajo, 1),
                "precio_promedio": round(stats["mean"]),
                "es_barato":       es_barato,
                "pocos_km":        pocos_km,
                "score":           (2 if es_barato else 0) + (1 if pocos_km else 0),
            })

    opportunities.sort(key=lambda x: (x["score"], x["precio_pct_bajo"]), reverse=True)
    return opportunities[:20]


def format_oportunidades(opportunities: list[dict], hoy: str) -> str:
    if not opportunities:
        return f"🚗 *Oportunidades del día — {hoy}*\n\nNo se encontraron oportunidades hoy.\n_ML Autos Bot_"

    lines = [f"🚗 *Oportunidades del día — {hoy}*\n"]
    for i, op in enumerate(opportunities, 1):
        precio_fmt   = f"${op['price']:,.0f}".replace(",", ".")
        promedio_fmt = f"${op['precio_promedio']:,.0f}".replace(",", ".")
        km_fmt       = f"{op['km']:,.0f} km".replace(",", ".") if op["km"] else "Sin km"
        year_fmt     = str(op["year"]) if op["year"] else "S/A"
        tags = []
        if op["es_barato"]:
            tags.append(f"💰 {op['precio_pct_bajo']}% bajo el promedio")
        if op["pocos_km"]:
            tags.append("📉 Pocos km para el precio")
        emoji = "🔥" if op["score"] == 3 else "⭐"
        lines.append(
            f"{emoji} *{i}. {op['title']}*\n"
            f"   📅 {year_fmt}  |  🛣️ {km_fmt}\n"
            f"   💵 {precio_fmt} _(prom: {promedio_fmt})_\n"
            f"   {'  |  '.join(tags)}\n"
            f"   🔗 [Ver en ML]({op['url']})\n"
        )
    lines.append("_ML Autos Bot_")
    return "\n".join(lines)


# ══════════════════════════════════════════════
# MENSAJE 2 — Mejores precio/km por modelo
# ══════════════════════════════════════════════
def best_by_km_per_model(listings: list[dict]) -> dict[str, list[dict]]:
    """Para cada modelo, devuelve el top 3 con menor ratio km/precio (más km por menos plata)."""
    by_model: dict[str, list] = defaultdict(list)
    for item in listings:
        if item["km"] and item["price"] and item["km"] > 0:
            item["precio_por_km"] = round(item["price"] / item["km"], 1)
            by_model[item["modelo"]].append(item)

    result = {}
    for modelo, items in by_model.items():
        sorted_items = sorted(items, key=lambda x: x["precio_por_km"])
        result[modelo] = sorted_items[:3]
    return result


def format_mejor_por_km(best_by_model: dict, hoy: str) -> str:
    lines = [f"📊 *Mejores precio/km por modelo — {hoy}*\n"]
    lines.append("_(precio por kilómetro = cuánto pagás por cada km recorrido)_\n")

    for nombre, _ in MODELOS:
        items = best_by_model.get(nombre)
        if not items:
            continue
        lines.append(f"🚘 *{nombre}*")
        for i, op in enumerate(items, 1):
            precio_fmt = f"${op['price']:,.0f}".replace(",", ".")
            km_fmt     = f"{op['km']:,.0f} km".replace(",", ".")
            ppkm_fmt   = f"${op['precio_por_km']:,.0f}/km".replace(",", ".")
            year_fmt   = str(op["year"]) if op["year"] else "S/A"
            lines.append(
                f"  {i}. {op['title']} ({year_fmt})\n"
                f"     💵 {precio_fmt}  |  🛣️ {km_fmt}  |  📉 {ppkm_fmt}\n"
                f"     🔗 [Ver en ML]({op['url']})"
            )
        lines.append("")

    lines.append("_ML Autos Bot_")
    return "\n".join(lines)


# ══════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════
def send_telegram(message: str) -> bool:
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code == 200:
        print("✅ Mensaje enviado.")
        return True
    print(f"❌ Error Telegram: {resp.status_code} — {resp.text}")
    return False


def main():
    print(f"\n{'='*50}")
    print(f"  ML AUTOS BOT — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}\n")

    hoy      = datetime.now().strftime("%d/%m/%Y")
    listings = collect_all()

    # ── Mensaje 1: Oportunidades ──
    opportunities = find_opportunities(listings)
    print(f"[✓] {len(opportunities)} oportunidades encontradas.")
    msg1 = format_oportunidades(opportunities, hoy)
    send_telegram(msg1)

    # ── Mensaje 2: Mejor precio/km por modelo ──
    best = best_by_km_per_model(listings)
    msg2 = format_mejor_por_km(best, hoy)
    send_telegram(msg2)


if __name__ == "__main__":
    main()
