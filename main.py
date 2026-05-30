import requests
import statistics
import os
import re
import time
from datetime import datetime
from collections import defaultdict
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "TU_CHAT_ID_AQUI")

DESCUENTO_MINIMO_PCT = 15
KM_PERCENTIL         = 30
PAGINAS_POR_MODELO   = 2  # cada página trae ~48 autos

MODELOS = [
    ("VW Gol / Gol Trend",      "volkswagen-gol"),
    ("Toyota Hilux",            "toyota-hilux"),
    ("Chevrolet Corsa/Classic",  "chevrolet-corsa"),
    ("VW Amarok",               "volkswagen-amarok"),
    ("Ford Ranger",             "ford-ranger"),
    ("Ford EcoSport",           "ford-ecosport"),
    ("Toyota Corolla",          "toyota-corolla"),
    ("Peugeot 208",             "peugeot-208"),
    ("Fiat Palio",              "fiat-palio"),
    ("Ford Ka",                 "ford-ka"),
    ("Mercedes C200",           "mercedes-benz-c200"),
    ("Mercedes C250",           "mercedes-benz-c250"),
    ("VW Vento",                "volkswagen-vento"),
    ("VW Golf",                 "volkswagen-golf"),
    ("VW Golf GTI",             "volkswagen-golf-gti"),
]
# ─────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})


def scrape_model(slug: str, paginas: int = 2) -> list[dict]:
    """Scrapea resultados de búsqueda de ML para un modelo."""
    results = []
    for page in range(paginas):
        offset = page * 48 + 1
        url    = f"https://autos.mercadolibre.com.ar/{slug}_Desde_{offset}_NoIndex_True"
        try:
            resp = SESSION.get(url, timeout=20)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("li.ui-search-layout__item")
            for card in cards:
                try:
                    title_el = card.select_one("h2.poly-box a, .ui-search-item__title")
                    price_el = card.select_one(".andes-money-amount__fraction")
                    link_el  = card.select_one("a.poly-component__title, a.ui-search-link")
                    km_el    = card.select_one(".ui-search-item__attributes-list li:nth-child(2), .poly-attributes-list li")
                    year_el  = card.select_one(".ui-search-item__attributes-list li:first-child, .poly-attributes-list li:first-child")

                    if not title_el or not price_el:
                        continue

                    price_str = re.sub(r"[^\d]", "", price_el.text)
                    if not price_str:
                        continue
                    price = int(price_str)

                    km   = None
                    year = None

                    if km_el:
                        km_text = km_el.text.strip()
                        km_nums = re.sub(r"[^\d]", "", km_text)
                        if km_nums and "km" in km_text.lower():
                            km = int(km_nums)

                    if year_el:
                        year_text = year_el.text.strip()
                        year_match = re.search(r"(19|20)\d{2}", year_text)
                        if year_match:
                            year = int(year_match.group())

                    link = link_el["href"] if link_el and link_el.get("href") else url

                    results.append({
                        "title": title_el.text.strip(),
                        "price": price,
                        "km":    km,
                        "year":  year,
                        "url":   link,
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"  ⚠️ Error scraping {url}: {e}")
        time.sleep(1)
    return results


def collect_all() -> list[dict]:
    all_listings = []
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scrapeando MercadoLibre...\n")
    for nombre, slug in MODELOS:
        items = scrape_model(slug, PAGINAS_POR_MODELO)
        for item in items:
            item["modelo"] = nombre
        all_listings.extend(items)
        print(f"  ✓ {nombre}: {len(items)} publicaciones")
        time.sleep(1)
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


def best_by_km_per_model(listings: list[dict]) -> dict[str, list[dict]]:
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
                f"     🔗 [Ver en ML]({op['url']})"
            )
        lines.append("")
    lines.append("_ML Autos Bot_")
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
    print(f"  ML AUTOS BOT — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}\n")

    hoy      = datetime.now().strftime("%d/%m/%Y")
    listings = collect_all()

    opportunities = find_opportunities(listings)
    print(f"[✓] {len(opportunities)} oportunidades encontradas.")
    send_telegram(format_oportunidades(opportunities, hoy))

    best = best_by_km_per_model(listings)
    send_telegram(format_mejor_por_km(best, hoy))


if __name__ == "__main__":
    main()
