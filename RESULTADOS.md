# Resultados del modelo

**Versión vigente:** [modelo-20261109-2156](https://github.com/LuisBellocs/on-sports-betting/releases/tag/modelo-20261109-2156)  
**Entrenado:** 2026-11-09 21:56 · **Datos hasta:** 2026-11-08 · **Partidos:** 1199 · **Métrica de forma:** xg

Descarga el modelo de esta versión, o de cualquier versión anterior, en la página de [versiones](https://github.com/LuisBellocs/on-sports-betting/releases).

## En pocas palabras

- **Over 2.5 contra el cierre del mercado:** el mercado es mejor (log-loss 0.6773 contra 0.6662, p = 0.029).
- **1X2 contra el cierre del mercado:** el mercado es mejor (log-loss 0.9784 contra 0.9531, p < 0.001).
- **Sesgo del over 2.5 en prueba:** el modelo esperaba 51.4% y ocurrió 52.4%.
- **Consenso entre modelos:** RegresionPoisson + DixonColes + GradientBoosting + Bayesiano — alerta SIN_CONSENSO.

Todas las métricas se miden en el 30 % más reciente de los partidos, reentrenando jornada a jornada. Más bajo es mejor.

## Métricas por modelo

| Modelo | Log-loss marcador | Log-loss over 2.5 | Brier over 2.5 | Log-loss 1X2 | RPS 1X2 |
|---|---|---|---|---|---|
| DixonColes | 2.8739 | 0.6826 | 0.2446 | 0.9748 | 0.2007 |
| RegresionPoisson | 2.8707 | 0.6804 | 0.2440 | 0.9858 | 0.2036 |
| GradientBoosting | 2.9022 | 0.7010 | 0.2533 | 0.9934 | 0.2044 |
| Bayesiano | 2.8766 | 0.6802 | 0.2434 | 0.9949 | 0.2040 |
| Ensamble | 2.8546 | 0.6773 | 0.2422 | 0.9784 | 0.1999 |
| Base (promedio liga) | 2.9783 | 0.6956 | 0.2512 | 1.0573 | 0.2261 |
| Mercado apertura |  | 0.6694 | 0.2386 | 0.9571 | 0.1944 |
| Mercado cierre |  | 0.6662 | 0.2372 | 0.9531 | 0.1931 |

## Ensamble contra el mercado (mismos partidos)

| Mercado | Métrica | Partidos | Ensamble | Mercado | Diferencia | p (Wilcoxon) | Veredicto |
|---|---|---|---|---|---|---|---|
| Mercado apertura | Log-loss over 2.5 | 378 | 0.6773 | 0.6694 | +0.0079 | 0.199 | sin diferencia significativa |
| Mercado apertura | Brier over 2.5 | 378 | 0.2422 | 0.2386 | +0.0036 | 0.242 | sin diferencia significativa |
| Mercado apertura | Log-loss 1X2 | 378 | 0.9784 | 0.9571 | +0.0213 | < 0.001 | el mercado es mejor |
| Mercado apertura | RPS 1X2 | 378 | 0.1999 | 0.1944 | +0.0055 | < 0.001 | el mercado es mejor |
| Mercado cierre | Log-loss over 2.5 | 378 | 0.6773 | 0.6662 | +0.0110 | 0.029 | el mercado es mejor |
| Mercado cierre | Brier over 2.5 | 378 | 0.2422 | 0.2372 | +0.0050 | 0.039 | el mercado es mejor |
| Mercado cierre | Log-loss 1X2 | 378 | 0.9784 | 0.9531 | +0.0253 | < 0.001 | el mercado es mejor |
| Mercado cierre | RPS 1X2 | 378 | 0.1999 | 0.1931 | +0.0068 | < 0.001 | el mercado es mejor |

## Calibrador de goles

Factor que multiplica los goles esperados de cada modelo (1 = sin corrección).

| DixonColes | RegresionPoisson | GradientBoosting | Bayesiano |
|---|---|---|---|
| 1.037 | 0.988 | 1.050 | 1.066 |

## Calibración del over 2.5 (ensamble)

| Tramo | Partidos | Probabilidad media | Frecuencia real |
|---|---|---|---|
| (0.25, 0.3] | 2 | 28.7% | 50.0% |
| (0.3, 0.35] | 12 | 33.9% | 41.7% |
| (0.35, 0.4] | 29 | 37.6% | 24.1% |
| (0.4, 0.45] | 53 | 42.7% | 47.2% |
| (0.45, 0.5] | 83 | 47.7% | 50.6% |
| (0.5, 0.55] | 81 | 52.5% | 58.0% |
| (0.55, 0.6] | 52 | 57.4% | 50.0% |
| (0.6, 0.65] | 31 | 62.2% | 64.5% |
| (0.65, 0.7] | 15 | 67.4% | 80.0% |
| (0.7, 0.75] | 15 | 72.1% | 60.0% |
| (0.75, 0.8] | 3 | 76.3% | 66.7% |
| (0.8, 0.85] | 2 | 83.1% | 100.0% |

## Historial de versiones

| Versión | Entrenado | Datos hasta | Partidos | Log-loss marcador | Log-loss over 2.5 | Cierre over 2.5 | Log-loss 1X2 | Over 2.5 esperado / real |
|---|---|---|---|---|---|---|---|---|
| [modelo-20261109-2156](https://github.com/LuisBellocs/on-sports-betting/releases/tag/modelo-20261109-2156) | 2026-11-09 21:56 | 2026-11-08 | 1199 | 2.8546 | 0.6773 | 0.6662 | 0.9784 | 51.4% / 52.4% |
