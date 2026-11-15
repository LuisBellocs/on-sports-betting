# Modelo de marcadores de LaLiga

Predice la probabilidad de cada marcador exacto de LaLiga y, a partir de ahí, el over/under, el 1X2 y el hándicap asiático, para compararlos con las cuotas. GitHub reentrena el modelo solo, guarda cada versión y publica las predicciones para que todo el equipo vea lo mismo.

- **[RESULTADOS.md](RESULTADOS.md)**: métricas de la versión vigente, comparación con el mercado e historial de versiones.
- **[predicciones/](predicciones/README.md)**: todas las predicciones, las más recientes primero.
- **[Versiones (Releases)](../../releases)**: cada modelo entrenado, con su código y su informe. Ninguna se borra.

## Qué pasa automáticamente

| Cuándo | Qué hace GitHub | Dónde queda |
|---|---|---|
| Martes y viernes, 02:00 (CDMX) | Descarga los datos de football-data. Si hay resultados nuevos, reentrena y crea una versión nueva. Si hay próximos partidos con cuotas, los predice. | Versión nueva en Releases, `RESULTADOS.md`, `predicciones/AAAA-MM-DD_proximos/` |
| Alguien sube un CSV a `partidos/` | Predice esos partidos con la versión más reciente | `predicciones/<nombre del archivo>/` |
| Alguien cambia `ajustes.csv` o `modelo_laliga.py` | Reentrena y crea una versión nueva | Releases y `RESULTADOS.md` |
| A mano (pestaña **Actions** → workflow → **Run workflow**) | Reentrena, o predice un archivo con la versión que elijas | Igual que arriba |

Cada versión se llama `modelo-AAAAMMDD-HHMM` e incluye:

- `modelo_laliga.pkl`: el modelo entrenado.
- `modelo_laliga.py`: el código exacto con que se entrenó.
- `informe_entrenamiento.json` y `salida_entrenamiento.txt`: las métricas.
- `entorno.txt`: las versiones de las librerías.

## Instalación (una sola vez)

Desde la carpeta con los archivos del modelo:

```powershell
# repositorio nuevo en tu cuenta (privado)
pwsh -ExecutionPolicy Bypass -File .\configurar_github.ps1 -Repo laliga-modelo

# repositorio que ya existe, por ejemplo uno que te compartieron
pwsh -ExecutionPolicy Bypass -File .\configurar_github.ps1 -Repo su-usuario/su-repositorio
```

Con un repositorio que ya existe, el script:

- Exige permiso de escritura (*Write*) en ese repositorio.
- No borra nada. Si ya hay un `README.md`, el del modelo queda como `README_modelo.md`.
- Se adapta a la rama principal, se llame `main` o `master`.
- Si esa rama está protegida, abre un pull request para que el dueño lo apruebe.

## Uso diario

Hace falta [Git](https://git-scm.com/download/win), [GitHub CLI](https://cli.github.com) (`winget install GitHub.cli`) y Python 3.12. La primera vez:

```powershell
gh auth login
gh repo clone <usuario>/laliga-modelo
cd laliga-modelo
python -m pip install -r requirements.txt
```

**Ver lo último:** `git pull`, o ábrelo directamente en GitHub.

**Predecir una jornada:**

1. Copia un CSV a `partidos/`, por ejemplo `partidos/jornada10_2627.csv`. El formato está en [partidos/README.md](partidos/README.md).
2. Ejecuta `.\subir_cambios.ps1`.
3. En unos 5 minutos, los resultados aparecen en `predicciones/jornada10_2627/`.

**Cargar lesiones, rotaciones o contexto:** edita `ajustes.csv` y ejecuta `.\subir_cambios.ps1`. Se reentrena y se crea una versión nueva.

**Usar un modelo en tu computadora:**

```powershell
.\obtener_modelo.ps1                                # el más reciente
.\obtener_modelo.ps1 -Lista                         # ver todas las versiones
.\obtener_modelo.ps1 -Version modelo-20261023-0800  # una versión anterior
```

**Predecir con una versión anterior en GitHub:** ve a **Actions** → **Predecir partidos** → **Run workflow**, e indica el archivo y la versión.

## Portafolio de apuestas

Cada vez que se predice un archivo de partidos, se genera también `portafolio.md` en la misma carpeta de `predicciones/`:

1. **Selección:** solo entran apuestas con valor esperado (VE) de al menos 3 %. El VE de cada una es exacto: se calcula con la matriz de marcadores del modelo para over/under 2.5, 1X2 y hándicap asiático.
2. **Prudencia:** el VE con que se decide mezcla al modelo con el mercado (`peso_modelo`, 0.3 por defecto). En el backtest, confiar solo en el modelo perdió dinero, porque justo donde más discrepa del mercado suele equivocarse. Además, el VE debe ser positivo en todo el intervalo de confianza del modelo.
3. **Montos:** Kelly fraccional (¼) optimizado para todas las apuestas a la vez. Así se respeta la correlación entre apuestas del mismo partido; por ejemplo, over 2.5 y victoria local suelen ir juntos. Ninguna apuesta pasa del 5 % del bankroll, y la jornada completa no pasa del 25 %.
4. **Riesgo:** una simulación Monte Carlo de 20,000 escenarios da la probabilidad de ganar, el VaR y el CVaR al 95 %, y la ganancia esperada si el mercado tuviera razón.

Si ninguna apuesta cumple los criterios, el portafolio lo dice: no apostar también es una decisión.

**Backtest:** cada entrenamiento simula la estrategia en el periodo de prueba, jornada a jornada, con las cuotas de apertura reales, y deja el resultado en `historial/backtest/backtest.md`. Incluye ganancia, caída máxima, CLV (si las cuotas tomadas fueron mejores que las de cierre), significancia estadística y sensibilidad al peso del modelo. Se ignoran las cuotas imposibles, que a veces vienen con errores en los datos.

**Configuración:** edita `portafolio.json` y súbelo con `.\subir_cambios.ps1`. Aplica desde la siguiente predicción:

| Parámetro | Por defecto | Qué hace |
|---|---|---|
| `bankroll` | 1000 | Capital, en las unidades que quieras |
| `kelly_fraccion` | 0.25 | Fracción del Kelly óptimo |
| `ev_minimo` | 0.03 | VE mínimo para apostar |
| `peso_modelo` | 0.3 | 1 = solo el modelo; 0 = solo el mercado |
| `exigir_intervalo` | true | El VE debe ser positivo en todo el intervalo del modelo |
| `max_por_apuesta`, `max_exposicion` | 0.05, 0.25 | Topes por apuesta y por jornada |
| `cuota_min`, `cuota_max` | 1.3, 6.0 | Rango de cuotas aceptadas |
| `mercados` | los tres | `over_under`, `1x2`, `handicap` |

En tu computadora: `python modelo_laliga.py backtest` repite el backtest con el modelo guardado y tu `portafolio.json`.

## Estructura

| Ruta | Contenido |
|---|---|
| `modelo_laliga.py` | El modelo (entrenar, predecir, en vivo, recalibrar) |
| `ajustes.csv` | Contexto por partido: lesiones, partidos extra, rotaciones, árbitro… |
| `portafolio.json` | Bankroll y criterios del portafolio de apuestas |
| `datos/` | CSV de football-data (se actualizan solos) |
| `partidos/` | Archivos de partidos a predecir (los de `partidos/auto/` los genera GitHub) |
| `predicciones/` | Resultados: `resumen.md`, gráficas y matrices de marcadores |
| `historial/metricas.csv` | Métricas de todas las versiones |
| `historial/backtest/` | Backtest de la estrategia de apuestas (se actualiza en cada entrenamiento) |
| `herramientas/` | Scripts que usan los workflows |
| `.github/workflows/` | Las automatizaciones (en la carpeta original vienen en `flujos_github/`; `configurar_github.ps1` las mueve) |

## Notas

- **Permisos:** para que otra persona pueda subir partidos o ajustes, invítala en **Settings → Collaborators**. Con permiso de lectura (*Read*) solo puede ver.
- **Costo:** en un repositorio privado, GitHub da 2,000 minutos de Actions al mes gratis. Un entrenamiento tarda entre 5 y 10 minutos, y una jornada de predicciones unos 5.
- **Si football-data falla**, el entrenamiento usa la última copia guardada en `datos/` y lo avisa en el resumen de la ejecución.
- **El modelo no garantiza ganancias.** Revisa en `RESULTADOS.md` si supera al mercado de cierre antes de usarlo para apostar.
