import requests
import statistics
import os
import re
import time
from datetime import datetime
from collections import defaultdict

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "TU_CHAT_ID_AQUI")

DESCUENTO_MINIMO_PCT = 15
KM_PERCENTIL         = 30

MODELOS = [
    ("VW Gol / Gol Trend",      "volkswagen/gol"),
    ("Toyota Hilux",            "toyota/hilux"),
    ("Chevrolet Corsa/Classic",  "chevrolet/corsa"),
    ("VW Amarok",               "volkswagen/amarok"),
    ("Ford Ranger",             "ford/ranger"),
    ("Ford EcoSport",           "ford/ecosport"),
    ("Toyota Corolla",          "toyota/corolla"),
    ("Peugeot 208",             "peugeot/208"),
    ("Fiat Palio",              "fiat/palio"),
    ("Ford Ka",                 "ford/ka"),
    ("Mercedes C200",           "mercedes-benz/clase-c"),
    ("VW Vento",                "volkswagen/vento"),
    ("VW Golf",                 "volkswagen/golf"),
]
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-AR,es;q=0.9",
    "Referer": "https://www.kavak.com/ar/usados",
}


def fetch_kavak(slug: str, page: int = 1) -> list[dict]:
    """Consulta la API de Kavak Argentina para un modelo."""
    url = f"https://www.kavak.com/ar/usados/{slug}"
    params = {
        "page": page,
        "pageSize": 24,
        "sort": "price_asc",
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return []
        # Kavak devuelve HTML con JSON embebido — buscamos el JSON
        text = resp.text
        match = re.search(r'"cars"\s*:\s*(\[.*?\])', text, re.DOTALL)
        if not match:
            # Intentar con otro patrón
            match = re.search(r'__NEXT_DATA__.*?"results"\s*:\s*(\[.*?\])', text, re.DOTALL)
        if not match:
            return []
        import json
        cars = json.loads(match.group(1))
        return cars
    except Exception as e:
        print(f"  ⚠️ Error para '{slug}': {e}")
        return []


def fetch_kavak_api(slug: str) -> list[dict]:
    """Intenta la API interna de Kavak."""
    parts = slug.split("/")
    brand = parts[0] if len(parts) > 0 else ""
    model = parts[1] if len(parts) > 1 else ""
    
    urls = [
        f"https://www.kavak.com/_next/data/ar/usados/{slug}.json",
        f"https://www.kavak.com/ar/api/cars?brand={brand}&model={model}&country=ar&pageSize=48",
    ]
    
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                # Intentar extraer autos del JSON
                if isinstance(data, list):
                    return data
                # Buscar en claves comunes
                for key in ["cars", "results", "data", "items", "vehicles"]:
                    if key in data:
                        val = data[key]
                        if isinstance(val, list):
                            return val
                        if isinstance(val, dict):
                            for k2 in ["cars", "results", "data", "items"]:
                                if k2 in val and isinstance(val[k2], list):
                                    return val[k2]
        except Exception:
            continue
    return []


def collect_all() -> list[dict]:
    all_listings = []
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Buscando en Kavak Argentina...\n")
    
    for nombre, slug in MODELOS:
        items = fetch_kavak_api(slug)
        if not items:
            items = fetch_kavak(slug)
        
        count = 0
        for item in items:
            # Kavak puede tener diferentes estructuras
            price = (item.get("price") or item.get("salePrice") or 
                    item.get("listPrice") or item.get("regularPrice") or 0)
            if not price:
                continue
                
            km = (item.get("km") or item.get("kilometers") or 
                 item.get("mileage") or item.get("odometer") or None)
            year = (item.get("year") or item.get("modelYear") or 
                   item.get("carYear") or None)
            title = (item.get("title") or item.get("name") or 
                    item.get("carName") or nombre)
            url_car = (item.get("url") or item.get("link") or 
                      item.get("permalink") or f"https://www.kavak.com/ar/usados/{slug}")

            all_listings.append({
                "title":  title,
                "price":  float(price),
                "km":     int(km) if km else None,
                "year":   int(year) if year else None,
                "modelo": nombre,
                "url":    url_car,
            })
            count += 1
        
        print(f"  ✓ {nombre}: {count} publicaciones")
        time.sleep(0.5)
    
    print(f"\n[✓] Total: {len(all_listings)} autos recolectados.")
    return all_listings


def find_opportunities(listings: list[dict]) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for item in listings:
        key = f"{item['modelo']} | {item['year'] or 'S/A'}"
        item["group_key"] = key
        groups[key].append(item)

    group_stats = {}
    for key, items in groups.items():
        prices = [i["price"] for i in items]
        if len(prices) < 2:
            continue
        group_stats[key] = {"mean": statistics.mean(prices)}

    km_ratios = [i["km"] / i["price"] for i in listings if i["km"] and i["price"]]
    km_threshold = None
    if km_ratios:
        km_sorted = sorted(km_ratios)
        idx = int(len(km_sorted) * KM_PERCENTIL / 100)
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


def best_by_km_per_model(listings: list[dict]) -> dict[str, list]:
    by_model: dict[str, list] = defaultdict(list)
    for item in listings:
        if item["km"] and item["price"] and item["km"] > 0:
            item["precio_por_km"] = round(item["price"] / item["km"], 1)
            by_model[item["modelo"]].append(item)
    result = {}
    for modelo, items in by_model.items():
        result[modelo] = sorted(items, key=lambda x: x["precio_por_km"])[:3]
    return result


def format_oportunidades(opportunities: list[dict], hoy: str) -> str:
    if not opportunities:
        return f"🚗 *Oportunidades del día — {hoy}*\n\nNo se encontraron oportunidades hoy.\n_Kavak Autos Bot_"
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
            f"   🔗 [Ver en Kavak]({op['url']})\n"
        )
    lines.append("_Kavak Autos Bot_")
    return "\n".join(lines)


def format_mejor_por_km(best_by_model: dict, hoy: str) -> str:
    lines = [f"📊 *Mejor precio/km por modelo — {hoy}*\n"]
    lines.append("_(menor $/km = más auto por menos plata)_\n")
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
                f"     🔗 [Ver en Kavak]({op['url']})"
            )
        lines.append("")
    lines.append("_Kavak Autos Bot_")
    return "\n".join(lines)


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
    print(f"  KAVAK AUTOS BOT — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}\n")

    hoy      = datetime.now().strftime("%d/%m/%Y")
    listings = collect_all()

    if not listings:
        send_telegram(f"⚠️ No se pudieron obtener datos de Kavak hoy ({hoy}).")
        return

    opportunities = find_opportunities(listings)
    print(f"[✓] {len(opportunities)} oportunidades encontradas.")
    send_telegram(format_oportunidades(opportunities, hoy))

    best = best_by_km_per_model(listings)
    send_telegram(format_mejor_por_km(best, hoy))


if __name__ == "__main__":
    main()
