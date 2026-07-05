import os
import requests
import sqlite3
import re
import time
import threading
from datetime import datetime, timedelta

# ============ CONFIGURACION ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8814877491:AAGBcrryLjzfroU-BVeIvjXKU1h9x1iDnAY")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "3d885f7eabf856c82288c4128aee78fd")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6LP2n2FlP5X8blEq2WUNm-C1e2cW-aWXQpEZWRuRTJ1iw")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1275122240")

CHANNEL_FREE = os.environ.get("CHANNEL_FREE", "@AdivinoPicksFree")
CHANNEL_PREMIUM = os.environ.get("CHANNEL_PREMIUM", "-1003266399573")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
FOOTBALL_URL = "https://v3.football.api-sports.io"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Contador de llamadas API
api_calls = 0
MAX_API_CALLS = 95  # Dejamos margen de 5

# ============ LIGAS ============
LIGAS_TOP = {
    39, 140, 135, 78, 61, 2, 3, 848, 94, 88, 144, 179, 292, 169, 119, 113, 203, 207, 271, 307,
    71, 262, 128, 235, 106, 345, 332, 357, 244, 218, 344, 346, 347, 348
}

LIGAS_SECUNDARIAS = {
    40, 41, 141, 136, 79, 62, 265, 72, 263, 131, 239, 242, 250, 252, 254, 256, 258, 266, 267,
    268, 269, 270, 274, 275, 98, 188
}

LIGAS_FEMENINAS = {
    44, 45, 142, 143, 137, 80, 63, 95, 89, 145, 170, 293, 120, 114, 204, 208, 272, 308, 73,
    264, 132, 240, 243, 251, 253, 255, 257, 259, 99, 189
}

TODAS_LIGAS = LIGAS_TOP | LIGAS_SECUNDARIAS | LIGAS_FEMENINAS

# ============ BASE DE DATOS ============
DB_PATH = "picks.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            canal TEXT,
            partido TEXT,
            liga TEXT,
            mercado TEXT,
            tipo TEXT,
            linea REAL,
            prediccion TEXT,
            probabilidad INTEGER,
            cuota_esperada REAL,
            stake INTEGER,
            analisis TEXT,
            resultado TEXT DEFAULT 'pendiente',
            fecha_resultado TEXT,
            goles_home INTEGER,
            goles_away INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_pick(fecha, canal, partido, liga, mercado, tipo, linea, prediccion, probabilidad, cuota_esperada, stake, analisis):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM picks WHERE fecha = ? AND partido = ? AND mercado = ? AND canal = ?",
              (fecha, partido, mercado, canal))
    if c.fetchone():
        conn.close()
        return False
    c.execute("""
        INSERT INTO picks (fecha, canal, partido, liga, mercado, tipo, linea, prediccion, probabilidad, cuota_esperada, stake, analisis)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (fecha, canal, partido, liga, mercado, tipo, linea, prediccion, probabilidad, cuota_esperada, stake, analisis))
    conn.commit()
    conn.close()
    return True

# ============ TELEGRAM ============
def telegram_send_message(chat_id, text):
    try:
        url = f"{TELEGRAM_URL}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        r = requests.post(url, json=payload, timeout=30)
        result = r.json()
        if result.get("ok"):
            return True
        else:
            print(f"[ERROR] Telegram: {result}")
            return False
    except Exception as e:
        print(f"[ERROR] Telegram send: {e}")
        return False

# ============ API FOOTBALL CON CONTADOR ============
def api_request(endpoint, params=None):
    global api_calls
    if api_calls >= MAX_API_CALLS:
        print(f"[WARN] Límite de API alcanzado ({api_calls}/{MAX_API_CALLS})")
        return []

    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    try:
        r = requests.get(f"{FOOTBALL_URL}/{endpoint}", headers=headers, params=params, timeout=15)
        api_calls += 1
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        print(f"[ERROR] API Football: {e}")
        return []

def get_matches(date_str, ligas):
    matches = api_request("fixtures", {"date": date_str, "timezone": "America/Caracas"})
    return [m for m in matches if m.get("league", {}).get("id") in ligas]

def get_team_form(team_id):
    return api_request("fixtures", {"team": team_id, "last": 5})

def get_h2h(t1, t2):
    return api_request("fixtures/headtohead", {"h2h": f"{t1}-{t2}", "last": 5})

def get_team_stats(team_id, league_id, season):
    """Estadísticas del equipo: goles, corners, tarjetas"""
    return api_request("teams/statistics", {"team": team_id, "league": league_id, "season": season})

# ============ GEMINI ============
def get_gemini_response(prompt):
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        data = r.json()
        if "candidates" in data and len(data["candidates"]) > 0:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        return ""
    except Exception as e:
        print(f"[ERROR] Gemini: {e}")
        return ""

# ============ FUNCIONES AUXILIARES ============
def calcular_cuota(probabilidad):
    if not probabilidad or probabilidad <= 0 or probabilidad > 100:
        return None
    cuota = 100.0 / probabilidad
    cuota = cuota * 0.95
    return round(cuota, 2)

def format_form(matches):
    lines = []
    for i, m in enumerate(matches[:5], 1):
        h = m["teams"]["home"]["name"]
        a = m["teams"]["away"]["name"]
        hg = m.get("goals", {}).get("home", "-") if m.get("goals") else "-"
        ag = m.get("goals", {}).get("away", "-") if m.get("goals") else "-"
        lines.append(f"{i}. {h} {hg}-{ag} {a}")
    return "\n".join(lines)

def format_h2h(matches):
    lines = []
    for i, m in enumerate(matches[:5], 1):
        h = m["teams"]["home"]["name"]
        a = m["teams"]["away"]["name"]
        hg = m.get("goals", {}).get("home", "-") if m.get("goals") else "-"
        ag = m.get("goals", {}).get("away", "-") if m.get("goals") else "-"
        lines.append(f"{i}. {h} {hg}-{ag} {a}")
    return "\n".join(lines)

def format_stats(stats, team_name):
    """Formatea estadísticas del equipo"""
    if not stats:
        return "Sin datos estadisticos"

    try:
        s = stats[0] if isinstance(stats, list) else stats
        goles_favor = s.get("goals", {}).get("for", {}).get("average", {}).get("total", "-")
        goles_contra = s.get("goals", {}).get("against", {}).get("average", {}).get("total", "-")
        forma = s.get("form", "-")
        return f"Forma: {forma} | GF: {goles_favor} | GC: {goles_contra}"
    except:
        return "Datos estadisticos limitados"

# ============ PARSEO GEMINI ============
def parse_gemini_response(text):
    result = {
        "mercado": "",
        "prediccion": "",
        "probabilidad": 0,
        "stake": 5,
        "analisis": "",
        "tipo": ""
    }

    if not text:
        return result

    lines = text.strip().split("\n")
    analisis_lines = []
    in_analisis = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        upper = line.upper()

        if any(k in upper for k in ["MERCADO:", "PREDICCION:", "PRONOSTICO:", "PICK:"]):
            val = line.split(":", 1)[1].strip() if ":" in line else line
            result["prediccion"] = val
            result["mercado"] = val
            low = val.lower()
            if "over" in low:
                result["tipo"] = "over"
            elif "under" in low:
                result["tipo"] = "under"
            elif "si" in low or "yes" in low or "btts" in low:
                result["tipo"] = "btts_si"
            elif "no" in low:
                result["tipo"] = "btts_no"
            continue

        if "PROBABILIDAD" in upper or "PROB" in upper:
            nums = re.findall(r'\d+', line)
            if nums:
                try:
                    p = int(nums[0])
                    if 1 <= p <= 100:
                        result["probabilidad"] = p
                except:
                    pass
            continue

        if "STAKE" in upper or "CONFIANZA" in upper:
            nums = re.findall(r'\d+', line)
            if nums:
                try:
                    s = int(nums[0])
                    if 1 <= s <= 10:
                        result["stake"] = s
                except:
                    pass
            continue

        if any(k in upper for k in ["POR QUE", "PORQUE", "RAZON", "JUSTIFICACION", "ANALISIS", "MOTIVO", "EXPLICACION"]):
            in_analisis = True
            if ":" in line:
                resto = line.split(":", 1)[1].strip()
                if resto:
                    analisis_lines.append(resto)
            continue

        if in_analisis and (line.startswith("-") or line.startswith("*") or line.startswith("•")):
            clean = line.lstrip("- *•").strip()
            if clean:
                analisis_lines.append(clean)
            continue

        if in_analisis and len(analisis_lines) < 3 and len(line) > 10:
            analisis_lines.append(line)

    result["analisis"] = "\n".join(analisis_lines[:3])
    return result

# ============ PROMPT MEJORADO ============
def build_prompt(match, home_form, away_form, h2h, home_stats, away_stats):
    home = match["teams"]["home"]["name"]
    away = match["teams"]["away"]["name"]
    league = match["league"]["name"]
    season = match["league"].get("season", 2026)

    data = f"""PARTIDO: {home} vs {away}
LIGA: {league}

ESTADISTICAS {home}:
{format_stats(home_stats, home)}

ESTADISTICAS {away}:
{format_stats(away_stats, away)}

FORMA {home} (ultimos 5):
{format_form(home_form)}

FORMA {away} (ultimos 5):
{format_form(away_form)}

H2H (ultimos 5):
{format_h2h(h2h)}"""

    prompt = f"""Eres un experto en apuestas deportivas. Analiza el siguiente partido y da tu MEJOR pick.

REGLAS ESTRICTAS:
1. Analiza TODAS estas opciones y elige la MEJOR:
   - Over/Under 0.5, 1.5, 2.5, 3.5, 4.5, 5.5 goles
   - Over/Under 8.5, 9.5, 10.5, 11.5, 12.5, 13.5 corners
   - Ambos equipos marcan (BTTS): Si / No
2. La probabilidad debe ser REALISTA basada en los datos. NO inventes.
3. Stake 8-10 SOLO si los datos son contundentes.
4. Stake 5-7 para picks con buen soporte estadistico.
5. Stake 1-4 si hay incertidumbre.
6. Si no hay valor claro, di "SIN VALOR" y no des pick.
7. La probabilidad es TU opinion honesta.
8. Se honesto. Mejor no dar pick que dar uno falso.
9. Elige la linea con MEJOR relacion valor/riesgo, no solo la mas obvia.

DATOS:
{data}

FORMATO OBLIGATORIO:
MERCADO: [Over/Under X.5 goles/corners] o [BTTS Si/No]
PROBABILIDAD: [numero del 1 al 100]%
STAKE: [1-10]
POR QUE:
- [linea 1 con dato concreto]
- [linea 2 con dato concreto]
- [linea 3 con dato concreto]"""
    return prompt

# ============ ANALIZAR PARTIDO ============
def analizar_partido(match):
    global api_calls

    hid = match["teams"]["home"]["id"]
    aid = match["teams"]["away"]["id"]
    league_id = match["league"]["id"]
    season = match["league"].get("season", 2026)

    # Verificar que no nos pasemos del límite de API
    if api_calls >= MAX_API_CALLS - 5:
        print(f"[WARN] Casi en límite de API ({api_calls}/{MAX_API_CALLS}), saltando análisis")
        return None

    home_form = get_team_form(hid)
    away_form = get_team_form(aid)
    h2h = get_h2h(hid, aid)

    # Estadísticas (opcional, si quedan llamadas)
    home_stats = None
    away_stats = None
    if api_calls < MAX_API_CALLS - 10:
        home_stats = get_team_stats(hid, league_id, season)
        away_stats = get_team_stats(aid, league_id, season)

    prompt = build_prompt(match, home_form, away_form, h2h, home_stats, away_stats)
    response = get_gemini_response(prompt)

    if not response:
        print(f"[WARN] Gemini no respondio para {match['teams']['home']['name']} vs {match['teams']['away']['name']}")
        return None

    parsed = parse_gemini_response(response)

    if not parsed["prediccion"] or parsed["prediccion"].upper() == "SIN VALOR":
        print(f"[INFO] Sin valor para {match['teams']['home']['name']} vs {match['teams']['away']['name']}")
        return None

    if parsed["probabilidad"] < 30 or parsed["probabilidad"] > 95:
        print(f"[WARN] Probabilidad sospechosa: {parsed['probabilidad']}%")
        return None

    cuota = calcular_cuota(parsed["probabilidad"])
    if not cuota:
        return None

    linea = 0.0
    nums = re.findall(r'\d+\.\d+|\d+', parsed["prediccion"])
    if nums:
        try:
            linea = float(nums[0])
        except:
            pass

    return {
        "partido": f"{match['teams']['home']['name']} vs {match['teams']['away']['name']}",
        "liga": match["league"]["name"],
        "liga_id": match["league"]["id"],
        "mercado": parsed["mercado"],
        "prediccion": parsed["prediccion"],
        "tipo": parsed["tipo"],
        "linea": linea,
        "probabilidad": parsed["probabilidad"],
        "cuota_esperada": cuota,
        "stake": parsed["stake"],
        "analisis": parsed["analisis"],
        "home_id": hid,
        "away_id": aid
    }

# ============ PUBLICAR ============
def publicar_pick(canal, pick):
    pred_lower = pick["prediccion"].lower()
    if "goles" in pred_lower or "goal" in pred_lower:
        emoji = "⚽"
    elif "corner" in pred_lower or "corners" in pred_lower:
        emoji = "🚩"
    elif "btts" in pred_lower or "ambos" in pred_lower or "marcan" in pred_lower:
        emoji = "🎯"
    else:
        emoji = "📊"

    liga_emoji = "🔴" if pick["liga_id"] in LIGAS_TOP else "🟢"

    msg = f"""📅 PICK DEL DIA
{liga_emoji} {pick["partido"]}
🏆 {pick["liga"]}

{emoji} {pick["prediccion"]}
💰 Cuota esperada por la IA: ~{pick["cuota_esperada"]}
🎯 Stake: {pick["stake"]}

📝 Analisis:
{pick["analisis"]}"""

    if telegram_send_message(canal, msg):
        print(f"[OK] Publicado en {canal}: {pick['partido']}")
        return True
    else:
        print(f"[ERROR] No se pudo publicar en {canal}")
        return False

def notificar_admin(mensaje):
    try:
        telegram_send_message(ADMIN_CHAT_ID, f"🚨 NOTIFICACION DEL BOT:\n{mensaje}")
    except Exception as e:
        print(f"[ERROR] No se pudo notificar al admin: {e}")

# ============ FLUJO DIARIO ============
def run_daily():
    global api_calls
    api_calls = 0

    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"[INFO] Iniciando publicacion diaria - {fecha_hoy}")
    print(f"[INFO] Limite API: {MAX_API_CALLS} llamadas/dia")
    print(f"{sep}\n")

    # PASO 1: Buscar partidos en orden de prioridad
    print("[PASO 1] Buscando partidos...")

    partidos_top = get_matches(fecha_hoy, LIGAS_TOP)
    print(f"  🔴 Ligas TOP: {len(partidos_top)} partidos")

    partidos_sec = []
    partidos_fem = []
    partidos_todos = []

    # Si no hay suficientes partidos top, buscar secundarias
    if len(partidos_top) < 15:
        partidos_sec = get_matches(fecha_hoy, LIGAS_SECUNDARIAS - LIGAS_TOP)
        print(f"  🟡 Secundarias: {len(partidos_sec)} partidos")

    # Si aún no hay suficientes, buscar femeninas
    total = len(partidos_top) + len(partidos_sec)
    if total < 15:
        partidos_fem = get_matches(fecha_hoy, LIGAS_FEMENINAS - LIGAS_TOP - LIGAS_SECUNDARIAS)
        print(f"  🟣 Femeninas: {len(partidos_fem)} partidos")

    # Combinar y ordenar: top primero, luego secundarias, luego femeninas
    todos = partidos_top + partidos_sec + partidos_fem
    print(f"  📊 TOTAL: {len(todos)} partidos disponibles\n")

    if not todos:
        msg = f"No hay partidos hoy ({fecha_hoy}). Canales en silencio."
        print(f"[WARN] {msg}")
        notificar_admin(msg)
        return

    # PASO 2: Analizar partidos hasta conseguir 6 picks o agotar partidos
    print("[PASO 2] Analizando partidos con Gemini...")
    print("[INFO] Buscando minimo 6 picks (minimo 2 para canal gratis)\n")

    picks = []
    max_partidos_analizar = min(len(todos), 20)  # Max 20 para no saturar API

    for i, match in enumerate(todos[:max_partidos_analizar], 1):
        if len(picks) >= 6:
            print(f"\n[INFO] 6 picks alcanzados, deteniendo analisis")
            break

        if api_calls >= MAX_API_CALLS - 5:
            print(f"\n[WARN] Limite API cercano ({api_calls}/{MAX_API_CALLS}), deteniendo")
            break

        home = match["teams"]["home"]["name"]
        away = match["teams"]["away"]["name"]
        print(f"  [{i}/{max_partidos_analizar}] {home} vs {away}...", end=" ")

        pick = analizar_partido(match)
        if pick:
            picks.append(pick)
            print(f"✅ OK (prob: {pick['probabilidad']}%, cuota: {pick['cuota_esperada']})")
        else:
            print("❌ SIN VALOR")

    print(f"\n[INFO] {len(picks)} picks validos encontrados")
    print(f"[INFO] Llamadas API usadas: {api_calls}/{MAX_API_CALLS}\n")

    if not picks:
        msg = f"Gemini no encontro valor en ningun partido hoy ({fecha_hoy}). Canales en silencio."
        print(f"[WARN] {msg}")
        notificar_admin(msg)
        return

    # PASO 3: Clasificar por cuota
    print("[PASO 3] Clasificando picks por cuota...\n")

    picks_gratis = [p for p in picks if 1.20 <= p["cuota_esperada"] <= 1.70]
    picks_premium = [p for p in picks if p["cuota_esperada"] > 1.70]

    # Ordenar por mejor relacion (stake alto primero, luego cuota)
    picks_gratis.sort(key=lambda x: (x["stake"], x["cuota_esperada"]), reverse=True)
    picks_premium.sort(key=lambda x: (x["stake"], x["cuota_esperada"]), reverse=True)

    print(f"  ✅ Candidatos gratis (1.20-1.70): {len(picks_gratis)}")
    print(f"  💎 Candidatos premium (>1.70): {len(picks_premium)}\n")

    # Verificar minimo 2 para gratis
    if len(picks_gratis) < 2:
        print(f"[WARN] Solo {len(picks_gratis)} pick(s) para canal gratis (minimo 2)")
        notificar_admin(f"ALERTA: Solo {len(picks_gratis)} pick(s) para canal gratis hoy. Se publicaran los que haya.")

    # PASO 4: Publicar
    print("[PASO 4] Publicando picks...\n")

    # GRATIS: max 2
    gratis_publicados = 0
    for pick in picks_gratis[:2]:
        if publicar_pick(CHANNEL_FREE, pick):
            save_pick(fecha_hoy, "gratis", pick["partido"], pick["liga"], pick["mercado"],
                     pick["tipo"], pick["linea"], pick["prediccion"], pick["probabilidad"],
                     pick["cuota_esperada"], pick["stake"], pick["analisis"])
            gratis_publicados += 1

    # PREMIUM: los 2 del gratis + hasta 4 exclusivos (total 6)
    premium_publicados = 0
    premium_picks = picks_gratis[:2] + picks_premium[:4]

    for pick in premium_picks[:6]:
        if publicar_pick(CHANNEL_PREMIUM, pick):
            save_pick(fecha_hoy, "premium", pick["partido"], pick["liga"], pick["mercado"],
                     pick["tipo"], pick["linea"], pick["prediccion"], pick["probabilidad"],
                     pick["cuota_esperada"], pick["stake"], pick["analisis"])
            premium_publicados += 1

    print(f"\n{sep}")
    print(f"[RESUMEN] {fecha_hoy}")
    print(f"  Gratis publicados: {gratis_publicados}/2")
    print(f"  Premium publicados: {premium_publicados}/6")
    print(f"  API calls usadas: {api_calls}/{MAX_API_CALLS}")
    print(f"{sep}\n")

    # Notificar si no se alcanzaron minimos
    if gratis_publicados == 0:
        notificar_admin(f"🚨 CRITICO: No se publico ningun pick en el canal GRATUITO hoy ({fecha_hoy}).")
    elif gratis_publicados < 2:
        notificar_admin(f"⚠️ Solo {gratis_publicados}/2 picks publicados en canal GRATUITO hoy ({fecha_hoy}).")

    if premium_publicados == 0:
        notificar_admin(f"🚨 CRITICO: No se publico ningun pick en el canal PREMIUM hoy ({fecha_hoy}).")
    elif premium_publicados < 6:
        notificar_admin(f"ℹ️ Solo {premium_publicados}/6 picks publicados en canal PREMIUM hoy ({fecha_hoy}).")

# ============ AUTO-PING ============
def auto_ping():
    while True:
        time.sleep(600)
        print(f"[PING] Worker vivo - {datetime.now().strftime('%H:%M:%S')}")

ping_thread = threading.Thread(target=auto_ping, daemon=True)
ping_thread.start()

# ============ MAIN ============
def main():
    print("[INIT] Bot AdivinoPicks iniciado")
    print(f"[INIT] Canal gratuito: {CHANNEL_FREE}")
    print(f"[INIT] Canal premium: {CHANNEL_PREMIUM}")
    print(f"[INIT] Admin: {ADMIN_CHAT_ID}")
    print(f"[INIT] Limite API: {MAX_API_CALLS} llamadas/dia\n")

    while True:
        now = datetime.now()
        target = now.replace(hour=7, minute=0, second=0, microsecond=0)

        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        hours = int(wait_seconds // 3600)
        mins = int((wait_seconds % 3600) // 60)

        print(f"[SLEEP] Esperando hasta 7:00 AM Venezuela...")
        print(f"[SLEEP] Faltan {hours}h {mins}m ({int(wait_seconds)}s)\n")

        time.sleep(wait_seconds)

        try:
            run_daily()
        except Exception as e:
            print(f"[FATAL] Error en run_daily: {e}")
            notificar_admin(f"ERROR CRITICO en ejecucion diaria: {e}")

        print("[SLEEP] Esperando 1h antes de recalcular...\n")
        time.sleep(3600)

if __name__ == "__main__":
    main()
