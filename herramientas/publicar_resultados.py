#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publica los resultados en archivos que GitHub muestra directamente.

  entrenamiento  Añade la versión al historial (historial/metricas.csv), reescribe
                 RESULTADOS.md y genera las notas de la versión (notas_release.md).
  indice         Reescribe predicciones/README.md con la lista de predicciones.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

COLS_HIST = ["version", "fecha_entrenamiento", "ultima_fecha_datos", "n_partidos", "metrica",
             "ll_marcador", "ll_over25", "brier_over25", "ll_1x2", "rps_1x2",
             "ll_over25_base", "ll_over25_cierre", "ll_1x2_cierre", "dif_over25_vs_cierre", "p_over25_vs_cierre",
             "over25_esperado", "over25_real", "grupo_consenso", "alerta"]

NOMBRES = {"ll_marcador": "Log-loss marcador", "ll_over25": "Log-loss over 2.5", "brier_over25": "Brier over 2.5",
           "ll_1x2": "Log-loss 1X2", "rps_1x2": "RPS 1X2"}


def f4(x):
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x:.4f}"


def fp(p):
    return "< 0.001" if p < 0.001 else f"= {p:.3f}"


def pct(x):
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{x * 100:.1f}%"


def buscar_vs(inf, mercado, metrica):
    for r in inf.get("ensamble_vs_mercado", []):
        if r["mercado"] == mercado and r["métrica"] == metrica:
            return r
    return None


def veredicto(r):
    if r is None:
        return "sin cuotas para comparar"
    if r["p_wilcoxon"] >= 0.05:
        return "sin diferencia significativa"
    return "**el ensamble es mejor**" if r["diferencia"] < 0 else "el mercado es mejor"


def fila_historial(inf, version):
    res = inf["resumen"]
    ens = res["Ensamble"]
    cierre = res.get("Mercado cierre", {})
    vs = buscar_vs(inf, "Mercado cierre", "ll_over25") or {}
    return {
        "version": version, "fecha_entrenamiento": inf.get("fecha_entrenamiento", ""),
        "ultima_fecha_datos": inf.get("ultima_fecha_datos", ""), "n_partidos": inf.get("n_partidos", ""),
        "metrica": inf.get("metrica", ""),
        **{k: ens.get(k) for k in ("ll_marcador", "ll_over25", "brier_over25", "ll_1x2", "rps_1x2")},
        "ll_over25_base": res.get("Base (promedio liga)", {}).get("ll_over25"),
        "ll_over25_cierre": cierre.get("ll_over25"), "ll_1x2_cierre": cierre.get("ll_1x2"),
        "dif_over25_vs_cierre": vs.get("diferencia"), "p_over25_vs_cierre": vs.get("p_wilcoxon"),
        "over25_esperado": inf.get("sesgo_over25", {}).get("predicho"),
        "over25_real": inf.get("sesgo_over25", {}).get("real"),
        "grupo_consenso": " + ".join(inf.get("consenso", {}).get("grupo_consenso", [])),
        "alerta": inf.get("consenso", {}).get("alerta", ""),
    }


def enlace_version(repo, version):
    return f"[{version}](https://github.com/{repo}/releases/tag/{version})" if repo else version


def cmd_entrenamiento(a):
    inf = json.loads(Path(a.informe).read_text(encoding="utf-8"))
    hist_path = Path(a.historial)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame(columns=COLS_HIST)
    hist = hist[hist["version"] != a.version]
    hist = pd.concat([hist, pd.DataFrame([fila_historial(inf, a.version)])], ignore_index=True)[COLS_HIST]
    hist.to_csv(hist_path, index=False)

    res = inf["resumen"]
    ens = res["Ensamble"]
    s = inf.get("sesgo_over25", {})
    vs_over = buscar_vs(inf, "Mercado cierre", "ll_over25")
    vs_1x2 = buscar_vs(inf, "Mercado cierre", "ll_1x2")
    previa = hist.iloc[-2] if len(hist) > 1 else None

    L = [f"# Resultados del modelo", "",
         f"**Versión vigente:** {enlace_version(a.repo, a.version)}  ",
         f"**Entrenado:** {inf.get('fecha_entrenamiento', '')} · **Datos hasta:** {inf.get('ultima_fecha_datos', '')} · "
         f"**Partidos:** {inf.get('n_partidos', '')} · **Métrica de forma:** {inf.get('metrica', '')}", "",
         "Descarga el modelo de esta versión, o de cualquier versión anterior, en la página de "
         f"[versiones](https://github.com/{a.repo}/releases)." if a.repo else "", "",
         "## En pocas palabras", "",
         f"- **Over 2.5 contra el cierre del mercado:** {veredicto(vs_over)}"
         + (f" (log-loss {f4(vs_over['ensamble'])} contra {f4(vs_over['mercado_valor'])}, p {fp(vs_over['p_wilcoxon'])})." if vs_over else "."),
         f"- **1X2 contra el cierre del mercado:** {veredicto(vs_1x2)}"
         + (f" (log-loss {f4(vs_1x2['ensamble'])} contra {f4(vs_1x2['mercado_valor'])}, p {fp(vs_1x2['p_wilcoxon'])})." if vs_1x2 else "."),
         f"- **Sesgo del over 2.5 en prueba:** el modelo esperaba {pct(s.get('predicho'))} y ocurrió {pct(s.get('real'))}.",
         ]
    if previa is not None:
        d = ens["ll_over25"] - float(previa["ll_over25"])
        L.append(f"- **Contra la versión anterior ({previa['version']}):** log-loss over 2.5 "
                 f"{'mejoró' if d < 0 else 'empeoró' if d > 0 else 'igual'} ({d:+.4f}).")
    alerta = inf.get("consenso", {}).get("alerta", "")
    L.append(f"- **Consenso entre modelos:** {' + '.join(inf.get('consenso', {}).get('grupo_consenso', []))}"
             + (f" — alerta {alerta}" if alerta else "") + ".")
    L += ["", "Todas las métricas se miden en el 30 % más reciente de los partidos, reentrenando jornada a jornada. "
          "Más bajo es mejor.", "",
          "## Métricas por modelo", "",
          "| Modelo | " + " | ".join(NOMBRES.values()) + " |",
          "|---|" + "---|" * len(NOMBRES)]
    for nombre, r in res.items():
        L.append(f"| {nombre} | " + " | ".join(f4(r.get(k)) for k in NOMBRES) + " |")
    if inf.get("ensamble_vs_mercado"):
        L += ["", "## Ensamble contra el mercado (mismos partidos)", "",
              "| Mercado | Métrica | Partidos | Ensamble | Mercado | Diferencia | p (Wilcoxon) | Veredicto |",
              "|---|---|---|---|---|---|---|---|"]
        for r in inf["ensamble_vs_mercado"]:
            L.append(f"| {r['mercado']} | {NOMBRES.get(r['métrica'], r['métrica'])} | {r['n']} | {f4(r['ensamble'])} | "
                     f"{f4(r['mercado_valor'])} | {r['diferencia']:+.4f} | {fp(r['p_wilcoxon']).lstrip('= ')} | {veredicto(r)} |")
    if inf.get("factor_calibrador_goles"):
        L += ["", "## Calibrador de goles", "",
              "Factor que multiplica los goles esperados de cada modelo (1 = sin corrección).", "",
              "| " + " | ".join(inf["factor_calibrador_goles"]) + " |",
              "|" + "---|" * len(inf["factor_calibrador_goles"]),
              "| " + " | ".join(f"{v:.3f}" for v in inf["factor_calibrador_goles"].values()) + " |"]
    if inf.get("calibracion"):
        L += ["", "## Calibración del over 2.5 (ensamble)", "",
              "| Tramo | Partidos | Probabilidad media | Frecuencia real |", "|---|---|---|---|"]
        for r in inf["calibracion"]:
            L.append(f"| {r['tramo']} | {r['n']} | {pct(float(r['p_media']))} | {pct(float(r['frecuencia_real']))} |")
    L += ["", "## Historial de versiones", "",
          "| Versión | Entrenado | Datos hasta | Partidos | Log-loss marcador | Log-loss over 2.5 | Cierre over 2.5 | Log-loss 1X2 | Over 2.5 esperado / real |",
          "|---|---|---|---|---|---|---|---|---|"]
    for _, r in hist.iloc[::-1].iterrows():
        L.append(f"| {enlace_version(a.repo, r['version'])} | {r['fecha_entrenamiento']} | {r['ultima_fecha_datos']} | "
                 f"{r['n_partidos']} | {f4(r['ll_marcador'])} | {f4(r['ll_over25'])} | {f4(r['ll_over25_cierre'])} | "
                 f"{f4(r['ll_1x2'])} | {pct(r['over25_esperado'])} / {pct(r['over25_real'])} |")
    texto = "\n".join(L) + "\n"
    Path(a.salida).write_text(texto, encoding="utf-8")

    notas = [f"Modelo entrenado el {inf.get('fecha_entrenamiento', '')} con datos hasta el "
             f"{inf.get('ultima_fecha_datos', '')} ({inf.get('n_partidos', '')} partidos).", "",
             f"- Log-loss over 2.5 (ensamble): {f4(ens['ll_over25'])}",
             f"- Over 2.5 contra el cierre: {veredicto(vs_over)}",
             f"- Log-loss marcador: {f4(ens['ll_marcador'])}", "",
             "Archivos: `modelo_laliga.pkl` (modelo), `modelo_laliga.py` (el código exacto de esta versión), "
             "`informe_entrenamiento.json`, `salida_entrenamiento.txt` y `entorno.txt` (versiones exactas "
             "de las librerías; instálalas con `pip install -r entorno.txt` si el modelo no carga)."]
    Path(a.notas).write_text("\n".join(notas) + "\n", encoding="utf-8")
    print(f"Historial: {hist_path} ({len(hist)} versiones) · {a.salida} · {a.notas}")


def cmd_indice(a):
    raiz = Path(a.carpeta)
    filas = []
    for d in sorted([p for p in raiz.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
        res = d / "resumen.csv"
        if not res.exists():
            continue
        t = pd.read_csv(res)
        version = (d / "modelo.txt").read_text(encoding="utf-8").strip() if (d / "modelo.txt").exists() else ""
        fechas = f"{t['fecha'].min()} a {t['fecha'].max()}" if len(t) else ""
        filas.append((d.name, len(t), fechas, version))
    L = ["# Predicciones", "",
         "Cada carpeta tiene `resumen.md` (tabla), una gráfica por partido y las matrices de marcadores. "
         "Las más recientes van primero.", "",
         "| Carpeta | Partidos | Fechas | Modelo usado |", "|---|---|---|---|"]
    for nombre, n, fechas, version in filas:
        L.append(f"| [{nombre}]({nombre}/resumen.md) | {n} | {fechas} | {enlace_version(a.repo, version) if version else ''} |")
    (raiz / "README.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Índice: {raiz / 'README.md'} ({len(filas)} carpetas)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("entrenamiento")
    e.add_argument("--informe", default="informe_entrenamiento.json")
    e.add_argument("--version", required=True)
    e.add_argument("--repo", default="")
    e.add_argument("--historial", default="historial/metricas.csv")
    e.add_argument("--salida", default="RESULTADOS.md")
    e.add_argument("--notas", default="notas_release.md")
    e.set_defaults(f=cmd_entrenamiento)
    i = sub.add_parser("indice")
    i.add_argument("--carpeta", default="predicciones")
    i.add_argument("--repo", default="")
    i.set_defaults(f=cmd_indice)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
