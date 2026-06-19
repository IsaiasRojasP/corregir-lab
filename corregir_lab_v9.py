import csv
import json
import os
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

try:
    import requests
except ImportError:
    print("Instala requests: pip install requests")
    sys.exit(1)

try:
    import unidecode
except ImportError:
    print("Instala unidecode: pip install unidecode")
    sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

# Establecer el directorio de trabajo al de ubicación del script
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════════════════════════
# REGLAS Y EXPRESIONES REGULARES DE CONTROL
# ══════════════════════════════════════════════════════════════════════════════

_PATRON_RESTRICCION_EVAL = re.compile(
    r'\b(exigir|restringir|regla|descontar|restriccion|restricción|obligar|penalizar|considerar|descuento)\b',
    re.IGNORECASE
)

_PATRON_ELIMINAR_FRASE = re.compile(
    r'\b(?:eliminar|quitar|borrar|remover)\s+(?:la\s+)?(?:frase|oracion|oración|texto)\s+[\'""«“](?P<frase>[^\'""»”]+)[\'""»”]',
    re.IGNORECASE
)

_PATRON_CORRECCION = re.compile(
    r'\b(item|ítem|criterio|id|#)\s*[:#]?\s*(\d{1,2})\b',
    re.IGNORECASE
)


# ══════════════════════════════════════════════════════════════════════════════
# 1. MOTOR LOCAL OFFLINE DE CONTINGENCIA (PULIDO ORTOGRÁFICO Y LATEX)
# ══════════════════════════════════════════════════════════════════════════════

class LocalAcademicCorrector:
    """Sustituto offline de contingencia para pulido ortográfico y corrección de artefactos de LaTeX."""
    
    REGLAS_SUSTITUCION = {
        r'´ı': 'í',
        r'´a': 'á',
        r'´e': 'é',
        r'´o': 'ó',
        r'´u': 'u',
        r'´I': 'I',
        r'´A': 'Á',
        r'´E': 'É',
        r'´O': 'Ó',
        r'´U': 'Ú',
        r'˜n': 'ñ',
        r'˜N': 'Ñ',
        r'\bpié\s+de\s+página\b': 'pie de página',
        r'\bpicos?\s+espectrales\b': 'máximos espectrales',
        r'\bjorobas?\b': 'máximos de intensidad',
        r'\bTabla\s+(\d+)\b': r'Tabla \1',
        r'\bFigura\s+(\d+)\b': r'Figura \1',
    }

    @classmethod
    def corregir(cls, texto: str) -> str:
        """Aplica sustituciones, limpia espacios espurios y normaliza la puntuación."""
        if not texto:
            return ""

        for patron, reemplazo in cls.REGLAS_SUSTITUCION.items():
            texto = re.sub(patron, reemplazo, texto, flags=re.IGNORECASE)

        texto = re.sub(r'[ \t]{2,}', ' ', texto)
        texto = re.sub(r'\s+([,;\.\:\?!\)])', r'\1', texto)
        texto = re.sub(r'([,;\.\:\?!\)])(?=[a-zA-ZáéíóúñÁÉÍÓÚÑ])', r'\1 ', texto)

        def _mayuscula_match(m):
            return m.group(1) + m.group(2).upper()
        texto = re.sub(r'(\.\s+)([a-zñáéíóú])', _mayuscula_match, texto)

        return texto.strip()


# ══════════════════════════════════════════════════════════════════════════════
# 2. CONFIGURACIÓN Y PERSISTENCIA
# ══════════════════════════════════════════════════════════════════════════════

class ConfigManager:
    """Gestor de configuración de entorno persistente."""
    
    def __init__(self):
        self.config_dir = Path(".synapse_config")
        self.config_file = self.config_dir / "config_corregir_lab_v3.json"
        self._ensure_directory()
    
    def _ensure_directory(self):
        try:
            self.config_dir.mkdir(exist_ok=True)
        except PermissionError:
            self.config_dir = Path.cwd()
            self.config_file = self.config_dir / "config_corregir_lab_v3.json"
    
    def guardar(self, config: dict):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ Error guardando config: {e}")
    
    def cargar(self) -> dict:
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
            return {}


# ══════════════════════════════════════════════════════════════════════════════
# 3. DEFINICIONES GLOBALES
# ══════════════════════════════════════════════════════════════════════════════

MODO = "local"
LM_STUDIO_URL = "http://127.0.0.1:1234/v1/chat/completions"
LM_MODEL_NAME = "Qwen3.5-35B-A3B"
TEMPERATURE = 0.05
MAX_OUTPUT_TOKENS = 32768
TIMEOUT_API = 45

GEMINI_MODEL = "gemma-4-26b-a4b-it"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS = [
    "gemma-4-26b-a4b-it",
    "gemma-4-31b-it",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-flash",
    "gemini-3.1-pro"
]

PROVEEDORES_API = {
    "1": ("Google Gemini", "gemini"),
    "2": ("OpenAI", "openai_compat"),
    "3": ("Anthropic Claude", "anthropic"),
    "4": ("Otro (OpenAI-compatible)", "openai_compat"),
}

PUNTAJES_MAP = {
    "E":  {"descripcion": "Excelente",      "puntaje": 5},
    "B":  {"descripcion": "Bueno",          "puntaje": 4},
    "A":  {"descripcion": "Aceptable",      "puntaje": 3},
    "D":  {"descripcion": "Deficiente",     "puntaje": 2},
    "MD": {"descripcion": "Muy Deficiente", "puntaje": 1},
    "AU": {"descripcion": "Ausente",        "puntaje": 0},
}

ASIGNATURAS = {
    "1": {"codigo": "FIS120", "nombre": "Física General II"},
    "2": {"codigo": "FIS130", "nombre": "Física General III"},
    "3": {"codigo": "FIS140", "nombre": "Física General IV"},
}

DIAS = {
    "1": {"nombre": "Lunes",     "cod": "LU"},
    "2": {"nombre": "Martes",    "cod": "MA"},
    "3": {"nombre": "Miércoles", "cod": "MI"},
    "4": {"nombre": "Jueves",    "cod": "JU"},
    "5": {"nombre": "Viernes",   "cod": "VI"},
}

def _gen_bloques():
    b = {}
    c = 1
    for i in range(1, 12, 2):
        b[str(c)] = {"display": f"Bloques {i}-{i+1}", "suf": f"{i}-{i+1}"}
        c += 1
    return b

BLOQUES = _gen_bloques()

RUBRICA_ITEMS_BASE = [
    {"id": 1, "seccion": "1. RESUMEN", "desc": "Menciona leyes físicas y las ecuaciones vinculadas con la experiencia"},
    {"id": 2, "seccion": "1. RESUMEN", "desc": "Explica sintéticamente lo realizado"},
    {"id": 3, "seccion": "1. RESUMEN", "desc": "Indica explícitamente los resultados obtenidos"},
    {"id": 4, "seccion": "1. RESUMEN", "desc": "Indica el propósito de la actividad, declara objetivos"},
    {"id": 5, "seccion": "2. MEDICIONES", "desc": "Lista los equipos y los instrumentos que usa, indicando sus errores experimentales"},
    {"id": 6, "seccion": "2. MEDICIONES", "desc": "Adjunta esquemas y/o fotografías de su autoría, del montaje, explaining componentes"},
    {"id": 7, "seccion": "2. MEDICIONES", "desc": "Explica el procedimiento experimental para realizar las mediciones"},
    {"id": 8, "seccion": "2. MEDICIONES", "desc": "Analiza fuentes de error en las mediciones y las acciones que tomó para minimizarlos"},
    {"id": 9, "seccion": "2. MEDICIONES", "desc": "Incorpora tablas con los valores medidos, sus unidades, cifras significativas y errores"},
    {"id": 10, "seccion": "2. MEDICIONES", "desc": "Incorpora gráficas o diagramas de dispersión de las mediciones legibles"},
    {"id": 11, "seccion": "2. MEDICIONES", "desc": "Realiza ajustes indicando explícitamente parámetros con unidades y bondad de ajuste"},
    {"id": 12, "seccion": "3. ANÁLISIS DE DATOS", "desc": "Vincula el modelo teórico, justificando modelo e interpretando parámetros"},
    {"id": 13, "seccion": "3. ANÁLISIS DE DATOS", "desc": "Realiza cálculos de propagación de errores y/o errores porcentuales"},
    {"id": 14, "seccion": "3. ANÁLISIS DE DATOS", "desc": "Informa las magnitudes físicas buscadas con unidades y cifras significativas"},
    {"id": 15, "seccion": "3. ANÁLISIS DE DATOS", "desc": "Responde preguntas planteadas en la guía"},
    {"id": 16, "seccion": "4. CONCLUSIONES", "desc": "Resume los resultados obtenidos"},
    {"id": 17, "seccion": "4. CONCLUSIONES", "desc": "Hace reflexiones de los resultados obtenidos"},
    {"id": 18, "seccion": "4. CONCLUSIONES", "desc": "Analiza si se cumplieron los objetivos de la actividad"},
    {"id": 19, "seccion": "5. REFERENCIAS", "desc": "Cita adecuadamente cualquier valor referencial usado"},
    {"id": 20, "seccion": "5. REFERENCIAS", "desc": "Cita los libros usados y otras fuentes válidas"}
]


# ══════════════════════════════════════════════════════════════════════════════
# 10. SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Eres un evaluador de informes de Laboratorio de Física. Tu única tarea es evaluar criterios de la rúbrica cuando se te lo indiquen. Responde SOLO lo que se te pide en cada llamada. No inicies conversación, no hagas preguntas, no avances a pasos siguientes por tu cuenta.

════════════════════════════════════════
TONO Y ESTILO
════════════════════════════════════════
- Toda justificación debe estar escrita estrictamente en tono impersonal usando la pasiva con "se" (ej. "Se presenta una lista completa de instrumentos...", "No obstante, se omitieron las referencias bibliográficas..."). PROHIBIDO el uso de primera persona ("mi análisis indica", "según mi evaluación", "al revisar los documentos").
- No utilices lenguaje punitivo. Evita "castigo", "penalización" o "falta grave".
- Usa lenguaje de mejora: "Para alcanzar la excelencia en este criterio, se requiere...", "La omisión de [X] impide calificar este ítem con el puntaje máximo".
- Explica el PORQUÉ académico de cada observación.
- PROHIBIDO penalizar el estilo de redacción. La evaluación es exclusivamente de contenido técnico.
- Observaciones de claridad o gramática: etiquétalas como "💡 Sugerencia Pedagógica (sin efecto en el puntaje)" al final.
- Está prohibido el uso de expresiones rebuscadas, pomposas o innecesariamente formales que no aporten información técnica real. Ejemplos de frases prohibidas: 'se inscribe en una progresión documentada', 'trayectoria ascendente', 'evidencia que el grupo dispone de los fundamentos conceptuales', 'consolida una base estructural y discursiva estable', o cualquier construcción similar que infle retóricamente una idea simple. Si una idea puede expresarse en palabras directas, debe expresarse así. Por ejemplo, en lugar de 'se inscribe en una progresión documentada a lo largo del semestre', escribe simplemente 'es inferior a las notas previas del semestre'. Antes de incluir cualquier frase, aplica esta prueba: ¿diría esto un ingeniero o científico en un informe técnico real, o suena a relleno retórico? Si suena a relleno, elimínala o reemplázala por una oración concreta y directa.

════════════════════════════════════════
REGLAS TÉCNICAS DE VALIDACIÓN ESTADÍSTICA
════════════════════════════════════════
REGLA 1 — Para valores de referencia (constantes físicas tabuladas):
  Usa Error Porcentual: E% = |experimental - teórico| / teórico × 100.
  Un E% bajo (< 5%) valida la medición.

REGLA 2 — Para modelos empíricos / exponentes de ajuste:
  Usa Intervalo de Confianza: el valor teórico debe estar en [B ± σ_B].
  PROHIBIDO evaluar exponentes con solo error porcentual.

════════════════════════════════════════
RIGOR EN PARÁMETROS DE AJUSTE
════════════════════════════════════════
Para cada parámetro de ajuste, exigir: (1) unidades físicas, (2) incertidumbre asociada, (3) cifras significativas correctas. Sin los tres, no dar puntaje máximo en criterios de análisis.

════════════════════════════════════════
EVALUACIÓN DE PREGUNTAS DE LA GUÍA
════════════════════════════════════════
Evaluar por CORRECCIÓN LÓGICA respecto a los datos del grupo, no por extensión.
- Coherente con sus datos → puntaje máximo.
- PROHIBIDO penalizar por "falta de profundidad" salvo que la rúbrica exija explícitamente desarrollo argumentativo.
- Solo reducir puntaje si la respuesta es físicamente incorrecta, contradice sus datos, o está ausente.

════════════════════════════════════════
EL RESUMEN COMO ABSTRACT CIENTÍFICO
════════════════════════════════════════
El ítem "Indica explícitamente los resultados obtenidos" usa esta escala OBLIGATORIA:
  AU (0) → No menciona ningún resultado, ni cualitativo.
  D  (2) → Resultados SOLO cualitativos, sin ningún valor numérico, o declaración explícita de carencia de valores en el resumen.
  A  (3) → Algún dato numérico pero sin incertidumbre o sin unidades.
  B  (4) → Datos numéricos con unidades, sin incertidumbre.
  E  (5) → Datos cuantitativos completos: valor, incertidumbre y unidades.
Esta regla afecta ÚNICAMENTE a ese ítem.

════════════════════════════════════════
LIMITACIÓN VISUAL
════════════════════════════════════════
No puedes ver imágenes directamente. La única información visual válida es la declarada
explícitamente en el bloque "VERIFICACIÓN VISUAL DEL EVALUADOR HUMANO" del contexto.
PROHIBIDO inferir, suponer o describir atributos visuales (colores, fondos, tamaños de fuente,
resolución, legibilidad específica de elementos) que no estén textualmente confirmados en ese bloque.
Para ítems de gráficas o fotografías (ítems 6 y 10), si no hay información visual adicional,
la justificación DEBE basarse ÚNICAMENTE en lo declarado en la verificación del evaluador.
NUNCA repitas ni amplíes con inventos lo que aparece en ese bloque.

════════════════════════════════════════
TERMINOLOGÍA — FÍSICA EN CHILE
════════════════════════════════════════
PROHIBIDO: "pico" para referirse a máximos espectrales o de intensidad.
CORRECTO: "máximo", "línea espectral", "línea de emisión", "máximo de intensidad".
PROHIBIDO: expresiones coloquiales como "joroba", "respecto al pico máximo".

════════════════════════════════════════
REGLAS DE JUSTIFICACIÓN
════════════════════════════════════════
- Tono impersonal: Toda justificación debe redactarse exclusivamente en pasiva refleja con "se". PROHIBIDO el uso de primera persona ("mi análisis indica", "según mi evaluación", "al revisar los documentos") o segunda persona ("el estudiante no incluyó"). Usar siempre construcciones como "Se presenta una lista completa de instrumentos...", "No obstante, se omitieron las referencias bibliográficas...", "Se observa que el informe incluye...".
- PROHIBIDO repetir la calificación en la justificación.
  Incorrecto: "Se califica como Deficiente"
  Correcto: "La omisión de valores numéricos impide calificar este ítem con el puntaje máximo."
- Estructura pedagógica diferenciada por puntaje para las 20 justificaciones:
  * Para calificaciones máximas (Excelente o 5/5): Redacta una o dos oraciones breves, fluidas y directas que describan afirmativa y positivamente el éxito técnico real del grupo en ese criterio. ESTÁ TERMINANTEMENTE PROHIBIDO inventar recomendaciones, sugerencias de "mejora futura", consejos de "precisión aún mayor", o pasos adicionales para mantener el nivel. Si obtuvieron un 5/5, el comentario debe limitarse al reconocimiento del acierto y finalizar ahí.
  * Para calificaciones menores a la máxima (0 a 4/5): Redacta un único párrafo fluido, continuo y libre de numeraciones (PROHIBIDO usar (1), (2), (3), incisos o viñetas). El texto debe transicionar orgánicamente entre el elemento técnico que sí se logró incorporar, la carencia o error específico identificado y la sugerencia constructiva para resolverlo en el futuro.
- Regla de asimilación orgánica: PROHIBIDO mencionar bajo ninguna circunstancia que se están siguiendo indicaciones del chat, del evaluador docente o de correcciones previas de la conversación. Las correcciones deben presentarse como si fueran observaciones directas del evaluador sobre el informe, no como respuestas a instrucciones externas.
- Regla de traducción: Los términos en inglés de la rúbrica deben traducirse inmediatamente al español. PROHIBIDO usar: "abstract" (usar "resumen"), "plot" (usar "gráfica"), "fitting" (usar "ajuste"), "explaining" (usar "explicando" o "se explica"), "layout" (usar "disposición" o "formato"). Usa siempre el término técnico en español.
- PROHIBIDO agregar información que no esté en el informe o que no haya sido confirmada por el evaluador en el chat. Si no tienes evidencia, no la inventes.
- ESTÁ ESTRICTAMENTE PROHIBIDO usar frases de carácter defensivo, redundante o basadas en la "ausencia de errores" al justificar notas de 5/5 (por ejemplo: "No se identifica carencia específica que impida calificar este ítem con el puntaje máximo" o "No se observan fallas para restar puntaje"). 
- ESTÁ ESTRICTAMENTE PROHIBIDO el uso de sintaxis, comandos, formatos o símbolos de LaTeX (como \\theta, \\Delta, \\times, \\sigma, etc.) en las justificaciones o en el comentario general. Toda expresión, variable o magnitud debe ser redactada en texto plano y en español legible para el alumno (ej. escribe 'ángulo theta' en vez de '\\theta'; 'incertidumbre' en vez de '\\Delta').
- ESTÁ ESTRICTAMENTE PROHIBIDO usar expresiones como "se descuentan", "se penaliza", "se restan puntos", "se sanciona", "castigo" o cualquier lenguaje que cuantifique numéricamente la carencia. En lugar de eso, describe simplemente qué falta o qué está incorrecto según la rúbrica.
- Toda observación debe fundamentarse en una carencia o error REAL de acuerdo a la rúbrica.
- Evalúa cada criterio de forma independiente. Si un error afecta a múltiples criterios, menciónalo en cada uno de ellos según corresponda a la descripción del ítem. No omitas observaciones en un ítem solo porque ya fueron señaladas en otro.

════════════════════════════════════════
SINCRONIZACIÓN Y REESCRITURA DE COMENTARIOS
════════════════════════════════════════
Si el usuario te solicita explícitamente modificar, reescribir o corregir los "COMENTARIOS ADICIONALES" (comentario general) o ajustar calificaciones desde el chat, debes responder de manera conversacional explicando el cambio, pero DEBES incluir de manera OBLIGATORIA al final de tu respuesta un bloque de código JSON con la clave "comentario_general" que contenga el párrafo de comentarios reescrito completo (ej: {"comentario_general": "..."}). Esto permite que el sistema del evaluador sincronice y guarde tus cambios de forma persistente.
"""

PROMPT_LECTURA = (
    "Lee los archivos y declara únicamente: "
    "(1) qué archivos recibiste, "
    "(2) el nombre de la experiencia según la guía, "
    "(3) los estudiantes identificados en el informe, "
    "(4) si hay correcciones previas del MISMO GRUPO: lista para cada ítem relevante "
    "(id 1-20) el puntaje anterior y el error específico señalado, "
    "(5) si hay correcciones de OTROS GRUPOS: rango de puntajes totales e ítems "
    "consistentemente bajos. No hagas preguntas ni avances a ninguna otra tarea."
)

ITEMS_VISUALES = {6, 10}


# ══════════════════════════════════════════════════════════════════════════════
# 11. SERVICIO DE CONEXIÓN CON LLM (ESTRUCTURADO)
# ══════════════════════════════════════════════════════════════════════════════

class LLMService:
    """Enrutador principal de peticiones a la API del modelo de lenguaje, con soporte JSON estricto."""
    
    def __init__(self, config: dict):
        self.config = config

    def _llamar_modelo_local(self, messages: list, stop_sequences: list = None, json_mode: bool = False) -> str:
        url = self.config.get("url", LM_STUDIO_URL)
        model = self.config.get("model", LM_MODEL_NAME)
        timeout = self.config.get("timeout", TIMEOUT_API)

        payload_local = {
            "model": model,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "messages": messages,
        }
        if stop_sequences:
            payload_local["stop"] = stop_sequences

        try:
            response = requests.post(url, json=payload_local, timeout=timeout)
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Tiempo de espera agotado ({timeout} s) conectando al servidor local.")

        if response.status_code == 400:
            total_chars = sum(len(m.get("content", "")) for m in messages)
            total_tokens_est = total_chars // 4
            try:
                resp_data = response.json()
                if isinstance(resp_data, dict):
                    error_val = resp_data.get("error", {})
                    if isinstance(error_val, dict):
                        err_detail = error_val.get("message", "Sin detalle")
                    else:
                        err_detail = str(error_val)
                else:
                    err_detail = str(resp_data)
                raise RuntimeError(f"400 Bad Request — {err_detail} (contexto estimado: ~{total_tokens_est} tokens).")
            except (json.JSONDecodeError, KeyError, TypeError):
                err_text = response.text[:500] if response.text else "Sin detalle"
                raise RuntimeError(f"400 Bad Request — {err_text} (contexto estimado: ~{total_tokens_est} tokens).")
            
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        if isinstance(message, dict):
            content = message.get("content", "")
            if content:
                return content
            reasoning = message.get("reasoning_content", "")
            if reasoning:
                return reasoning
            return ""
        return str(message)

    def _llamar_modelo_gemini(self, messages: list, json_mode: bool = False) -> str:
        url_tpl = GEMINI_API_URL
        model_name = self.config.get("model", GEMINI_MODEL)
        api_key = self.config.get("api_key", "")
        url = url_tpl.format(model=model_name)
        timeout = self.config.get("timeout", TIMEOUT_API)
        
        contents = []
        system_text = ""
        for m in messages:
            if m["role"] == "system":    
                system_text = m["content"]
            elif m["role"] == "user":      
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant": 
                contents.append({"role": "model", "parts": [{"text": m["content"]}]})

        gen_config = {"temperature": TEMPERATURE, "maxOutputTokens": MAX_OUTPUT_TOKENS}
        if json_mode:
            gen_config["responseMimeType"] = "application/json"

        payload = {
            "contents": contents,
            "generationConfig": gen_config,
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
        }
        if system_text: 
            payload["system_instruction"] = {"parts": [{"text": system_text}]}

        try:
            response = requests.post(url, params={"key": api_key}, json=payload, timeout=timeout)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Tiempo de espera agotado ({timeout} s) conectando con la API de Google Gemini.")
            
        data = response.json()
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p["text"] for p in parts if "text" in p).strip()
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"Error procesando estructura de respuesta Gemini: {e}")

    def _llamar_openai_compatible(self, messages: list, stop_sequences: list = None, json_mode: bool = False) -> str:
        url = self.config.get("url", "")
        model = self.config.get("model", "")
        api_key = self.config.get("api_key", "")
        timeout = self.config.get("timeout", TIMEOUT_API)
        
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload_oa = {
            "model": model,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "messages": messages,
        }
        if stop_sequences:
            payload_oa["stop"] = stop_sequences

        try:
            r = requests.post(url, headers=headers, json=payload_oa, timeout=timeout)
            r.raise_for_status()
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Tiempo de espera agotado ({timeout} s) en Endpoint OpenAI-compatible.")
        message = r.json()["choices"][0]["message"]
        if isinstance(message, dict):
            content = message.get("content", "")
            if content:
                return content
            reasoning = message.get("reasoning_content", "")
            if reasoning:
                return reasoning
            return ""
        return str(message)

    def _llamar_modelo_anthropic(self, messages: list, stop_sequences: list = None) -> str:
        api_key = self.config.get("api_key", "")
        model = self.config.get("model", "claude-3-5-sonnet-latest")
        timeout = self.config.get("timeout", TIMEOUT_API)
        
        system_text = ""
        historial = []
        for m in messages:
            if m["role"] == "system": 
                system_text = m["content"]
            else: 
                historial.append({"role": m["role"], "content": m["content"]})

        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": TEMPERATURE,
            "messages": historial,
        }
        if stop_sequences:
            payload["stop_sequences"] = stop_sequences
        if system_text: 
            payload["system"] = system_text

        try:
            r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
        except requests.exceptions.Timeout:
            raise RuntimeError(f"Tiempo de espera agotado ({timeout} s) conectando con Anthropic Claude.")
            
        data = r.json()
        return data["content"][0]["text"]

    def llamar(self, messages: list, stop_sequences: list = None, json_mode: bool = False) -> str:
        """Enrutador de llamadas según configuración y modo estructural con reintentos para 429 y 5xx."""
        modo = self.config.get("modo", MODO)
        if "timeout" not in self.config:
            self.config["timeout"] = TIMEOUT_API
            
        max_intentos = 4
        backoff_base = 2.0  # Segundos base de espera
        
        for intento in range(max_intentos):
            try:
                if   modo == "gemini":        return self._llamar_modelo_gemini(messages, json_mode=json_mode)
                elif modo == "anthropic":     return self._llamar_modelo_anthropic(messages, stop_sequences=stop_sequences)
                elif modo == "openai_compat": return self._llamar_openai_compatible(messages, stop_sequences=stop_sequences, json_mode=json_mode)
                else:                         return self._llamar_modelo_local(messages, stop_sequences=stop_sequences, json_mode=json_mode)
            
            except requests.exceptions.HTTPError as e:
                codigo = e.response.status_code if e.response is not None else 0
                
                # Si es un error de cuota superada (429) o error de servidor (5xx), reintentar de forma autónoma
                if (codigo == 429 or 500 <= codigo < 600) and intento < max_intentos - 1:
                    import time
                    espera = backoff_base * (2 ** intento)
                    print(f"[REINTENTO] Código HTTP {codigo} detectado (Intento {intento + 1}/{max_intentos}). Reintentando en {espera} s...")
                    time.sleep(espera)
                    continue
                raise e
                
            except Exception as e:
                # Reintentar en caso de timeouts temporales o microcaídas de conexión de red
                if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)) and intento < max_intentos - 1:
                    import time
                    espera = backoff_base * (2 ** intento)
                    print(f"[REINTENTO] Falla de red temporal (Intento {intento + 1}/{max_intentos}). Reintentando en {espera} s...")
                    time.sleep(espera)
                    continue
                raise e


# ══════════════════════════════════════════════════════════════════════════════
# 12. PARSER Y SLICING QUIRÚRGICO DE PDF
# ══════════════════════════════════════════════════════════════════════════════

class DocumentParser:
    """Encapsulación de lectura, parseo, heurísticas y segmentación quirúrgica de documentos."""
    
    @staticmethod
    def limpiar_latex(texto: str) -> str:
        texto = re.sub(r'%.*?\n', '\n', texto)
        texto = re.sub(r'\\textbf\{([^}]+)\}', r'**\1**', texto)
        texto = re.sub(r'\\textit\{([^}]+)\}', r'_\1_', texto)
        return texto.strip()

    @staticmethod
    def pdf_a_texto(ruta: Path) -> str:
        if fitz is None:
            return "[ERROR: Librería PyMuPDF no instalada. Ejecuta: pip install pymupdf]"
        try:
            doc = fitz.open(ruta)
            texto = "".join(pagina.get_text() for pagina in doc)
            doc.close()
            return texto
        except Exception as e:
            return f"[ERROR al leer PDF: {e}]"

    @classmethod
    def leer_archivo(cls, ruta: Path) -> str:
        suf = ruta.suffix.lower()
        if suf == ".pdf":
            return cls.pdf_a_texto(ruta)
        elif suf == ".tex":
            try:
                raw = ruta.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw = ruta.read_text(encoding="latin-1")
            return cls.limpiar_latex(raw)
        
        try:
            return ruta.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ruta.read_text(encoding="latin-1")

    @staticmethod
    def extraer_cabecera_informe(texto: str) -> str:
        """
        Extrae la cabecera del informe (desde el inicio hasta justo antes del resumen).
        Esto optimiza la lectura inicial de metadatos al descartar el cuerpo principal.
        """
        patron_limite = re.compile(
            r'(?im)^(?:\d+\.?\s*)?(resumen|abstract|introduccion|introducción|introducci\'on)\b'
        )
        match = patron_limite.search(texto)
        if match:
            return texto[:match.start()].strip()
        
        lineas = texto.split('\n')
        return "\n".join(lineas[:60]).strip()

    @staticmethod
    def extraer_seccion_informe(texto: str, item_id: int) -> str:
        """
        Segmenta el informe de forma inteligente en tres grandes bloques contextuales:
        - Ítems 1 a 4: Todo lo anterior al título 'ANÁLISIS DE DATOS'.
        - Ítems 5 a 15: Todo lo que esté entre el título 'MEDICIONES' y 'CONCLUSIONES'.
        - Ítems 16 a 20: Todo lo que esté a partir del título 'CONCLUSIONES'.
        """
        if not texto:
            return ""

        texto_normalizado = re.sub(r'\r\n', '\n', texto)

        # Patrones ampliados para cubrir variantes reales de PDFs
        # Se buscan en todo el texto (no solo inicio de línea) porque algunos
        # PDFs tienen los títulos centrados sin newline antes
        patron_mediciones = re.compile(
            r'(?im)(?:\d+\.?\s*|[IVXLCDM]+\.?\s*)?'
            r'(?:mediciones|montaje\s+experimental|equipos\s+e\s+instrumentos|'
            r'materiales\s+y\s+métodos|procedimiento\s+experimental|'
            r'en\s+el\s+laboratorio|resultados\s+experimentales)\b'
        )
        patron_analisis = re.compile(
            r'(?im)(?:\d+\.?\s*|[IVXLCDM]+\.?\s*)?'
            r'(?:análisis\s+de\s+datos|analisis\s+de\s+datos|'
            r'análisis\s+y\s+resultados|analisis\s+y\s+resultados|'
            r'discusión\s+de\s+resultados|discusion\s+de\s+resultados|'
            r'análisis\s+de\s+mediciones|analisis\s+de\s+mediciones)\b'
        )
        patron_conclusiones = re.compile(
            r'(?im)(?:\d+\.?\s*|[IVXLCDM]+\.?\s*)?'
            r'(?:conclusiones|conclusión|reflexiones|reflexión\s+final|'
            r'conclusi[óo]n|observaciones\s+finales|comentarios\s+finales)\b'
        )

        pos_mediciones = None
        pos_analisis = None
        pos_conclusiones = None

        # Buscar empezando desde posición 0 en adelante, para evitar
        # que palabras sueltas en el resumen disparen falsos positivos
        for m in patron_mediciones.finditer(texto_normalizado):
            if m.start() > 50:  # ignorar coincidencias muy tempranas (encabezados de portada)
                pos_mediciones = m.start()
                break

        for m in patron_analisis.finditer(texto_normalizado):
            if m.start() > 50:
                pos_analisis = m.start()
                break

        for m in patron_conclusiones.finditer(texto_normalizado):
            if m.start() > 50:
                pos_conclusiones = m.start()
                break

        total_len = len(texto_normalizado)
        # Fallbacks: si no se encontró un título, buscar palabras de esa sección
        # en el texto para estimar su posición
        if pos_mediciones is None:
            # Buscar palabras típicas de la sección de mediciones
            m = re.search(r'(?im)\b(tabla\s+\d+|gr[áa]fica\s+\d+|medici[oó]n|dato\s+experimental)\b', texto_normalizado)
            pos_mediciones = m.start() if m else int(total_len * 0.18)

        if pos_analisis is None:
            # Buscar palabras típicas de la sección de análisis
            m = re.search(r'(?im)\b(propagaci[oó]n|incertidumbre|error\s+porcentual|ajuste\s+lineal|bondad\s+de\s+ajuste)\b', texto_normalizado)
            pos_analisis = m.start() if m else int(total_len * 0.40)

        if pos_conclusiones is None:
            m = re.search(r'(?im)\b(se\s+concluye|los\s+resultados\s+muestran|en\s+conclusi[oó]n)\b', texto_normalizado)
            pos_conclusiones = m.start() if m else int(total_len * 0.78)

        # Aplicación de reglas de partición por bloques de rúbrica
        if 1 <= item_id <= 4:
            return texto_normalizado[:pos_analisis].strip()

        elif 5 <= item_id <= 15:
            fin = pos_conclusiones if pos_conclusiones > pos_mediciones else total_len
            return texto_normalizado[pos_mediciones:fin].strip()

        elif 16 <= item_id <= 20:
            return texto_normalizado[pos_conclusiones:].strip()

        return texto_normalizado

    @staticmethod
    def _es_nombre(s: str) -> bool:
        """Valida si un string tiene estructura de nombre (2-6 palabras y no todo mayúsculas)."""
        return 2 <= len(s.split()) <= 6 and not s.isupper()

    @staticmethod
    def _es_linea_estudiante(s: str, contexto: str = "") -> bool:
        """Filtra frases de encabezado, etiquetas y texto genérico que suelen aparecer en PDFs."""
        texto = s.strip().lower()
        contexto_texto = contexto.lower()
        if not texto:
            return False

        bloque_invalido = re.search(
            r'\b(integrantes|estudiantes|alumnos|alumno|autor|autores|grupo|profesor|profesora|docente|curso|horario|laboratorio|lab|seccion|sección|nombre|apellido|fecha|experiencia|materiales|procedimiento|resultados|conclusiones|introduccion|resumen|efecto|fotoelectrico|fotoeléctrico)\b',
            texto,
            flags=re.IGNORECASE,
        )
        if bloque_invalido:
            return False

        # Evita encabezados de plantilla del PDF como "del grupo, integrantes".
        if re.search(r'\b(del|de la|de las|de los)\b.*\b(grupo|integrantes|profesor|horario|lab)\b', texto):
            return False

        if re.search(r'\b(profesor|profesora|docente|nombre del profesor|nombre del docente)\b', contexto_texto):
            return False

        # Evita líneas de sección/horario como "3-4, Jueves" que no son nombres reales.
        if re.search(r'\b\d+(?:-\d+)?\b', texto) and not re.search(r'\b\d{7,9}-?[\dkK]\b', texto):
            return False

        return True

    @staticmethod
    def extraer_estudiantes_del_pdf(texto: str) -> list[dict]:
        """Estrategia de extracción híbrida altamente robusta."""
        estudiantes = []
        
        # Estrategia 1: Bloque de integrantes típico
        patron_bloque = r"(?i)(?:integrantes?|alumnos?|autores?|nombres?|estudiantes?)\s*[:\-]?\s*(.+?)(?=Materiales|Procedimiento|Resultados|Conclusiones|Resumen|Introducción|Introduccion|$)"
        match = re.search(patron_bloque, texto, re.IGNORECASE | re.DOTALL)
        if match:
            bloque = match.group(1).strip()
            if len(bloque) > 600:
                bloque = "\n".join(bloque.split('\n')[:8])
                
            lineas_bloque = bloque.split('\n')
            for idx, linea in enumerate(lineas_bloque):
                linea_clean = linea.strip()
                contexto = ' '.join([l.strip() for l in lineas_bloque[max(0, idx-1):idx] if l.strip()])
                if not linea_clean or len(linea_clean) > 80:
                    continue
                if any(p in linea_clean.lower() for p in ['del grupo:', 'integrantes:', 'estudiantes:', 'alumno:']):
                    continue
                
                m_rol = re.search(r'\b(\d{7,9}-?[\dkK])\b', linea_clean)
                rol = m_rol.group(1) if m_rol else ""
                if not rol:
                    continue

                linea_sin_rol = re.sub(r'\(.*?\)', '', linea_clean)
                linea_sin_rol = re.sub(r'rol\s*:\s*\S*', '', linea_sin_rol, flags=re.IGNORECASE)
                linea_sin_rol = re.sub(r'\b\d{7,9}-?[\dkK]\b', '', linea_sin_rol, flags=re.IGNORECASE)
                linea_sin_rol = re.sub(r'[,;\.\s\-]+$', '', linea_sin_rol).strip()

                if not linea_sin_rol or not DocumentParser._es_linea_estudiante(linea_sin_rol, contexto):
                    continue

                if ',' in linea_sin_rol:
                    partes = linea_sin_rol.split(',', 1)
                    estudiantes.append({
                        'nombre': partes[1].strip(),
                        'apellido': partes[0].strip(),
                        'rol': rol,
                        'display': f"{partes[0].strip().upper()}, {partes[1].strip().upper()}"
                    })
                elif ' ' in linea_sin_rol:
                    partes = linea_sin_rol.split()
                    if len(partes) >= 2:
                        estudiantes.append({
                            'nombre': partes[0],
                            'apellido': ' '.join(partes[1:]),
                            'rol': rol,
                            'display': f"{' '.join(partes[1:]).upper()}, {partes[0].upper()}"
                        })
                    else:
                        estudiantes.append({
                            'nombre': linea_sin_rol,
                            'apellido': '',
                            'rol': rol,
                            'display': linea_sin_rol.upper()
                        })
            if estudiantes:
                return estudiantes

        # Estrategia 2: Escaneo línea por línea en las primeras 50 líneas (Fallback muy robusto)
        lineas = texto.split('\n')[:50]
        for idx, linea in enumerate(lineas):
            contexto = ' '.join([l for l in lineas[max(0, idx-1):idx] if l.strip()])
            linea_lower = linea.lower()
            if any(p in linea_lower for p in ['estudiante:', 'integrante:', 'alumno:', 'autor:']):
                linea_clean = re.sub(r'(?i)(?:estudiante|integrante|alumno|autor)[:\s]*', '', linea).strip()
                m_rol = re.search(r'\b(\d{7,9}-?[\dkK])\b', linea_clean)
                rol = m_rol.group(1) if m_rol else ""
                if not rol:
                    continue
                linea_clean = re.sub(r'\b\d{7,9}-?[\dkK]\b', '', linea_clean).strip()
                if not linea_clean or not DocumentParser._es_linea_estudiante(linea_clean, contexto):
                    continue

                palabras = linea_clean.split()
                if len(palabras) >= 2:
                    nombre = palabras[0]
                    apellido = ' '.join(palabras[1:])
                    estudiantes.append({
                        'nombre': nombre,
                        'apellido': apellido,
                        'rol': rol,
                        'display': f"{apellido.upper()}, {nombre.upper()}"
                    })
                elif len(palabras) == 1:
                    estudiantes.append({
                        'nombre': palabras[0],
                        'apellido': '',
                        'rol': rol,
                        'display': palabras[0].upper()
                    })
        return estudiantes

    @staticmethod
    def compactar_estado(json_str: str) -> str:
        """Toma el contenido de un JSON de sesión anterior y lo compacta para ahorrar tokens."""
        try:
            data = json.loads(json_str)
            grupo = data.get("grupo", "Grupo anterior")
            nota = data.get("nota", "??")
            criterios = data.get("criterios", data.get("evaluaciones", []))
            
            compacto = [f"=== ESTADO PREVIO GRUPO: {grupo} (Nota: {nota}) ==="]
            for c in criterios:
                cid = c.get("id")
                pts = c.get("puntaje")
                just = c.get("justificacion", "").strip()
                compacto.append(f"C{cid}:{pts}pts | Obs: {just[:150]}")
            return "\n".join(compacto)
        except Exception as e:
            return f"[Error compactando JSON: {e}]\n{json_str[:1000]}"

    @staticmethod
    def parsear_archivo_calibracion(ruta: Path) -> dict:
        """Parsea un archivo de estado de evaluación (JSON o TXT) y retorna un diccionario unificado."""
        if not ruta.exists(): 
            return None
        try:
            ext = ruta.suffix.lower()
            if ext == ".json":
                # Usar utf-8-sig para omitir el BOM de Windows
                data = json.loads(ruta.read_text(encoding="utf-8-sig"))
                criterios = data.get("criterios", data.get("evaluaciones", []))
                c_lista = criterios.values() if isinstance(criterios, dict) else criterios
                pts = data.get("total", data.get("nota", 0))
                if not pts:
                    pts = sum([float(c.get("puntaje", 0)) for c in c_lista if "puntaje" in c])
                return {
                    "grupo": data.get("grupo", ruta.stem),
                    "total": float(pts),
                    "integrantes": data.get("integrantes", ""),
                    "estudiantes": data.get("estudiantes", []),
                    "criterios": [
                        {
                            "id": int(cr.get("id")),
                            "puntaje": float(cr.get("puntaje", 0)),
                            "justificacion": cr.get("justificacion", ""),
                            "razonamiento_interno": cr.get("razonamiento_interno", "")
                        }
                        for cr in c_lista if "id" in cr
                    ]
                }
            elif ext == ".txt":
                texto = ruta.read_text(encoding="utf-8-sig", errors="replace")
                m_grupo = re.search(r'grupo_(\d+)', ruta.stem, re.I)
                grupo_id = f"grupo_{m_grupo.group(1)}" if m_grupo else ruta.stem
                
                # Buscar criterios y puntajes asignados
                criterios_match = re.findall(r'C(?:riterio)?\s*(\d+).*?(\d+(?:\.\d+)?)\s*/\s*5', texto, re.I)
                criterios_dict = {}
                pts_totales = 0.0
                for cid_str, pts, in criterios_match:
                    cid = int(cid_str)
                    if 1 <= cid <= 20:
                        pts_float = float(pts)
                        pts_totales += pts_float
                        criterios_dict[cid] = {
                            "id": cid, "puntaje": pts_float,
                            "justificacion": "", "razonamiento_interno": ""
                        }
                
                # Buscar justificaciones por criterio
                just_matches = re.findall(r'•\s*C(\d+):\s*(.*?)(?=\n•|\n\n|\n===|\Z)', texto, re.DOTALL | re.I)
                for cid_str, just in just_matches:
                    cid = int(cid_str)
                    if cid in criterios_dict:
                        criterios_dict[cid]["justificacion"] = just.strip()
                
                return {"grupo": grupo_id, "total": pts_totales, "criterios": list(criterios_dict.values())}
        except Exception as e:
            print(f"Error parseando archivo de calibración {ruta}: {e}")
        return None


def _normalizar_criterio_historial(item) -> dict | None:
    """Convierte entradas históricas de criterios a un dict seguro para lectura posterior."""
    if isinstance(item, dict):
        return item

    if isinstance(item, str):
        texto = item.strip()
        try:
            parsed = json.loads(texto)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

        m_id = re.search(r'"?id"?\s*[:=]\s*(\d+)', texto)
        m_pts = re.search(r'"?puntaje"?\s*[:=]\s*(\d+(?:\.\d+)?)', texto)
        m_just = re.search(r'"?justificacion"?\s*[:=]\s*"?(.*?)(?:"|$)', texto, re.DOTALL)
        return {
            "id": int(m_id.group(1)) if m_id else 0,
            "puntaje": float(m_pts.group(1)) if m_pts else 0,
            "justificacion": m_just.group(1).strip().replace('\\n', ' ') if m_just else "",
        }

    return None


def _parsear_json_llm(texto: str) -> dict:
    """Extrae y parsea el dict JSON de la respuesta del LLM soportando floats y LaTeX de forma segura."""
    raw = None
    texto_limpio = texto.strip()
    
    BT = "``" + "`"
    bloque = re.search(BT + r'(?:json)?\s*(\{.*?\})\s*' + BT, texto_limpio, re.DOTALL)
    if bloque:
        raw = bloque.group(1)
    else:
        start = texto_limpio.find('{')
        end   = texto_limpio.rfind('}') + 1
        if start >= 0 and end > start:
            raw = texto_limpio[start:end]

    if raw is None:
        pts_raw  = re.search(r'"?puntaje"?\s*:\s*(\d+(?:\.\d+)?)', texto_limpio, re.IGNORECASE)
        just_raw = re.search(r'"?justificacion"?\s*:\s*["\'](.+?)["\']', texto_limpio, re.DOTALL | re.IGNORECASE)
        if pts_raw:
            p_val = float(pts_raw.group(1)) if '.' in pts_raw.group(1) else int(pts_raw.group(1))
            return {"id": 0, "puntaje": p_val, "nivel": "?", "evidencia": "Generada por fallback.", "justificacion": just_raw.group(1).strip() if just_raw else "(Justificación no extraíble.)"}
        raise ValueError("No se encontró ningún objeto JSON ni puntaje en la respuesta del modelo.")

    # ESCAPE QUIRÚRGICO DE COMANDOS LATEX QUE INICIAN CON \t (ej: \theta, \times, \tan)
    raw_sanitized = re.sub(r'\\(?=[a-zA-Z])', r'\\\\', raw)

    for attempt in (raw_sanitized, re.sub(r'\\(?!["\\\\/bfnrtu])', r'\\\\', raw_sanitized), re.sub(r'(?<!\\)\n', r'\\n', raw_sanitized)):
        try:
            parsed = json.loads(attempt)
            if not isinstance(parsed, dict):
                raise ValueError(f"json.loads devolvió {type(parsed).__name__}, no dict")
            return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    id_match    = re.search(r'"id"\s*:\s*(\d+)', raw)
    pts_match   = re.search(r'"puntaje"\s*:\s*(\d+(?:\.\d+)?)', raw)
    just_match  = re.search(r'"justificacion"\s*:\s*"(.*?)"(?:\s*,|\s*\})', raw, re.DOTALL)
    
    if pts_match:
        p_val = float(pts_match.group(1)) if '.' in pts_match.group(1) else int(pts_match.group(1))
        return {
            "id": int(id_match.group(1)) if id_match else 0,
            "puntaje": p_val,
            "nivel": "?",
            "evidencia": "(Recuperado por regex)",
            "justificacion": just_match.group(1).replace('\\n', ' ').strip() if just_match else "(Justificación no extraíble.)",
        }

    raise ValueError(f"Ninguna estrategia pudo parsear la respuesta: {texto[:200]}")


def _validar_evaluacion(data: dict, default_id: int = 1) -> dict:
    """Normaliza y valida un dict de evaluacion: id 1-20, puntaje 0-5, nivel valido, campos requeridos."""
    import logging
    validado = dict(data)
    cid = validado.get("id", default_id)
    if not isinstance(cid, int) or cid < 1 or cid > 20:
        cid = default_id
    validado["id"] = cid

    pts = validado.get("puntaje", 0)
    if not isinstance(pts, (int, float)):
        pts = 0
    pts = max(0, min(5, int(pts)))
    validado["puntaje"] = pts

    nivel = str(validado.get("nivel", "AU")).upper().strip()
    if nivel not in PUNTAJES_MAP:
        for k in PUNTAJES_MAP:
            if PUNTAJES_MAP[k].get("descripcion", "").upper() == nivel:
                nivel = k
                break
        else:
            nivel = "AU"
    validado["nivel"] = nivel
    validado["puntaje"] = PUNTAJES_MAP[nivel].get("puntaje", validado["puntaje"])

    if not validado.get("evidencia"):
        validado["evidencia"] = "(No especificada)"
    if not validado.get("justificacion"):
        validado["justificacion"] = "(Sin justificación)"
    if not validado.get("razonamiento_interno"):
        validado["razonamiento_interno"] = "(Sin razonamiento interno)"
    return validado


# ══════════════════════════════════════════════════════════════════════════════
# 13. WIZARD DE CONFIGURACIÓN DEL MODELO
# ══════════════════════════════════════════════════════════════════════════════

class WizardConfig(tk.Tk):
    """Ventana de configuración inicial con autodetección y persistencia."""
    
    def __init__(self):
        super().__init__()
        self.title("Configuración — Evaluador Lab Física V3")
        self.configure(bg="#f8fafc")
        self.resizable(False, False)
        
        self.result = None
        self.config_mgr = ConfigManager()
        
        cfg_previa = self.config_mgr.cargar() or {}
        modo_guardado = cfg_previa.get("modo", "local")
        
        try: 
            ctx_guardado = int(cfg_previa.get("ctx_window", 262144))
        except (TypeError, ValueError): 
            ctx_guardado = 262144

        gemini_guardado = cfg_previa.get("gemini_model", GEMINI_MODEL)
        if gemini_guardado not in GEMINI_MODELS:
            gemini_guardado = GEMINI_MODEL

        self._modo = tk.StringVar(value=modo_guardado)
        self._url_local = tk.StringVar(value=cfg_previa.get("url_local", LM_STUDIO_URL))
        self._proveedor = tk.StringVar(value=cfg_previa.get("proveedor_id", "1"))
        self._api_key = tk.StringVar(value=cfg_previa.get("api_key", ""))
        self._api_url = tk.StringVar(value=cfg_previa.get("api_url", ""))
        self._api_model = tk.StringVar(value=cfg_previa.get("api_model", ""))
        self._gemini_mod = tk.StringVar(value=gemini_guardado)
        self._ctx_window = tk.StringVar(value=str(ctx_guardado))
        self._local_model_display = tk.StringVar(value="Presiona 'Detectar' para ver el modelo")

        self._build_ui()
        self._on_modo_change()

    def _build_ui(self):
        BG, FG, ENTRY_BG, ACCENT = "#f8fafc", "#334155", "#ffffff", "#2563eb"
        FONT, FONT_B = ("Segoe UI", 10), ("Segoe UI", 10, "bold")

        tk.Label(self, text="CORRECTOR DE LABORATORIOS — CONFIGURACIÓN", font=("Segoe UI", 11, "bold"), bg=BG, fg=ACCENT).pack(padx=18, pady=(18, 4))
        tk.Label(self, text="Selecciona el entorno del modelo de lenguaje (LLM):", font=FONT, bg=BG, fg=FG).pack(anchor="w", padx=18)

        modo_frame = tk.Frame(self, bg=BG)
        modo_frame.pack(fill="x", padx=18, pady=6)
        for val, lbl in [("local", "1. Local (LM Studio / Ollama)"), ("api", "2. API Remota")]:
            tk.Radiobutton(modo_frame, text=lbl, variable=self._modo, value=val, font=FONT_B, bg=BG, fg=FG, selectcolor=ENTRY_BG, activebackground=BG, command=self._on_modo_change).pack(anchor="w", pady=2)

        self._frame_local = tk.LabelFrame(self, text=" Entorno Local ", font=FONT, bg=BG, fg=ACCENT, bd=1, relief="groove")
        
        tk.Label(self._frame_local, text="URL del servidor LM Studio / Ollama:", font=FONT, bg=BG, fg=FG).pack(anchor="w", padx=10, pady=(8, 2))
        url_row = tk.Frame(self._frame_local, bg=BG)
        url_row.pack(padx=10, pady=(0, 4), fill="x")
        tk.Entry(url_row, textvariable=self._url_local, width=40, font=FONT, bg=ENTRY_BG, fg=FG, insertbackground=FG, relief="solid", bd=1).pack(side="left", fill="x", expand=True)
        tk.Button(url_row, text="Detectar", font=FONT, bg="#475569", fg="white", relief="flat", padx=8, cursor="hand2", command=self._detectar_modelo).pack(side="left", padx=(6, 0))
        
        tk.Label(self._frame_local, textvariable=self._local_model_display, font=("Consolas", 9, "bold"), bg=BG, fg="#16a34a").pack(anchor="w", padx=10, pady=(2, 2))

        ctx_row = tk.Frame(self._frame_local, bg=BG)
        ctx_row.pack(padx=10, pady=(8, 8), fill="x")
        tk.Label(ctx_row, text="Límite de contexto (tokens):", font=FONT, bg=BG, fg=FG).pack(side="left")
        tk.Entry(ctx_row, textvariable=self._ctx_window, width=10, font=FONT, bg="#fef3c7", fg="#92400e", relief="solid", bd=1).pack(side="left", padx=(8, 0))

        self._frame_api = tk.LabelFrame(self, text=" Entorno Remoto (API) ", font=FONT, bg=BG, fg=ACCENT, bd=1, relief="groove")
        tk.Label(self._frame_api, text="Proveedor:", font=FONT, bg=BG, fg=FG).pack(anchor="w", padx=10, pady=(8, 2))
        for k, (nombre, _) in PROVEEDORES_API.items():
            extra = "  ← API key en aistudio.google.com" if k == "1" else ""
            tk.Radiobutton(self._frame_api, text=f"{k}. {nombre}{extra}", variable=self._proveedor, value=k, font=FONT, bg=BG, fg=FG, selectcolor=ENTRY_BG, command=self._on_proveedor_change).pack(anchor="w", padx=16)

        api_fields = tk.Frame(self._frame_api, bg=BG)
        api_fields.pack(fill="x", padx=10, pady=8)
        tk.Label(api_fields, text="API Key:", font=FONT, bg=BG, fg="#64748b").grid(row=0, column=0, sticky="w")
        tk.Entry(api_fields, textvariable=self._api_key, show="●", width=44, font=FONT, bg=ENTRY_BG, fg=FG, insertbackground=FG, relief="solid", bd=1).grid(row=1, column=0, sticky="ew", pady=(0, 6))

        self._frame_gemini = tk.Frame(api_fields, bg=BG)
        tk.Label(self._frame_gemini, text="Modelo Gemini:", font=FONT, bg=BG, fg="#64748b").pack(anchor="w")
        
        om = tk.OptionMenu(self._frame_gemini, self._gemini_mod, *GEMINI_MODELS)
        om.config(font=FONT, bg=ENTRY_BG, fg=FG, relief="solid", bd=1, highlightthickness=0)
        om.pack(anchor="w")

        self._lbl_url = tk.Label(api_fields, text="URL Endpoint (Otros):", font=FONT, bg=BG, fg="#64748b")
        self._entry_url = tk.Entry(api_fields, textvariable=self._api_url, width=44, font=FONT, bg=ENTRY_BG, fg=FG, insertbackground=FG, relief="solid", bd=1)
        self._lbl_model = tk.Label(api_fields, text="Modelo:", font=FONT, bg=BG, fg="#64748b")
        self._entry_model = tk.Entry(api_fields, textvariable=self._api_model, width=44, font=FONT, bg=ENTRY_BG, fg=FG, insertbackground=FG, relief="solid", bd=1)

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=14)
        tk.Button(btn_frame, text="CONTINUAR", font=FONT_B, bg=ACCENT, fg="white", relief="flat", padx=18, pady=6, cursor="hand2", command=self._confirmar).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Cancelar", font=FONT, bg="#475569", fg="white", relief="flat", padx=12, pady=6, cursor="hand2", command=self.destroy).pack(side="left", padx=6)

    def _auto_detect(self, url):
        try:
            base = url.rstrip("/")
            if base.endswith("/v1/chat/completions"): 
                base = base[:-len("/v1/chat/completions")]
            
            ep = f"{base}/v1/models"
            r = requests.get(ep, timeout=4)
            r.raise_for_status()
            data = r.json()
            
            modelos = data.get("data", [])
            if not modelos:
                self.after(0, lambda: self._local_model_display.set("✓ Conectado — No se hallaron modelos activos"))
                return
            
            nombres = [m["id"] for m in modelos if isinstance(m, dict)]
            if nombres:
                self.after(0, lambda: self._mostrar_modelos(nombres))
            else:
                self.after(0, lambda: self._local_model_display.set("✓ Conectado — Estructura de modelos inesperada"))
            
        except requests.exceptions.ConnectionError: 
            self.after(0, lambda: self._local_model_display.set("⚠ Sin conexión — Verifica que LM Studio esté activo"))
        except Exception as e: 
            self.after(0, lambda: self._local_model_display.set(f"⚠ Error: {e}"))

    def _mostrar_modelos(self, nombres):
        mejor = self._inferir_parametros(nombres[0])
        info = f"✓ {len(nombres)} modelo(s) activo(s)"
        if mejor:
            info += f"\n  → Principal: {mejor['nombre']} ({mejor['params']})\n"
            info += f"     VRAM estimada: ~{mejor['vram_gb']}GB | Contexto sugerido: {mejor['ctx_min']:,} tokens"
        else:
            info += f"\n  → {nombres[0][:25]}..."
        self._local_model_display.set(info)

    @staticmethod
    def _inferir_parametros(nombre_modelo):
        match = re.search(r'(\d+\.?\d*)\s*[Bb](illion|B|M|m)?', nombre_modelo)
        if not match:
            match = re.search(r'(\d+\.?\d*)\s*([BM])', nombre_modelo, re.I)
        
        if match:
            valor = float(match.group(1))
            unidad = (match.group(2) or 'B').upper()
            if unidad == 'M':
                params_str = f"{valor}M parámetros"
                vram_gb = max(2, int(valor / 1000 * 2))
                ctx_min = 8192
            else:
                params_str = f"{valor}B parámetros"
                vram_gb = max(8, int(valor * 0.5))
                ctx_min = valor * 8192
            
            return {
                "nombre": nombre_modelo,
                "params": params_str,
                "vram_gb": vram_gb,
                "ctx_min": int(ctx_min)
            }
        return None

    def _detectar_modelo(self):
        url = self._url_local.get().strip()
        if not url: 
            self._local_model_display.set("⚠ URL vacía.")
            return
        self._local_model_display.set("Detectando...")
        self.update_idletasks()
        threading.Thread(target=self._auto_detect, args=(url,), daemon=True).start()

    def _on_modo_change(self):
        if self._modo.get() == "local": 
            self._frame_api.pack_forget()
            self._frame_local.pack(fill="x", padx=18, pady=6)
        else: 
            self._frame_local.pack_forget()
            self._frame_api.pack(fill="x", padx=18, pady=6)
            self._on_proveedor_change()

    def _on_proveedor_change(self):
        es_gemini = self._proveedor.get() == "1"
        es_otro = self._proveedor.get() == "4"
        if es_gemini: 
            self._frame_gemini.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        else: 
            self._frame_gemini.grid_forget()
        if es_otro:
            self._lbl_url.grid(row=3, column=0, sticky="w", pady=2)
            self._entry_url.grid(row=4, column=0, sticky="ew", pady=(0, 6))
            self._lbl_model.grid(row=5, column=0, sticky="w", pady=2)
            self._entry_model.grid(row=6, column=0, sticky="ew")
        else: 
            self._lbl_url.grid_forget()
            self._entry_url.grid_forget()
            self._lbl_model.grid_forget()
            self._entry_model.grid_forget()

    def _confirmar(self):
        modo = self._modo.get()
        cfg_to_save = {"modo": modo, "proveedor_id": self._proveedor.get()}

        if modo == "local":
            url = self._url_local.get().strip()
            if not url: 
                messagebox.showerror("Error", "Ingresa la URL local.")
                return
            try: 
                ctx = int(self._ctx_window.get().strip())
                if ctx < 1024: 
                    raise ValueError
            except ValueError: 
                messagebox.showerror("Error", "El límite de contexto debe ser un entero ≥ 1024.")
                return
            
            display_text = self._local_model_display.get()
            m = re.search(r'Principal:\s*(\S+)', display_text)
            modelo_final = m.group(1) if m else LM_MODEL_NAME
            
            self.result = {"modo": "local", "url": url, "model": modelo_final, "ctx_window": ctx}
            cfg_to_save.update({"url_local": url, "modelo_local": modelo_final, "ctx_window": ctx})
        else:
            prov = self._proveedor.get()
            key = self._api_key.get().strip()
            if not key: 
                messagebox.showerror("Error", "Ingresa la API Key.")
                return
            cfg_to_save["api_key"] = key
            
            if prov == "1": 
                mod = self._gemini_mod.get()
                self.result = {"modo": "gemini", "api_key": key, "model": mod}
                cfg_to_save["gemini_model"] = mod
            elif prov == "2": 
                self.result = {"modo": "openai_compat", "api_key": key, "url": "https://api.openai.com/v1/chat/completions", "model": "gpt-4o"}
            elif prov == "3": 
                self.result = {"modo": "anthropic", "api_key": key, "model": "claude-3-5-sonnet-latest"}
            elif prov == "4": 
                url = self._api_url.get().strip()
                mod = self._api_model.get().strip()
                if not url or not mod: 
                    messagebox.showerror("Error", "Ingresa la URL y modelo.")
                    return
                self.result = {"modo": "openai_compat", "api_key": key, "url": url, "model": mod}
                cfg_to_save.update({"api_url": url, "api_model": mod})

        self.config_mgr.guardar(cfg_to_save)
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# 13. DIÁLOGO DE GRUPO Y SELECCIÓN DE ESTUDIANTES (DIALOGOGRUPO)
# ══════════════════════════════════════════════════════════════════════════════

class DialogoGrupo(tk.Toplevel):
    """Ventana de configuración del grupo: Asignatura, día, bloque y estudiantes."""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Configuración de Curso y Grupo")
        self.configure(bg="#f8fafc")
        self.resizable(False, False)
        self.grab_set()
        
        self.result = None
        
        # Recuperar nómina persistida de la sesión activa
        self._estudiantes_cargados = list(parent.nomina_estudiantes) if hasattr(parent, 'nomina_estudiantes') else []

        BG, FG = "#f8fafc", "#334155"
        ENTRY_BG, ACCENT = "#ffffff", "#2563eb"
        FONT, FONT_B = ("Segoe UI", 10), ("Segoe UI", 10, "bold")
        pad = {"padx": 14, "pady": 4}

        tk.Label(self, text="CONFIGURACIÓN DEL GRUPO", font=("Segoe UI", 11, "bold"), bg=BG, fg=ACCENT).pack(padx=14, pady=(14, 4))

        # Asignatura
        tk.Label(self, text="Asignatura:", font=FONT_B, bg=BG, fg=FG).pack(anchor="w", **pad)
        self._asig = tk.StringVar(value="1")
        for k, v in ASIGNATURAS.items():
            tk.Radiobutton(self, text=f"{v['codigo']} — {v['nombre']}", variable=self._asig, value=k, font=FONT, bg=BG, fg=FG, selectcolor=ENTRY_BG).pack(anchor="w", padx=28)

        # Horario (Día y Bloque)
        tk.Label(self, text="Horario Día:", font=FONT_B, bg=BG, fg=FG).pack(anchor="w", **pad)
        self._dia = tk.StringVar(value="1")
        dia_f = tk.Frame(self, bg=BG)
        dia_f.pack(anchor="w", padx=28)
        for k, v in DIAS.items():
            tk.Radiobutton(dia_f, text=v["nombre"], variable=self._dia, value=k, font=FONT, bg=BG, fg=FG, selectcolor=ENTRY_BG).pack(side="left", padx=4)

        tk.Label(self, text="Bloque horario:", font=FONT_B, bg=BG, fg=FG).pack(anchor="w", **pad)
        self._bloque = tk.StringVar(value="1")
        blo_f = tk.Frame(self, bg=BG)
        blo_f.pack(anchor="w", padx=28)
        for k, v in BLOQUES.items():
            tk.Radiobutton(blo_f, text=v["display"], variable=self._bloque, value=k, font=("Segoe UI", 9), bg=BG, fg=FG, selectcolor=ENTRY_BG).pack(side="left", padx=3)

        # Número de experiencia
        tk.Label(self, text="Número de experiencia:", font=FONT_B, bg=BG, fg=FG).pack(anchor="w", **pad)
        self._exp = tk.StringVar()
        tk.Entry(self, textvariable=self._exp, width=6, font=FONT, bg=ENTRY_BG, fg=FG, relief="solid", bd=1).pack(anchor="w", padx=28)

        # Cargar estudiantes desde CSV utilizando ruta persistida en caliente
        tk.Label(self, text="CSV de Estudiantes (Nombre;Apellidos;Rol):", font=FONT_B, bg=BG, fg=FG).pack(anchor="w", **pad)
        ruta_previa = parent.ruta_csv_nomina if hasattr(parent, 'ruta_csv_nomina') else ""
        self._csv_path = tk.StringVar(value=ruta_previa)
        csv_row = tk.Frame(self, bg=BG)
        csv_row.pack(fill="x", padx=28)
        tk.Entry(csv_row, textvariable=self._csv_path, width=36, font=("Segoe UI", 9), bg=ENTRY_BG, fg=FG, relief="solid", bd=1).pack(side="left", fill="x", expand=True)
        tk.Button(csv_row, text="...", font=FONT, bg=ACCENT, fg="white", relief="flat", command=self._sel_csv).pack(side="right", padx=(4, 0))

        # Lista de estudiantes
        tk.Label(self, text="Selecciona integrantes del grupo (Ctrl+Clic múltiple):", font=FONT, bg=BG, fg=FG).pack(anchor="w", **pad)
        self._listbox = tk.Listbox(self, selectmode="multiple", font=("Segoe UI", 9), bg=ENTRY_BG, fg=FG, selectbackground=ACCENT, height=8, relief="solid", bd=1)
        self._listbox.pack(fill="x", padx=28, pady=(0, 6))

        # Poblar listbox si ya existen estudiantes cargados en caliente
        for est in self._estudiantes_cargados:
            self._listbox.insert("end", f"{est['apellido']}, {est['nombre']} ({est['rol']})")

        tk.Button(self, text="Cargar CSV del Curso", font=FONT, bg="#475569", fg="white", relief="flat", command=self._cargar_csv).pack(anchor="w", padx=28, pady=4)

        # Botones de confirmación
        btn_f = tk.Frame(self, bg=BG)
        btn_f.pack(pady=12)
        tk.Button(btn_f, text="ACEPTAR", font=FONT_B, bg=ACCENT, fg="white", relief="flat", padx=16, pady=6, cursor="hand2", command=self._aceptar).pack(side="left", padx=6)
        tk.Button(btn_f, text="Cancelar", font=FONT, bg="#475569", fg="white", relief="flat", padx=10, pady=6, cursor="hand2", command=self.destroy).pack(side="left", padx=6)

    def _sel_csv(self):
        ruta = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("Todos", "*.*")])
        if ruta:
            self._csv_path.set(ruta)
            self._cargar_csv()

    def _cargar_csv(self):
        ruta = self._csv_path.get().strip()
        if not ruta or not Path(ruta).exists():
            asig_cod = ASIGNATURAS[self._asig.get()]["codigo"]
            dia_cod = DIAS[self._dia.get()]["cod"]
            blo_cod = BLOQUES[self._bloque.get()]["suf"]
            nombre_auto = f"{asig_cod}_{dia_cod}-{blo_cod}.csv"
            
            ruta_auto = Path(nombre_auto)
            if ruta_auto.exists():
                ruta = str(ruta_auto)
                self._csv_path.set(ruta)
            else:
                messagebox.showerror("Error", f"No se encontró el archivo CSV de estudiantes.\n"
                                              f"Se buscó manualmente y de forma automática como '{nombre_auto}'")
                return

        try:
            # Leer primeros 1024 bytes en crudo para detectar codificación de forma infalible (BOM / Null bytes)
            with open(ruta, "rb") as f_raw:
                raw_bytes = f_raw.read(1024)
            
            if raw_bytes.startswith(b"\xff\xfe") or raw_bytes.startswith(b"\xfe\xff"):
                encoding_usado = "utf-16"
            elif raw_bytes.startswith(b"\xef\xbb\xbf"):
                encoding_usado = "utf-8-sig"
            elif b"\x00" in raw_bytes:
                encoding_usado = "utf-16"
            else:
                try:
                    raw_bytes.decode("utf-8")
                    encoding_usado = "utf-8"
                except UnicodeDecodeError:
                    encoding_usado = "latin-1"
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo determinar la codificación del archivo: {e}")
            return

        try:
            with open(ruta, encoding=encoding_usado) as f:
                # Sanitizar cabecera removiendo BOMs residuales o caracteres nulos invisibles
                cabecera_linea = f.readline().replace('\ufeff', '').replace('\x00', '').strip()
                if not cabecera_linea:
                    messagebox.showerror("Error CSV", "Archivo vacío.")
                    return
                
                delim = ";" if ";" in cabecera_linea else ","
                cabeceras = [col.strip().lower() for col in cabecera_linea.split(delim)]
                
                col_indices = {'nombre': None, 'apellido': None, 'rol': None}
                for idx_c, cab in enumerate(cabeceras):
                    if 'nombre' in cab or 'estudiante' in cab:
                        col_indices['nombre'] = idx_c
                    elif 'apellido' in cab or 'alumno' in cab:
                        col_indices['apellido'] = idx_c
                    elif 'rol' in cab or 'usm' in cab or 'id' in cab:
                        col_indices['rol'] = idx_c

                if col_indices['nombre'] is None or col_indices['apellido'] is None:
                    messagebox.showerror("Error CSV", "El CSV debe contener cabeceras de 'Nombre' y 'Apellido'.")
                    return

                f.seek(0)
                lector = csv.reader(f, delimiter=delim)
                next(lector)
                
                self._estudiantes_cargados = []
                self._listbox.delete(0, "end")
                
                for idx_i, fila in enumerate(lector):
                    if len(fila) <= max(col_indices['nombre'], col_indices['apellido']):
                        continue
                    
                    nombre = fila[col_indices['nombre']].strip()
                    apellido = fila[col_indices['apellido']].strip()
                    if not nombre or not apellido:
                        continue
                    
                    rol = ""
                    if col_indices['rol'] is not None and col_indices['rol'] < len(fila):
                        rol = fila[col_indices['rol']].strip()

                    self._estudiantes_cargados.append({
                        "id": idx_i + 1,
                        "nombre": nombre,
                        "apellido": apellido,
                        "apellidos": apellido,
                        "display": f"{apellido}, {nombre}",
                        "rol": rol
                    })
                    self._listbox.insert("end", f"{apellido}, {nombre} ({rol})")
            
            messagebox.showinfo("Carga Lista", f"Se cargaron {len(self._estudiantes_cargados)} estudiantes.")
        except Exception as e:
            messagebox.showerror("Error CSV", f"No se pudo leer el CSV: {e}")

    def _aceptar(self):
        exp_str = self._exp.get().strip()
        if not exp_str.isdigit():
            messagebox.showerror("Error", "El número de experiencia debe ser un entero.")
            return
        
        sel_idx = self._listbox.curselection()
        if not sel_idx:
            if messagebox.askyesno("Sin Selección", "¿Deseas continuar sin seleccionar del listado?\n(Se intentará extraer automáticamente del PDF del informe)"):
                sel_ests = []
            else:
                return
        else:
            sel_ests = [self._estudiantes_cargados[i] for i in sel_idx]

        asig_info = ASIGNATURAS[self._asig.get()]
        dia_info = DIAS[self._dia.get()]
        bloque_info = BLOQUES[self._bloque.get()]

        # Persistir nexos en la sesión del padre para evaluaciones sucesivas
        if hasattr(self, 'parent') and self.parent:
            self.parent.nomina_estudiantes = self._estudiantes_cargados
            self.parent.ruta_csv_nomina = self._csv_path.get().strip()

        self.result = {
            "asig": asig_info,
            "dia": dia_info,
            "bloque": bloque_info,
            "exp": f"Lab{int(exp_str)}",
            "estudiantes": sel_ests
        }
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# 14. APLICACIÓN PRINCIPAL (EVALUADORAPP)
# ══════════════════════════════════════════════════════════════════════════════

class EvaluadorApp(tk.Tk):
    """Ventana principal del evaluador de laboratorios con IA con soporte quirúrgico de PDF."""
    
    BG = "#f8fafc"
    PANEL_BG = "#ffffff"
    ACCENT = "#2563eb"
    FG = "#334155"
    MUTED = "#64748b"
    ENTRY_BG = "#ffffff"

    def __init__(self, llm_service: LLMService, parser: type[DocumentParser]):
        self.llm = llm_service
        self.parser = parser
        super().__init__()
        
        self.title("Evaluador de Informes de Laboratorio de Física V7 (Refactored)")
        self.geometry("1100x820")
        self.configure(bg=self.BG)
        self.resizable(True, True)

        self.messages = []
        self.contexto_base = []
        self.chat_history = []
        self.archivos = {}
        self.sesion_activa = False
        self.info_grupo = None
        self.resultados_criterios = []
        self.evaluaciones_historicas = []
        self.reporte_listo = False
        self.comentarios_adicionales = ""
        self.historial_matriz = {}
        self._texto_reporte_corregido = None
        self.visual_esquemas = ""
        self.visual_graficos = ""
        self.visual_tablas = ""
        self._restricciones_evaluador: list[str] = []
        self.cancelar_evaluacion = False
        self.cancelar_todo = False
        self.fase_reevaluacion_activa = False
        self.indice_auditoria = {}
        
        # Persistencia en caliente de nómina para evaluaciones consecutivas
        self.nomina_estudiantes = []
        self.ruta_csv_nomina = ""

        self.ruta_guia = tk.StringVar()
        self.ruta_informe = tk.StringVar()
        self.ruta_prev_grupo = []
        self.ruta_otros = []
        self.compactar_estados = tk.BooleanVar(value=True)

        self._build_ui()
        self._cargar_indice_auditoria()
        self._log("Configuración inicial del modelo establecida con éxito.", "sistema")

    def _build_ui(self):
        BG, PB, FG, ACCENT, MU, EB = self.BG, self.PANEL_BG, self.FG, self.ACCENT, self.MUTED, "#ffffff"
        FONT, FONT_B = ("Segoe UI", 10), ("Segoe UI", 10, "bold")

        main = tk.Frame(self, bg=BG)
        main.pack(fill="both", expand=True, padx=12, pady=12)

        # Panel Izquierdo (con scroll)
        left_container = tk.Frame(main, bg=PB, width=340)
        left_container.pack(side="left", fill="y", padx=(0, 8))
        left_container.pack_propagate(False)

        left_canvas = tk.Canvas(left_container, bg=PB, width=340, highlightthickness=0)
        left_scrollbar = tk.Scrollbar(left_container, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scrollbar.set)

        left_scrollbar.pack(side="right", fill="y")
        left_canvas.pack(side="left", fill="both", expand=True)

        left = tk.Frame(left_canvas, bg=PB, padx=12, pady=12)
        left_canvas.create_window((0, 0), window=left, anchor="nw", width=318)

        def _configurar_scroll_region(event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
        left.bind("<Configure>", _configurar_scroll_region)

        def _on_mousewheel(event):
            left_canvas.yview_scroll(-1 * (event.delta // 120), "units")
        left_canvas.bind("<Enter>", lambda e: left_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        left_canvas.bind("<Leave>", lambda e: left_canvas.unbind_all("<MouseWheel>"))

        tk.Label(left, text="EVALUACIÓN Y CONFIGURACIÓN", font=FONT_B, bg=PB, fg=ACCENT).pack(anchor="w", pady=(0, 6))

        lbl_grupo_frame = tk.Frame(left, bg=PB, height=52, relief="flat")
        lbl_grupo_frame.pack(fill="x", pady=(0, 4))
        lbl_grupo_frame.pack_propagate(False)
        self.lbl_grupo = tk.Label(lbl_grupo_frame, text="Grupo: (no configurado)", font=("Segoe UI", 9), bg=PB, fg=FG, wraplength=290, justify="left", anchor="nw")
        self.lbl_grupo.pack(fill="both", expand=True)
        
        tk.Button(left, text="⚙  CONFIGURAR GRUPO Y CURSO", font=FONT, bg="#475569", fg="white", relief="flat", cursor="hand2", command=self._configurar_grupo).pack(fill="x", pady=(0, 10))

        archivos_cfg = [
            ("📋 Guía de la experiencia (PDF/TXT):", self.ruta_guia, [("Archivos de texto o PDF", "*.pdf *.txt"), ("Todos", "*.*")]),
            ("📄 Informe del grupo (PDF):", self.ruta_informe, [("Archivos PDF", "*.pdf"), ("Todos", "*.*")]),
        ]
        for lbl, var, ftypes in archivos_cfg:
            self._file_picker(left, lbl, var, ftypes)

        self._add_correcciones_ui(left, "📁 Correcciones previas — mismo grupo (opcional):", self.ruta_prev_grupo, "lb_prev_grupo")
        self._add_correcciones_ui(left, "📁 Correcciones otros grupos (opcional):", self.ruta_otros, "lb_otros")

        tk.Checkbutton(left, text="Compactar estados previos", variable=self.compactar_estados, font=("Segoe UI", 8), bg=PB, fg=MU, activebackground=PB, selectcolor=EB).pack(anchor="w", pady=(8, 8))

        # Selector de Modelo Dinámico
        tk.Label(left, text="🤖 Modelo actual:", bg=PB, fg=FG, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(5, 0))
        self.var_modelo = tk.StringVar(value=self.llm.config.get("model", GEMINI_MODEL))
        
        modo_actual = self.llm.config.get("modo")
        if modo_actual == "gemini":
            opciones_modelos = GEMINI_MODELS
        elif modo_actual == "local":
            opciones_modelos = [self.llm.config.get("model", LM_MODEL_NAME)]
        else:
            opciones_modelos = [self.llm.config.get("model", "Default")]
            
        self.menu_modelo = tk.OptionMenu(left, self.var_modelo, *opciones_modelos, command=self._cambiar_modelo)
        self.menu_modelo.config(bg=EB, fg=FG, font=("Segoe UI", 9), relief="solid", bd=1)
        self.menu_modelo.pack(fill="x", pady=(2, 5))

        # Timeout ajustable en caliente
        timeout_row = tk.Frame(left, bg=PB)
        timeout_row.pack(fill="x", pady=(2, 8))
        tk.Label(timeout_row, text="⏱ Timeout (s):", bg=PB, fg=FG, font=("Segoe UI", 9)).pack(side="left")
        self._timeout_val = tk.StringVar(value=str(self.llm.config.get("timeout", TIMEOUT_API)))
        tk.Spinbox(
            timeout_row, from_=10, to=600, increment=10,
            textvariable=self._timeout_val, width=6, font=("Segoe UI", 9),
            bg="#fef3c7", fg="#92400e", relief="solid", bd=1
        ).pack(side="left", padx=(6, 4))
        tk.Button(
            timeout_row, text="Aplicar", font=("Segoe UI", 8, "bold"), bg="#475569", fg="white",
            relief="flat", cursor="hand2", command=self._aplicar_timeout
        ).pack(side="left")

        btn_cfg = dict(font=FONT_B, relief="flat", cursor="hand2", pady=6)
        
        self.btn_verificar = tk.Button(left, text="▶  VERIFICAR CONTEXTO", bg=ACCENT, fg="white", command=self._verificar_contexto, **btn_cfg)
        self.btn_verificar.pack(fill="x", pady=3)

        self.btn_evaluar = tk.Button(left, text="⚡ EVALUAR CRITERIOS", bg="#10b981", fg="white", state="disabled", command=self._iniciar_evaluacion, **btn_cfg)
        self.btn_evaluar.pack(fill="x", pady=3)
        
        self.btn_detener = tk.Button(left, text="🛑 DETENER PROCESO", bg="#dc2626", fg="white", state="disabled", command=self._cancelar_todo, **btn_cfg)
        self.btn_detener.pack(fill="x", pady=3)
        
        self.btn_generar_reporte = tk.Button(left, text="💾 GENERAR REPORTE FINAL", bg="#6366f1", fg="white", state="disabled", command=self._generar_reporte, **btn_cfg)
        self.btn_generar_reporte.pack(fill="x", pady=3)

        self.btn_consolidado = tk.Button(left, text="📊 ACTA Y CONSOLIDADO", bg="#64748b", fg="white", state="normal", command=self._generar_acta_consolidada, **btn_cfg)
        self.btn_consolidado.pack(fill="x", pady=3)

        self.btn_auditar = tk.Button(left, text="🔍 AUDITAR CONSISTENCIA", bg="#f59e0b", fg="white", command=self._auditar_inconsistencias_retroactivas, **btn_cfg)
        self.btn_auditar.pack(fill="x", pady=3)

        self.btn_nueva = tk.Button(left, text="↺  NUEVA EVALUACIÓN", bg="#dc2626", fg="white", command=self._nueva_evaluacion, **btn_cfg)
        self.btn_nueva.pack(fill="x", pady=3)

        self.lbl_status = tk.Label(left, text="", font=("Segoe UI", 8), bg=PB, fg=MU, wraplength=300, justify="left")
        self.lbl_status.pack(anchor="w", pady=(10, 0))

        # Panel Derecho
        right = tk.Frame(main, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text="CONVERSACIÓN Y EDICIÓN CON EL EVALUADOR IA", font=FONT_B, bg=BG, fg=MU).pack(anchor="w")
        
        self.chat_area = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, state="disabled", font=("Consolas", 10), bg="#ffffff", fg="#1e293b",
            relief="solid", bd=1, padx=12, pady=12, spacing3=4
        )
        self.chat_area.pack(fill="both", expand=True, pady=(4, 6))

        self.chat_area.tag_config("usuario", foreground="#1d4ed8", font=("Consolas", 10, "bold"))
        self.chat_area.tag_config("modelo",  foreground="#0f766e")
        self.chat_area.tag_config("sistema", foreground=MU, font=("Consolas", 9, "bold"))
        self.chat_area.tag_config("sync",    foreground="#b45309", font=("Consolas", 10, "bold"))
        self.chat_area.tag_config("reporte", foreground="#6d28d9", font=("Consolas", 10, "bold"))
        self.chat_area.tag_config("error",   foreground="#be123c")
        self.chat_area.tag_config("aviso",   foreground="#b45309")

        self.progress = ttk.Progressbar(right, mode="indeterminate")

        in_f = tk.Frame(right, bg=BG)
        in_f.pack(fill="x")
        self.entry = tk.Text(in_f, font=("Segoe UI", 10), bg="#ffffff", fg="#334155", relief="solid", bd=1, height=4, wrap=tk.WORD)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<KP_Enter>", self._on_enter)
        self.entry.bind("<Shift-Return>", self._on_shift_enter)

        self.btn_enviar = tk.Button(in_f, text="ENVIAR", font=FONT_B, bg=ACCENT, fg="white", relief="flat", cursor="hand2", pady=6, padx=14, command=self._enviar_mensaje, state="disabled")
        self.btn_enviar.pack(side="right")

    def _file_picker(self, parent, label, var, tipos, multiple=False):
        MU, HL = self.MUTED, self.ACCENT
        tk.Label(parent, text=label, font=("Segoe UI", 9), bg=self.PANEL_BG, fg=MU).pack(anchor="w", pady=(6, 0))
        row = tk.Frame(parent, bg=self.PANEL_BG)
        row.pack(fill="x", pady=(2, 0))
        tk.Entry(row, textvariable=var, font=("Segoe UI", 8), bg=self.ENTRY_BG, fg=self.FG, relief="solid", bd=1).pack(side="left", fill="x", expand=True)
        tk.Button(row, text="...", font=("Segoe UI", 8), bg=HL, fg="white", relief="flat", cursor="hand2", command=lambda v=var, t=tipos, m=multiple: self._seleccionar_archivo(v, t, m)).pack(side="right", padx=(4, 0))

    def _seleccionar_archivo(self, var, tipos, multiple=False):
        if multiple:
            rutas = filedialog.askopenfilenames(filetypes=tipos)
            if rutas:
                var.set(" ; ".join(rutas))
        else:
            ruta = filedialog.askopenfilename(filetypes=tipos)
            if ruta:
                var.set(ruta)

    def _on_enter(self, event=None):
        if self.btn_enviar.cget('state') == 'normal':
            self._enviar_mensaje()

    def _on_shift_enter(self, event=None):
        pass

    def _enviar_mensaje(self):
        texto = self.entry.get("1.0", tk.END).strip()
        if not texto:
            return
        
        if not self.sesion_activa:
            self._log(f"\nTú: {texto}", "usuario")
            self._log("⚠ No hay sesión activa. Carga archivos primero.", "aviso")
            self.entry.delete("1.0", tk.END)
            return
        
        self.entry.delete("1.0", tk.END)
        self._log(f"\nTú: {texto}", "usuario")
        self.messages.append({"role": "user", "content": texto})
        self.chat_history.append({"role": "user", "content": texto})

        # INTERCEPTOR DE EDICIÓN DIRECTA DE COMENTARIOS ADICIONALES
        import re as _re_edicion
        m_edicion = _re_edicion.search(
            r'^(?:cambia\s+los\s+comentarios\s+adicionales\s+por\s+esto|'
            r'cambia\s+los\s+comentarios\s+por\s+esto|'
            r'cambia\s+el\s+comentario\s+por\s+esto|'
            r'cámbiala\s+por\s+esto)[:\-]?\s*(.*)',
            texto.strip(),
            _re_edicion.IGNORECASE | _re_edicion.DOTALL
        )
        if m_edicion:
            nuevo_comentario = m_edicion.group(1).strip()
            if nuevo_comentario:
                self.comentarios_adicionales = nuevo_comentario
                self._log("✓ Comentarios adicionales actualizados instantáneamente. Abriendo reporte...", "sync")
                self._preguntar_aprobacion_puntajes()
            return
        
        if not self.resultados_criterios:
            self._restricciones_evaluador.append(texto)
            self._log("✓ Observación registrada. Inicia la evaluación para aplicarla.", "sistema")
            return

        # COMANDO MANUAL PARA APRECIAR EL REPORTE SIN FINALIZAR LA SESIÓN
        if texto.strip().upper() == "REPORTE" and self.resultados_criterios:
            self._preguntar_aprobacion_puntajes()
            return

        if texto.strip().upper() == "REESCRIBE COMENTARIO" and self.resultados_criterios:
            self._regenerar_comentario_final()
            return

        if texto.strip().upper() == "LISTO" and self.resultados_criterios:
            self._log("✅ Finalizando evaluación y procesando reporte...", "sistema")
            self._corregir_ortografia_justificaciones()
            return

        ids_a_reevaluar = self._extraer_ids_a_reevaluar(texto)

        ejecutar_reevaluacion = False

        if ids_a_reevaluar and self.resultados_criterios:
            if not self.fase_reevaluacion_activa:
                respuesta = messagebox.askyesno(
                    "Confirmar Reevaluación",
                    f"Se ha detectado una referencia a los ítems {ids_a_reevaluar}. "
                    "¿Desea proceder con la reevaluación activa de estos criterios?"
                )
                if respuesta:
                    self.fase_reevaluacion_activa = True
                    ejecutar_reevaluacion = True
                else:
                    ids_a_reevaluar = []
                    ejecutar_reevaluacion = False
            else:
                ejecutar_reevaluacion = True

            if _PATRON_RESTRICCION_EVAL.search(texto):
                self._restricciones_evaluador.append(texto.strip())
                self._restricciones_evaluador = self._restricciones_evaluador[-8:]

            if ejecutar_reevaluacion and ids_a_reevaluar:
                if self._intentar_edicion_directa(texto, ids_a_reevaluar):
                    self._mostrar_progreso(False)
                    self.btn_enviar.configure(state="normal")
                    return

                self._log(f"↺ Detectados ítems a re-evaluar: {ids_a_reevaluar}", "sistema")
                self._mostrar_progreso(True)
                self.btn_enviar.configure(state="disabled")

                def worker_reeval():
                    nuevos = []
                    errores = []
                    cancelacion_solicitada = False
                    for item_id in ids_a_reevaluar:
                        if self.cancelar_todo or self.cancelar_evaluacion:
                            cancelacion_solicitada = True
                            break
                        item = RUBRICA_ITEMS_BASE[item_id - 1]
                        informe_completo = self.archivos.get("informe", "")
                        # Se segmenta el informe de forma inteligente pasando el ID numérico del ítem
                        informe_seccion = self.parser.extraer_seccion_informe(informe_completo, item_id)
                        resultado_actual = next(
                            (c for c in self.resultados_criterios if c["id"] == item_id),
                            {"puntaje": "?", "nivel": "?"}
                        )

                        prompt = (
                            f"=== SECCIÓN EN EVALUACIÓN: {item['seccion']} ===\n"
                            f"{informe_seccion}\n\n"
                            f"CRITERIO id={item_id} | {item['seccion']}\n"
                            f"Descripción: {item['desc']}\n\n"
                            f"Puntaje actual: {resultado_actual.get('puntaje', '?')}/5 "
                            f"({resultado_actual.get('nivel', '?')})\n\n"
                            f"=== OBSERVACIÓN VINCULANTE DEL DOCENTE ===\n"
                            f"{texto}\n"
                            f"INSTRUCCIÓN: Aplica esta observación de forma OBLIGATORIA. "
                            f"Si el docente señala que algo SÍ está presente, súbelo. "
                            f"Si señala que algo falta o está mal, bájalo.\n\n"
                            f"Responde SOLO con este JSON:\n"
                            f"{{\n"
                            f'  "id": {item_id},\n'
                            f'  "puntaje": <0-5>,\n'
                            f'  "nivel": "<E|B|A|D|MD|AU>",\n'
                            f'  "evidencia": "<cita breve del fragmento>",\n'
                            f'  "justificacion": "<explica qué está presente y qué falta, sin lenguaje punitivo>"\n'
                            f"}}"
                        )

                        msgs = self.contexto_base + [{"role": "user", "content": prompt}]
                        try:
                            resp = self.llm.llamar(msgs, json_mode=True)
                            data = _parsear_json_llm(resp)
                            if isinstance(data, dict):
                                nuevos.append(_validar_evaluacion(data, item_id))
                        except Exception as e:
                            errores.append(f"Ítem {item_id}: {e}")

                    if cancelacion_solicitada:
                        self.after(0, lambda: self._mostrar_progreso(False))
                        self.after(0, lambda: self.btn_enviar.configure(state="normal"))
                        self.after(0, lambda: self._restaurar_botones_post_evaluacion())
                        self.after(0, lambda: setattr(self, 'fase_reevaluacion_activa', False))
                        self.after(0, lambda: self._log("🛑 Proceso de reevaluación cancelado por el usuario.", "aviso"))
                        return
                    self.after(0, lambda t=texto, n=nuevos, e=errores: self._mostrar_resultado_reeval(t, n, e))

                threading.Thread(target=worker_reeval, daemon=True).start()
                return

        # Si no se activó reevaluación, desviar al chat normal
        if _PATRON_RESTRICCION_EVAL.search(texto):
            self._restricciones_evaluador.append(texto.strip())
            self._restricciones_evaluador = self._restricciones_evaluador[-8:]

        self._mostrar_progreso(True)
        self.btn_enviar.configure(state="disabled")
        def worker_chat():
            try:
                mensajes_llamada = list(self.messages)
                resp = self.llm.llamar(mensajes_llamada)
                self.messages.append({"role": "assistant", "content": resp})
                self.chat_history.append({"role": "assistant", "content": resp})
                self.after(0, lambda r=resp: self._mostrar_respuesta_chat(r))
            except Exception as e:
                self.after(0, lambda err=e: self._log(f"✗ Error: {err}", "error"))
                self.after(0, lambda: self._mostrar_progreso(False))
                self.after(0, lambda: self.btn_enviar.configure(state="normal"))
        threading.Thread(target=worker_chat, daemon=True).start()

    def _mostrar_resultado_reeval(self, texto: str, nuevos: list, errores: list):
        self.fase_reevaluacion_activa = False
        if errores:
            for err in errores:
                self._log(f"✗ Error re-evaluando {err}", "error")
        if nuevos:
            self._aplicar_reevaluacion(nuevos)
        else:
            self._log("⚠ No se pudo obtener ninguna reevaluación del modelo.", "aviso")
            self.btn_enviar.configure(state="normal")
            self._mostrar_progreso(False)

    def _extraer_ids_a_reevaluar(self, texto: str) -> list[int]:
        """
        Extrae IDs de criterios a reevaluar. Prioriza coincidencias explícitas
        para evitar falsos positivos y utiliza coincidencia de palabras completas
        en el análisis heurístico secundario.
        """
        ids = []
        texto_lower = texto.lower()

        # 1. Coincidencias explícitas (ej: "item 11", "criterio 11", "#11")
        for m in _PATRON_CORRECCION.finditer(texto):
            try:
                cid = int(m.group(2))
                if 1 <= cid <= 20 and cid not in ids:
                    ids.append(cid)
            except ValueError:
                pass

        # Si ya se identificaron IDs explícitos, se retornan inmediatamente
        # para evitar falsos positivos de la búsqueda heurística por palabras clave.
        if ids:
            return ids

        # 2. Heurística por fragmentos exactos entre comillas
        fragmentos = re.findall(r"""["'']([^"'']{8,80})["'']""", texto)
        for frag in fragmentos:
            frag_lower = frag.lower()
            mejor_id, mejor_score = None, 0
            for item in RUBRICA_ITEMS_BASE:
                palabras_item = [p for p in item["desc"].lower().split() if len(p) > 4]
                score = sum(1 for p in palabras_item if p in frag_lower)
                if score > mejor_score:
                    mejor_score, mejor_id = score, item["id"]
            if mejor_id and mejor_score >= 2 and mejor_id not in ids:
                ids.append(mejor_id)

        if ids:
            return ids

        # 3. Heurística por palabras clave (coincidencia de palabra completa, NO subcadenas)
        # Esto evita que 'indicador' active 'indica' o 'declaración' active 'declara'.
        palabras_texto = set(re.findall(r'\b\w+\b', unidecode.unidecode(texto_lower)))
        for item in RUBRICA_ITEMS_BASE:
            desc_norm = unidecode.unidecode(item["desc"].lower())
            palabras_criterio = [p for p in desc_norm.split() if len(p) > 4][:4]
            # Solo si coinciden al menos 2 palabras exactas y completas
            if sum(1 for p in palabras_criterio if p in palabras_texto) >= 2:
                if item["id"] not in ids:
                    ids.append(item["id"])

        return ids

    def _intentar_edicion_directa(self, texto: str, ids: list[int]) -> bool:
        m = _PATRON_ELIMINAR_FRASE.search(texto)
        if not m or len(ids) != 1:
            return False
        frase = m.group("frase").strip()
        cid = ids[0]
        for c in self.resultados_criterios:
            if c["id"] == cid:
                just_orig = c.get("justificacion", "")
                just_nueva = just_orig.replace(frase, "").strip()
                just_nueva = re.sub(r'\s{2,}', ' ', just_nueva)
                just_nueva = re.sub(r'\.\s*\.', '.', just_nueva)
                if just_nueva != just_orig:
                    c["justificacion"] = just_nueva
                    self._log(
                        f"✂ Frase eliminada directamente del ítem {cid} (sin re-evaluación).\n"
                        f"   Justificación actualizada: {just_nueva}",
                        "sync"
                    )
                    total_p, max_p, calif = self._recalcular_puntajes()
                    self._log(f"   Calificación recalculada: {calif}/100", "sync")
                    self.after(600, self._preguntar_aprobacion_puntajes)
                    return True
        return False

    def _add_correcciones_ui(self, parent, label, lista_rutas, lb_attr_name):
        MU, HL, EB = self.MUTED, self.ACCENT, "#ffffff"
        tk.Label(parent, text=label, font=("Segoe UI", 9), bg=self.PANEL_BG, fg=MU).pack(anchor="w", pady=(6, 0))
        frame = tk.Frame(parent, bg=self.PANEL_BG)
        frame.pack(fill="x", pady=(2, 0))
        lb = tk.Listbox(frame, font=("Segoe UI", 8), bg=self.ENTRY_BG, fg=self.FG, selectbackground=HL, height=3, relief="solid", bd=1)
        lb.pack(side="left", fill="x", expand=True)
        setattr(self, lb_attr_name, lb)
        btn_frame = tk.Frame(parent, bg=self.PANEL_BG)
        btn_frame.pack(fill="x", pady=(2, 0))
        
        add_cmd = lambda l=lista_rutas, lb_widget=lb: self._agregar_correcciones(l, lb_widget)
        rem_cmd = lambda lb_widget=lb, l=lista_rutas: self._quitar_correcciones(lb_widget, l)
        
        tk.Button(btn_frame, text="+ Agregar", font=("Segoe UI", 8), bg=HL, fg="white", relief="flat", cursor="hand2", command=add_cmd).pack(side="left", padx=(0, 4))
        tk.Button(btn_frame, text="✕ Quitar sel.", font=("Segoe UI", 8), bg="#475569", fg="white", relief="flat", cursor="hand2", command=rem_cmd).pack(side="left")

    def _agregar_correcciones(self, lista_rutas, lb):
        rutas = filedialog.askopenfilenames(
            title="Seleccionar reportes de corrección",
            filetypes=[("Archivos de reporte", "*.txt *.pdf *.json"), ("Todos", "*.*")]
        )
        for r in rutas:
            if r and r not in lista_rutas:
                lista_rutas.append(r)
                lb.insert("end", Path(r).name)

    def _quitar_correcciones(self, lb, lista_rutas):
        for i in reversed(list(lb.curselection())):
            lb.delete(i)
            if i < len(lista_rutas):
                lista_rutas.pop(i)

    def _log(self, texto, tag="sistema"):
        if threading.current_thread() is not threading.main_thread():
            self.after(0, self._log, texto, tag)
            return
        self.chat_area.configure(state="normal")
        self.chat_area.insert("end", texto + "\n", tag)
        self.chat_area.see("end")
        self.chat_area.configure(state="disabled")

    def _set_status(self, texto):
        self.lbl_status.configure(text=texto)

    def _mostrar_progreso(self, activo: bool):
        if activo:
            self.progress.pack(fill="x", pady=(4, 0))
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()


# ══════════════════════════════════════════════════════════════════════════════
# GESTIÓN Y NORMALIZACIÓN DE INTEGRANTES
# ══════════════════════════════════════════════════════════════════════════════

    def _normalizar_nombre(self, nombre: str) -> str:
        s = unidecode.unidecode(nombre).lower()
        s = re.sub(r'[^a-z\s]', ' ', s)
        return " ".join(s.split())

    def _match_estudiante(self, e1: dict, e2: dict) -> bool:
        if e1.get("rol") and e2.get("rol"):
            r1 = re.sub(r'\D', '', e1["rol"])
            r2 = re.sub(r'\D', '', e2["rol"])
            if r1 == r2:
                return True
                
        n1 = self._normalizar_nombre(f"{e1.get('nombre', '')} {e1.get('apellido', '')}")
        n2 = self._normalizar_nombre(f"{e2.get('nombre', '')} {e2.get('apellido', '')}")
        
        words1 = set(n1.split())
        words2 = set(n2.split())
        
        overlap = words1.intersection(words2)
        overlap = {w for w in overlap if len(w) > 2}
        
        if len(overlap) >= 2:
            return True
        if (words1.issubset(words2) or words2.issubset(words1)) and len(overlap) >= 1:
            return True
        return False

    def _configurar_grupo(self):
        dlg = DialogoGrupo(self)
        self.wait_window(dlg)
        if dlg.result:
            self.info_grupo = dlg.result
            asig = dlg.result["asig"]
            dia = dlg.result["dia"]
            blo = dlg.result["bloque"]
            exp = dlg.result["exp"]
            
            informe_ruta = self.ruta_informe.get().strip()
            if not dlg.result["estudiantes"] and informe_ruta:
                try:
                    self._log("Intentando extraer integrantes automáticamente del PDF...", "sistema")
                    txt_pdf = self.parser.leer_archivo(Path(informe_ruta))
                    ests_pdf = self.parser.extraer_estudiantes_del_pdf(txt_pdf)
                    if ests_pdf:
                        for idx_e, e_pdf in enumerate(ests_pdf):
                            e_pdf['id'] = idx_e + 1
                            e_pdf['display'] = f"{e_pdf['apellido']}, {e_pdf['nombre']}"
                            e_pdf['apellidos'] = e_pdf['apellido']
                        self.info_grupo["estudiantes"] = ests_pdf
                except Exception as e:
                    self._log(f"⚠ Fallo al extraer del PDF: {e}", "error")

            ests = self.info_grupo["estudiantes"]
            nombres = ", ".join(e["display"] for e in ests) if ests else "Sin estudiantes asignados"

            self.lbl_grupo.configure(text=f"{asig['codigo']} | {dia['nombre']} {blo['display']} | {exp}\nIntegrantes: {nombres}")
            self._log(f"✓ Grupo establecido: {asig['codigo']} - {exp}", "sistema")

    def _cargar_archivos(self) -> bool:
        self.archivos = {}
        errores = []

        obligatorios = [
            ("guia", self.ruta_guia, "Guía de la experiencia"),
            ("informe", self.ruta_informe, "Informe del grupo"),
        ]
        for clave, var, nombre in obligatorios:
            ruta_str = var.get().strip()
            if not ruta_str:
                errores.append(f"Falta: {nombre}")
                continue
            ruta = Path(ruta_str)
            if not ruta.exists():
                errores.append(f"No encontrado: {nombre}")
                continue
            try:
                contenido = self.parser.leer_archivo(ruta)
                self.archivos[clave] = contenido
                kb = len(contenido) // 1024
                self._log(f"✓ {nombre} cargado ({kb} KB texto extraído)", "sistema")
            except Exception as e:
                errores.append(f"Error leyendo {nombre}: {e}")

        opcionales = [
            ("prev_grupo", self.ruta_prev_grupo, "Correcciones previas — mismo grupo"),
            ("otros", self.ruta_otros, "Correcciones otros grupos"),
        ]
        for clave, lista_rutas, nombre in opcionales:
            if not lista_rutas:
                continue
            
            textos_leidos = []
            for r in lista_rutas:
                ruta_obj = Path(r)
                if not ruta_obj.exists():
                    self._log(f"⚠ {nombre}: archivo no encontrado ({ruta_obj.name}) (se omite)", "aviso")
                    continue
                try:
                    contenido = self.parser.leer_archivo(ruta_obj)
                    if self.compactar_estados.get() and ruta_obj.suffix.lower() == ".json":
                        contenido = self.parser.compactar_estado(contenido)
                    textos_leidos.append(f"--- Documento: {ruta_obj.name} ---\n{contenido}")
                    self._log(f"✓ {nombre} cargado: {ruta_obj.name}", "sistema")
                except Exception as e:
                    self._log(f"⚠ Error leyendo {ruta_obj.name}: {e} (se omite)", "aviso")
            
            if textos_leidos:
                self.archivos[clave] = "\n\n".join(textos_leidos)

        for e in errores:
            self._log(f"✗ {e}", "error")
        return len(errores) == 0

    def _preguntar_verificacion_inicial(self) -> bool:
        esquemas_propios = messagebox.askyesno(
            "Verificación de Esquemas",
            "¿El informe incluye esquemas o fotografías del montaje experimental?\n\n"
            "Si es así, ¿son de autoría propia de los estudiantes? (No copiados de la guía o de internet)"
        )
        self.visual_esquemas = "si_propios" if esquemas_propios else "no_o_copiados"

        graficos_legibles = messagebox.askyesno(
            "Verificación de Gráficos — Legibilidad",
            "¿Los gráficos de dispersión/ajuste son legibles y fueron exportados "
            "digitalmente desde el software? (No capturas de pantalla con celular)"
        )
        graficos_fondo_blanco = messagebox.askyesno(
            "Verificación de Gráficos — Fondo",
            "¿Los gráficos tienen fondo blanco?"
        ) if graficos_legibles else False

        if graficos_legibles and graficos_fondo_blanco:
            self.visual_graficos = "si_digitales_fondo_blanco"
        elif graficos_legibles:
            self.visual_graficos = "si_digitales_sin_fondo_blanco"
        else:
            self.visual_graficos = "no_o_fotos_pantalla"

        tablas_ok = messagebox.askyesno(
            "Verificación de Tablas",
            "¿Las tablas con valores medidos están tipeadas directamente en el documento "
            "y no son capturas de pantalla de hojas de cálculo?"
        )
        self.visual_tablas = "si_tipeadas" if tablas_ok else "no_o_capturas"

        return True

    def _construir_mensaje_inicial(self, incluir_informe_cabecera: bool = False) -> str:
        partes = []

        if self.info_grupo:
            asig = self.info_grupo["asig"]
            dia = self.info_grupo["dia"]
            blo = self.info_grupo["bloque"]
            exp = self.info_grupo["exp"]
            ests = self.info_grupo["estudiantes"]
            nombres = "\n".join(f"- {e['display']} (Rol: {e.get('rol', 'No especificado')})" for e in ests)
            partes.append(
                f"=== INFORMACIÓN DEL GRUPO ===\n"
                f"Asignatura: {asig['codigo']} — {asig['nombre']}\n"
                f"Horario: {dia['nombre']}, {blo['display']}\n"
                f"Experiencia: {exp}\n"
                f"Estudiantes:\n{nombres}"
            )

        partes.append(f"=== GUÍA DE LA EXPERIENCIA ===\n{self.archivos.get('guia', '')}")
        
        if incluir_informe_cabecera:
            informe_completo = self.archivos.get("informe", "")
            cabecera = self.parser.extraer_cabecera_informe(informe_completo)
            partes.append(f"=== INFORME DEL GRUPO (CABECERA Y METADATOS) ===\n{cabecera}")

        if self.visual_graficos == "si_digitales_fondo_blanco":
            desc_graficos = "SÍ son legibles, tienen fondo blanco y fueron exportados digitalmente."
        elif self.visual_graficos == "si_digitales_sin_fondo_blanco":
            desc_graficos = "SÍ son legibles y exportados digitalmente, pero NO tienen fondo blanco."
        else:
            desc_graficos = "NO son legibles, no fueron exportados digitalmente o son fotos de pantalla."

        info_visual = (
            f"=== VERIFICACIÓN VISUAL DEL EVALUADOR HUMANO ===\n"
            f"- Esquemas/fotografías del montaje: {'SÍ son de autoría propia de los estudiantes' if self.visual_esquemas == 'si_propios' else 'NO son de autoría propia o están ausentes'}.\n"
            f"- Gráficos de dispersión: {desc_graficos}\n"
            f"- Tablas de mediciones: {'SÍ están tipeadas en el documento (LaTeX/texto)' if self.visual_tablas == 'si_tipeadas' else 'NO están tipeadas (son imágenes o capturas de pantalla)'}.\n"
            f"INSTRUCCIÓN: Usa ÚNICAMENTE la información anterior para justificar ítems visuales (6 y 10). "
            f"No amplíes ni infieras atributos visuales no declarados aquí."
        )
        partes.append(info_visual)

        if "prev_grupo" in self.archivos:
            partes.append(f"=== CORRECCIONES PREVIAS DEL MISMO GRUPO ===\n{self.archivos['prev_grupo']}")
        if "otros" in self.archivos:
            partes.append(f"=== CORRECCIONES DE OTROS GRUPOS (misma experiencia) ===\n{self.archivos['otros']}")

        return "\n\n".join(partes)


# ══════════════════════════════════════════════════════════════════════════════
# FLUJO DE EVALUACIÓN CON SLICING QUIRÚRGICO DE PDF Y CANCELACIÓN
# ══════════════════════════════════════════════════════════════════════════════

    def _parsear_documento_calibracion(self, nombre_archivo: str, texto: str) -> list[dict]:
        ext = Path(nombre_archivo).suffix.lower()
        if ext == ".json":
            try:
                data = json.loads(texto)
                criterios = data.get("criterios", data.get("evaluaciones", []))
                c_lista = criterios.values() if isinstance(criterios, dict) else criterios
                normalizados = []
                for c in c_lista:
                    norm = _normalizar_criterio_historial(c)
                    if norm and isinstance(norm, dict) and "id" in norm:
                        normalizados.append({
                            "id": int(norm.get("id")),
                            "puntaje": float(norm.get("puntaje", 0)),
                            "justificacion": norm.get("justificacion", ""),
                        })
                return normalizados
            except Exception:
                pass
        else:
            # 1. PARSEO DE FORMATO ESTÁNDAR GENERADO POR LA APLICACIÓN
            patron_estandar = re.compile(
                r'Criterio:\s*(?P<desc>.+?)\nPuntaje:\s*(?P<pts>\d+)\s*\([^)]*\)'
                r'(?:\nJustificación:\s*(?P<just>.+?))?(?=\nCriterio:|\nCALIFICACIÓN|\Z)',
                re.DOTALL | re.IGNORECASE
            )

            desc_to_id = {}
            for item in RUBRICA_ITEMS_BASE:
                norm_desc = re.sub(r'[^a-z0-9]', '', unidecode.unidecode(item["desc"]).lower())
                desc_to_id[norm_desc] = item["id"]

            resultados = []
            matches_estandar = list(patron_estandar.finditer(texto))

            if matches_estandar:
                for m in matches_estandar:
                    desc_leida = m.group("desc").strip()
                    norm_leida = re.sub(r'[^a-z0-9]', '', unidecode.unidecode(desc_leida).lower())
                    cid = desc_to_id.get(norm_leida)
                    if cid is not None:
                        pts = float(m.group("pts"))
                        just = (m.group("just") or "").strip()
                        resultados.append({
                            "id": cid,
                            "puntaje": pts,
                            "justificacion": just
                        })
                if resultados:
                    return resultados

            # 2. FALLBACK: FORMATO COMPACTO (C1: 5/5) con soporte de doble dígito
            criterios_match = re.findall(r'C(\d+)\b.*?(\d+(?:\.\d+)?)\s*/\s*(\d+)', texto, re.IGNORECASE)
            res = {}
            for cid_str, pts, _ in criterios_match:
                try:
                    cid = int(cid_str)
                    if 1 <= cid <= 20:
                        res[cid] = {"id": cid, "puntaje": float(pts), "justificacion": ""}
                except ValueError:
                    pass

            just_matches = re.findall(r'•\s*C(\d+)\s*:\s*(.*?)(?=\n•|\n\n|\n===|\Z)', texto, re.DOTALL | re.IGNORECASE)
            for cid_str, just in just_matches:
                try:
                    cid = int(cid_str)
                    if cid in res:
                        res[cid]["justificacion"] = just.strip()
                except ValueError:
                    pass

            if res:
                return list(res.values())

        return []

    def _verificar_contexto(self):
        if not self.info_grupo:
            messagebox.showwarning("Grupo no configurado", "Configura el curso y el grupo antes de iniciar.")
            return

        self._log("\n── CARGANDO ARCHIVOS Y PREPARANDO CONTEXTO ──", "sistema")
        if not self._cargar_archivos():
            messagebox.showerror("Error", "No se cargaron los archivos obligatorios.")
            return

        self._preguntar_verificacion_inicial()

        self.contexto_base = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._construir_mensaje_inicial(incluir_informe_cabecera=False)},
        ]
        
        mensaje_lectura_cabecera = self._construir_mensaje_inicial(incluir_informe_cabecera=True)
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": mensaje_lectura_cabecera},
            {"role": "user", "content": PROMPT_LECTURA}
        ]
        
        self.resultados_criterios = []
        self.evaluaciones_historicas = []
        self.reporte_listo = False
        self._mostrar_progreso(True)
        self.btn_verificar.configure(state="disabled")

        for r in self.ruta_prev_grupo:
            p = Path(r)
            if p.exists():
                calib = self.parser.parsear_archivo_calibracion(p)
                if calib and "criterios" in calib:
                    for ev in calib["criterios"]:
                        self.evaluaciones_historicas.append({
                            "grupo": calib["grupo"],
                            "mismo_grupo": True,
                            "id": ev["id"],
                            "puntaje": ev["puntaje"],
                            "justificacion": ev["justificacion"]
                        })
                    
        for r in self.ruta_otros:
            p = Path(r)
            if p.exists():
                calib = self.parser.parsear_archivo_calibracion(p)
                if calib and "criterios" in calib:
                    for ev in calib["criterios"]:
                        self.evaluaciones_historicas.append({
                            "grupo": calib["grupo"],
                            "id": ev["id"],
                            "puntaje": ev["puntaje"],
                            "justificacion": ev["justificacion"]
                        })

        self.historial_matriz = {}
        for ev in self.evaluaciones_historicas:
            self.historial_matriz.setdefault(ev["grupo"], []).append(ev)

        def worker():
            try:
                resp = self.llm.llamar(self.messages, json_mode=False)
                self.messages = list(self.contexto_base) + [{"role": "assistant", "content": resp}]
                self.after(0, lambda: self._post_lectura_inicial(resp))
            except Exception as e:
                self.after(0, lambda: self._log(f"✗ Error: {e}", "error"))
                self.after(0, lambda: self._mostrar_progreso(False))
                self.after(0, lambda: self.btn_verificar.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def _post_lectura_inicial(self, resp: str):
        self._mostrar_progreso(False)
        self._log("\nModelo (Lectura Silenciosa):", "sistema")
        self._log(resp, "modelo")
        self._log(
            "\n✔ Lectura completada. Revisa los integrantes detectados y el historial anterior.\n"
            "Aclara dudas en el chat o haz clic en '⚡ EVALUAR CRITERIOS' para continuar.",
            "aviso"
        )
        
        nombres_llm = []
        if self.info_grupo:
            m_sec3 = re.search(
                r'\(3\)[^\n]*:\s*(.+?)(?=\n\s*\([45]\)|\Z)',
                resp, re.IGNORECASE | re.DOTALL
            )
            if m_sec3:
                bloque = m_sec3.group(1).strip()
                bloques_sep = re.split(r'\s+y\s+|\s*,\s*|\s*&\s*', bloque)
                lineas_bloque = [l.strip().rstrip('.').strip() for l in bloques_sep if l.strip()]
                for idx, l in enumerate(lineas_bloque):
                    prev_l = lineas_bloque[idx - 1] if idx > 0 else ""
                    l = re.sub(r'^[-*•\s\d\.\)]+', '', l).strip()
                    if not l:
                        continue
                    
                    m_rol = re.search(r'\b(\d{7,9}-?[\dkK])\b', l)
                    rol = m_rol.group(1) if m_rol else ""
                    if not rol:
                        continue

                    l_clean = re.sub(r'\(.*?\)', '', l)
                    l_clean = re.sub(r'rol\s*:\s*\S*', '', l_clean, flags=re.IGNORECASE)
                    l_clean = re.sub(r'\b\d{7,9}-?[\dkK]\b', '', l_clean, flags=re.IGNORECASE)
                    l_clean = re.sub(r'[,;\.\s\-]+$', '', l_clean).strip()
                    contexto = ' '.join([prev_l.strip(), l.strip()])

                    if not l_clean or not DocumentParser._es_nombre(l_clean) or not DocumentParser._es_linea_estudiante(l_clean, contexto):
                        continue
                    
                    if ',' in l_clean:
                        partes = l_clean.split(',', 1)
                        nombres_llm.append({'nombre': partes[1].strip(), 'apellido': partes[0].strip(), 'rol': rol, 'display': f"{partes[0].strip().upper()}, {partes[1].strip().upper()}"})
                    else:
                        partes = l_clean.split()
                        if len(partes) >= 2:
                            nombre = partes[0]
                            apellido = " ".join(partes[1:])
                        else:
                            nombre = l_clean
                            apellido = ""
                        nombres_llm.append({
                            'nombre': nombre,
                            'apellido': apellido,
                            'rol': rol,
                            'display': f"{apellido.upper()}, {nombre.upper()}" if apellido else nombre.upper()
                        })

        if self.info_grupo:
            nombres_pdf = nombres_llm if nombres_llm else self.parser.extraer_estudiantes_del_pdf(self.parser.extraer_cabecera_informe(self.archivos.get("informe", "")))
            
            ests_manual = self.info_grupo.get("estudiantes", [])
            ests_merged = []
            
            for em in ests_manual:
                matched = False
                for ep in nombres_pdf:
                    if self._match_estudiante(em, ep):
                        if ep.get("rol") and not em.get("rol"):
                            em["rol"] = ep["rol"]
                        ests_merged.append(em)
                        matched = True
                        break
                if not matched:
                    ests_merged.append(em)
                    
            if not ests_manual:
                ests_merged = nombres_pdf
            else:
                for ep in nombres_pdf:
                    ya_incluido = any(self._match_estudiante(ep, em) for em in ests_merged)
                    if not ya_incluido:
                        self._log(f"⚠ El PDF menciona integrante no seleccionado: {ep['display']} (Rol: {ep['rol']}) — agregado automáticamente", "aviso")
                        ests_merged.append(ep)

            self.info_grupo["estudiantes"] = ests_merged
            ests_final = self.info_grupo["estudiantes"]
            self.lbl_grupo.configure(
                text=f"{self.info_grupo['asig']['codigo']} | {self.info_grupo['dia']['nombre']} "
                     f"{self.info_grupo['bloque']['display']} | {self.info_grupo['exp']}\n"
                     f"Integrantes: {', '.join(e['display'] for e in ests_final)}"
            )

        self.sesion_activa = True
        self.btn_enviar.configure(state="normal")
        self.btn_evaluar.configure(state="normal")
        self.btn_verificar.configure(state="normal")
        if self.ruta_otros or self.ruta_prev_grupo:
            self.btn_consolidado.configure(state="normal")

    def _iniciar_evaluacion(self):
        self.cancelar_evaluacion = False
        self.cancelar_todo = False
        self.resultados_criterios = []
        self._log("\n── EVALUACIÓN AUTOMATIZADA CRITERIO A CRITERIO ──", "sistema")
        
        self.btn_evaluar.configure(
            text="🛑 CANCELAR EVALUACIÓN",
            bg="#dc2626",
            command=self._cancelar_proceso_evaluacion,
            state="normal"
        )
        self.btn_detener.configure(state="normal")
        self.btn_verificar.configure(state="disabled")
        self.btn_nueva.configure(state="disabled")
        self._mostrar_progreso(True)
        self._evaluar_siguiente_item(0)

    def _cancelar_proceso_evaluacion(self):
        if not messagebox.askyesno("Confirmar cancelación", 
                                   "¿Estás seguro de que deseas cancelar la evaluación?\n"
                                   "Los criterios ya evaluados se conservarán."):
            return
        self.cancelar_evaluacion = True
        self._log("\n⏳ Cancelación solicitada. Terminará después del criterio actual...", "aviso")
        self._set_status("⏳ Cancelando...")
        self.btn_evaluar.configure(state="disabled")
        self.btn_detener.configure(state="disabled")

    def _cancelar_todo(self):
        if not messagebox.askyesno("Confirmar detención total",
                                   "¿Estás seguro de que deseas detener todo el proceso?\n"
                                   "Esto puede dejar la evaluación en un estado incompleto."):
            return
        self.cancelar_evaluacion = True
        self.cancelar_todo = True
        self.btn_detener.configure(state="disabled")
        self.after(0, lambda: self._log("\n🛑 PROCESO DETENIDO POR EL USUARIO. Se ha abortado la operación actual.", "error"))
        self.after(0, self._restaurar_botones_post_evaluacion)

    def _restaurar_botones_post_evaluacion(self):
        self.btn_evaluar.configure(
            text="⚡ EVALUAR CRITERIOS",
            bg="#10b981",
            command=self._iniciar_evaluacion,
            state="normal"
        )
        self.btn_verificar.configure(state="normal")
        self.btn_nueva.configure(state="normal")
        self.btn_detener.configure(state="disabled")
        if self.resultados_criterios:
            self.btn_generar_reporte.configure(state="normal")

    def _cambiar_modelo(self, nuevo_modelo):
        self.llm.config["model"] = nuevo_modelo
        self._log(f"🤖 Modelo cambiado en vivo a: {nuevo_modelo}", "sistema")

    def _aplicar_timeout(self):
        try:
            nuevo = int(self._timeout_val.get().strip())
            if nuevo < 10:
                raise ValueError
            self.llm.config["timeout"] = nuevo
            self._log(f"⏱ Timeout cambiado en vivo a {nuevo} s", "sistema")
        except ValueError:
            messagebox.showerror("Error", "El timeout debe ser un número entero ≥ 10 segundos.")

    def _evaluar_siguiente_item(self, idx: int):
        if getattr(self, "cancelar_evaluacion", False) or getattr(self, "cancelar_todo", False):
            self._mostrar_progreso(False)
            self._log("\n🛑 Evaluación interrumpida exitosamente por el evaluador.", "error")
            self._restaurar_botones_post_evaluacion()
            return

        total = len(RUBRICA_ITEMS_BASE)
        if idx >= total:
            self._mostrar_progreso(False)
            self._log("\n── Análisis individual de ítems completado ──", "sistema")
            self._restaurar_botones_post_evaluacion()
            self._generar_comentario_final()
            return

        item = RUBRICA_ITEMS_BASE[idx]
        self._set_status(f"Evaluando ítem {idx + 1}/{total}...")
        self._log(f"\n⏳ [{idx + 1}/{total}] Analizando: '{item['desc'][:65]}...'", "sistema")

        informe_completo = self.archivos.get("informe", "")
        informe_seccion = self.parser.extraer_seccion_informe(informe_completo, item["id"])
        chars_sec = len(informe_seccion)
        if chars_sec < 100:
            self._log(f"   ⚠ Sección para ítem {item['id']} muy corta ({chars_sec} caracteres). El modelo podría no tener contexto suficiente.", "aviso")
        else:
            self._log(f"   ↳ Sección extraída: {chars_sec} caracteres para ítem {item['id']}.", "sistema")

        criterios_previos = [
            ev for ev in self.evaluaciones_historicas if ev["id"] == item["id"]
        ]

        if criterios_previos:
            pts_hist = [c["puntaje"] for c in  criterios_previos]
            avg = sum(pts_hist) / len(pts_hist)
            self._log(f"   ↳ Histórico similar: promedio {avg:.1f}/5 en {len(pts_hist)} correcciones.", "aviso")

        antecedente_txt = ""
        if "prev_grupo" in self.archivos:
            texto_prev = self.archivos["prev_grupo"]
            for segmento in re.split(r'--- Documento:.*?---', texto_prev):
                segmento = segmento.strip()
                if not segmento:
                    continue
                try:
                    dprev = json.loads(segmento)
                    exp_prev = dprev.get("nombre_experiencia", dprev.get("grupo", "anterior"))
                    lista_evs = dprev.get("criterios", dprev.get("evaluaciones", []))
                    if isinstance(lista_evs, list):
                        for ev in lista_evs:
                            ev_norm = _normalizar_criterio_historial(ev)
                            if ev_norm and ev_norm.get("id") == item["id"]:
                                antecedente_txt += (
                                    f"Corrección previa (exp {exp_prev}): "
                                    f"puntaje={ev_norm.get('puntaje', '?')}/5. "
                                    f"Justificación: {ev_norm.get('justificacion', '')}\n"
                                )
                except (json.JSONDecodeError, TypeError):
                    pass

            desc_lower = item["desc"].lower()
            patron_txt = re.compile(
                r'Criterio:\s*(?P<desc>.+?)\nPuntaje:\s*(?P<pts>\d+)\s*\([^)]*\)'
                r'(?:\nJustificación:\s*(?P<just>.+?))?(?=\nCriterio:|\nCALIFICACIÓN|\Z)',
                re.DOTALL
            )
            for m_txt in patron_txt.finditer(texto_prev):
                if m_txt.group("desc").strip().lower() == desc_lower:
                    just_txt = (m_txt.group("just") or "").strip()
                    antecedente_txt += (
                        f"Corrección previa (reporte anterior): "
                        f"puntaje={m_txt.group('pts')}/5. "
                        f"Justificación: {just_txt}\n"
                    )

        calibracion_txt = ""
        if criterios_previos:
            calibracion_txt = "=== HISTORIAL DE CALIBRACIÓN PARA ESTE CRITERIO ===\n"
            calibracion_txt += (
                "Este historial de calibración incluye tanto evaluaciones de otros grupos para la experiencia actual "
                "como evaluaciones históricas de este mismo grupo en laboratorios anteriores del semestre.\n"
                "Dado que los estándares de evaluación de la cátedra para este criterio específico son estrictamente "
                "invariantes a lo largo del año académico (independiente de la materia física que aborde el laboratorio), "
                "debes utilizarlos como referencia EXACTA y ancla absoluta de escala y severidad para determinar los puntajes "
                "y justificaciones de esta entrega:\n"
            )
            for c in criterios_previos:
                calibracion_txt += f"- Grupo/Experiencia «{c.get('grupo', '?')}»: obtuvo {c['puntaje']} pts.\n  Contexto/Evidencia previa: {c.get('justificacion', '')[:200]}...\n"
            calibracion_txt += "Asegúrate de que el puntaje que asocies ahora sea totalmente consistente (sin sesgos) con estos precedentes históricos y cruzados.\n\n"

        evaluados_txt = ""
        if self.resultados_criterios:
            evaluados_txt = "\n=== CRITERIOS YA EVALUADOS EN ESTA SESIÓN ===\n"
            for prev_res in self.resultados_criterios:
                evaluados_txt += (
                    f"- {prev_res['id']}: {prev_res['puntaje']} pts.\n"
                    f"  Justificación: {prev_res['justificacion']}\n"
                )
            evaluados_txt += (
                "Evalúa este criterio de forma independiente. Si un error afecta a múltiples criterios, "
                "penalízalo en cada uno de ellos según corresponda a la descripción del ítem.\n\n"
            )

        bloque_restricciones = ""
        if self._restricciones_evaluador:
            item_desc = item["desc"].lower()
            item_sec = item["seccion"].lower()
            relevantes = []
            for r in self._restricciones_evaluador:
                r_lower = r.lower()
                obs_keywords = set(w for w in r_lower.split() if len(w) > 4 and w not in {"experimentales", "propia", "instrucción", "características"})
                item_keywords = set(w for w in item_desc.split() if len(w) > 4)
                if obs_keywords & item_keywords or any(w in r_lower for w in ["tabla", "gráfica", "excel", "esquema", "fotografía", "montaje", "error", "cita", "referencia", "bibliogr", "instrumento", "ajuste", "medición", "incertidumbre", "manual", "rol", "nombre"] if w in item_desc):
                    relevantes.append(r)
            if relevantes:
                restricciones_str = "\n".join(f"  - {r}" for r in relevantes)
                bloque_restricciones = (
                    f"=== OBSERVACIONES Y RESTRICCIONES MANUALES DEL EVALUADOR ===\n"
                    f"(Solo se muestran las observaciones con potencial relevance a este criterio)\n"
                    f"{restricciones_str}\n"
                    f"INSTRUCCIÓN: Mapea de forma autónoma si el comentario del evaluador docente "
                    f"afecta a este criterio {item['id']}. Si es así, aplícalo directamente para determinar "
                    f"el puntaje e inyéctalo críticamente en la justificación.\n\n"
                )

        prompt_item = (
            f"=== SECCIÓN EN EVALUACIÓN: {item['seccion']} ===\n"
            f"A continuación se presenta la sección correspondiente extraída quirúrgicamente del informe del estudiante:\n\n"
            f"{informe_seccion}\n\n"
            f"==================================================\n\n"
        )
        if bloque_restricciones:
            prompt_item += bloque_restricciones
            
        prompt_item += (
            f"Evalúa ÚNICAMENTE este criterio. Responde SOLO con el JSON estructurado indicado, sin explicaciones ni texto adicional.\n\n"
            f"CRITERIO id={item['id']} | Sección: {item['seccion']}\n"
            f"Descripción: {item['desc']}\n\n"
        )
        if antecedente_txt:
            prompt_item += f"ANTECEDENTE DE ESTE ÍTEM EN CORRECCIONES PREVIAS DEL MISMO GRUPO:\n{antecedente_txt}\n"
        if calibracion_txt:
            prompt_item += f"{calibracion_txt}\n\n"
        if evaluados_txt:
            prompt_item += f"{evaluados_txt}\n\n"
            
        prompt_item += (
            f"MAPEO OBLIGATORIO DE CALIFICACIÓN:\n"
            f"- Excelente (E): 5 pts\n"
            f"- Bueno (B): 4 pts\n"
            f"- Aceptable (A): 3 pts\n"
            f"- Deficiente (D): 2 pts\n"
            f"- Muy Deficiente (MD): 1 pt\n"
            f"- Ausente/No cumple (AU): 0 pts\n\n"
            f"Responde SOLO con este formato JSON:\n"
            f"```json\n"
            f"{{\n"
            f'  "id": {item["id"]},\n'
            f'  "puntaje": <0-5>,\n'
            f'  "nivel": "<E|B|A|D|MD|AU>",\n'
            f'  "evidencia": "<cita o paráfrasis breve del fragmento que sustenta la nota>",\n'
            f'  "justificacion": "<justificación del puntaje obtenido, 1-3 oraciones. Menciona qué está presente y qué falta o está incorrecto según la rúbrica para justificar el nivel asignado, pero SIN lenguaje punitivo. Prohibido: \'se descuentan\', \'se penaliza\', \'se restan puntos\', \'se sanciona\', \'castigo\'. En su lugar, usa lenguaje como: \'el informe incluye X pero no presenta Y, por lo que corresponde al nivel A\'.>"\n'
            f'  "razonamiento_interno": "<Para el profesor: desglosa \'-X pts por [error específico]\'. Si es puntaje completo, indica \'Puntaje completo\'>"\n'
            f"}}\n"
            f"```"
        )

        msgs = self.contexto_base + [{"role": "user", "content": prompt_item}]

        def worker():
            try:
                # --- PASO 1: Propuesta Inicial ---
                resp = self.llm.llamar(msgs, json_mode=True)
                datos_iniciales = _parsear_json_llm(resp)
                if not isinstance(datos_iniciales, dict):
                    raise ValueError("El evaluador devolvió texto en lugar de JSON válido.")
                if self.cancelar_todo:
                    self.after(0, lambda: self._log("🛑 Cancelando evaluación...", "error"))
                    return

                # --- PASO 2: Consulta de Jurisprudencia (Auditoría Maestra) ---
                # DESACTIVADO TEMPORALMENTE — causaba doble llamada LLM
                # que introducía ruido en las evaluaciones. Se reactivará
                # cuando el flujo base esté estabilizado.
                # tag = self._extraer_tag_falencia(datos_iniciales.get("razonamiento_interno", ""))
                # if tag:
                #     precedentes = self.indice_auditoria.get(str(item["id"]), {}).get(tag, [])
                #     if precedentes:
                #         hist_txt = "\n".join(...)
                #         audit_prompt = ...
                #         resp_final = self.llm.llamar(msgs_copia, json_mode=True)
                #         datos_iniciales = _parsear_json_llm(resp_final)

                if not self.cancelar_todo:
                    self.after(0, lambda: self._registrar_criterio(datos_iniciales, item, idx))
            except Exception as e:
                self.after(0, lambda: self._log(f"✗ Error evaluando criterio {item['id']}: {e}", "error"))
                data_err = {"id": item["id"], "justificacion": f"Error de llamada: {e}"}
                self.after(0, lambda: self._registrar_criterio(data_err, item, idx))

        threading.Thread(target=worker, daemon=True).start()

    def _registrar_criterio(self, data: dict, item: dict, idx: int):
        if not isinstance(data, dict):
            data = {}
        data = _validar_evaluacion(data, item["id"])

        # --- GUARDRAIL DE COHERENCIA ---
        just = data.get("justificacion", "").lower()
        pts = data.get("puntaje", 0)

        positivas = ["correcto", "cumple", "presenta", "bueno", "excelente",
                     "sólido", "completo", "clara", "incluye", "contiene",
                     "completos", "correcta", "correctos", "presentan"]

        def _no_negada(texto, palabra):
            patron = r'\b(no|sin|carece|omite|falta|ausencia|excepto|salvo)\s+(?:de\s+)?' + re.escape(palabra)
            return not re.search(patron, texto)

        palabras_reales = [p for p in positivas if p in just and _no_negada(just, p)]

        if pts < 2 and palabras_reales:
            pts_nuevo = 2 if pts <= 1 else pts
            self._log(f"⚠ Ajuste de coherencia: justificación positiva con puntaje {pts} corregido a {pts_nuevo} para ítem {item['id']}", "aviso")
            data["puntaje"] = pts_nuevo
            for k, v in PUNTAJES_MAP.items():
                if v["puntaje"] == pts_nuevo:
                    data["nivel"] = k
                    break
        # -------------------------------
        self.resultados_criterios.append(data)
        pts = data.get("puntaje", 0)
        desc_niv = PUNTAJES_MAP.get(data.get("nivel", "AU"), {}).get("descripcion", data.get("nivel", "AU"))
        just = data.get("justificacion", "")

        self._log(f"   ↳ {desc_niv} ({pts}/5 pts)\n   ↳ Justificación: {just}", "modelo")
        
        # Auditoría de consistencia en caliente
        self._auditar_consistencia(data)
        if "justificacion" in data and not data["justificacion"].startswith("Error de llamada:"):
            try:
                self._actualizar_indice_auditoria(data)
            except Exception as e:
                print(f"[DEBUG] Error actualizando índice de auditoría: {e}")

        if not getattr(self, "cancelar_todo", False):
            self.after(350, lambda: self._evaluar_siguiente_item(idx + 1))
        else:
            self.after(0, self._restaurar_botones_post_evaluacion)

    def _aplicar_reevaluacion(self, nuevos: list[dict]):
        self._mostrar_progreso(False)
        antes = {ev["id"]: ev.get("puntaje", "?") for ev in self.resultados_criterios}

        actualizados = 0
        cambios_texto = []
        for ev in nuevos:
            cid = ev.get("id")
            pts_viejo = antes.get(cid, "?")
            if self._actualizar_criterio_individual(ev):
                pts = ev.get("puntaje", "?")
                niv = ev.get("nivel", "")
                just = ev.get("justificacion", "")
                flecha = "↗" if pts > pts_viejo else "↘" if pts < pts_viejo else "="
                self._log(
                    f"   {flecha} Ítem {cid}: {pts_viejo}/5 → {pts}/5 ({niv})\n"
                    f"      Justificación: {just}",
                    "sync"
                )
                cambios_texto.append(f"  - Ítem {cid}: {pts_viejo}/5 → {pts}/5")
                actualizados += 1

        total_p, max_p, calif = self._recalcular_puntajes()

        # ACTUALIZACIÓN REACTIVA DE LOS COMENTARIOS ADICIONALES EN TIEMPO REAL
        if self.comentarios_adicionales:
            self.comentarios_adicionales = re.sub(
                r'\b\d+(?:\.\d+)?/100\b',
                f"{calif}/100",
                self.comentarios_adicionales
            )
            for ev in nuevos:
                cid = ev.get("id")
                pts = ev.get("puntaje")
                if cid and pts is not None:
                    pts_viejo = antes.get(cid)
                    if isinstance(pts_viejo, (int, float)):
                        if pts > pts_viejo:
                            verbo = "asciende a"
                        elif pts < pts_viejo:
                            verbo = "retrocede a"
                        else:
                            verbo = "se mantiene en"
                    else:
                        verbo = "se establece en"
                    patron_item = rf'\bÍtem {cid}\) (?:retroceden|cae|asciende|se consolida|retrocede|se mantiene|retroceden a|cae a|asciende a|se consolida a|retrocede a|se mantiene a) \d+/5\b'
                    self.comentarios_adicionales = re.sub(
                        patron_item,
                        f"Ítem {cid}) {verbo} {pts}/5",
                        self.comentarios_adicionales,
                        flags=re.IGNORECASE
                    )

            self.comentarios_adicionales = re.sub(r'\(\d+\)\s*', '', self.comentarios_adicionales)

        resumen_calif = (
            f"\n═══ RESUMEN TRAS RE-EVALUACIÓN ═══\n"
            f"Ítems modificados: {actualizados}\n"
        )
        if cambios_texto:
            resumen_calif += "\n".join(cambios_texto) + "\n"
        resumen_calif += (
            f"\nPuntaje Acumulado: {total_p}/{max_p}\n"
            f"Calificación Final: {calif}/100\n"
        )
        self._log(resumen_calif, "sistema")

        self.btn_enviar.configure(state="normal")
        self.after(600, self._preguntar_aprobacion_puntajes)

    def _mostrar_respuesta_chat(self, resp: str):
        self._mostrar_progreso(False)
        texto_visible = re.sub(r'```json\s*\{.*?```\s*', '', resp, flags=re.DOTALL).strip()
        if texto_visible:
            self._log(f"\nModelo: {texto_visible}", "modelo")
        self.btn_enviar.configure(state="normal")
        self.entry.focus()
        self._sincronizar_desde_chat(resp)

    def _sincronizar_desde_chat(self, texto: str):
        nuevos = []
        sincronizado = False

        bloques_md = list(re.finditer(r'```json\s*(\{.*?)```', texto, re.DOTALL))
        bloques_planos = list(re.finditer(r'\{.*?\}', texto, re.DOTALL)) if not bloques_md else []

        for m in bloques_md or bloques_planos:
            raw = m.group(1) if bloques_md else m.group(0)
            try:
                data = _parsear_json_llm(raw.strip())
            except Exception:
                continue
            if not isinstance(data, dict):
                continue

            if data.get("comentario_general"):
                self.comentarios_adicionales = data["comentario_general"]
                self._log(f"✏️ Comentario general actualizado desde el chat.", "sync")
                sincronizado = True

            if data.get("nombre_experiencia"):
                self.nombre_experiencia_final = data["nombre_experiencia"]
                sincronizado = True

            if "evaluaciones" in data:
                nuevos.extend(data["evaluaciones"])
            elif data.get("id") is not None:
                nuevos.append(data)

        if nuevos:
            self._aplicar_reevaluacion(nuevos)
        elif sincronizado:
            total_p, max_p, calif = self._recalcular_puntajes()
            self._log(f"   ↳ Calificación recalculada: {calif}/100", "sync")
            self.after(600, self._preguntar_aprobacion_puntajes)
        elif "REPORTE_LISTO_PARA_GUARDAR" in texto:
            self._finalizar_aprobacion()

    def _actualizar_criterio_individual(self, ev_data: dict) -> bool:
        ev_data = _validar_evaluacion(ev_data, 1)
        cid = ev_data.get("id")
        if cid is None:
            return False

        try:
            cid = int(cid)
        except ValueError:
            return False

        for c in self.resultados_criterios:
            if c["id"] == cid:
                c["puntaje"] = ev_data["puntaje"]
                c["nivel"] = ev_data["nivel"]
                c["justificacion"] = ev_data["justificacion"]
                c["evidencia"] = ev_data["evidencia"]
                return True
        return False

    @staticmethod
    def _extraer_tag_falencia(razonamiento: str) -> str:
        if not razonamiento:
            return "general"
        if "puntaje completo" in razonamiento.lower():
            return None
        m = re.search(r'-\d+(\.\d+)?\s*pts\s*(?:por|debido a)?\s*(.+?)(?:\.|;|$)', razonamiento, re.IGNORECASE)
        return m.group(2).strip().lower() if m else None

    def _cargar_indice_auditoria(self):
        idx_path = Path("auditoria_maestra.json")
        if idx_path.exists():
            try:
                self.indice_auditoria = json.loads(idx_path.read_text(encoding="utf-8"))
                self._log("✓ Índice de auditoría maestra cargado exitosamente.", "sistema")
            except Exception as e:
                self._log(f"⚠ Error cargando índice de auditoría: {e}", "error")
        else:
            self._log("ℹ️ No se encontró auditoria_maestra.json. Intentando generarlo desde archivos existentes...", "sistema")
            self._generar_indice_inicial()

    def _generar_indice_inicial(self):
        """Genera e indexa la base de datos de auditoría a partir de sesiones locales e historiales cargados."""
        ruta_base = Path(self.ruta_informe.get()).parent if self.ruta_informe.get() else Path(".")
        
        # Buscar sesiones JSON locales en el directorio de trabajo
        archivos_locales = list(ruta_base.glob("sesion_*.json"))
        
        # Integrar de manera unificada todos los historiales cargados en los listbox de la UI
        archivos_historicos = list(self.ruta_prev_grupo) + list(self.ruta_otros)
        
        todos_los_archivos = list(archivos_locales) + [Path(r) for r in archivos_historicos if Path(r).exists()]
        todos_los_archivos = list(set(todos_los_archivos))  # Eliminar duplicados de ruta
        
        if not todos_los_archivos:
            self._log("⚠ No se encontraron archivos históricos de sesión para indexar.", "aviso")
            return
        index = {}
        for f in todos_los_archivos:
            p_ruta = Path(f) if isinstance(f, str) else f
            try:
                calibracion_data = self.parser.parsear_archivo_calibracion(p_ruta)
                if not calibracion_data or "criterios" not in calibracion_data:
                    continue
                
                criterios_extraidos = calibracion_data["criterios"]
                grupo_nombre = calibracion_data.get("grupo", p_ruta.stem)
                
                # Indexar criterios detectados en la auditoría maestra
                for c in criterios_extraidos:
                    razonamiento = c.get("razonamiento_interno", "") or c.get("justificacion", "")
                    tag = self._extraer_tag_falencia(razonamiento)
                    if not tag:
                        continue
                        
                    cid = str(c.get("id"))
                    index.setdefault(cid, {}).setdefault(tag, [])
                    
                    # Evitar duplicaciones del mismo grupo bajo el mismo tag
                    index[cid][tag] = [entry for entry in index[cid][tag] if entry.get("grupo") != grupo_nombre]
                    
                    index[cid][tag].append({
                        "grupo": grupo_nombre,
                        "puntaje": c.get("puntaje", 0),
                        "justificacion": c.get("justificacion", "")  # Se conserva justificación completa (sin truncar)
                    })
            except Exception as e:
                self._log(f"⚠ Error indexando archivo {p_ruta.name} en auditoría: {e}", "aviso")
                
        idx_path = Path("auditoria_maestra.json")
        idx_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
        self.indice_auditoria = index
        self._log(f"✓ Índice maestro generado y actualizado con {len(todos_los_archivos)} archivo(s) históricos.", "sistema")

    def _actualizar_indice_auditoria(self, resultado: dict):
        idx_path = Path("auditoria_maestra.json")
        data = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {}
        cid = str(resultado["id"])
        tag = self._extraer_tag_falencia(resultado.get("razonamiento_interno", ""))
        if not tag:
            return
            
        grupo_nombre = self._nombre_base_archivo() or "desconocido"
        entry = {
            "grupo": grupo_nombre,
            "puntaje": resultado["puntaje"],
            "justificacion": resultado.get("justificacion", "")
        }
        if cid not in data:
            data[cid] = {}
        if tag not in data[cid]:
            data[cid][tag] = []
            
        # Evitar duplicaciones idempotentes en caliente para el mismo grupo
        data[cid][tag] = [e for e in data[cid][tag] if e.get("grupo") != grupo_nombre]
        data[cid][tag].append(entry)
        
        idx_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.indice_auditoria = data

    def _auditar_inconsistencias_retroactivas(self):
        idx_path = Path("auditoria_maestra.json")
        if not idx_path.exists(): return
        try:
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            hallazgos = 0
            for cid, tags in data.items():
                for tag, lista in tags.items():
                    pts_list = [d["puntaje"] for d in lista if "puntaje" in d]
                    if len(pts_list) > 1 and max(pts_list) - min(pts_list) > 2.5:
                        self._log(f"⚠ Discrepancia en criterio {cid} / '{tag[:60]}...' — "
                                  f"penalizaciones entre {min(pts_list)} y {max(pts_list)} pts ({len(pts_list)} casos).", "aviso")
                        hallazgos += 1
            if hallazgos == 0:
                self._log("✔ Auditoría retroactiva: sin discrepancias significativas (>2.5 pts).", "sistema")
            else:
                self._log(f"🔍 Auditoría retroactiva completada: {hallazgos} discrepancia(s) encontrada(s).", "sistema")
        except Exception as e:
            self._log(f"⚠ Error escaneando auditoría retroactiva: {e}", "aviso")

    def _auditar_consistencia(self, resultado: dict):
        if not self.evaluaciones_historicas: return
        precedentes = [h for h in self.evaluaciones_historicas if h["id"] == resultado["id"]]
        if not precedentes: return
        tag = self._extraer_tag_falencia(resultado.get("razonamiento_interno", ""))
        relevantes = [p for p in precedentes if any(
            self._extraer_tag_falencia(p.get("razonamiento_interno", "")) == tag
            for _ in [1]
        )]
        if not relevantes:
            relevantes = precedentes
        self._log(f"\n{'='*20} AUDITORÍA: Criterio {resultado['id']} {'='*20}", "sync")
        self._log(f"Tag detectado: {tag}", "sync")
        self._log(f"Precedentes con misma falencia: {len(relevantes)} — " +
                  ", ".join(f"{p['grupo']} ({p['puntaje']} pts)" for p in relevantes), "sync")
        promedio = sum(float(p["puntaje"]) for p in relevantes) / len(relevantes)
        actual = float(resultado["puntaje"])
        delta = actual - promedio
        if abs(delta) >= 1.5:
            direccion = "supera" if delta > 0 else "inferior a"
            self._log(
                f"[AUDITORÍA] Criterio {resultado['id']}: promedio histórico {promedio:.1f} pts → "
                f"este grupo {actual:.1f} pts (Δ = {delta:+.1f}). El puntaje es {direccion} "
                f"la media histórica. Verifica consistencia.", "sync"
            )
        self._log(f"{'='*56}", "sync")


# ══════════════════════════════════════════════════════════════════════════════
# EXPORTACIÓN E INCONSISTENCIAS EN MEMORIA
# ══════════════════════════════════════════════════════════════════════════════

    def _nombre_base_archivo(self) -> str:
        if not self.info_grupo:
            return f"lab_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        asig = self.info_grupo["asig"]["codigo"]
        dia = self.info_grupo["dia"]["cod"]
        blo = self.info_grupo["bloque"]["suf"]
        exp = self.info_grupo["exp"]
        
        ests = self.info_grupo.get("estudiantes", [])
        if ests:
            apellidos = []
            for e in ests:
                ap = e.get("apellido", e.get("apellidos", "")).strip()
                if ap:
                    ap_norm = unidecode.unidecode(ap.split()[0]).lower()
                    apellidos.append(ap_norm)
            cadena_apellidos = "-".join(sorted(apellidos))
        else:
            cadena_apellidos = "grupo"

        nombre = f"{exp}_{asig}_{dia}-{blo}_{cadena_apellidos}"
        return "".join("_" if c in '<>:"/\\|?* ' else c for c in nombre)

    def _recalcular_puntajes(self):
        total_p = sum(ev["puntaje"] for ev in self.resultados_criterios)
        max_p = len(RUBRICA_ITEMS_BASE) * 5
        calif = round(total_p * 100 / max_p, 1) if max_p > 0 else 0
        return total_p, max_p, calif

    def _preguntar_aprobacion_puntajes(self):
        if getattr(self, "cancelar_todo", False):
            return

        texto_reporte = self._generar_texto_reporte()

        self._log("\n═══════════════════════════════════════════════════", "reporte")
        self._log("📋 REPORTE DE EVALUACIÓN", "reporte")
        self._log(texto_reporte, "reporte")
        self._log("═══════════════════════════════════════════════════\n", "reporte")

        self.messages.append({"role": "assistant", "content": texto_reporte})

        self.sesion_activa = True
        self.btn_enviar.configure(state="normal")
        self.entry.configure(state="normal")
        self.entry.focus()
        self._log(
            "✏️ Puedes seguir ajustando calificaciones en el chat. "
            "Escribe los cambios que consideres y presiona ENVIAR. "
            "Cuando estés conforme, escribe LISTO para finalizar.",
            "sistema"
        )

    def _procesar_comentarios_con_llm(self, texto_orig: str):
        self._mostrar_progreso(True)
        prompt = (
            "Eres un corrector de estilo. Corrige errores gramaticales y de ortografía en las "
            "siguientes observaciones de un docente, asegurando un tono constructivo y académico.\n"
            "NO agregues información nueva. Mantén la esencia original.\n"
            "Devuelve ÚNICAMENTE el texto corregido, sin prefijos, etiquetas ni comillas.\n\n"
            f"Texto original:\n{texto_orig}"
        )

        resultado = {"texto": None}
        evento = threading.Event()

        def llamada_llm():
            try:
                config_corta = dict(self.llm.config)
                config_corta["timeout"] = self._TIMEOUT_ORTOGRAFIA
                llm_rapido = type(self.llm)(config=config_corta)
                resp = llm_rapido.llamar([{"role": "user", "content": prompt}], json_mode=False)
                texto_corr = resp.strip()
                texto_corr = re.sub(r'^["\u201c\u00ab]|["\u201d\u00bb]$', '', texto_corr).strip()
                texto_corr = re.sub(r'^(?:Texto corregido|Corrección|Corrected text)\s*:\s*', '', texto_corr, flags=re.IGNORECASE).strip()
                resultado["texto"] = texto_corr
            except Exception as e:
                self.after(0, lambda: self._log(f"⚠ Fallo al corregir estilo de comentarios con IA: {e}", "aviso"))
            finally:
                evento.set()

        def esperar_y_continuar():
            completado = evento.wait(timeout=self._TIMEOUT_ORTOGRAFIA + 3)
            if not completado or resultado["texto"] is None:
                self.after(0, lambda: self._log("⚠ Pulido con IA omitido (timeout/error). Aplicando corrector local offline...", "aviso"))
                texto_final = LocalAcademicCorrector.corregir(texto_orig)
            else:
                texto_final = resultado["texto"]
            self.after(0, lambda: self._finalizar_procesamiento_comentarios(texto_final))

        threading.Thread(target=llamada_llm, daemon=True).start()
        threading.Thread(target=esperar_y_continuar, daemon=True).start()

    def _finalizar_procesamiento_comentarios(self, texto_corr: str):
        self._mostrar_progreso(False)
        self.comentarios_adicionales = texto_corr
        self._corregir_ortografia_justificaciones()

    _TIMEOUT_ORTOGRAFIA = 15

    def _corregir_ortografia_justificaciones(self):
        self._mostrar_progreso(True)
        texto_reporte = self._generar_texto_reporte()
        est_tokens = len(texto_reporte) // 4
        self._log(f"✓ Revisando ortografía y redacción del reporte final (~{est_tokens} tokens)...", "sistema")

        prompt = (
            "Corrige exclusivamente errores de ortografía, tildes y gramática del siguiente reporte de evaluación. "
            "NO cambies el significado, NO agregues ni elimines contenido, NO alteres números, puntajes ni encabezados. "
            "Devuelve el texto completo corregido, sin explicaciones ni comentarios.\n\n"
            f"{texto_reporte}"
        )

        resultado = {"texto": None, "error": None}
        evento = threading.Event()

        def llamada_llm():
            try:
                config_corta = dict(self.llm.config)
                config_corta["timeout"] = self._TIMEOUT_ORTOGRAFIA
                llm_rapido = type(self.llm)(config=config_corta)
                resultado["texto"] = llm_rapido.llamar([{"role": "user", "content": prompt}], json_mode=False)
            except Exception as e:
                resultado["error"] = e
            finally:
                evento.set()

        threading.Thread(target=llamada_llm, daemon=True).start()

        def esperar_y_continuar():
            completado = evento.wait(timeout=self._TIMEOUT_ORTOGRAFIA + 3)
            if not completado or resultado["texto"] is None:
                motivo = str(resultado["error"]) if resultado["error"] else "tiempo de espera agotado"
                self.after(0, lambda: self._log(f"⚠ Corrección ortográfica con IA omitida ({motivo}). Aplicando corrector local offline...", "aviso"))
                self._texto_reporte_corregido = LocalAcademicCorrector.corregir(texto_reporte)
            else:
                self._texto_reporte_corregido = resultado["texto"]
            self.after(0, self._finalizar_aprobacion)

        threading.Thread(target=esperar_y_continuar, daemon=True).start()

    def _finalizar_aprobacion(self):
        self._mostrar_progreso(False)
        self.reporte_listo = True
        self.btn_generar_reporte.configure(state="normal")
        self._log("✅ Calificaciones listas y aprobadas. Haz clic en '💾 GENERAR REPORTE FINAL' para guardar.", "sistema")
        self._guardar_automatico()

    def _generar_comentario_final(self):
        total_p, max_p, calif = self._recalcular_puntajes()
        self._log(
            f"\n═══ EVALUACIÓN COMPLETADA ═══\n"
            f"Puntaje Acumulado: {total_p}/{max_p}\n"
            f"Calificación Final: {calif}/100\n",
            "sistema"
        )
        self._mostrar_progreso(True)

        exp_info = self.info_grupo.get("exp", "Experiencia de Laboratorio") if self.info_grupo else "Experiencia de Laboratorio"

        def worker():
            try:
                self.after(0, lambda: self._log("⏳ Analizando consistencia con evaluaciones previas...", "sistema"))

                inconsistencias = self.detectar_inconsistencias_entre_grupos(exp_info)

                comentario = self._construir_comentario_llm(inconsistencias)

                self.after(0, lambda: self._post_evaluacion_final(comentario, exp_info, inconsistencias))
            except Exception as e:
                self.after(0, lambda: self._log(f"✗ Error al generar comentario: {e}", "error"))
                self.after(0, lambda: self._post_evaluacion_final(
                    "Evaluación completada. Revisa los ítems con puntaje bajo para mejorar futuros informes.",
                    exp_info,
                    []
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _construir_comentario_llm(self, inconsistencias: list = None) -> str:
        desc_por_id = {item["id"]: item["desc"] for item in RUBRICA_ITEMS_BASE}
        evals_prev = [h for h in self.evaluaciones_historicas if h.get("mismo_grupo", False)]
        _, _, calif = self._recalcular_puntajes()

        partes = ["PUNTAJES ACTUALES:"]
        for ev in sorted(self.resultados_criterios, key=lambda x: x["id"]):
            desc = desc_por_id.get(ev["id"], f"Ítem {ev['id']}")
            partes.append(f"- {desc}: {ev['puntaje']}/5")
        partes.append(f"Total: {calif}/100")
        partes.append("")

        if evals_prev:
            partes.append("HISTORIAL DE ENTREGAS ANTERIORES DEL GRUPO:")
            hist_por_item = {}
            for h in evals_prev:
                hid = h["id"]
                if hid not in hist_por_item:
                    hist_por_item[hid] = []
                hist_por_item[hid].append(h["puntaje"])
            for cid in sorted(hist_por_item.keys()):
                desc_corta = desc_por_id.get(cid, f"Ítem {cid}")[:60]
                pts_prev = hist_por_item[cid]
                actual = next((ev["puntaje"] for ev in self.resultados_criterios if ev["id"] == cid), None)
                if actual is not None:
                    hist_str = ", ".join(str(p) for p in pts_prev)
                    partes.append(f"- {desc_corta}: antes [{hist_str}], ahora {actual}/5")
            partes.append("")

        if inconsistencias:
            partes.append("COMPARACIÓN CON OTROS GRUPOS:")
            for inc in inconsistencias:
                desc_corta = inc["descripcion"][:60]
                dif = inc["diferencia_maxima"]
                nombres = ", ".join(g["grupo"] for g in inc["grupos_comparados"] if g["grupo"] != "Grupo Actual")
                if nombres:
                    partes.append(f"- En '{desc_corta}', grupos como {nombres} recibieron {dif} pts menos por fallos similares.")
            partes.append("Si aplica, menciona brevemente que esas falencias también se observaron en otros grupos, para dar contexto.")
            partes.append("IMPORTANTE: No inventes falencias. Si no hay datos de otros grupos para un aspecto, no lo menciones.")
            partes.append("")

        partes.append("Escribe 3-4 líneas de comentario final constructivo para el informe de laboratorio.")
        partes.append("REGLAS:")
        partes.append("- Si el grupo mejoró en algo respecto a entregas anteriores, destácalo y felicítalos.")
        partes.append("- Si empeoró en algo que antes dominaban, menciónalo como oportunidad de mejora, no como fracaso.")
        partes.append("- Termina con una frase motivadora que los anime a seguir mejorando.")
        partes.append("- No uses frases rebuscadas ni jerga pomposa. Sé directo y profesional.")
        partes.append("- No menciones números de ítems. Refiérete a los aspectos por su nombre descriptivo.")
        partes.append("- Sin comillas, sin prefijos, sin formato adicional. Solo el texto del comentario.")

        resp = self.llm.llamar([{"role": "user", "content": "\n".join(partes)}])
        return resp.strip().strip('"')

    def _regenerar_comentario_final(self):
        self._log("🔄 Regenerando comentario final...", "sistema")
        self._mostrar_progreso(True)

        def worker():
            try:
                exp_info = self.info_grupo.get("exp", "Experiencia de Laboratorio") if self.info_grupo else "Experiencia de Laboratorio"
                inconsistencias = self.detectar_inconsistencias_entre_grupos(exp_info)
                comentario = self._construir_comentario_llm(inconsistencias)
                self.after(0, lambda c=comentario: self._aplicar_comentario_regenerado(c))
            except Exception as e:
                self.after(0, lambda err=e: self._log(f"✗ Error al regenerar comentario: {err}", "error"))
                self.after(0, lambda: self._mostrar_progreso(False))

        threading.Thread(target=worker, daemon=True).start()

    def _aplicar_comentario_regenerado(self, comentario: str):
        self.comentarios_adicionales = comentario
        self._mostrar_progreso(False)
        self._log(f"📋 COMENTARIO ACTUALIZADO:\n{comentario}", "sistema")

    def _post_evaluacion_final(self, comentario: str, nombre_exp: str, inconsistencias: list):
        self._mostrar_progreso(False)
        self.comentarios_adicionales = comentario
        self.nombre_experiencia_final = nombre_exp

        if inconsistencias:
            self._log(
                f"\n{'='*60}\n"
                f"⚠️  POSIBLE INCONSISTENCIA DE CRITERIO DETECTADA\n"
                f"Se encontraron {len(inconsistencias)} criterio(s) donde el modelo detectó\n"
                f"la misma falencia con puntajes distintos en evaluaciones previas.\n"
                f"Puedes ajustar los ítems afectados en el chat antes de aprobar.\n"
                f"{'='*60}",
                "aviso"
            )
            for inc in inconsistencias:
                bloque = f"\n▶ Criterio {inc['id_criterio']}: {inc['descripcion']}\n"
                if inc.get("razon"):
                    bloque += f"  Motivo: {inc['razon']}\n"
                bloque += f"  Diferencia máxima: {inc['diferencia_maxima']} pts\n"
                for grp in inc['grupos_comparados']:
                    bloque += f"  • {grp['grupo']}: {grp['puntaje']}/5 pts\n"
                    if grp.get('justificacion'):
                        bloque += f"    Obs: {grp['justificacion']}\n"
                bloque += "-" * 60
                self._log(bloque, "aviso")

            self._log("\n✏️ Si deseas corregir algún ítem, indícalo en el chat antes de continuar.\n"
                      "Cuando quieras visualizar el borrador del reporte de evaluación, escribe 'REPORTE'.\n"
                      "Cuando estés totalmente conforme con las notas, escribe 'LISTO' para finalizar.", "sistema")
            self.sesion_activa = True
            self.btn_enviar.configure(state="normal")
            return

        self._preguntar_aprobacion_puntajes()

    def _generar_texto_reporte(self) -> str:
        exp_raw = self.info_grupo["exp"] if self.info_grupo else "Lab"
        exp_num = exp_raw.replace("Lab", "") if isinstance(exp_raw, str) else str(exp_raw)
        
        lineas_est = []
        if self.info_grupo and self.info_grupo.get("estudiantes"):
            for e in self.info_grupo["estudiantes"]:
                ap = e.get("apellido", "").strip()
                nom = e.get("nombre", "").strip()
                rol = e.get("rol", "").strip()
                lineas_est.append(f"{ap}, {nom}, Rol: {rol}")
        nombres_roles = "\n".join(lineas_est) if lineas_est else "Sin integrantes identificados."

        _, _, calif = self._recalcular_puntajes()

        reporte = [
            "REPORTE DE EVALUACIÓN DE INFORME DE LABORATORIO",
            "",
            f"Experiencia {exp_num}: {getattr(self, 'nombre_experiencia_final', 'Experiencia')}",
            "",
            "Estudiantes y Roles (Rol USM):",
            nombres_roles,
            "",
            "-" * 50,
        ]

        secciones_items = {}
        for item in RUBRICA_ITEMS_BASE:
            sec = item["seccion"]
            secciones_items.setdefault(sec, []).append(item)

        evals_map = {ev["id"]: ev for ev in self.resultados_criterios}

        for sec, items in secciones_items.items():
            sec_max = len(items) * 5
            sec_score = sum(evals_map.get(it["id"], {}).get("puntaje", 0) for it in items)

            reporte.append(f"{sec} (Puntaje: {sec_score} / {sec_max})")
            reporte.append("-" * 50)

            for item in items:
                ev = evals_map.get(item["id"], {"puntaje": 0, "justificacion": "No evaluado."})
                p = ev.get("puntaje", 0)
                just = ev.get("justificacion", "")
                
                nivel = "Ausente"
                for k, v in PUNTAJES_MAP.items():
                    if v["puntaje"] == p:
                        nivel = v["descripcion"]
                        break

                reporte.append(f"Criterio: {item['desc']}")
                reporte.append(f"Puntaje: {p} ({nivel})")
                if just:
                    reporte.append(f"Justificación: {just}")
                reporte.append("")

            reporte.append("-" * 50)

        reporte.extend([
            "",
            f"CALIFICACIÓN FINAL (0-100): {calif}",
            "-" * 50,
            "",
            "COMENTARIOS ADICIONALES:",
            self.comentarios_adicionales if self.comentarios_adicionales else (
                "El grupo demuestra un manejo adecuado de los conceptos físicos involucrados y "
                "presenta un análisis estructurado de sus resultados. Se recomienda seguir "
                "fortaleciendo el registro explícito de incertidumbres experimentales y la "
                "justificación detallada de cada fuente de error para alcanzar el nivel de "
                "excelencia en todos los criterios. ¡Sigan así, el progreso es evidente!"
            )
        ])

        return "\n".join(reporte)

    def _guardar_automatico(self):
        try:
            # Sincronizar idempotencia en la base de datos de jurisprudencia
            for ev in self.resultados_criterios:
                if "justificacion" in ev and not ev["justificacion"].startswith("Error de llamada:"):
                    try:
                        self._actualizar_indice_auditoria(ev)
                    except Exception as e:
                        print(f"[DEBUG] Error actualizando auditoría maestro al guardar: {e}")

            nombre_base = self._nombre_base_archivo()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            texto_reporte = self._generar_texto_reporte()

            txt_path = Path.cwd() / f"reporte_{nombre_base}_{ts}.txt"
            txt_path.write_text(texto_reporte, encoding="utf-8")
            self._log(f"✓ Guardado automático de reporte: {txt_path.name}", "sistema")

            json_path = Path.cwd() / f"sesion_{nombre_base}_{ts}.json"
            
            _, _, calif = self._recalcular_puntajes()
            
            # Guardar identidad de estudiantes de forma estructurada para el acta oficial
            datos_integrantes = "No identificado"
            lista_estudiantes = []
            if self.info_grupo:
                datos_integrantes = ", ".join(f"{e.get('nombre')} {e.get('apellido')}" for e in self.info_grupo.get("estudiantes", []))
                lista_estudiantes = self.info_grupo.get("estudiantes", [])

            datos_sesion = {
                "grupo": nombre_base,
                "fecha": ts,
                "total": sum(ev["puntaje"] for ev in self.resultados_criterios),
                "nota": calif,
                "integrantes": datos_integrantes,
                "estudiantes": lista_estudiantes,
                "criterios": self.resultados_criterios,
                "comentario_general": self.comentarios_adicionales,
                "nombre_experiencia": getattr(self, 'nombre_experiencia_final', 'Experiencia')
            }
            json_path.write_text(json.dumps(datos_sesion, ensure_ascii=False, indent=2), encoding="utf-8")
            self._log(f"✓ Guardado automático de sesión: {json_path.name}", "sistema")

        except Exception as e:
            self._log(f"✗ Error en guardado automático: {e}", "error")

    def _generar_reporte(self):
        carpeta = filedialog.askdirectory(title="Selecciona la carpeta para exportar los reportes")
        if not carpeta:
            return

        nombre_base = self._nombre_base_archivo()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        texto_reporte = getattr(self, "_texto_reporte_corregido", None) or self._generar_texto_reporte()

        txt_path = Path(carpeta) / f"reporte_{nombre_base}_{ts}.txt"
        json_path = Path(carpeta) / f"sesion_{nombre_base}_{ts}.json"

        try:
            txt_path.write_text(texto_reporte, encoding="utf-8")
            
            _, _, calif = self._recalcular_puntajes()
            
            # Guardar identidad de estudiantes de forma estructurada para el acta oficial
            datos_integrantes = "No identificado"
            lista_estudiantes = []
            if self.info_grupo:
                datos_integrantes = ", ".join(f"{e.get('nombre')} {e.get('apellido')}" for e in self.info_grupo.get("estudiantes", []))
                lista_estudiantes = self.info_grupo.get("estudiantes", [])

            datos_sesion = {
                "grupo": nombre_base,
                "fecha": ts,
                "total": sum(ev["puntaje"] for ev in self.resultados_criterios),
                "nota": calif,
                "integrantes": datos_integrantes,
                "estudiantes": lista_estudiantes,
                "criterios": self.resultados_criterios,
                "comentario_general": self.comentarios_adicionales,
                "nombre_experiencia": getattr(self, 'nombre_experiencia_final', 'Experiencia')
            }
            json_path.write_text(json.dumps(datos_sesion, ensure_ascii=False, indent=2), encoding="utf-8")

            self._log(f"\n✅ Reporte exportado a: {txt_path}", "reporte")
            messagebox.showinfo("Exportado", f"Se guardó el reporte exitosamente:\n{txt_path.name}")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo escribir en el directorio seleccionado: {e}")

    def detectar_inconsistencias_entre_grupos(self, exp_actual: str) -> list[dict]:
        """Detecta inconsistencias reales de calificación de forma 100% residente en memoria."""
        inconsistencias = []
        if not self.evaluaciones_historicas:
            return []

        evals_actuales = {ev["id"]: ev for ev in self.resultados_criterios}
        criterio_a_desc = {c["id"]: c["desc"] for c in RUBRICA_ITEMS_BASE}

        for cid, ev_act in evals_actuales.items():
            pts_act = ev_act.get("puntaje", 0)
            just_act = ev_act.get("justificacion", "").strip()

            candidatos = []
            for hist in self.evaluaciones_historicas:
                if hist["id"] != cid:
                    continue
                pts_prev = hist["puntaje"]
                just_prev = hist["justificacion"].strip()
                if abs(pts_act - pts_prev) >= 2 and just_act and just_prev:
                    candidatos.append({
                        "grupo": hist["grupo"],
                        "puntaje": pts_prev,
                        "justificacion": just_prev,
                    })

            if not candidatos:
                continue

            bloques = "\n".join(
                f"- Evaluación «{c['grupo']}» ({c['puntaje']}/5 pts): {c['justificacion']}"
                for c in candidatos
            )
            prompt_semantico = (
                f"Criterio evaluado: «{criterio_a_desc.get(cid, 'Criterio')}»\n\n"
                f"Evaluación actual (Grupo Actual, {pts_act}/5 pts): {just_act}\n\n"
                f"Evaluaciones previas de la misma experiencia:\n{bloques}\n\n"
                "Determina si alguna de las evaluaciones previas describe EXACTAMENTE la misma "
                "falencia o error que la evaluación actual, y sin embargo tiene un puntaje diferente "
                "(diferencia ≥ 2 puntos). Eso sería una inconsistencia real de criterio.\n"
                "Si las justificaciones reflejan errores DISTINTOS (aunque el puntaje difiera), "
                "NO es inconsistencia.\n\n"
                "Responde ÚNICAMENTE con un JSON con esta estructura exacta, sin texto adicional:\n"
                '{"es_inconsistencia": true/false, "razon": "explicación breve en una oración", '
                '"grupos_inconsistentes": ["nombre_grupo_1", ...]}'
            )

            try:
                respuesta = self.llm.llamar([
                    {"role": "system", "content": "Eres un auditor de consistencia de rúbricas de laboratorio. Responde solo con JSON válido."},
                    {"role": "user", "content": prompt_semantico},
                ], json_mode=True)
                respuesta_limpia = re.sub(r"```(?:json)?|```", "", respuesta).strip()
                resultado = json.loads(respuesta_limpia)

                if resultado.get("es_inconsistencia"):
                    grupos_inconsistentes = resultado.get("grupos_inconsistentes", [])
                    grupos_comp = [
                        {"grupo": "Grupo Actual", "puntaje": pts_act, "justificacion": just_act}
                    ] + [c for c in candidatos if c["grupo"] in grupos_inconsistentes]

                    inconsistencias.append({
                        "id_criterio": cid,
                        "descripcion": criterio_a_desc.get(cid, "Criterio"),
                        "diferencia_maxima": max(
                            (abs(pts_act - c["puntaje"]) for c in candidatos if c["grupo"] in grupos_inconsistentes),
                            default=0
                        ),
                        "razon": resultado.get("razon", ""),
                        "grupos_comparados": grupos_comp,
                    })

            except Exception as e:
                self._log(f"⚠ No se pudo verificar consistencia semántica del criterio {cid}: {e}", "aviso")

        return inconsistencias

# ══════════════════════════════════════════════════════════════════════════════
# ACTA Y CONSOLIDADO
# ══════════════════════════════════════════════════════════════════════════════

    def _normalizar_texto_fuzzy(self, texto: str) -> list[str]:
        """Normaliza texto eliminando acentos, puntuaciones y convirtiendo a minúsculas."""
        if not texto: 
            return []
        texto_norm = unidecode.unidecode(texto).lower()
        return re.findall(r'\b\w+\b', texto_norm)

    def _coincide_fuzzy(self, estudiante_csv: dict, integrantes_str: str) -> bool:
        """Determina correspondencia difusa comparando tokens de nombres y apellidos."""
        int_words = self._normalizar_texto_fuzzy(integrantes_str)
        if not int_words: 
            return False
            
        first_name_words = self._normalizar_texto_fuzzy(estudiante_csv.get("nombre", ""))
        last_name_words = self._normalizar_texto_fuzzy(estudiante_csv.get("apellido", ""))
        
        # Retorna verdadero si coincide al menos una palabra del nombre y una del apellido
        tiene_nombre = any(w in int_words for w in first_name_words)
        tiene_apellido = any(w in int_words for w in last_name_words)
        return tiene_nombre and tiene_apellido

    def _generar_acta_consolidada(self):
        rutas_lista = list(self.ruta_otros) + list(self.ruta_prev_grupo)

        # Si no hay reportes cargados de forma manual en los listbox, escanear la carpeta de trabajo
        if not rutas_lista:
            self._log("ℹ️ Buscando archivos de sesión locales en el directorio para autocompletar consolidación...", "sistema")
            rutas_lista = [str(f) for f in Path.cwd().glob("sesion_*.json")]

        sesion_temp_path = None
        if self.resultados_criterios:
            try:
                ts_tmp = datetime.now().strftime("%Y%m%d_%H%M%S")
                nombre_base_tmp = self._nombre_base_archivo()
                sesion_temp_path = Path.cwd() / f"_tmp_sesion_{nombre_base_tmp}_{ts_tmp}.json"
                _, _, calif_tmp = self._recalcular_puntajes()
                datos_tmp = {
                    "grupo": nombre_base_tmp,
                    "fecha": ts_tmp,
                    "total": sum(ev["puntaje"] for ev in self.resultados_criterios),
                    "nota": calif_tmp,
                    "criterios": self.resultados_criterios,
                    "comentario_general": self.comentarios_adicionales,
                    "nombre_experiencia": getattr(self, 'nombre_experiencia_final', 'Experiencia')
                }
                sesion_temp_path.write_text(json.dumps(datos_tmp, ensure_ascii=False, indent=2), encoding="utf-8")
                rutas_lista = [str(sesion_temp_path)] + rutas_lista
                self._log("✓ Sesión activa incluida en el consolidado.", "sistema")
            except Exception as e:
                self._log(f"⚠ No se pudo incluir la sesión activa en el consolidado: {e}", "aviso")

        if not rutas_lista:
            messagebox.showwarning("Faltan Archivos", "Ingresa reportes en los campos opcionales del panel izquierdo para generar el consolidado.")
            return

        self._log("\n── GENERANDO ACTA CONSOLIDADA Y REPORTE DE FALENCIAS ──", "sistema")
        self._mostrar_progreso(True)
        datos_consolidados = {}
        justificaciones_totales = []

        for r in rutas_lista:
            p_ruta = Path(r)
            if not p_ruta.exists():
                continue
            
            try:
                ext = p_ruta.suffix.lower()
                m_grupo = re.search(r'grupo_(\d+)|([a-zA-Z\-_0-9]+)$', p_ruta.stem, re.I)
                grupo_id = m_grupo.group(0) if m_grupo else p_ruta.stem
                
                datos_consolidados.setdefault(grupo_id, {
                    "grupo": grupo_id, "integrantes": "No identificado", "nota": 0, "total": 0, "justs": []
                })
                e = datos_consolidados[grupo_id]

                if ext == ".json":
                    data = json.loads(p_ruta.read_text(encoding="utf-8"))
                    e["integrantes"] = data.get("integrantes", data.get("grupo", "No identificado"))
                    e["total"] = data.get("total", 0)
                    e["nota"] = data.get("nota", 0)
                    for crit in data.get("criterios", []):
                        cid = crit.get("id")
                        pts = crit.get("puntaje", 0)
                        just = crit.get("justificacion", "")
                        e[f"c{cid}"] = pts
                        e["justs"].append(f"[{cid}]: {just}")

                elif ext == ".txt":
                    texto = p_ruta.read_text(encoding="utf-8")
                    m_int = re.search(r'Estudiantes y Roles \(Rol USM\):\s*\n(.*?)\n\n', texto, re.DOTALL)
                    if m_int:
                        e["integrantes"] = ", ".join(l.split(", Rol:")[0].strip() for l in m_int.group(1).splitlines() if l.strip())
                    
                    m_tot = re.search(r'CALIFICACIÓN FINAL \(0-100\):\s*(\d+)', texto)
                    if m_tot:
                        e["nota"] = int(m_tot.group(1))
                        e["total"] = int(m_tot.group(1))

                    criterios_match = re.findall(r'Criterio:\s*(.*?)\nPuntaje:\s*(\d+)\s*\((.*?)\)\nJustificación:\s*(.*?)\n', texto, re.DOTALL)
                    desc_a_id = {item["desc"].lower(): item["id"] for item in RUBRICA_ITEMS_BASE}
                    for desc, pts, nivel, just in criterios_match:
                        cid = desc_a_id.get(desc.strip().lower())
                        if cid:
                            e[f"c{cid}"] = int(pts)
                            e["justs"].append(f"[{cid}]: {just.strip()}")

            except Exception as ex:
                self._log(f"⚠ Error procesando consolidado de {p_ruta.name}: {ex}", "error")

        if sesion_temp_path and sesion_temp_path.exists():
            try:
                sesion_temp_path.unlink()
            except Exception:
                pass

        if not datos_consolidados:
            self._mostrar_progreso(False)
            messagebox.showerror("Error", "No se pudieron consolidar datos.")
            return

        # ── ESTRATEGIA DE CRUCE DIFUSO CON NÓMINA OFICIAL (FIS3149) ──
        csv_filas = []
        usar_nomina_oficial = len(self.nomina_estudiantes) > 0

        if usar_nomina_oficial:
            self._log("ℹ️ Nómina cargada en caliente detectada. Cruzando registros con correspondencia difusa...", "sistema")
            
            for est in self.nomina_estudiantes:
                grupo_id = ""
                nota_final = ""
                c_vals = [""] * 20
                eval_encontrada = None
                
                # 1. Intentar cruzar por RUT/Rol de forma exacta
                for gid, e in datos_consolidados.items():
                    est_detalles = e.get("estudiantes", [])
                    if est_detalles and isinstance(est_detalles, list):
                        for est_det in est_detalles:
                            r1 = re.sub(r'\D', '', str(est_det.get("rol", "")))
                            r2 = re.sub(r'\D', '', str(est.get("rol", "")))
                            if r1 and r2 and r1 == r2:
                                eval_encontrada = e
                                grupo_id = gid
                                break
                    if eval_encontrada:
                        break

                # 2. Fallback: Búsqueda fuzzy por coincidencia semántica de nombres
                if not eval_encontrada:
                    for gid, e in datos_consolidados.items():
                        integrantes_str = e.get("integrantes", "")
                        if self._coincide_fuzzy(est, integrantes_str):
                            eval_encontrada = e
                            grupo_id = gid
                            break
                
                if eval_encontrada:
                    nota_final = str(eval_encontrada.get("nota", 0)).replace(".", ",")
                    for idx_c in range(1, 21):
                        c_vals[idx_c - 1] = str(eval_encontrada.get(f"c{idx_c}", 0)).replace(".", ",")
                
                nombre_completo = f"{est['apellido']}, {est['nombre']}"
                fila_csv = f"{nombre_completo}; {est.get('rol', '')}; {grupo_id}; {nota_final}; " + "; ".join(c_vals)
                csv_filas.append(fila_csv)
                
                if eval_encontrada and eval_encontrada["justs"]:
                    justificaciones_totales.append(f"=== ESTUDIANTE {nombre_completo} (Grupo: {grupo_id}) ===\n" + "\n".join(eval_encontrada["justs"]))
            
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = Path.cwd() / f"acta_consolidada_oficial_{ts}.csv"
            
            cabecera_criterios = "; ".join(f"C{i}" for i in range(1, 21))
            csv_path.write_text(
                f"Estudiante; Rol/RUT; Grupo Asignado; Calificacion (0-100); {cabecera_criterios}\n" + "\n".join(csv_filas),
                encoding="utf-8"
            )
        else:
            # Fallback original: Solo exportar lo que fue evaluado de forma cruda
            for gid, e in sorted(datos_consolidados.items()):
                pts_criterios = [str(e.get(f"c{i}", 0)).replace(".", ",") for i in range(1, 21)]
                csv_filas.append(f"{e['integrantes']}; {e['grupo']}; {str(e['nota']).replace('.', ',')}; " + "; ".join(pts_criterios))
                justificaciones_totales.append(f"=== GRUPO {gid} ({e['integrantes']}) ===\n" + "\n".join(e["justs"]))

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_path = Path.cwd() / f"acta_laboratorio_{ts}.csv"
            
            cabecera_criterios = "; ".join(f"C{i}" for i in range(1, 21))
            csv_path.write_text(
                f"Integrantes; Horario/Grupo; Calificacion (0-100); {cabecera_criterios}\n" + "\n".join(csv_filas),
                encoding="utf-8"
            )
        self._log(f"✓ Acta CSV consolidada guardada: {csv_path.name}", "sistema")

        if not justificaciones_totales:
            self._mostrar_progreso(False)
            messagebox.showinfo("Consolidado Listo", f"Planilla generada:\n- {csv_path.name}")
            return

        def worker():
            try:
                prompt_global = (
                    "Eres un coordinador docente de Física. Analiza el consolidado de justificaciones de calificaciones "
                    "de múltiples grupos para un informe de laboratorio y genera un REPORTE GLOBAL DE FALENCIAS Y FORTALEZAS COMUNES.\n\n"
                    "REGLAS:\n"
                    "- No menciones cada grupo uno por uno.\n"
                    "- Agrupa por temas de debilidad común (ej: 'Errores recurrentes en propagación de incertidumbres', 'Problemas en cifras significativas').\n"
                    "- Redacta en tono formal e incluye sugerencias didácticas concretas para el pizarrón.\n\n"
                    f"DATOS DE EVALUACIÓN:\n\n" + "\n\n".join(justificaciones_totales)
                )
                
                resp = self.llm.llamar([
                    {"role": "system", "content": "Analista de resultados didácticos de ciencias físicas."},
                    {"role": "user", "content": prompt_global}
                ], json_mode=False)
                
                report_path = Path.cwd() / f"reporte_global_falencias_laboratorio_{ts}.txt"
                report_path.write_text(resp, encoding="utf-8")
                
                self.after(0, lambda: self._log(f"✓ Reporte global de falencias guardado: {report_path.name}", "sistema"))
                self.after(0, lambda: messagebox.showinfo("Consolidado Listo", f"Planillas Generadas:\n- {csv_path.name}\n- {report_path.name}"))
            except Exception as e:
                self.after(0, lambda: self._log(f"✗ Fallo al generar reporte global: {e}", "error"))
                self.after(0, lambda: messagebox.showinfo("Consolidado Parcial", f"CSV generado:\n- {csv_path.name}\n\nReporte global no disponible: {e}"))
            finally:
                self.after(0, lambda: self._mostrar_progreso(False))

        threading.Thread(target=worker, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# NUEVA EVALUACIÓN
# ══════════════════════════════════════════════════════════════════════════════

    def _nueva_evaluacion(self):
        if self.messages:
            if not messagebox.askyesno("Nueva Evaluación", "¿Deseas iniciar una nueva evaluación?\nSe descartará el historial de la sesión activa."):
                return

        self.messages = []
        self.contexto_base = []
        self.chat_history = []
        self.archivos = {}
        self.sesion_activa = False
        self.info_grupo = None
        self.resultados_criterios = []
        self.evaluaciones_historicas = []
        self.reporte_listo = False
        self.comentarios_adicionales = ""
        self.historial_matriz = {}
        self._texto_reporte_corregido = None
        self.visual_esquemas = ""
        self.visual_graficos = ""
        self.visual_tablas = ""
        self._restricciones_evaluador = []
        self.cancelar_evaluacion = False
        self.cancelar_todo = False

        self.ruta_guia.set("")
        self.ruta_informe.set("")
        self.ruta_prev_grupo.clear()
        self.lb_prev_grupo.delete(0, "end")
        self.ruta_otros.clear()
        self.lb_otros.delete(0, "end")
        self.lbl_grupo.configure(text="Grupo: (no configurado)")
        
        self.chat_area.configure(state="normal")
        self.chat_area.delete("1.0", "end")
        self.chat_area.configure(state="disabled")
        
        self.btn_verificar.configure(state="normal")
        self.btn_evaluar.configure(state="disabled")
        self.btn_generar_reporte.configure(state="disabled")
        self.btn_consolidado.configure(state="disabled")
        self.btn_enviar.configure(state="disabled")
        
        self._set_status("")
        self._log("Nueva sesión iniciada. Configura el grupo y carga los archivos.", "sistema")


# ══════════════════════════════════════════════════════════════════════════════
# 15. INICIO
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    wizard = WizardConfig()
    wizard.mainloop()
    
    if wizard.result is None:
        sys.exit(0)

    llm_service = LLMService(config=wizard.result)
    app = EvaluadorApp(llm_service=llm_service, parser=DocumentParser)
    app.mainloop()