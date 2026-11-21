#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gráfica de aciertos acumulados del over/under 2.5 con las predicciones reales del modelo.

Definición de acierto
  El modelo dice "over" si p_over25 > 0.5 y "under" en cualquier otro caso. Es acierto si coincide
  con el resultado real: over = 3 o más goles en el partido, under = 2 o menos.

Cómo funciona (dos pasos)
  registrar   Guarda en historial/aciertos_over25.csv las predicciones de una carpeta de
              predicciones/ (la lee de su resumen.csv). Solo entran partidos que todavía no
              tienen resultado en datos/, así una predicción hecha después del partido no cuenta.
              Si el partido sigue pendiente y se vuelve a predecir (por ejemplo, con nuevos ajustes),
              se reemplaza la predicción anterior. Cuando el partido ya tiene resultado, queda fija.
  actualizar  Busca en datos/ (los CSV de football-data que los workflows bajan solos) el resultado
              de los partidos pendientes, calcula los aciertos y dibuja historial/aciertos_over25.png.

Uso
    python herramientas/graficar_aciertos.py registrar --carpeta predicciones/jornada13_2627
    python herramientas/graficar_aciertos.py actualizar
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

COLUMNAS = ["fecha", "local", "visitante", "carpeta", "modelo", "p_over25", "prediccion",
            "goles_reales", "acierto"]
TOLERANCIA_DIAS = 14  # por si football-data trae el partido con otra fecha (aplazado)


def leer_datos(carpeta: Path) -> pd.DataFrame:
    """Partidos ya jugados de todos los CSV de football-data."""
    partes = []
    for f in sorted(carpeta.glob("*.csv")):
        df = pd.read_csv(f, encoding="utf-8-sig")
        df.columns = [c.strip() for c in df.columns]
        if not {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"} <= set(df.columns):
            continue
        df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
        partes.append(pd.DataFrame({
            "fecha": pd.to_datetime(df["Date"], dayfirst=True, errors="coerce"),
            "local": df["HomeTeam"].str.strip(),
            "visitante": df["AwayTeam"].str.strip(),
            "goles": df["FTHG"].astype(int) + df["FTAG"].astype(int),
        }))
    if not partes:
        return pd.DataFrame(columns=["fecha", "local", "visitante", "goles"])
    return pd.concat(partes, ignore_index=True).dropna(subset=["fecha"])


def buscar_resultado(datos: pd.DataFrame, fecha, local: str, visitante: str):
    """Goles totales del partido, o None si todavía no está en los datos."""
    c = datos[(datos["local"] == local) & (datos["visitante"] == visitante)]
    c = c[(c["fecha"] - pd.Timestamp(fecha)).abs() <= pd.Timedelta(days=TOLERANCIA_DIAS)]
    return None if c.empty else int(c.iloc[0]["goles"])


def leer_registro(ruta: Path) -> pd.DataFrame:
    if ruta.exists():
        df = pd.read_csv(ruta)
        for c in COLUMNAS:
            if c not in df.columns:
                df[c] = pd.NA
        return df[COLUMNAS]
    return pd.DataFrame(columns=COLUMNAS)


def guardar_registro(df: pd.DataFrame, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df = df.sort_values(["fecha", "local"]).reset_index(drop=True)
    for c in ("goles_reales", "acierto"):
        df[c] = df[c].astype("Int64")
    df.to_csv(ruta, index=False)


def cmd_registrar(a) -> None:
    carpeta = Path(a.carpeta)
    resumen = carpeta / "resumen.csv"
    if not resumen.exists():
        print(f"No hay {resumen}; no se registra nada.")
        return
    r = pd.read_csv(resumen)
    modelo = (carpeta / "modelo.txt").read_text(encoding="utf-8").strip() if (carpeta / "modelo.txt").exists() else ""
    datos = leer_datos(Path(a.datos))
    ruta = Path(a.registro)
    reg = leer_registro(ruta)

    nuevos, omitidos = 0, []
    for _, f in r.iterrows():
        partes = str(f["partido"]).split(" vs ")
        p = f.get("p_over25")
        if len(partes) != 2 or pd.isna(p):
            continue
        local, visitante = partes[0].strip(), partes[1].strip()
        if "EQUIPO_DESCONOCIDO" in str(f.get("alertas", "")):
            omitidos.append(f"{f['partido']} (equipo desconocido)")
            continue
        if buscar_resultado(datos, f["fecha"], local, visitante) is not None:
            omitidos.append(f"{f['partido']} (ya tiene resultado)")
            continue
        fila = {"fecha": str(f["fecha"])[:10], "local": local, "visitante": visitante, "carpeta": carpeta.name,
                "modelo": modelo, "p_over25": round(float(p), 4),
                "prediccion": "over" if float(p) > 0.5 else "under", "goles_reales": pd.NA, "acierto": pd.NA}
        mismo = (reg["fecha"].astype(str) == fila["fecha"]) & (reg["local"] == local) & (reg["visitante"] == visitante)
        if mismo.any():
            if reg.loc[mismo, "goles_reales"].notna().any():
                continue  # ya resuelto: la predicción queda fija
            reg = reg[~mismo]
            reg = pd.concat([reg.astype(object), pd.DataFrame([fila]).astype(object)], ignore_index=True)
        nuevos += 1

    guardar_registro(reg, ruta)
    print(f"Registradas {nuevos} predicciones de {carpeta.name}.")
    for o in omitidos:
        print(f"  omitido: {o}")


def dibujar(res: pd.DataFrame, pendientes: int, destino: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    superficie, tinta, tinta2, rejilla = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0"
    azul, naranja, gris = "#2a78d6", "#eb6834", "#9a9993"

    res = res.sort_values(["fecha", "local"]).reset_index(drop=True)
    n = len(res)
    x = list(range(1, n + 1))
    aciertos = res["acierto"].astype(int).cumsum()
    p = res["p_over25"].astype(float)
    esperados = p.where(p > 0.5, 1 - p).cumsum()  # lo que el modelo cree que acertará
    azar = [0.5 * k for k in x]
    total, pct = int(aciertos.iloc[-1]), 100 * aciertos.iloc[-1] / n

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": rejilla, "axes.labelcolor": tinta2,
                         "xtick.color": tinta2, "ytick.color": tinta2})
    fig, ax = plt.subplots(figsize=(9, 4.4), facecolor=superficie)
    ax.set_facecolor(superficie)
    ax.grid(axis="y", color=rejilla, linewidth=1)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)

    ax.plot([0] + x, [0] + azar, color=gris, linewidth=2, label="Azar (50 %)")
    ax.plot([0] + x, [0] + list(esperados), color=naranja, linewidth=2, label="Esperados según el modelo")
    marcas = dict(marker="o", markersize=6, markeredgecolor=superficie, markeredgewidth=1.5) if n <= 60 else {}
    ax.plot([0] + x, [0] + list(aciertos), color=azul, linewidth=2, label="Aciertos acumulados", **marcas)
    ax.plot([n], [total], marker="o", markersize=7, color=azul, markeredgecolor=superficie, markeredgewidth=2)
    ax.annotate(f"{total} de {n} ({pct:.0f} %)", (n, total), xytext=(8, 6), textcoords="offset points",
                color=tinta, fontsize=10, fontweight="bold")

    ax.set_xlim(0, n + max(1, n * 0.12))
    ax.set_ylim(0, max(n * 0.5, total, float(esperados.iloc[-1])) * 1.15 + 1)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_xlabel("Partidos jugados, en orden de fecha")
    ax.set_ylabel("Aciertos")
    ax.legend(loc="upper left", frameon=False, labelcolor=tinta2)

    fig.text(0.125, 0.965, "Aciertos del over/under 2.5, acumulados", color=tinta, fontsize=12,
             fontweight="bold", ha="left", va="top")
    sub = (f"Predicciones reales del modelo. Over si p > 50 %. {res['fecha'].iloc[0]} a {res['fecha'].iloc[-1]}"
           + (f" · {pendientes} partidos esperan resultado" if pendientes else ""))
    fig.text(0.125, 0.915, sub, color=tinta2, fontsize=9, ha="left", va="top")
    fig.subplots_adjust(top=0.84)

    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, dpi=120, facecolor=superficie)
    plt.close(fig)


def dibujar_vacio(pendientes: int, destino: Path) -> None:
    """Imagen provisional mientras ningún partido registrado tiene resultado (evita una imagen rota en GitHub)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    superficie, tinta, tinta2 = "#fcfcfb", "#0b0b0b", "#52514e"
    fig = plt.figure(figsize=(9, 2.6), facecolor=superficie)
    fig.text(0.04, 0.80, "Aciertos del over/under 2.5, acumulados", color=tinta, fontsize=12,
             fontweight="bold", ha="left", va="center")
    fig.text(0.04, 0.50, f"Todavía no hay partidos jugados con predicción registrada ({pendientes} esperan resultado).",
             color=tinta2, fontsize=10, ha="left", va="center")
    fig.text(0.04, 0.30, "La curva aparece sola cuando football-data publique el primer resultado.",
             color=tinta2, fontsize=10, ha="left", va="center")
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destino, dpi=120, facecolor=superficie)
    plt.close(fig)


def cmd_actualizar(a) -> None:
    ruta = Path(a.registro)
    reg = leer_registro(ruta)
    if reg.empty:
        print("El registro está vacío: todavía no hay predicciones que graficar.")
        return
    datos = leer_datos(Path(a.datos))

    for i, f in reg[reg["goles_reales"].isna()].iterrows():
        goles = buscar_resultado(datos, f["fecha"], f["local"], f["visitante"])
        if goles is None:
            continue
        reg.at[i, "goles_reales"] = goles
        reg.at[i, "acierto"] = int((f["prediccion"] == "over") == (goles >= 3))
    guardar_registro(reg, ruta)

    reg = leer_registro(ruta)
    resueltos = reg[reg["acierto"].notna()]
    pendientes = int(reg["acierto"].isna().sum())
    if resueltos.empty:
        dibujar_vacio(pendientes, Path(a.imagen))
        print(f"{pendientes} predicciones esperan resultado; la curva aparece con el primer partido jugado -> {a.imagen}")
        return
    dibujar(resueltos, pendientes, Path(a.imagen))
    n, ok = len(resueltos), int(resueltos["acierto"].astype(int).sum())
    print(f"Aciertos del over/under 2.5: {ok} de {n} ({100 * ok / n:.1f} %); {pendientes} pendientes -> {a.imagen}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--datos", default="datos", help="carpeta con los CSV de football-data")
    comun.add_argument("--registro", default="historial/aciertos_over25.csv")
    r = sub.add_parser("registrar", parents=[comun])
    r.add_argument("--carpeta", required=True, help="carpeta de predicciones/ con resumen.csv")
    r.set_defaults(f=cmd_registrar)
    u = sub.add_parser("actualizar", parents=[comun])
    u.add_argument("--imagen", default="historial/aciertos_over25.png")
    u.set_defaults(f=cmd_actualizar)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    sys.exit(main())
