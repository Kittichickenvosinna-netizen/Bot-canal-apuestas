import os
import sys
import requests
import sqlite3
import re
import time
import threading
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask

# ============ CONFIGURACION ============
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8814877491:AAGBcrryLjzfroU-BVeIvjXKU1h9x1iDnAY")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "3d885f7eabf856c82288c4128aee78fd")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6LP2n2FlP5X8blEq2WUNm-C1e2cW-aWXQpEZWRuRTJ1iw")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "1275122240")

CHANNEL_FREE = os.environ.get("CHANNEL_FREE", "@AdivinoPicksFree")
CHANNEL_PREMIUM = os.environ.get("CHANNEL_PREMIUM", "-1003266399573")
PORT = int(os.environ.get("PORT", 10000))

CARACAS = ZoneInfo("America/Caracas")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
FOOTBALL_URL = "https://v3.football.api-sports.io"
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# Contador de llamadas API
api_calls = 0
MAX_API_CALLS = 95

# ============ SERVIDOR WEB PARA RENDER (FLASK) ============
app = Flask(__name__)

@app.route('/')
def home():
    return """
    <html>
    <head><meta charset="utf-8"><title>AdivinoPicks</title></head>
    <body style="font-family:sans-serif; padding:40px; background:#111; color:#eee;">
        <h1>🤖 AdivinoPicks Bot</h1>
        <p>Estado: <span style="color:#0f0">✅ Online</span></p>
        <hr>
        <h2>🔧 Panel de Control</h2>
        <ul>
            <li><a href="/status" style="color:#4af">📊 Ver estado y hora</a></li>
            <li><a href="/telegram-test" style="color:#4af">📨 Probar Telegram (detallado)</a></li>
            <li><a href="/debug" style="color:#4af">🔍 Ver partidos de hoy (debug)</a></li>
            <li><a href="/test" style="color:#f84">⚡ FORZAR analisis AHORA</a></li>
        </ul>
        <p style="color:#888; font-size:12px; margin-top:40px;">
            Si usas <b>/test</b>, espera 3-5 minutos y revisa los logs de Render + tus canales de Telegram.
        </p>
    </body>
    </html>
    """

@app.route('/health')
def health():
    return {"status": "alive", "hora_caracas": datetime.now(CARACAS).strftime("%Y-%m-%d %H:%M:%S")}, 200

@app.route('/status')
def status():
    ahora = datetime.now(CARACAS)
    siguiente = ahora.replace(hour=7, minute=0, second=0, microsecond=0)
    if ahora >= siguiente:
        siguiente += timedelta(days=1)
    faltan = int((siguiente - ahora).total_seconds())
    horas = faltan // 3600
    mins = (faltan % 3600) // 60
    return {
        "estado": "vivo",
        "hora_actual_caracas": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "proxima_ejecucion_automatica": siguiente.strftime("%Y-%m-%d %H:%M:%S"),
        "faltan_para_7am": f"{horas}h {mins}m",
        "nota": "El bot corre automaticamente a las 7:00 AM hora Venezuela (Caracas)"
    }, 200

@app.route('/telegram-test')
def telegram_test():
    """Prueba si el bot puede enviar mensajes por Telegram con diagnostico completo"""
    resultados = {}

    # Paso 1: Verificar token con getMe
    try:
        r = requests.get(f"{TELEGRAM_URL}/getMe", timeout=10)
        data = r.json()
        if data.get("ok"):
            bot_info = data.get("result", {})
            resultados["token_valido"] = True
            resultados["bot_username"] = bot_info.get("username")
            resultados["bot_nombre"] = bot_info.get("first_name")
        else:
            resultados["token_valido"] = False
            resultados["error_getMe"] = data
            return {"ok": False, "diagnostico": resultados}, 200
    except Exception as e:
        resultados["token_valido"] = False
        resultados["error_conexion"] = str(e)
        return {"ok": False, "diagnostico": resultados}, 200

    # Paso 2: Intentar enviar mensaje al admin
    try:
        url = f"{TELEGRAM_URL}/sendMessage"
        payload = {
            "chat_id": ADMIN_CHAT_ID,
            "text": "🧪 *TEST* El bot de AdivinoPicks esta funcionando correctamente.",
            "parse_mode": "HTML"
        }
        r = requests.post(url, json=payload, timeout=10)
        data = r.json()
        resultados["respuesta_telegram"] = data

        if data.get("ok"):
            resultados["mensaje_enviado"] = True
            resultados["ok"] = True
            resultados["mensaje"] = "Mensaje de prueba enviado. Revisa tu chat privado."
        else:
            resultados["mensaje_enviado"] = False
            resultados["ok"] = False
            error_code = data.get("error_code")
            description = data.get("description")
            resultados["error_code"] = error_code
            resultados["error_description"] = description

            if error_code == 400 and "chat not found" in str(description).lower():
                resultados["sugerencia"] = "El chat_id no existe o no has iniciado conversacion con el bot. Ve a Telegram, busca el bot, enviale /start."
            elif error_code == 403:
                resultados["sugerencia"] = "El bot fue bloqueado por el usuario o no tiene permisos. Desbloquea el bot en Telegram."
            elif error_code == 401:
                resultados["sugerencia"] = "El token del bot es invalido. Revisa que no haya sido revocado en @BotFather."
            else:
                resultados["sugerencia"] = f"Error de Telegram: {description}"

    except Exception as e:
        resultados["mensaje_enviado"] = False
        resultados["ok"] = False
        resultados["error_exception"] = str(e)
        resultados["sugerencia"] = "Error de red al conectar con Telegram."

    return resultados, 200

@app.route('/debug')
def debug():
    """Busca partidos de hoy y devuelve info rapida (sin Gemini)"""
    global api_calls
    api_calls = 0
    fecha_hoy = datetime.now(CARACAS).strftime("%Y-%m-%d")

    try:
        partidos_top = get_matches(fecha_hoy, LIGAS_TOP)
        partidos_sec = get_matches(fecha_hoy, LIGAS_SECUNDARIAS - LIGAS_TOP) if len(partidos_top) < 15 else []
        partidos_fem = get_matches(fecha_hoy, LIGAS_FEMENINAS - LIGAS_TOP - LIGAS_SECUNDARIAS) if (len(partidos_top) + len(partidos_sec)) < 15 else []
        todos = partidos_top + partidos_sec + partidos_fem

        nombres = [f"{m['teams']['home']['name']} vs {m['teams']['away']['name']} ({m['league']['name']})" for m in todos[:10]]

        return {
            "fecha": fecha_hoy,
            "partidos_top": len(partidos_top),
            "partidos_secundarios": len(partidos_sec),
            "partidos_femeninos": len(partidos_fem),
            "total": len(todos),
            "api_calls_usadas": api_calls,
            "primeros_10_partidos": nombres,
            "nota": "Si TOTAL es 0, hoy no hay partidos en las ligas configuradas."
        }, 200
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}, 200

@app.route('/test')
def test_run():
    """Fuerza la ejecucion del bot ahora mismo"""
    def run_with_capture():
        try:
            print("[TEST] ============================================", flush=True)
            print("[TEST] INICIANDO RUN_DAILY FORZADO", flush=True)
            print("[TEST] ============================================", flush=True)
            run_daily()
            print("[TEST] RUN_DAILY TERMINO CORRECTAMENTE", flush=True)
        except Exception as e:
            error_msg = f"[TEST] ERROR EN RUN_DAILY: {str(e)}\n{traceback.format_exc()}"
            print(error_msg, flush=True)
            try:
                notificar_admin(f"🧪 ERROR EN TEST:\n{str(e)[:3000]}")
            except:
                pass

    t = threading.Thread(target=run_with_capture, daemon=True)
    t.start()
    return """
    <html>
    <head><meta charset="utf-8"><title>Test - AdivinoPicks</title></head>
    <body style="font-family:sans-serif; padding:40px; background:#111; color:#eee;">
        <h1>⚡ Test iniciado</h1>
        <p>El bot esta analizando partidos <b>AHORA MISMO</b>.</p>
        <p>Esto puede tardar <b>3 a 5 minutos</b>.</p>
        <hr>
        <h2>Revisa en este orden:</h2>
        <ol>
            <li><b>Logs de Render</b> (boton "Live tail") - busca [TEST] o [INFO]</li>
            <li><b>Tus canales de Telegram</b> - deberian llegar picks</li>
            <li><b>Tu chat privado</b> - si hay error, llega alerta</li>
        </ol>
        <p style="color:#888; font-size:12px;">Si no ves nada en 5 min, recarga los logs.</p>
        <p><a href="/" style="color:#4af">← Volver al panel</a></p>
    </body>
    </html>
    """, 200

def start_web_server():
    print(f"[RENDER] Servidor Flask activo en 0.0.0.0:{PORT}", flush=True)
    app.run(host='0.0.0.0', port=PORT, threaded=True, debug=False, use_reloader=False)

# Iniciar servidor en thread separado
server_thread = threading.Thread(target=start_web_server, daemon=True)
server_thread.start()

# ============ AUTO-PING AL PROPIO PUERTO ============
def auto_ping_local():
    while True:
        time.sleep(600)
        try:
            requests.get(f"http://localhost:{PORT}/health", timeout=5)
            print(f"[PING] Auto-ping OK - {datetime.now(CARACAS).strftime('%H:%M:%S')}", flush=True)
        except Exception as e:
            print(f"[PING] Auto-ping fallo: {e}", flush=True)

ping_thread = threading.Thread(target=auto_ping_local, daemon=True)
ping_thread.start()

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
            print(f"[ERROR] Telegram: {result}", flush=True)
            return False
    except Exception as e:
        print(f"[ERROR] Telegram send: {e}", flush=True)
        return False

# ============ API FOOTBALL ============
def api_request(endpoint, params=None):
    global api_calls
    if api_calls >= MAX_API_CALLS:
        print(f"[WARN] Limite API alcanzado ({api_calls}/{MAX_API_CALLS})", flush=True)
        return []

    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    try:
        r = requests.get(f"{FOOTBALL_URL}/{endpoint}", headers=headers, params=params, timeout=15)
        api_calls += 1
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        print(f"[ERROR] API Football: {e}", flush=True)
        return []

def get_matches(date_str, ligas):
    matches = api_request("fixtures", {"date": date_str, "timezone": "America/Caracas"})
    return [m for m in matches if m.get("league", {}).get("id") in ligas]

def get_team_form(team_id):
    return api_request("fixtures", {"team": team_id, "last": 5})

def get_h2h(t1, t2):
    return api_request("fixtures/headtohead", {"h2h": f"{t1}-{t2}", "last": 5})

def get_team_stats(team_id, league_id, season):
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
        print(f"[ERROR] Gemini: {e}", flush=True)
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

# ============ PROMPT ============
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
9. Elige la linea con MEJOR relacion valor/riesgo.

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

    if api_calls >= MAX_API_CALLS - 5:
        print(f"[WARN] Casi en limite API ({api_calls}/{MAX_API_CALLS})", flush=True)
        return None

    home_form = get_team_form(hid)
    away_form = get_team_form(aid)
    h2h = get_h2h(hid, aid)

    home_stats = None
    away_stats = None
    if api_calls < MAX_API_CALLS - 10:
        home_stats = get_team_stats(hid, league_id, season)
        away_stats = get_team_stats(aid, league_id, season)

    prompt = build_prompt(match, home_form, away_form, h2h, home_stats, away_stats)
    response = get_gemini_response(prompt)

    if not response:
        print(f"[WARN] Gemini no respondio", flush=True)
        return None

    parsed = parse_gemini_response(response)

    if not parsed["prediccion"] or parsed["prediccion"].upper() == "SIN VALOR":
        print(f"[INFO] Sin valor", flush=True)
        return None

    if parsed["probabilidad"] < 30 or parsed["probabilidad"] > 95:
        print(f"[WARN] Probabilidad sospechosa: {parsed['probabilidad']}%", flush=True)
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
        print(f"[OK] Publicado en {canal}: {pick['partido']}", flush=True)
        return True
    else:
        print(f"[ERROR] No se pudo publicar en {canal}", flush=True)
        return False

def notificar_admin(mensaje):
    try:
        telegram_send_message(ADMIN_CHAT_ID, f"🚨 NOTIFICACION DEL BOT:\n{mensaje}")
    except Exception as e:
        print(f"[ERROR] No se pudo notificar al admin: {e}", flush=True)

# ============ FLUJO DIARIO ============
def run_daily():
    global api_calls
    api_calls = 0

    fecha_hoy = datetime.now(CARACAS).strftime("%Y-%m-%d")
    sep = "=" * 60
    print(f"\n{sep}", flush=True)
    print(f"[INFO] Iniciando publicacion diaria - {fecha_hoy}", flush=True)
    print(f"[INFO] Limite API: {MAX_API_CALLS} llamadas/dia", flush=True)
    print(f"{sep}\n", flush=True)

    print("[PASO 1] Buscando partidos...", flush=True)

    partidos_top = get_matches(fecha_hoy, LIGAS_TOP)
    print(f"  🔴 Ligas TOP: {len(partidos_top)} partidos", flush=True)

    partidos_sec = []
    partidos_fem = []

    if len(partidos_top) < 15:
        partidos_sec = get_matches(fecha_hoy, LIGAS_SECUNDARIAS - LIGAS_TOP)
        print(f"  🟡 Secundarias: {len(partidos_sec)} partidos", flush=True)

    total = len(partidos_top) + len(partidos_sec)
    if total < 15:
        partidos_fem = get_matches(fecha_hoy, LIGAS_FEMENINAS - LIGAS_TOP - LIGAS_SECUNDARIAS)
        print(f"  🟣 Femeninas: {len(partidos_fem)} partidos", flush=True)

    todos = partidos_top + partidos_sec + partidos_fem
    print(f"  📊 TOTAL: {len(todos)} partidos disponibles\n", flush=True)

    if not todos:
        msg = f"No hay partidos hoy ({fecha_hoy}). Canales en silencio."
        print(f"[WARN] {msg}", flush=True)
        notificar_admin(msg)
        return

    print("[PASO 2] Analizando partidos con Gemini...", flush=True)
    print("[INFO] Buscando minimo 6 picks (minimo 2 para canal gratis)\n", flush=True)

    picks = []
    max_partidos_analizar = min(len(todos), 20)

    for i, match in enumerate(todos[:max_partidos_analizar], 1):
        if len(picks) >= 6:
            print(f"\n[INFO] 6 picks alcanzados, deteniendo analisis", flush=True)
            break

        if api_calls >= MAX_API_CALLS - 5:
            print(f"\n[WARN] Limite API cercano ({api_calls}/{MAX_API_CALLS}), deteniendo", flush=True)
            break

        home = match["teams"]["home"]["name"]
        away = match["teams"]["away"]["name"]
        print(f"  [{i}/{max_partidos_analizar}] {home} vs {away}...", end=" ", flush=True)

        pick = analizar_partido(match)
        if pick:
            picks.append(pick)
            print(f"✅ OK (prob: {pick['probabilidad']}%, cuota: {pick['cuota_esperada']})", flush=True)
        else:
            print("❌ SIN VALOR", flush=True)

    print(f"\n[INFO] {len(picks)} picks validos encontrados", flush=True)
    print(f"[INFO] Llamadas API usadas: {api_calls}/{MAX_API_CALLS}\n", flush=True)

    if not picks:
        msg = f"Gemini no encontro valor hoy ({fecha_hoy}). Canales en silencio."
        print(f"[WARN] {msg}", flush=True)
        notificar_admin(msg)
        return

    print("[PASO 3] Clasificando picks por cuota...\n", flush=True)

    picks_gratis = [p for p in picks if 1.20 <= p["cuota_esperada"] <= 1.70]
    picks_premium = [p for p in picks if p["cuota_esperada"] > 1.70]

    picks_gratis.sort(key=lambda x: (x["stake"], x["cuota_esperada"]), reverse=True)
    picks_premium.sort(key=lambda x: (x["stake"], x["cuota_esperada"]), reverse=True)

    print(f"  ✅ Candidatos gratis (1.20-1.70): {len(picks_gratis)}", flush=True)
    print(f"  💎 Candidatos premium (>1.70): {len(picks_premium)}\n", flush=True)

    if len(picks_gratis) < 2:
        print(f"[WARN] Solo {len(picks_gratis)} pick(s) para canal gratis (minimo 2)", flush=True)
        notificar_admin(f"ALERTA: Solo {len(picks_gratis)} pick(s) para canal gratis hoy.")

    print("[PASO 4] Publicando picks...\n", flush=True)

    gratis_publicados = 0
    for pick in picks_gratis[:2]:
        if publicar_pick(CHANNEL_FREE, pick):
            save_pick(fecha_hoy, "gratis", pick["partido"], pick["liga"], pick["mercado"],
                     pick["tipo"], pick["linea"], pick["prediccion"], pick["probabilidad"],
                     pick["cuota_esperada"], pick["stake"], pick["analisis"])
            gratis_publicados += 1

    premium_publicados = 0
    premium_picks = picks_gratis[:2] + picks_premium[:4]

    for pick in premium_picks[:6]:
        if publicar_pick(CHANNEL_PREMIUM, pick):
            save_pick(fecha_hoy, "premium", pick["partido"], pick["liga"], pick["mercado"],
                     pick["tipo"], pick["linea"], pick["prediccion"], pick["probabilidad"],
                     pick["cuota_esperada"], pick["stake"], pick["analisis"])
            premium_publicados += 1

    print(f"\n{sep}", flush=True)
    print(f"[RESUMEN] {fecha_hoy}", flush=True)
    print(f"  Gratis publicados: {gratis_publicados}/2", flush=True)
    print(f"  Premium publicados: {premium_publicados}/6", flush=True)
    print(f"  API calls usadas: {api_calls}/{MAX_API_CALLS}", flush=True)
    print(f"{sep}\n", flush=True)

    if gratis_publicados == 0:
        notificar_admin(f"🚨 CRITICO: No se publico ningun pick en GRATUITO hoy ({fecha_hoy}).")
    elif gratis_publicados < 2:
        notificar_admin(f"⚠️ Solo {gratis_publicados}/2 picks en GRATUITO hoy ({fecha_hoy}).")

    if premium_publicados == 0:
        notificar_admin(f"🚨 CRITICO: No se publico ningun pick en PREMIUM hoy ({fecha_hoy}).")
    elif premium_publicados < 6:
        notificar_admin(f"ℹ️ Solo {premium_publicados}/6 picks en PREMIUM hoy ({fecha_hoy}).")

# ============ MAIN ============
def main():
    print("[INIT] Bot AdivinoPicks iniciado", flush=True)
    print(f"[INIT] Puerto: {PORT}", flush=True)
    print(f"[INIT] Canal gratuito: {CHANNEL_FREE}", flush=True)
    print(f"[INIT] Canal premium: {CHANNEL_PREMIUM}", flush=True)
    print(f"[INIT] Admin: {ADMIN_CHAT_ID}", flush=True)
    print(f"[INIT] Limite API: {MAX_API_CALLS} llamadas/dia\n", flush=True)

    while True:
        now = datetime.now(CARACAS)
        target = now.replace(hour=7, minute=0, second=0, microsecond=0)

        if now >= target:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        hours = int(wait_seconds // 3600)
        mins = int((wait_seconds % 3600) // 60)

        print(f"[SLEEP] Esperando hasta 7:00 AM Venezuela...", flush=True)
        print(f"[SLEEP] Faltan {hours}h {mins}m ({int(wait_seconds)}s)\n", flush=True)

        time.sleep(wait_seconds)

        try:
            run_daily()
        except Exception as e:
            print(f"[FATAL] Error en run_daily: {e}", flush=True)
            notificar_admin(f"ERROR CRITICO: {e}")

        print("[SLEEP] Esperando 1h antes de recalcular...\n", flush=True)
        time.sleep(3600)

if __name__ == "__main__":
    main()
