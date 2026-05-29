import requests
import statistics
import os
from datetime import datetime
from collections import defaultdict

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "TU_CHAT_ID_AQUI")

ML_CLIENT_ID     = os.getenv("ML_CLIENT_ID",     "TU_CLIENT_ID_AQUI")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "TU_CLIENT_SECRET_AQUI")

DESCUENTO_MINIMO_PCT = 20
KM_PERCENTIL         = 30
CATEGORIA            = "MLA1744"
ML_SITE              = "MLA"
LIMIT_POR_REQUEST    = 50
TOTAL_A_BUSCAR       = 200
# ─────────────────────────────────────────────


def get_ml_token() -> str:
    """Obtiene un access token de MercadoLibre usando Client Credentials."""
    url  = "https://api.mercadolibre.com/oauth/token"
    data = {
        "grant_type":    "client_credentials",
        "client_id":     ML_CLIENT_ID,
        "client_secret": ML_CLIENT_SECRET,
    }
    resp = requests.post(url, data=data, timeout=15)
    resp.raise_for_status()
    token = resp.json().get("access_token")
    print(f"[✓] Token de ML obtenido.")
    return token


def fetch_listings(token: str, offset: int = 0) -> list[dict]:
    url    = f"https://api.mercadolibre.com/sites/{ML_SITE}/search"
    params = {
        "category": CATEGORIA,
        "limit":    LIMIT_POR_REQUEST,
        "offset":   offset,
        "sort":     "date_desc",
    }
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_item_details(token: str, item_id: str) -> dict:
    url     = f"https://api.mercadolibre.com/items/{item_id}"
    headers = {"Authorization": f"Bearer {token}"}
    resp    = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        return {}
    return resp.json()


def extract_km(attributes: list[dict]) -> int | None:
    for attr in attributes:
        if attr.get("id") in ("KILOMETERS", "ODOMETER"):
            try:
                return int(attr.get("value_name", "").replace(".", "").replace(",", ""))
            except (ValueError, AttributeError):
                pass
    return None


def extract_year(attributes: list[dict]) -> int | None:
    for attr in attributes:
        if attr.get("id") == "VEHICLE_YEAR":
            try:
                return int(attr.get("value_name", ""))
            except (ValueError, AttributeError):
                pass
    return None


def collect_all_listings(token: str) -> list[dict]:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Descargando publicaciones...")
    raw = []
    for offset in range(0, TOTAL_A_BUSCAR, LIMIT_POR_REQUEST):
        batch = fetch_listings(token, offset)
        if not batch:
            break
        raw.extend(batch)
        print(f"  → {len(raw)} publicaciones obtenidas...")

    listings = []
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Enriqueciendo {len(raw)} publicaciones...")
    for i, item in enumerate(raw):
        price = item.get("price")
        if not price:
            continue

        details  = get_item_details(token, item["id"])
        attrs    = details.get("attributes", [])
        km       = extract_km(attrs)
        year     = extract_year(attrs)
        title    = item.get("title", "Sin título")
        words    = title.upper().split()
        model_key = " ".join(words[:3]) if len(words) >= 3 else title.upper()
        year_key  = str(year) if year else "S/A"

        listings.append({
            "id":        item["id"],
            "title":     title,
            "price":     price,
            "km":        km,
            "year":      year,
            "group_key": f"{model_key} | {year_key}",
            "url":       item.get("permalink", ""),
        })

        if (i + 1) % 20 == 0:
            print(f"  → {i + 1}/{len(raw)} procesados...")

    return listings


def find_opportunities(listings: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in listings:
        groups[item["group_key"]].append(item)

    group_stats: dict[str, dict] = {}
    for key, items in groups.items():
        prices = [i["price"] for i in items if i["price"]]
        if len(prices) < 2:
            continue
        group_stats[key] = {"mean": statistics.mean(prices), "count": len(prices)}

    km_ratios = []
    for item in listings:
        if item["km"] and item["price"]:
            km_ratios.append(item["km"] / item["price"])

    km_threshold = None
    if km_ratios:
        km_ratios_sorted = sorted(km_ratios)
        idx = int(len(km_ratios_sorted) * KM_PERCENTIL / 100)
        km_threshold = km_ratios_sorted[idx]

    opportunities = []
    for item in listings:
        stats = group_stats.get(item["group_key"])
        if not stats:
            continue

        precio_pct_bajo = ((stats["mean"] - item["price"]) / stats["mean"]) * 100
        es_barato       = precio_pct_bajo >= DESCUENTO_MINIMO_PCT
        pocos_km        = False
        if km_threshold and item["km"] and item["price"]:
            pocos_km = (item["km"] / item["price"]) <= km_threshold

        if es_barato or pocos_km:
            opportunities.append({
                **item,
                "precio_pct_bajo": round(precio_pct_bajo, 1),
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
        return f"🚗 *Oportunidades de autos — {hoy}*\n\nNo se encontraron oportunidades destacadas hoy."

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
        score_emoji = "🔥" if op["score"] == 3 else "⭐"
        lines.append(
            f"{score_emoji} *{i}. {op['title']}*\n"
            f"   💵 Precio: {precio_fmt} _(promedio: {promedio_fmt})_\n"
            f"   🛣️ Km: {km_fmt}\n"
            f"   {'  |  '.join(tags)}\n"
            f"   🔗 [Ver publicación]({op['url']})\n"
        )
    lines.append("_Generado automáticamente — ML Autos Bot_")
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
    else:
        print(f"❌ Error Telegram: {resp.status_code} — {resp.text}")
        return False


def main():
    print(f"\n{'='*50}")
    print(f"  ML AUTOS BOT — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}\n")

    token         = get_ml_token()
    listings      = collect_all_listings(token)
    print(f"\n[✓] {len(listings)} publicaciones válidas recolectadas.")
    opportunities = find_opportunities(listings)
    print(f"[✓] {len(opportunities)} oportunidades encontradas.")
    message = format_message(opportunities)
    print("\n--- PREVIEW ---")
    print(message)
    print("---------------\n")
    send_telegram(message)


if __name__ == "__main__":
    main()
