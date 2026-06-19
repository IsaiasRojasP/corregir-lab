# Corregir Lab — Corrector Asistido por IA de Informes de Laboratorio

Herramienta para la evaluación automatizada y asistida de informes de laboratorio de física, con soporte para historial del grupo, auditoría entre grupos y generación de comentarios constructivos.

## Características principales

- **Evaluación criterio a criterio** — El modelo analiza cada ítem de la rúbrica independientemente
- **Historial del mismo grupo** — Carga correcciones previas del grupo y las usa como contexto para comparar progreso (mejoras, retrocesos)
- **Auditoría entre grupos** — Detecta inconsistencias semánticas cruzadas con evaluaciones de otros grupos que presentaron fallos similares
- **Comentarios constructivos** — Genera un párrafo final motivador basado en puntajes reales y trayectoria histórica del grupo
- **Chat interactivo** — Ajusta calificaciones, reescribe comentarios (`REESCRIBE COMENTARIO`) y visualiza reportes (`REPORTE`) sin reiniciar la evaluación
- **Panel scrollable** — Botones inferiores siempre accesibles con soporte de rueda del mouse
- **Modelo dinámico** — Cambia el LLM en caliente desde el dropdown sin reiniciar
- **Timeout ajustable** — Spinbox 10–600s para controlar tiempos de respuesta

## Instalación

```bash
pip install PyMuPDF requests unidecode
```

## Uso

```bash
python corregir_lab_v9.py
```

1. Configura el grupo y carga los archivos (guía, informe PDF)
2. Opcionalmente agrega correcciones previas del mismo grupo y de otros grupos
3. Presiona **EVALUAR CRITERIOS** para iniciar la evaluación automática
4. Ajusta calificaciones en el chat si es necesario
5. Escribe `REPORTE` para visualizar el borrador o `LISTO` para finalizar

## Archivos de historial compatibles

El sistema carga automáticamente correcciones previas en formato:
- **JSON** — archivo exportado por una sesión anterior del evaluador
- **TXT** — formato con criterios, puntajes y justificaciones extraíbles por regex

## Licencia

Este proyecto está licenciado bajo la [GPLv3](LICENSE). Cualquier derivación debe mantener esta licencia y el aviso de copyright original.
