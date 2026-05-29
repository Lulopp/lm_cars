import requests
import statistics
import os
import re
import json
from datetime import datetime
from collections import defaultdict

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "TU_CHAT_ID_AQUI")

DESCUENTO_MINIMO_PCT = 20
KM_PERCENTIL         = 30
TOTAL_PAGINAS        = 5   # cada página trae ~48 autos = ~240 autos en total
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept": "application/json, text/plain, */*",
}


def fetch_page(offset: int = 0) -> list[dict]:
    """Busca autos en MercadoLibre Argentina via API pública con headers de browser."""
    url = "https://api.mercadolibre.com/sites/MLA/search"
    params = {
        "category": "MLA1744",
        "limit":    48,
        "offset":   offset,
        "sort":     "date_desc",
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    if resp.status_code != 200:
        print(f"  ⚠️ Error {resp.status_code} en offset {offset}")
        return []
    return resp.json().get("results", [])


def extract_km_from_attributes(attributes: list) -> int | None:
    for attr in attributes:
        if attr.get("id") in ("KILOMETERS", "ODOMETER"):
            try:
                val = attr.get("value_name", "")
                val = re.sub(r"[^\d]", "", val)
                return int(val) if val else None
            except (ValueError, AttributeError):
                pass
    return None


def extract_year_from_attributes(attributes: list) -> int | None:
    for attr in attributes:
        if attr.get("id") == "VEHICLE_YEAR":
            try:
                return int(attr.get("value_name", ""))
            except (ValueError, AttributeError):
                pass
    return None


def collect_listings() -> list[dict]:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Recolectando autos de MercadoLibre...")
    all_items = []

    for page in range(TOTAL_PAGINAS):
        offset = page * 48
        items  = fetch_page(offset)
        if not items:
            break
        print(f"  → Página {page+1}: {len(items)} autos")

        for item in items:
            price = item.get("price")
            if not price:
                continue

            attrs = item.get("attributes", [])
            km    = extract_km_from_attributes(attrs)
            year  = extract_year_from_attributes(attrs)
            title = item.get("title", "Sin título")

            words     = title.upper().split()
            model_key = " ".join(words[:3]) if len(words) >= 3 else title.upper()
            year_key  = str(year) if year else "S/A"

            all_items.append({
                "id":        item.get("id"),
                "title":     title,
                "price":     price,
                "km":        km,
                "year":      year,
                "group_key": f"{model_key} | {year_key}",
                "url":       item.get("permalink", ""),
            })

    print(f"[✓] {len(all_items)} autos recolectados.")
    return all_items


def find_opportunities(listings: list[dict]) -> list[dict]:
    # Agrupar por modelo+año
    groups: dict[str, list] = defaultdict(list)
    for item in listings:
        groups[item["group_key"]].append(item)

    # Promedios por grupo
    group_stats = {}
    for key, items in groups.items():
        prices = [i["price"] for i in items]
        if len(prices) < 2:
            continue
        group_stats[key] = {"mean": statistics.mean(prices)}

    # Percentil de km/precio
    km_ratios = [i["km"] / i["price"] for i in listings if i["km"] and i["price"]]
    km_threshold = None
    if km_ratios:
        km_sorted    = sorted(km_ratios)
        idx          = int(len(km_sorted) * KM_PERCENTIL / 100)
        km_threshold = km_sorted[idx]

    opportunities = []
    for item in listings:
        stats = group_stats.get(item["group_key"])
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
    return opportunities[:15]


def format_message(opportunities: list[dict]) -> str:
    hoy = datetime.now().strftime("%d/%m/%Y")
    if not opportunities:
        return f"🚗 *Oportunidades de autos — {hoy}*\n\nNo se encontraron oportunidades hoy."

    lines = [f"🚗 *Oportunidades de autos — {hoy}*\n"]
    for i, op in enumerate(opportunities, 1):
        precio_fmt   = f"${op['price']:,.0f}".replace(",", ".")
        promedio_fmt = f"${op['precio_promedio']:,.0f}".replace(",", ".")
        km_fmt       = f"{op['km']:,.0f} km".replace(",", ".") if op["km"] else "Sin datos de km"
        tags = []
        if op["es_barato"]:
            tags.append(f"💰 {op['precio_pct_bajo']}% bajo el promedio")
        if op["pocos_km"]:
            tags.append("📉 Pocos km para el precio")
        emoji = "🔥" if op["score"] == 3 else "⭐"
        lines.append(
            f"{emoji} *{i}. {op['title']}*\n"
            f"   💵 {precio_fmt} _(prom: {promedio_fmt})_\n"
            f"   🛣️ {km_fmt}\n"
            f"   {'  |  '.join(tags)}\n"
            f"   🔗 [Ver en ML]({op['url']})\n"
        )
    lines.append("_ML Autos Bot_")
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, json=payload, timeout=15)
    if resp.status_code == 200:
        print("✅ Mensaje enviado por Telegram.")
        return True
    print(f"❌ Error Telegram: {resp.status_code} — {resp.text}")
    return False


def main():
    print(f"\n{'='*50}")
    print(f"  ML AUTOS BOT — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}\n")

    listings      = collect_listings()
    opportunities = find_opportunities(listings)
    print(f"[✓] {len(opportunities)} oportunidades encontradas.")
    message = format_message(opportunities)
    print("\n--- PREVIEW ---")
    print(message)
    print("---------------\n")
    send_telegram(message)


if __name__ == "__main__":
    main()
