# Partidos a predecir

Cada CSV que subas a esta carpeta se predice automáticamente con la versión más reciente del modelo. Los resultados quedan en `predicciones/<nombre del archivo>/`.

- Usa nombres sin espacios, por ejemplo `jornada10_2627.csv`.
- Si vuelves a subir el mismo archivo con cambios (por ejemplo, con cuotas nuevas), la predicción se rehace.
- `partidos/auto/` lo llena GitHub con los próximos partidos de football-data. No lo edites.

## Formato

```csv
Div,Date,Time,HomeTeam,AwayTeam,Avg>2.5,Avg<2.5,AHh,AvgAHH,AvgAHA
SP1,24/10/2026,21:00,Real Madrid,Barcelona,1.72,2.10,-0.25,1.95,1.92
SP1,25/10/2026,14:00,Celta,Getafe,,,,,
```

| Columna | Obligatoria | Qué es |
|---|---|---|
| `Date` | sí | Fecha del partido, en formato dd/mm/aaaa |
| `HomeTeam`, `AwayTeam` | sí | Nombres exactamente como en football-data (tabla de abajo) |
| `Div` | no | `SP1` |
| `Time` | no | Hora |
| `Avg>2.5`, `Avg<2.5` | no | Cuotas decimales de over y under 2.5. Si están, se calcula el valor esperado |
| `AHh`, `AvgAHH`, `AvgAHA` | no | Línea de hándicap asiático del local y sus cuotas |

Las columnas de cuotas pueden quedar vacías. En ese caso, el modelo da las probabilidades pero no el valor esperado.

## Nombres de los equipos (2026-27)

| En el archivo | Equipo |
|---|---|
| `Alaves` | Deportivo Alavés |
| `Ath Bilbao` | Athletic Club |
| `Ath Madrid` | Atlético de Madrid |
| `Barcelona` | FC Barcelona |
| `Betis` | Real Betis |
| `Celta` | Celta de Vigo |
| `Elche` | Elche |
| `Espanol` | Espanyol |
| `Getafe` | Getafe |
| `La Coruna` | Deportivo de La Coruña |
| `Levante` | Levante |
| `Malaga` | Málaga |
| `Osasuna` | Osasuna |
| `Real Madrid` | Real Madrid |
| `Santander` | Racing de Santander |
| `Sevilla` | Sevilla |
| `Sociedad` | Real Sociedad |
| `Valencia` | Valencia |
| `Vallecano` | Rayo Vallecano |
| `Villarreal` | Villarreal |

Si un nombre no coincide, la predicción sale con la alerta `EQUIPO_DESCONOCIDO` y usa valores promedio.

En `ejemplo/` hay un archivo de muestra con la jornada 9. Está en una subcarpeta para que no se prediga solo.
