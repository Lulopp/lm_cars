import requests
import json
import statistics
import os
from datetime import datetime
from collections import defaultdict

# ─────────────────────────────────────────────
# CONFIGURACIÓN — completá estos valores
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "TU_CHAT_ID_AQUI")

# Umbral: un auto es "barato" si su precio está X% por debajo del promedio
DESCUENTO_MINIMO_PCT = 20   # 20% más barato que el promedio

# Umbral: km "bajos para el precio" — ratio km/precio menor a este percentil
KM_PERCENTIL        = 30    # el 30% con menos km relativo al precio

# Categorías de gama media en MercadoLibre Argentina
# MLA1744 = Autos y Camionetas
CATEGORIA = "MLA1744"
ML_SITE   = "MLA"

# Cuántos autos buscar por request (máx 50 por la API)
LIMIT_POR_REQUEST = 50
TOTAL_A_BUSCAR    = 200   # total de publicaciones a analizar
# ─────────────────────────────────────────────


def fetch_listings(offset: int = 0) -> list[dict]:
    """Obtiene publicaciones de autos de MercadoLibre Argentina."""
    url = f"https://api.mercadolibre.com/sites/{ML_SITE}/search"
    params = {
        "category": CATEGORIA,
        "limit":    LIMIT_POR_REQUEST,
        "offset":   offset,
        "sort":     "date_desc",   # más recientes primero
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("results", [])


def get_item_details(item_id: str) -> dict:
    """Obtiene detalles completos de una publicación (incluye km)."""
    url = f"https://api.mercadolibre.com/items/{item_id}"
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200:
        return {}
    return resp.json()


def extract_km(attributes: list[dict]) -> int | None:
    """Extrae los kilómetros del campo de atributos."""
    for attr in attributes:
        if attr.get("id") in ("KILOMETERS", "ODOMETER"):
            try:
                return int(attr.get("value_name", "").replace(".", "").replace(",", ""))
            except (ValueError, AttributeError):
                pass
    return None


def extract_year(attributes: list[dict]) -> int | None:
    """Extrae el año del vehículo."""
    for attr in attributes:
        if attr.get("id") == "VEHICLE_YEAR":
            try:
                return int(attr.get("value_name", ""))
            except (ValueError, AttributeError):
                pass
    return None


def collect_all_listings() -> list[dict]:
    """Recolecta y enriquece todas las publicaciones."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Descargando publicaciones de MercadoLibre...")
    raw = []
    for offset in range(0, TOTAL_A_BUSCAR, LIMIT_POR_REQUEST):
        batch = fetch_listings(offset)
        if not batch:
            break
        raw.extend(batch)
        print(f"  → {len(raw)} publicaciones obtenidas...")

    listings = []
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Enriqueciendo {len(raw)} publicaciones con detalles...")
    for i, item in enumerate(raw):
        price = item.get("price")
        if not price:
            continue

        details   = get_item_details(item["id"])
        attrs     = details.get("attributes", [])
        km        = extract_km(attrs)
        year      = extract_year(attrs)
        title     = item.get("title", "Sin título")
        permalink = item.get("permalink", "")

        # Extraer modelo del título (primeras 3 palabras)
        words      = title.upper().split()
        model_key  = " ".join(words[:3]) if len(words) >= 3 else title.upper()
        year_key   = str(year) if year else "S/A"
        group_key  = f"{model_key} | {year_key}"

        listings.append({
            "id":        item["id"],
            "title":     title,
            "price":     price,
            "km":        km,
            "year":      year,
            "group_key": group_key,
            "url":       permalink,
            "seller_type": item.get("seller", {}).get("seller_reputation", {}).get("level_id", ""),
        })

        if (i + 1) % 20 == 0:
            print(f"  → {i + 1}/{len(raw)} procesados...")

    return listings


def find_opportunities(listings: list[dict]) -> list[dict]:
    """Detecta oportunidades por precio bajo y pocos km para el precio."""

    # ── 1. Agrupar por modelo+año para calcular precios promedio ──
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in listings:
        groups[item["group_key"]].append(item)

    # ── 2. Calcular promedio de precio por grupo ──
    group_stats: dict[str, dict] = {}
    for key, items in groups.items():
        prices = [i["price"] for i in items if i["price"]]
        if len(prices) < 2:
            continue
        group_stats[key] = {
            "mean":  statistics.mean(prices),
            "count": len(prices),
        }

    # ── 3. Calcular ratio km/precio para detectar "pocos km para el precio" ──
    km_ratios = []
    for item in listings:
        if item["km"] and item["price"]:
            ratio = item["km"] / item["price"]
            km_ratios.append(ratio)

    km_threshold = None
    if km_ratios:
        km_ratios_sorted = sorted(km_ratios)
        idx = int(len(km_ratios_sorted) * KM_PERCENTIL / 100)
        km_threshold = km_ratios_sorted[idx]

    # ── 4. Filtrar oportunidades ──
    opportunities = []
    for item in listings:
        key   = item["group_key"]
        stats = group_stats.get(key)
        if not stats:
            continue

        precio_pct_bajo = ((stats["mean"] - item["price"]) / stats["mean"]) * 100
        es_barato       = precio_pct_bajo >= DESCUENTO_MINIMO_PCT

        pocos_km = False
        if km_threshold and item["km"] and item["price"]:
            ratio    = item["km"] / item["price"]
            pocos_km = ratio <= km_threshold

        if es_barato or pocos_km:
            opportunities.append({
                **item,
                "precio_pct_bajo":   round(precio_pct_bajo, 1),
                "precio_promedio":   round(stats["mean"]),
                "es_barato":         es_barato,
                "pocos_km":          pocos_km,
                "score":             (2 if es_barato else 0) + (1 if pocos_km else 0),
            })

    # Ordenar por score descendente, luego por % de descuento
    opportunities.sort(key=lambda x: (x["score"], x["precio_pct_bajo"]), reverse=True)
    return opportunities[:15]   # top 15


def format_message(opportunities: list[dict]) -> str:
    """Formatea el mensaje de Telegram con las oportunidades."""
    hoy = datetime.now().strftime("%d/%m/%Y")

    if not opportunities:
        return (
            f"🚗 *Oportunidades de autos — {hoy}*\n\n"
            "No se encontraron oportunidades destacadas hoy. "
            "Intentá de nuevo mañana."
        )

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
    """Envía el mensaje por Telegram."""
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

    listings      = collect_all_listings()
    print(f"\n[✓] {len(listings)} publicaciones válidas recolectadas.")

    opportunities = find_opportunities(listings)
    print(f"[✓] {len(opportunities)} oportunidades encontradas.")

    message = format_message(opportunities)
    print("\n--- PREVIEW DEL MENSAJE ---")
    print(message)
    print("---------------------------\n")

    send_telegram(message)


if __name__ == "__main__":
    main()
