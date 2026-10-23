#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Modelo de marcadores exactos para LaLiga (prepartido y en vivo)
================================================================

Para cada partido produce una matriz de probabilidades por marcador exacto
(0-0 ... 10-10) que suma 1, con cuatro modelos:

    1. Poisson Dixon-Coles        (máxima verosimilitud + decaimiento temporal)
    2. Regresión de Poisson (GLM) (variables de forma, rival, contexto, estilo)
    3. Gradient boosting Poisson  (mismas variables, con árboles)
    4. Bayesiano Gamma-Poisson    (ataque/defensa como distribuciones)

Valida en orden cronológico (70 % entrena, 30 % prueba), mide log-loss, Brier y
RPS, compara los modelos con Friedman + Wilcoxon (Holm), decide qué modelos
convergen, arma un ensamble y da intervalos del 90 % por bootstrap / posterior.

USO RÁPIDO
----------
    pip install numpy scipy pandas scikit-learn matplotlib

    # 1) Entrenar con 3 temporadas + la actual (CSV de football-data.co.uk, liga SP1)
    python modelo_laliga.py entrenar --datos SP1_2324.csv SP1_2425.csv SP1_2526.csv SP1_2627.csv \
        --ajustes ajustes.csv --metrica ambas

    # 2) Predecir un partido (y comparar contra cuotas si se dan)
    python modelo_laliga.py predecir --local "Barcelona" --visitante "Sevilla" \
        --fecha 2026-10-18 --cuota-over 1.72 --cuota-under 2.15 --ah -1.5 --cuota-ah-local 1.95 --cuota-ah-visit 1.95

    # 3) Predecir todos los partidos de un archivo (formato fixtures.csv de football-data)
    python modelo_laliga.py predecir --partidos proximos.csv

    # 4) En vivo: minuto, marcador actual y expulsados
    python modelo_laliga.py vivo --local "Barcelona" --visitante "Sevilla" --minuto 60 --marcador 1-0 \
        --rojas-local 0 --rojas-visit 1 --cuota-over 2.40

    # 5) Después de cada jornada: evalúa la jornada nueva con el modelo viejo y reentrena
    python modelo_laliga.py recalibrar --datos SP1_2324.csv SP1_2425.csv SP1_2526.csv SP1_2627.csv

Todo lo configurable (pesos de forma, pesos del rival, umbrales de consenso,
multiplicadores de contexto, nº de bootstrap...) está en la clase CONFIG.
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
import warnings
from dataclasses import dataclass, field, asdict
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import optimize, stats
from scipy.special import gammaln

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# CONFIGURACIÓN (todo lo que se espera modificar está aquí)
# ---------------------------------------------------------------------------


@dataclass
class Config:
    # --- Forma ponderada (partido 1 = el más reciente). Bloque 1-5 pesa más que 6-10.
    pesos_forma: tuple = (1.00, 0.90, 0.80, 0.70, 0.60, 0.30, 0.25, 0.20, 0.15, 0.10)
    # --- Fuerza del rival: misma condición (últimos 5), general (últimos 5), H2H (últimos 3)
    peso_rival_condicion: float = 0.60
    peso_rival_general: float = 0.25
    peso_rival_h2h: float = 0.15
    # --- Mezcla forma vs temporada completa (alpha). Recién ascendidos usan alpha = 1.
    alpha_forma: float = 0.60
    # --- Recién ascendidos: valores previos mientras tengan < 5 partidos
    ascendido_ataque_prior: float = 0.85
    ascendido_defensa_prior: float = 1.15
    # --- Matriz de marcadores
    max_goles: int = 10
    # --- Dixon-Coles: decaimiento temporal por día (0.0019 = vida media ~1 año)
    xi: float = 0.0019
    # --- Bayesiano: fuerza del prior en "partidos equivalentes"
    bayes_k0: float = 10.0
    # --- Contracción hacia la media (árbitro, estilos): k partidos
    shrink_k: float = 10.0
    # --- Split temporal
    frac_entreno: float = 0.70
    # --- Incertidumbre
    n_bootstrap: int = 100
    intervalo: tuple = (5, 95)
    # --- Consenso entre modelos
    alfa_test: float = 0.05
    umbral_delta_over: float = 0.03      # diferencia mediana en P(over 2.5)
    umbral_js: float = 0.02              # divergencia Jensen-Shannon media
    # --- Multiplicadores de contexto (se usan cuando el modelo no puede aprenderlos)
    mult_portero_ausente: float = 1.05   # defensa propia (concede más)
    mult_central_ausente: float = 1.05
    mult_partido_extra: float = 0.95     # ataque propio a la baja (Europa/Copa entre semana)
    mult_rotacion: float = 0.93
    dias_descanso_corto: int = 3
    factor_reemplazo_ausente: float = 0.5  # ataque *= 1 - factor * aporte
    # --- En vivo
    mult_roja_propia: float = 0.70
    mult_roja_rival: float = 1.25
    # --- Estilos (k-means)
    n_estilos: int = 4
    # --- xG aproximado desde tiros (si no hay columnas HxG/AxG)
    xg_por_tiro_puerta: float = 0.30
    xg_por_tiro_fuera: float = 0.035
    semilla: int = 42
    # --- Validación jornada a jornada (walk-forward): se reentrena cada `dias_bloque` días
    walk_forward: bool = True
    dias_bloque: int = 7
    # --- Promedio de goles de la liga: ventana corta (en partidos) para captar cambios de nivel
    ventana_liga: int = 190
    # --- Calibrador de goles: escala λ y μ de cada modelo para que sus goles esperados
    #     coincidan con los reales en los últimos `ventana_calibracion` partidos predichos
    calibrar_goles: bool = True
    ventana_calibracion: int = 150
    shrink_calibracion_goles: float = 40.0   # goles "ficticios" que tiran el factor hacia 1
    # --- Peso temporal (mismo xi) también en la regresión y el gradient boosting
    decaimiento_en_ml: bool = True


CONFIG = Config()
MODELOS = ["DixonColes", "RegresionPoisson", "GradientBoosting", "Bayesiano"]
ARCHIVO_MODELO = "modelo_laliga.pkl"

# ---------------------------------------------------------------------------
# CARGA DE DATOS
# ---------------------------------------------------------------------------

COLS_MINIMAS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]


def cargar_datos(rutas: list[str]) -> pd.DataFrame:
    """Lee uno o varios CSV de football-data.co.uk (SP1) y los ordena por fecha."""
    partes = []
    for i, ruta in enumerate(rutas):
        try:
            df = pd.read_csv(ruta, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(ruta, encoding="latin-1")
        df.columns = [c.replace("﻿", "").replace("ï»¿", "").strip() for c in df.columns]
        df = df.dropna(subset=["HomeTeam", "AwayTeam"])
        df["archivo"] = i
        partes.append(df)
    df = pd.concat(partes, ignore_index=True)
    faltan = [c for c in COLS_MINIMAS if c not in df.columns]
    if faltan:
        raise ValueError(f"Faltan columnas en los CSV: {faltan}")
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date"])
    df["HomeTeam"] = df["HomeTeam"].str.strip()
    df["AwayTeam"] = df["AwayTeam"].str.strip()
    # Temporada: julio-junio
    df["temporada"] = np.where(df["Date"].dt.month >= 7, df["Date"].dt.year, df["Date"].dt.year - 1)
    hora = df["Time"].astype(str) if "Time" in df.columns else "00:00"
    df["_orden"] = pd.to_datetime(df["Date"].dt.strftime("%Y-%m-%d") + " " + hora, errors="coerce").fillna(df["Date"])
    df = df.sort_values(["_orden", "HomeTeam"]).reset_index(drop=True)
    jugados = df["FTHG"].notna() & df["FTAG"].notna()
    df["jugado"] = jugados
    for c in ["FTHG", "FTAG", "HTHG", "HTAG", "HS", "AS", "HST", "AST", "HC", "AC", "HF", "AF", "HR", "AR"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def agregar_xg(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """xG por partido, mezclando fuentes:
    1. xG real (HxG/AxG) donde exista (football-data lo trae desde 2026-27).
    2. Donde no exista, xG aproximado con tiros y tiros a puerta. Si hay al menos 40 partidos
       con xG real, los coeficientes se estiman con ellos; si no, se usan los de CONFIG y se
       reescala para que el promedio coincida con el de goles.
    3. Si no hay tiros, los goles."""
    df = df.copy()
    real_l = pd.to_numeric(df["HxG"], errors="coerce") if "HxG" in df.columns else pd.Series(np.nan, index=df.index)
    real_v = pd.to_numeric(df["AxG"], errors="coerce") if "AxG" in df.columns else pd.Series(np.nan, index=df.index)
    aprox_l = pd.Series(np.nan, index=df.index)
    aprox_v = pd.Series(np.nan, index=df.index)
    a_puerta, a_fuera = cfg.xg_por_tiro_puerta, cfg.xg_por_tiro_fuera
    if {"HS", "AS", "HST", "AST"}.issubset(df.columns):
        con_real = real_l.notna() & real_v.notna() & df["HST"].notna() & df["HS"].notna()
        if con_real.sum() >= 40:
            # mínimos cuadrados no negativos: xG ≈ a·tiros a puerta + b·tiros fuera
            X = np.vstack([np.c_[df.loc[con_real, "HST"], df.loc[con_real, "HS"] - df.loc[con_real, "HST"]],
                           np.c_[df.loc[con_real, "AST"], df.loc[con_real, "AS"] - df.loc[con_real, "AST"]]])
            y = np.r_[real_l[con_real], real_v[con_real]]
            (a_puerta, a_fuera), _ = optimize.nnls(X.astype(float), y.astype(float))
            esc = 1.0
        aprox_l = a_puerta * df["HST"] + a_fuera * (df["HS"] - df["HST"])
        aprox_v = a_puerta * df["AST"] + a_fuera * (df["AS"] - df["AST"])
        if con_real.sum() < 40:
            j = df["jugado"] & aprox_l.notna()
            esc = ((df.loc[j, "FTHG"].sum() + df.loc[j, "FTAG"].sum()) /
                   max(aprox_l[j].sum() + aprox_v[j].sum(), 1e-9)) if j.sum() > 20 else 1.0
        aprox_l, aprox_v = aprox_l * esc, aprox_v * esc
    df["xg_l"] = real_l.fillna(aprox_l).fillna(df["FTHG"])
    df["xg_v"] = real_v.fillna(aprox_v).fillna(df["FTAG"])
    df["xg_fuente"] = np.where(real_l.notna(), "real", np.where(aprox_l.notna(), "aproximado", "goles"))
    df.attrs["xg_coef"] = (float(a_puerta), float(a_fuera))
    return df


COLS_AJUSTES = [
    "aporte_ausente_local", "aporte_ausente_visitante",
    "portero_ausente_local", "portero_ausente_visitante",
    "central_ausente_local", "central_ausente_visitante",
    "descanso_local", "descanso_visitante",
    "extra_local", "extra_visitante",
    "rotacion_local", "rotacion_visitante",
    "cambio_entrenador_local", "cambio_entrenador_visitante",
    "en_juego", "arbitro",
]


def cargar_ajustes(ruta: str | None) -> pd.DataFrame:
    """ajustes.csv: un renglón por partido (Date, HomeTeam, AwayTeam + columnas de contexto)."""
    if not ruta or not Path(ruta).exists():
        return pd.DataFrame(columns=["Date", "HomeTeam", "AwayTeam"] + COLS_AJUSTES)
    aj = pd.read_csv(ruta)
    aj["Date"] = pd.to_datetime(aj["Date"], dayfirst=True, errors="coerce")
    for c in COLS_AJUSTES:
        if c not in aj.columns:
            aj[c] = np.nan
    return aj


def unir_ajustes(df: pd.DataFrame, aj: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if len(aj):
        aj2 = aj.copy()
        aj2["_clave"] = aj2["Date"].dt.strftime("%Y-%m-%d") + "|" + aj2["HomeTeam"].str.strip() + "|" + aj2["AwayTeam"].str.strip()
        df["_clave"] = df["Date"].dt.strftime("%Y-%m-%d") + "|" + df["HomeTeam"] + "|" + df["AwayTeam"]
        aj2 = aj2.drop_duplicates("_clave").set_index("_clave")[COLS_AJUSTES]
        df = df.drop(columns=[c for c in COLS_AJUSTES if c in df.columns]).join(aj2, on="_clave")
    else:
        for c in COLS_AJUSTES:
            if c not in df.columns:
                df[c] = np.nan
    if "Referee" in df.columns:
        df["arbitro"] = df["arbitro"].fillna(df["Referee"])
    return df


# ---------------------------------------------------------------------------
# VARIABLES SIN FUGA DE INFORMACIÓN (cada partido solo ve partidos anteriores)
# ---------------------------------------------------------------------------


class Historial:
    """Guarda, por equipo, sus partidos ya jugados y calcula forma y fuerza del rival."""

    def __init__(self, cfg: Config, metrica: str):
        self.cfg = cfg
        self.metrica = metrica  # "goles" o "xg"
        self.partidos: dict[str, list[dict]] = {}
        self.liga_l: list[float] = []   # goles (o xg) del local en la liga
        self.liga_v: list[float] = []
        self.equipos_temporada: dict[int, set] = {}

    # --- promedios de liga (rodantes)
    def prom_liga(self, venue: str) -> float:
        serie = self.liga_l if venue == "H" else self.liga_v
        if len(serie) < 20:
            return 1.45 if venue == "H" else 1.15
        return float(np.mean(serie[-self.cfg.ventana_liga:]))

    def ultimos(self, equipo, n, venue=None):
        lst = self.partidos.get(equipo, [])
        if venue:
            lst = [p for p in lst if p["venue"] == venue]
        return lst[-n:]

    # --- fuerza del rival como factor (1.0 = promedio de la liga)
    def factor_rival(self, rival, venue_rival, equipo):
        """Devuelve (factor_defensa_rival, factor_ataque_rival).
        defensa > 1 = el rival concede más que la media; ataque > 1 = marca más."""
        c = self.cfg
        piezas_def, piezas_att, pesos = [], [], []

        def factores(lista):
            if not lista:
                return None
            d = np.mean([p["ga"] / self.prom_liga("A" if p["venue"] == "H" else "H") for p in lista])
            a = np.mean([p["gf"] / self.prom_liga(p["venue"]) for p in lista])
            return d, a

        f = factores(self.ultimos(rival, 5, venue_rival))
        if f:
            piezas_def.append(f[0]); piezas_att.append(f[1]); pesos.append(c.peso_rival_condicion)
        f = factores(self.ultimos(rival, 5))
        if f:
            piezas_def.append(f[0]); piezas_att.append(f[1]); pesos.append(c.peso_rival_general)
        h2h = [p for p in self.partidos.get(rival, []) if p["rival"] == equipo][-3:]
        f = factores(h2h)
        if f:
            piezas_def.append(f[0]); piezas_att.append(f[1]); pesos.append(c.peso_rival_h2h)
        if not pesos:
            return 1.0, 1.0
        w = np.array(pesos) / np.sum(pesos)
        d = float(np.clip(np.dot(w, piezas_def), 0.4, 2.5))
        a = float(np.clip(np.dot(w, piezas_att), 0.4, 2.5))
        return d, a

    def forma(self, equipo, venue=None):
        """Ataque y defensa ponderados (últimos 10), ajustados por la fuerza del rival."""
        lst = self.ultimos(equipo, len(self.cfg.pesos_forma), venue)
        if not lst:
            return np.nan, np.nan, 0
        lst = lst[::-1]  # más reciente primero
        w = np.array(self.cfg.pesos_forma[: len(lst)])
        att = np.array([p["gf"] / max(p["esp_gf"], 0.2) for p in lst])
        dfn = np.array([p["ga"] / max(p["esp_ga"], 0.2) for p in lst])
        return float(np.dot(w, att) / w.sum()), float(np.dot(w, dfn) / w.sum()), len(lst)

    def temporada(self, equipo, temporada):
        lst = [p for p in self.partidos.get(equipo, []) if p["temporada"] == temporada]
        if not lst:
            return np.nan, np.nan, 0
        att = np.mean([p["gf"] / max(p["esp_gf"], 0.2) for p in lst])
        dfn = np.mean([p["ga"] / max(p["esp_ga"], 0.2) for p in lst])
        return float(att), float(dfn), len(lst)

    def estilo(self, equipo, temporada):
        """Promedios de la temporada: tiros, tiros a puerta, córners, faltas (a favor y en contra)."""
        lst = [p for p in self.partidos.get(equipo, []) if p["temporada"] == temporada][-10:]
        if not lst:
            lst = self.partidos.get(equipo, [])[-5:]
        if not lst:
            return [np.nan] * 6
        return [np.nanmean([p[k] for p in lst]) for k in ("tiros", "tiros_c", "puerta", "corners", "faltas", "tarjetas")]

    def ultima_fecha(self, equipo):
        lst = self.partidos.get(equipo, [])
        return lst[-1]["fecha"] if lst else None

    def es_ascendido(self, equipo, temporada):
        prev = self.equipos_temporada.get(temporada - 1)
        if prev is None:
            return 0
        return int(equipo not in prev)

    def registrar(self, fila, valores, esperados):
        """Añade un partido jugado al historial de ambos equipos."""
        gl, gv = valores
        esp_l_gf, esp_l_ga, esp_v_gf, esp_v_ga = esperados
        g = lambda k: fila.get(k, np.nan)
        base = dict(fecha=fila["Date"], temporada=fila["temporada"])
        self.partidos.setdefault(fila["HomeTeam"], []).append(dict(
            base, venue="H", rival=fila["AwayTeam"], gf=gl, ga=gv, esp_gf=esp_l_gf, esp_ga=esp_l_ga,
            tiros=g("HS"), tiros_c=g("AS"), puerta=g("HST"), corners=g("HC"), faltas=g("HF"), tarjetas=g("HY")))
        self.partidos.setdefault(fila["AwayTeam"], []).append(dict(
            base, venue="A", rival=fila["HomeTeam"], gf=gv, ga=gl, esp_gf=esp_v_gf, esp_ga=esp_v_ga,
            tiros=g("AS"), tiros_c=g("HS"), puerta=g("AST"), corners=g("AC"), faltas=g("AF"), tarjetas=g("AY")))
        self.liga_l.append(gl)
        self.liga_v.append(gv)
        self.equipos_temporada.setdefault(fila["temporada"], set()).update([fila["HomeTeam"], fila["AwayTeam"]])

    # --- variables de un partido (antes de jugarse)
    def variables(self, fila) -> dict:
        c = self.cfg
        L, V, T = fila["HomeTeam"], fila["AwayTeam"], fila["temporada"]
        out, alertas = {}, []
        mu_l, mu_v = self.prom_liga("H"), self.prom_liga("A")
        out["prom_liga_l"], out["prom_liga_v"] = mu_l, mu_v

        # fuerza actual de cada rival (se guarda para juzgar este partido más adelante)
        def_v, att_v = self.factor_rival(V, "A", L)
        def_l, att_l = self.factor_rival(L, "H", V)
        out["rival_def_v"], out["rival_att_v"] = def_v, att_v
        out["rival_def_l"], out["rival_att_l"] = def_l, att_l
        out["_esp"] = (mu_l * def_v, mu_v * att_v, mu_v * def_l, mu_l * att_l)

        for lado, eq, ven in (("l", L, "H"), ("v", V, "A")):
            asc = self.es_ascendido(eq, T)
            fa, fd, n = self.forma(eq)
            fa_c, fd_c, n_c = self.forma(eq, ven)
            ta, td, nt = self.temporada(eq, T)
            if asc:
                alertas.append(f"RECIEN_ASCENDIDO:{eq}")
            if n < 5:
                alertas.append(f"MUESTRA_CORTA:{eq}")
                faltan = 5 - n
                fa = ((0 if np.isnan(fa) else fa) * n + c.ascendido_ataque_prior * faltan) / 5
                fd = ((0 if np.isnan(fd) else fd) * n + c.ascendido_defensa_prior * faltan) / 5
            fa_c = fa if np.isnan(fa_c) else 0.5 * fa_c + 0.5 * fa
            fd_c = fd if np.isnan(fd_c) else 0.5 * fd_c + 0.5 * fd
            alpha = 1.0 if asc else c.alpha_forma
            ta = fa if np.isnan(ta) else ta
            td = fd if np.isnan(td) else td
            out[f"att_{lado}"] = alpha * fa_c + (1 - alpha) * ta
            out[f"def_{lado}"] = alpha * fd_c + (1 - alpha) * td
            out[f"forma_att_{lado}"], out[f"forma_def_{lado}"] = fa, fd
            out[f"temp_att_{lado}"], out[f"temp_def_{lado}"] = ta, td
            out[f"ascendido_{lado}"] = asc
            uf = self.ultima_fecha(eq)
            out[f"descanso_calc_{lado}"] = min((fila["Date"] - uf).days, 21) if uf is not None else 7
            est = self.estilo(eq, T)
            for k, v in zip(("tiros", "tiros_c", "puerta", "corners", "faltas", "tarjetas"), est):
                out[f"est_{k}_{lado}"] = v

        # H2H (últimos 3, ambos sentidos): goles totales promedio
        h2h = [p for p in self.partidos.get(L, []) if p["rival"] == V][-3:]
        out["h2h_goles"] = float(np.mean([p["gf"] + p["ga"] for p in h2h])) if h2h else np.nan
        out["h2h_n"] = len(h2h)

        # λ "de fórmula" (sección 4.4): promedio local × ataque local × defensa visitante
        out["lam_formula"] = mu_l * out["att_l"] * out["def_v"]
        out["mu_formula"] = mu_v * out["att_v"] * out["def_l"]
        out["_alertas"] = alertas
        return out


def construir_variables(df: pd.DataFrame, cfg: Config, metrica: str = "goles"):
    """Recorre los partidos en orden; las variables de cada uno solo usan partidos previos.
    Devuelve (df con variables, historial final para predecir partidos futuros)."""
    h = Historial(cfg, metrica)
    filas = []
    for _, fila in df.iterrows():
        fila = fila.to_dict()
        var = h.variables(fila)
        esp = var.pop("_esp")
        filas.append(var)
        if fila["jugado"]:
            if metrica == "xg":
                vals = (fila["xg_l"], fila["xg_v"])
            else:
                vals = (fila["FTHG"], fila["FTAG"])
            h.registrar(fila, vals, esp)
    X = pd.DataFrame(filas, index=df.index)
    X["alertas"] = X.pop("_alertas").apply(lambda a: ";".join(a))
    return pd.concat([df, X], axis=1), h


# ---------------------------------------------------------------------------
# PROBABILIDAD: matriz de marcadores y mercados
# ---------------------------------------------------------------------------


def poisson_pmf(lam, n):
    k = np.arange(n + 1)
    return np.exp(k * np.log(max(lam, 1e-9)) - lam - gammaln(k + 1))


def matriz_marcadores(lam: float, mu: float, rho: float = 0.0, n: int = 10) -> np.ndarray:
    """P(local = x, visitante = y) para x, y = 0..n con corrección Dixon-Coles; suma 1."""
    m = np.outer(poisson_pmf(lam, n), poisson_pmf(mu, n))
    if rho:
        m[0, 0] *= max(1 - lam * mu * rho, 0)
        m[0, 1] *= max(1 + lam * rho, 0)
        m[1, 0] *= max(1 + mu * rho, 0)
        m[1, 1] *= max(1 - rho, 0)
    return m / m.sum()


def matriz_vivo(lam, mu, rho, minuto, gl, gv, rojas_l, rojas_v, frac_2t, cfg: Config):
    """Marcador final dado el minuto, el marcador actual y las expulsiones."""
    t = float(np.clip(minuto, 0, 90))
    if t < 45:
        resto = (1 - frac_2t) * (45 - t) / 45 + frac_2t
    else:
        resto = frac_2t * (90 - t) / 45
    resto = max(resto, 0.0)
    ml = cfg.mult_roja_propia ** rojas_l * cfg.mult_roja_rival ** rojas_v
    mv = cfg.mult_roja_propia ** rojas_v * cfg.mult_roja_rival ** rojas_l
    n = cfg.max_goles
    resto_m = matriz_marcadores(lam * resto * ml, mu * resto * mv, rho if (gl == 0 and gv == 0) else 0.0, n)
    final = np.zeros((n + 1, n + 1))
    for i in range(n + 1 - gl):
        for j in range(n + 1 - gv):
            final[gl + i, gv + j] += resto_m[i, j]
    return final / final.sum()


def mercados(m: np.ndarray) -> dict:
    n = m.shape[0] - 1
    x, y = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
    tot = x + y
    return {
        "over_0.5": float(m[tot >= 1].sum()), "over_1.5": float(m[tot >= 2].sum()),
        "over_2.5": float(m[tot >= 3].sum()), "over_3.5": float(m[tot >= 4].sum()),
        "over_4.5": float(m[tot >= 5].sum()),
        "local": float(m[x > y].sum()), "empate": float(m[x == y].sum()), "visitante": float(m[x < y].sum()),
        "ambos_marcan": float(m[(x > 0) & (y > 0)].sum()),
        "goles_esperados": float((m * tot).sum()),
    }


def ev_handicap(m: np.ndarray, linea_local: float, cuota_local: float, cuota_visit: float) -> dict:
    """Valor esperado por unidad apostada en hándicap asiático (línea del local, p. ej. -0.75)."""
    n = m.shape[0] - 1
    x, y = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
    dif = (x - y).astype(float)

    def pago(linea, cuota, signo):
        partes = [linea] if (linea * 4) % 2 == 0 else [linea - 0.25, linea + 0.25]
        total = np.zeros_like(dif)
        for p in partes:
            d = signo * dif + p
            total += np.where(d > 0, cuota - 1, np.where(d == 0, 0.0, -1.0)) / len(partes)
        return float((m * total).sum())

    return {"ev_local": pago(linea_local, cuota_local, 1), "ev_visit": pago(-linea_local, cuota_visit, -1)}


def prob_sin_margen(cuotas: list[float]) -> list[float]:
    inv = np.array([1 / c for c in cuotas])
    return list(inv / inv.sum())


# ---------------------------------------------------------------------------
# MODELOS
# ---------------------------------------------------------------------------


class DixonColes:
    nombre = "DixonColes"

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def fit(self, d: pd.DataFrame, x0=None):
        self.equipos = sorted(set(d["HomeTeam"]) | set(d["AwayTeam"]))
        idx = {e: i for i, e in enumerate(self.equipos)}
        n = len(self.equipos)
        hi = d["HomeTeam"].map(idx).values
        ai = d["AwayTeam"].map(idx).values
        gl = d["FTHG"].values.astype(float)
        gv = d["FTAG"].values.astype(float)
        dias = (d["Date"].max() - d["Date"]).dt.days.values
        w = np.exp(-self.cfg.xi * dias)

        def nll(p):
            att = np.r_[p[: n - 1], -p[: n - 1].sum()]   # suma de ataques = 0
            dfn = p[n - 1: 2 * n - 1]
            home, rho, c = p[-3], p[-2], p[-1]
            lam = np.exp(c + home + att[hi] + dfn[ai])
            mu = np.exp(c + att[ai] + dfn[hi])
            tau = np.ones_like(lam)
            m00 = (gl == 0) & (gv == 0); m01 = (gl == 0) & (gv == 1)
            m10 = (gl == 1) & (gv == 0); m11 = (gl == 1) & (gv == 1)
            tau[m00] = 1 - lam[m00] * mu[m00] * rho
            tau[m01] = 1 + lam[m01] * rho
            tau[m10] = 1 + mu[m10] * rho
            tau[m11] = 1 - rho
            tau = np.clip(tau, 1e-6, None)
            ll = (np.log(tau) + gl * np.log(lam) - lam - gammaln(gl + 1)
                  + gv * np.log(mu) - mu - gammaln(gv + 1))
            return -(w * ll).sum() + 0.01 * (p[: 2 * n - 1] ** 2).sum()

        if x0 is None or len(x0) != 2 * n + 2:
            x0 = np.r_[np.zeros(2 * n - 1), 0.25, -0.05, 0.1]
        bounds = [(-3, 3)] * (2 * n - 1) + [(-1, 1), (-0.3, 0.3), (-2, 2)]
        r = optimize.minimize(nll, x0, method="L-BFGS-B", bounds=bounds)
        p = r.x
        self.att = dict(zip(self.equipos, np.r_[p[: n - 1], -p[: n - 1].sum()]))
        self.dfn = dict(zip(self.equipos, p[n - 1: 2 * n - 1]))
        self.home, self.rho, self.c = p[-3], p[-2], p[-1]
        self.params = p
        return self

    def predict(self, d: pd.DataFrame):
        a = lambda e: self.att.get(e, 0.0)
        f = lambda e: self.dfn.get(e, 0.0)
        lam = np.exp(self.c + self.home + d["HomeTeam"].map(a).values + d["AwayTeam"].map(f).values)
        mu = np.exp(self.c + d["AwayTeam"].map(a).values + d["HomeTeam"].map(f).values)
        return lam, mu


VARS_BASE = [
    "att_l", "def_l", "att_v", "def_v", "forma_att_l", "forma_def_l", "forma_att_v", "forma_def_v",
    "temp_att_l", "temp_def_l", "temp_att_v", "temp_def_v", "rival_def_v", "rival_att_v",
    "rival_def_l", "rival_att_l", "prom_liga_l", "prom_liga_v", "ascendido_l", "ascendido_v",
    "descanso_l", "descanso_v", "h2h_goles", "log_lam_formula", "log_mu_formula",
    "est_tiros_l", "est_tiros_c_l", "est_puerta_l", "est_corners_l", "est_faltas_l",
    "est_tiros_v", "est_tiros_c_v", "est_puerta_v", "est_corners_v", "est_faltas_v",
]
VARS_CONTEXTO = [
    "aporte_ausente_local", "aporte_ausente_visitante", "portero_ausente_local", "portero_ausente_visitante",
    "central_ausente_local", "central_ausente_visitante", "extra_local", "extra_visitante",
    "rotacion_local", "rotacion_visitante", "cambio_entrenador_local", "cambio_entrenador_visitante",
    "juego_titulo", "juego_descenso", "juego_rivalidad",
]


def preparar_matriz(d: pd.DataFrame) -> pd.DataFrame:
    X = d.copy()
    X["log_lam_formula"] = np.log(X["lam_formula"].clip(0.05))
    X["log_mu_formula"] = np.log(X["mu_formula"].clip(0.05))
    X["descanso_l"] = X["descanso_local"].fillna(X["descanso_calc_l"])
    X["descanso_v"] = X["descanso_visitante"].fillna(X["descanso_calc_v"])
    ej = X["en_juego"].astype(str).str.lower()
    X["juego_titulo"] = ej.str.contains("titulo|título").astype(float)
    X["juego_descenso"] = ej.str.contains("descenso").astype(float)
    X["juego_rivalidad"] = ej.str.contains("rivalidad|derbi").astype(float)
    return X


def pesos_temporales(d: pd.DataFrame, cfg: Config) -> np.ndarray:
    """Peso e^(-xi·días) por partido (1 = el más reciente), normalizado a media 1."""
    if not cfg.decaimiento_en_ml:
        return np.ones(len(d))
    w = np.exp(-cfg.xi * (d["Date"].max() - d["Date"]).dt.days.values)
    return w / w.mean()


class RegresionPoisson:
    nombre = "RegresionPoisson"

    def __init__(self, cfg: Config, alpha_l2: float = 0.5):
        self.cfg, self.alpha_l2 = cfg, alpha_l2

    def fit(self, d: pd.DataFrame, columnas: list[str]):
        from sklearn.linear_model import PoissonRegressor
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler
        from sklearn.impute import SimpleImputer
        self.columnas = columnas
        X = d[columnas].values.astype(float)
        w = pesos_temporales(d, self.cfg)
        mk = lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                   PoissonRegressor(alpha=self.alpha_l2, max_iter=1000))
        self.m_l = mk().fit(X, d["FTHG"].values, poissonregressor__sample_weight=w)
        self.m_v = mk().fit(X, d["FTAG"].values, poissonregressor__sample_weight=w)
        return self

    def predict(self, d):
        X = d[self.columnas].values.astype(float)
        return self.m_l.predict(X), self.m_v.predict(X)


class GradientBoosting(RegresionPoisson):
    nombre = "GradientBoosting"

    def __init__(self, cfg: Config, profundidad: int = 3, semilla: int = 0):
        self.cfg, self.prof, self.semilla = cfg, profundidad, semilla

    def fit(self, d, columnas):
        from sklearn.ensemble import HistGradientBoostingRegressor
        self.columnas = columnas
        X = d[columnas].values.astype(float)
        mk = lambda: HistGradientBoostingRegressor(
            loss="poisson", max_depth=self.prof, learning_rate=0.04, max_iter=300,
            min_samples_leaf=30, l2_regularization=1.0, early_stopping=True,
            validation_fraction=0.2, n_iter_no_change=20, random_state=self.semilla)
        w = pesos_temporales(d, self.cfg)
        # con ~1,000 partidos un solo hilo es más rápido que repartir el trabajo entre núcleos
        from threadpoolctl import threadpool_limits
        with threadpool_limits(1):
            self.m_l = mk().fit(X, d["FTHG"].values, sample_weight=w)
            self.m_v = mk().fit(X, d["FTAG"].values, sample_weight=w)
        return self


class BayesGammaPoisson:
    """Ataque y defensa de cada equipo con prior Gamma(k0, k0) (media 1) y verosimilitud
    Poisson; la posterior es Gamma y se puede muestrear para dar intervalos creíbles.
    Los goles 'esperados' de cada partido usan el promedio de la liga y la fuerza del rival."""
    nombre = "Bayesiano"

    def __init__(self, cfg: Config, metrica="goles"):
        self.cfg, self.metrica = cfg, metrica

    def fit(self, d: pd.DataFrame):
        c = self.cfg
        dias = (d["Date"].max() - d["Date"]).dt.days.values
        w = np.exp(-c.xi * dias)
        gl = (d["xg_l"] if self.metrica == "xg" else d["FTHG"]).values.astype(float)
        gv = (d["xg_v"] if self.metrica == "xg" else d["FTAG"]).values.astype(float)
        self.prom_l = float(np.average(gl, weights=w))
        self.prom_v = float(np.average(gv, weights=w))
        # Dos pasadas: la segunda usa la fuerza estimada del rival en la primera
        att = {e: 1.0 for e in set(d["HomeTeam"]) | set(d["AwayTeam"])}
        dfn = dict(att)
        for _ in range(4):
            sa = {e: [c.bayes_k0, c.bayes_k0] for e in att}  # [forma, tasa]
            sd = {e: [c.bayes_k0, c.bayes_k0] for e in att}
            for L, V, a, b, wi in zip(d["HomeTeam"], d["AwayTeam"], gl, gv, w):
                sa[L][0] += wi * a; sa[L][1] += wi * self.prom_l * dfn[V]
                sa[V][0] += wi * b; sa[V][1] += wi * self.prom_v * dfn[L]
                sd[V][0] += wi * a; sd[V][1] += wi * self.prom_l * att[L]
                sd[L][0] += wi * b; sd[L][1] += wi * self.prom_v * att[V]
            att = {e: s[0] / s[1] for e, s in sa.items()}
            dfn = {e: s[0] / s[1] for e, s in sd.items()}
        self.post_att, self.post_def = sa, sd
        return self

    def _ab(self, e, post):
        return post.get(e, [self.cfg.bayes_k0, self.cfg.bayes_k0 / 0.95])

    def predict(self, d):
        lam, mu = [], []
        for L, V in zip(d["HomeTeam"], d["AwayTeam"]):
            aL, aV = self._ab(L, self.post_att), self._ab(V, self.post_att)
            dL, dV = self._ab(L, self.post_def), self._ab(V, self.post_def)
            lam.append(self.prom_l * aL[0] / aL[1] * dV[0] / dV[1])
            mu.append(self.prom_v * aV[0] / aV[1] * dL[0] / dL[1])
        return np.array(lam), np.array(mu)

    def muestras(self, L, V, n, rng):
        aL, aV = self._ab(L, self.post_att), self._ab(V, self.post_att)
        dL, dV = self._ab(L, self.post_def), self._ab(V, self.post_def)
        g = lambda ab: rng.gamma(ab[0], 1 / ab[1], n)
        return self.prom_l * g(aL) * g(dV), self.prom_v * g(aV) * g(dL)


# ---------------------------------------------------------------------------
# CONTEXTO: multiplicadores manuales, árbitro, estilos
# ---------------------------------------------------------------------------


def multiplicadores_contexto(d: pd.DataFrame, cfg: Config, aprendidas: set) -> tuple[np.ndarray, np.ndarray]:
    """Multiplicadores de λ (local) y μ (visitante) para lo que el modelo no pudo aprender."""
    ml = np.ones(len(d)); mv = np.ones(len(d))
    v = lambda c: d[c].fillna(0).astype(float).values if c in d.columns else np.zeros(len(d))
    if "aporte_ausente_local" not in aprendidas:
        ml *= 1 - cfg.factor_reemplazo_ausente * np.clip(v("aporte_ausente_local"), 0, 1)
        mv *= 1 - cfg.factor_reemplazo_ausente * np.clip(v("aporte_ausente_visitante"), 0, 1)
    if "portero_ausente_local" not in aprendidas:
        mv *= np.where(v("portero_ausente_local") > 0, cfg.mult_portero_ausente, 1)
        ml *= np.where(v("portero_ausente_visitante") > 0, cfg.mult_portero_ausente, 1)
    if "central_ausente_local" not in aprendidas:
        mv *= np.where(v("central_ausente_local") > 0, cfg.mult_central_ausente, 1)
        ml *= np.where(v("central_ausente_visitante") > 0, cfg.mult_central_ausente, 1)
    if "extra_local" not in aprendidas:
        corto_l = (v("extra_local") > 0) & (d.get("descanso_local", pd.Series(99, index=d.index)).fillna(99).values <= cfg.dias_descanso_corto)
        corto_v = (v("extra_visitante") > 0) & (d.get("descanso_visitante", pd.Series(99, index=d.index)).fillna(99).values <= cfg.dias_descanso_corto)
        ml *= np.where(corto_l | (v("extra_local") > 0), cfg.mult_partido_extra, 1)
        mv *= np.where(corto_v | (v("extra_visitante") > 0), cfg.mult_partido_extra, 1)
    if "rotacion_local" not in aprendidas:
        ml *= np.where(v("rotacion_local") > 0, cfg.mult_rotacion, 1)
        mv *= np.where(v("rotacion_visitante") > 0, cfg.mult_rotacion, 1)
    return ml, mv


def efecto_arbitro(train: pd.DataFrame, cfg: Config) -> dict:
    """Total de goles con cada árbitro frente al promedio, contraído hacia 1."""
    if "arbitro" not in train.columns or train["arbitro"].isna().all():
        return {}
    t = train.dropna(subset=["arbitro"])
    prom = (t["FTHG"] + t["FTAG"]).mean()
    out = {}
    for a, g in t.groupby("arbitro"):
        n = len(g)
        obs = (g["FTHG"] + g["FTAG"]).mean() / prom
        out[a] = (n / (n + cfg.shrink_k)) * obs + (cfg.shrink_k / (n + cfg.shrink_k))
    return out


class Estilos:
    """Agrupa equipos por estilo (k-means sobre tiros, a puerta, córners, faltas) y estima
    cuánto se desvía el total de goles de cada cruce estilo × estilo, contraído hacia 1."""

    COLS = ["est_tiros", "est_tiros_c", "est_puerta", "est_corners", "est_faltas"]

    def fit(self, train: pd.DataFrame, lam_pred, mu_pred, cfg: Config):
        from sklearn.cluster import KMeans
        from sklearn.preprocessing import StandardScaler
        filas = pd.concat([
            train[[f"{c}_l" for c in self.COLS]].set_axis(self.COLS, axis=1),
            train[[f"{c}_v" for c in self.COLS]].set_axis(self.COLS, axis=1)]).dropna()
        self.ok = len(filas) > 50
        self.efecto = {}
        if not self.ok:
            return self
        self.esc = StandardScaler().fit(filas)
        self.km = KMeans(n_clusters=cfg.n_estilos, n_init=10, random_state=cfg.semilla).fit(self.esc.transform(filas))
        el, ev = self.etiquetas(train)
        real = (train["FTHG"] + train["FTAG"]).values
        pred = np.asarray(lam_pred) + np.asarray(mu_pred)
        tab = pd.DataFrame({"el": el, "ev": ev, "real": real, "pred": pred}).dropna()
        for (a, b), g in tab.groupby(["el", "ev"]):
            n = len(g)
            obs = g["real"].sum() / max(g["pred"].sum(), 1e-6)
            self.efecto[(int(a), int(b))] = (n / (n + cfg.shrink_k)) * obs + cfg.shrink_k / (n + cfg.shrink_k)
        return self

    def etiquetas(self, d):
        if not getattr(self, "ok", False):
            return np.full(len(d), np.nan), np.full(len(d), np.nan)
        out = []
        for lado in ("l", "v"):
            X = d[[f"{c}_{lado}" for c in self.COLS]].set_axis(self.COLS, axis=1)
            lab = np.full(len(d), np.nan)
            ok = X.notna().all(axis=1).values
            if ok.any():
                lab[ok] = self.km.predict(self.esc.transform(X[ok]))
            out.append(lab)
        return out

    def multiplicador(self, d):
        el, ev = self.etiquetas(d)
        return np.array([self.efecto.get((int(a), int(b)), 1.0) if not (np.isnan(a) or np.isnan(b)) else 1.0
                         for a, b in zip(el, ev)])


# ---------------------------------------------------------------------------
# MÉTRICAS Y PRUEBAS
# ---------------------------------------------------------------------------


def metricas_partido(m: np.ndarray, gl: int, gv: int) -> dict:
    """Log-loss del marcador exacto, log-loss y Brier del over 2.5, RPS del 1X2."""
    n = m.shape[0] - 1
    gl_, gv_ = min(int(gl), n), min(int(gv), n)
    p_exacto = max(m[gl_, gv_], 1e-12)
    mk = mercados(m)
    y_over = int(gl + gv >= 3)
    p_over = np.clip(mk["over_2.5"], 1e-12, 1 - 1e-12)
    res = np.array([gl > gv, gl == gv, gl < gv], dtype=float)
    pr = np.array([mk["local"], mk["empate"], mk["visitante"]])
    rps = 0.5 * np.sum((np.cumsum(pr)[:2] - np.cumsum(res)[:2]) ** 2)
    return {
        "ll_marcador": -math.log(p_exacto),
        "ll_over25": -(y_over * math.log(p_over) + (1 - y_over) * math.log(1 - p_over)),
        "brier_over25": (p_over - y_over) ** 2,
        "ll_1x2": -math.log(max(float(pr @ res), 1e-12)),
        "rps_1x2": rps,
        "p_over25": mk["over_2.5"],
        "p_1": pr[0], "p_x": pr[1], "p_2": pr[2],
    }


def metricas_mercado_partido(p_over, p1x2, gl, gv) -> dict:
    """Las mismas métricas, calculadas con las probabilidades del mercado (sin margen)."""
    y_over = int(gl + gv >= 3)
    out = {}
    if p_over is not None:
        p = float(np.clip(p_over, 1e-12, 1 - 1e-12))
        out["ll_over25"] = -(y_over * math.log(p) + (1 - y_over) * math.log(1 - p))
        out["brier_over25"] = (p - y_over) ** 2
    if p1x2 is not None:
        res = np.array([gl > gv, gl == gv, gl < gv], dtype=float)
        pr = np.asarray(p1x2, float)
        out["ll_1x2"] = -math.log(max(float(pr @ res), 1e-12))
        out["rps_1x2"] = 0.5 * np.sum((np.cumsum(pr)[:2] - np.cumsum(res)[:2]) ** 2)
    return out


# Columnas de cuotas (se usa la primera que exista con datos en cada grupo)
CUOTAS_OVER = {
    "apertura": [("Avg>2.5", "Avg<2.5"), ("B365>2.5", "B365<2.5"), ("P>2.5", "P<2.5")],
    "cierre": [("AvgC>2.5", "AvgC<2.5"), ("B365C>2.5", "B365C<2.5"), ("PC>2.5", "PC<2.5")],
}
CUOTAS_1X2 = {
    "apertura": [("AvgH", "AvgD", "AvgA"), ("B365H", "B365D", "B365A"), ("PSH", "PSD", "PSA")],
    "cierre": [("AvgCH", "AvgCD", "AvgCA"), ("B365CH", "B365CD", "B365CA"), ("PSCH", "PSCD", "PSCA")],
}


def prob_mercado_fila(r, grupos) -> list | None:
    """Probabilidades sin margen de la primera fuente de cuotas válida (todas > 1)."""
    for cols in grupos:
        try:
            c = [float(r[k]) for k in cols]
        except (KeyError, TypeError, ValueError):
            continue
        if all(np.isfinite(x) and x > 1.0 for x in c):
            return prob_sin_margen(c)
    return None


def jensen_shannon(p: np.ndarray, q: np.ndarray) -> float:
    p = p.ravel() + 1e-12; q = q.ravel() + 1e-12
    p /= p.sum(); q /= q.sum(); m = 0.5 * (p + q)
    return float(0.5 * np.sum(p * np.log(p / m)) + 0.5 * np.sum(q * np.log(q / m)))


def holm(pvals: dict) -> dict:
    orden = sorted(pvals.items(), key=lambda kv: kv[1])
    k = len(orden); out = {}; previo = 0
    for i, (clave, p) in enumerate(orden):
        aj = min(1.0, max(previo, (k - i) * p)); out[clave] = aj; previo = aj
    return out


def prueba_consenso(perdidas: dict, matrices: dict, cfg: Config) -> dict:
    """Friedman sobre el log-loss por partido; Wilcoxon por parejas con Holm;
    regla de convergencia: rinden igual (p > alfa) y predicen parecido."""
    nombres = list(perdidas)
    L = np.column_stack([perdidas[n] for n in nombres])
    out = {"modelos": nombres}
    if len(nombres) >= 3:
        st, p = stats.friedmanchisquare(*[L[:, i] for i in range(len(nombres))])
        out["friedman"] = {"chi2": float(st), "p": float(p)}
        rangos = stats.rankdata(L, axis=1).mean(axis=0)
        out["rango_medio"] = dict(zip(nombres, map(float, rangos)))
    pw = {}
    for a, b in combinations(nombres, 2):
        dif = perdidas[a] - perdidas[b]
        p = stats.wilcoxon(dif).pvalue if np.any(dif != 0) else 1.0
        pw[(a, b)] = float(p)
    pw_holm = holm(pw)
    pares = {}
    for (a, b), p in pw_holm.items():
        po_a = np.array([mercados(x)["over_2.5"] for x in matrices[a]])
        po_b = np.array([mercados(x)["over_2.5"] for x in matrices[b]])
        delta = float(np.median(np.abs(po_a - po_b)))
        js = float(np.mean([jensen_shannon(x, y) for x, y in zip(matrices[a], matrices[b])]))
        conv = (p > cfg.alfa_test) and (delta < cfg.umbral_delta_over) and (js < cfg.umbral_js)
        pares[f"{a} vs {b}"] = {"p_wilcoxon_holm": p, "delta_over25_mediana": delta, "js_media": js, "convergen": conv}
    out["pares"] = pares
    # grupo de consenso: el mayor conjunto donde todos los pares convergen
    mejor = []
    for r in range(len(nombres), 1, -1):
        for grupo in combinations(nombres, r):
            if all(pares.get(f"{a} vs {b}", pares.get(f"{b} vs {a}"))["convergen"] for a, b in combinations(grupo, 2)):
                ll = np.mean([perdidas[g].mean() for g in grupo])
                if not mejor or ll < mejor[1]:
                    mejor = [list(grupo), ll]
        if mejor:
            break
    ll_medio = {n: float(perdidas[n].mean()) for n in nombres}
    if mejor:
        grupo = mejor[0]
        out["alerta"] = ""
    else:
        # sin consenso: se usan los modelos no significativamente peores que el mejor
        best = min(ll_medio, key=ll_medio.get)
        grupo = [best] + [n for n in nombres if n != best and
                          pw_holm.get((best, n), pw_holm.get((n, best), 0)) > cfg.alfa_test]
        out["alerta"] = "SIN_CONSENSO"
    inv = {n: 1 / ll_medio[n] for n in grupo}
    s = sum(inv.values())
    out["grupo_consenso"] = grupo
    out["pesos_ensamble"] = {n: inv[n] / s for n in grupo}
    out["ll_medio"] = ll_medio
    return out


# ---------------------------------------------------------------------------
# SISTEMA COMPLETO
# ---------------------------------------------------------------------------


class Sistema:
    def __init__(self, cfg: Config = CONFIG):
        self.cfg = cfg

    # --- entrenamiento + validación
    def entrenar(self, df_raw: pd.DataFrame, aj: pd.DataFrame, metrica: str = "goles", verbose=True):
        cfg = self.cfg
        self.metrica = metrica
        con_xg = agregar_xg(df_raw, cfg)
        self.fuentes_xg = con_xg.loc[con_xg["jugado"], "xg_fuente"].value_counts().to_dict()
        self.xg_coef = con_xg.attrs.get("xg_coef")
        df = unir_ajustes(con_xg, aj)
        df, self.hist = construir_variables(df, cfg, metrica)
        df = preparar_matriz(df)
        self.df = df
        jug = df[df["jugado"]].copy()
        corte = int(len(jug) * cfg.frac_entreno)
        train, test = jug.iloc[:corte].copy(), jug.iloc[corte:].copy()
        # partidos sin forma suficiente al inicio del historial (primeras jornadas) se quitan del train
        train = train[train.index >= jug.index[min(60, len(jug) - 1)]] if len(train) > 200 else train
        self.fecha_corte = test["Date"].min()
        self.frac_2t = self._fraccion_segunda_parte(jug)

        # columnas de contexto que sí varían en entrenamiento (las aprende el modelo)
        self.aprendidas = {c for c in VARS_CONTEXTO if c in train.columns and train[c].fillna(0).nunique() > 1}
        self.columnas = VARS_BASE + sorted(self.aprendidas)

        inicio_valido = train.index.min()
        self.factor_cal = {n: 1.0 for n in MODELOS}
        self._ajustar_todo(train)
        # pares (goles predichos sin calibrar, goles reales) para el calibrador: se arranca
        # con los últimos partidos del entrenamiento y se van sumando los de prueba
        self.pares_cal = {n: self._pares_en_muestra(n, train) for n in MODELOS}

        # evaluación en el 30 % de prueba, jornada a jornada (walk-forward)
        mats = {n: [] for n in MODELOS}
        detalle = {n: [] for n in MODELOS}
        dias = (test["Date"] - test["Date"].min()).dt.days.values
        bloques = dias // max(cfg.dias_bloque, 1)
        self.n_bloques = len(np.unique(bloques))
        self.historial_factores = []
        for i, b in enumerate(np.unique(bloques)):
            blq = test[bloques == b]
            if cfg.walk_forward and i > 0:
                hist = jug[(jug.index >= inicio_valido) & (jug["Date"] < blq["Date"].min())]
                self._ajustar_todo(hist)
            self._actualizar_factores()
            self.historial_factores.append({"fecha": blq["Date"].min().date(), **self.factor_cal})
            for nombre in MODELOS:
                lam, mu = self._predecir_lam_mu(nombre, blq)
                rho = self.dc.rho if nombre == "DixonColes" else 0.0
                f = self.factor_cal[nombre]
                for l, u, (_, r) in zip(lam, mu, blq.iterrows()):
                    m = matriz_marcadores(l, u, rho, cfg.max_goles)
                    mats[nombre].append(m)
                    detalle[nombre].append(metricas_partido(m, r["FTHG"], r["FTAG"]))
                    self.pares_cal[nombre].append(((l + u) / f, r["FTHG"] + r["FTAG"]))
        perd = {n: np.array([x["ll_marcador"] for x in detalle[n]]) for n in MODELOS}

        self.consenso = prueba_consenso(perd, mats, cfg)
        w = self.consenso["pesos_ensamble"]
        ens = [sum(w[n] * mats[n][i] for n in w) for i in range(len(test))]
        det_ens = [metricas_partido(m, r["FTHG"], r["FTAG"]) for m, (_, r) in zip(ens, test.iterrows())]

        # modelo base: promedio de la liga en entrenamiento
        base_m = matriz_marcadores(train["FTHG"].mean(), train["FTAG"].mean(), 0, cfg.max_goles)
        det_base = [metricas_partido(base_m, r["FTHG"], r["FTAG"]) for _, r in test.iterrows()]

        resumen = {n: pd.DataFrame(detalle[n]).mean().to_dict() for n in MODELOS}
        resumen["Ensamble"] = pd.DataFrame(det_ens).mean().to_dict()
        resumen["Base (promedio liga)"] = pd.DataFrame(det_base).mean().to_dict()
        mercado, self.vs_mercado = self._metricas_mercado(test, det_ens)
        resumen.update(mercado)
        self.resumen = resumen
        p_ens = [m["p_over25"] for m in det_ens]
        self.calibracion = self._tabla_calibracion(test, p_ens)
        y_over = ((test["FTHG"] + test["FTAG"]) >= 3).values
        self.sesgo_over = {"predicho": float(np.mean(p_ens)), "real": float(np.mean(y_over))}
        self.n_train, self.n_test = len(train), len(test)

        # reajuste final con TODOS los partidos jugados, para predecir los siguientes
        self._ajustar_todo(jug[jug.index >= inicio_valido])
        self._actualizar_factores()
        self.jug_final = jug[jug.index >= inicio_valido]
        self.ultima_fecha = jug["Date"].max()
        if verbose:
            self.imprimir_resumen()
        return self

    def _ajustar_todo(self, d):
        """Modelos + efecto árbitro + estilos, con los partidos de `d`."""
        self._ajustar_modelos(d)
        self.arbitros = efecto_arbitro(d, self.cfg)
        lam_dc, mu_dc = self.dc.predict(d)
        self.estilos = Estilos().fit(d, lam_dc, mu_dc, self.cfg)

    def _pares_en_muestra(self, nombre, d):
        """(goles predichos sin calibrar, goles reales) en los últimos partidos de `d`."""
        cola = d.tail(self.cfg.ventana_calibracion)
        lam, mu = self._predecir_lam_mu(nombre, cola, calibrar=False)
        return list(zip(lam + mu, (cola["FTHG"] + cola["FTAG"]).values))

    def _actualizar_factores(self):
        """Factor de escala por modelo = goles reales / goles predichos en la ventana reciente,
        contraído hacia 1. Solo usa partidos ya jugados (sin fuga de información)."""
        cfg = self.cfg
        for n in MODELOS:
            if not cfg.calibrar_goles:
                self.factor_cal[n] = 1.0
                continue
            ult = self.pares_cal[n][-cfg.ventana_calibracion:]
            pred = sum(p for p, _ in ult)
            real = sum(r for _, r in ult)
            c = cfg.shrink_calibracion_goles
            self.factor_cal[n] = float(np.clip((real + c) / (pred + c), 0.8, 1.25))

    def _fraccion_segunda_parte(self, jug):
        if {"HTHG", "HTAG"}.issubset(jug.columns) and jug["HTHG"].notna().sum() > 50:
            tot = (jug["FTHG"] + jug["FTAG"]).sum()
            ht = (jug["HTHG"] + jug["HTAG"]).sum()
            return float(1 - ht / tot)
        return 0.55

    def _ajustar_modelos(self, d):
        cfg = self.cfg
        self.dc = DixonColes(cfg).fit(d)
        self.glm = RegresionPoisson(cfg).fit(d, self.columnas)
        self.gbm = GradientBoosting(cfg, semilla=cfg.semilla).fit(d, self.columnas)
        self.bayes = BayesGammaPoisson(cfg, self.metrica).fit(d)
        self._train_actual = d

    def _modelo(self, nombre):
        return {"DixonColes": self.dc, "RegresionPoisson": self.glm,
                "GradientBoosting": self.gbm, "Bayesiano": self.bayes}[nombre]

    def _ajustes_finales(self, nombre, d, lam, mu, calibrar=True):
        """Contexto manual (lo no aprendido), árbitro, estilos y calibrador de goles."""
        if calibrar:
            f = getattr(self, "factor_cal", {}).get(nombre, 1.0)
            lam, mu = lam * f, mu * f
        aprendidas = self.aprendidas if nombre in ("RegresionPoisson", "GradientBoosting") else set()
        ml, mv = multiplicadores_contexto(d, self.cfg, aprendidas)
        lam, mu = lam * ml, mu * mv
        if self.arbitros and "arbitro" in d.columns:
            fa = d["arbitro"].map(self.arbitros).fillna(1.0).values
            lam, mu = lam * fa, mu * fa
        if nombre in ("DixonColes", "Bayesiano") and getattr(self, "estilos", None) is not None:
            fe = self.estilos.multiplicador(d)
            lam, mu = lam * fe, mu * fe
        return np.clip(lam, 0.05, 6), np.clip(mu, 0.05, 6)

    def _predecir_lam_mu(self, nombre, d, calibrar=True):
        lam, mu = self._modelo(nombre).predict(d)
        return self._ajustes_finales(nombre, d, np.asarray(lam, float), np.asarray(mu, float), calibrar)

    def _metricas_mercado(self, test, det_ens):
        """Métricas del mercado (apertura y cierre, sin margen) en over 2.5 y 1X2, y comparación
        directa con el ensamble en los MISMOS partidos (Wilcoxon sobre la pérdida por partido)."""
        resumen, comparacion = {}, []
        for momento, etiqueta in (("apertura", "Mercado apertura"), ("cierre", "Mercado cierre")):
            filas = []
            for (_, r), e in zip(test.iterrows(), det_ens):
                po = prob_mercado_fila(r, CUOTAS_OVER[momento])
                p3 = prob_mercado_fila(r, CUOTAS_1X2[momento])
                mm = metricas_mercado_partido(po[0] if po else None, p3, r["FTHG"], r["FTAG"])
                filas.append({**{f"m_{k}": v for k, v in mm.items()}, **{f"e_{k}": e[k] for k in mm}})
            t = pd.DataFrame(filas)
            fila_res = {}
            for met in ("ll_over25", "brier_over25", "ll_1x2", "rps_1x2"):
                col = f"m_{met}"
                if col not in t.columns or t[col].notna().sum() < 30:
                    continue
                ok = t[col].notna()
                fila_res[met] = float(t.loc[ok, col].mean())
                dif = (t.loc[ok, f"e_{met}"] - t.loc[ok, col]).values
                p = stats.wilcoxon(dif).pvalue if np.any(dif != 0) else 1.0
                comparacion.append({"mercado": etiqueta, "métrica": met, "n": int(ok.sum()),
                                    "ensamble": float(t.loc[ok, f"e_{met}"].mean()),
                                    "mercado_valor": fila_res[met], "diferencia": float(dif.mean()),
                                    "p_wilcoxon": float(p)})
            if fila_res:
                fila_res["n"] = int(t.filter(like="m_").notna().any(axis=1).sum())
                resumen[etiqueta] = fila_res
        return resumen, pd.DataFrame(comparacion)

    def _tabla_calibracion(self, test, p_over):
        y = ((test["FTHG"] + test["FTAG"]) >= 3).astype(int).values
        t = pd.DataFrame({"p": p_over, "y": y})
        t["tramo"] = pd.cut(t["p"], bins=np.arange(0.2, 0.86, 0.05))
        return t.groupby("tramo", observed=True).agg(n=("y", "size"), p_media=("p", "mean"), frecuencia_real=("y", "mean")).reset_index()

    # --- predicción de partidos nuevos
    def fila_partido(self, local, visitante, fecha, ajustes: dict | None = None) -> pd.DataFrame:
        fecha = pd.Timestamp(fecha)
        temporada = fecha.year if fecha.month >= 7 else fecha.year - 1
        fila = {"Date": fecha, "HomeTeam": local, "AwayTeam": visitante, "temporada": temporada, "jugado": False}
        var = self.hist.variables(fila)
        var.pop("_esp")
        alertas = var.pop("_alertas")
        conocidos = set(self.hist.partidos)
        for e in (local, visitante):
            if e not in conocidos:
                alertas.append(f"EQUIPO_DESCONOCIDO:{e} (revisa el nombre; football-data usa p. ej. 'Ath Madrid')")
        d = {**fila, **var}
        aj = {c: np.nan for c in COLS_AJUSTES}
        aj.update(ajustes or {})
        if all(pd.isna(aj.get(c)) for c in COLS_AJUSTES if c != "arbitro"):
            alertas.append("SIN_CONTEXTO")
        d.update(aj)
        d["alertas"] = ";".join(alertas)
        return preparar_matriz(pd.DataFrame([d]))

    def predecir(self, local, visitante, fecha, ajustes=None, n_boot=None, minuto=None, marcador=(0, 0),
                 rojas=(0, 0)):
        """Matrices por modelo + ensamble, con muestras para el intervalo del 90 %."""
        cfg = self.cfg
        n_boot = n_boot or cfg.n_bootstrap
        d = self.fila_partido(local, visitante, fecha, ajustes)
        rng = np.random.default_rng(cfg.semilla)

        def a_matriz(l, u, rho):
            if minuto is None:
                return matriz_marcadores(l, u, rho, cfg.max_goles)
            return matriz_vivo(l, u, rho, minuto, marcador[0], marcador[1], rojas[0], rojas[1], self.frac_2t, cfg)

        res = {"partido": f"{local} vs {visitante}", "fecha": str(pd.Timestamp(fecha).date()),
               "alertas": d["alertas"].iloc[0], "modelos": {}}
        if minuto is not None:
            res["vivo"] = {"minuto": minuto, "marcador": f"{marcador[0]}-{marcador[1]}", "rojas": list(rojas)}
        jug = self.jug_final
        for nombre in MODELOS:
            lam, mu = self._predecir_lam_mu(nombre, d)
            rho = self.dc.rho if nombre == "DixonColes" else 0.0
            m = a_matriz(lam[0], mu[0], rho)
            # incertidumbre del modelo
            muestras = []
            if nombre == "Bayesiano":
                ls, us = self.bayes.muestras(local, visitante, n_boot, rng)
                f_l, f_u = lam[0] / max(self.bayes.predict(d)[0][0], 1e-9), mu[0] / max(self.bayes.predict(d)[1][0], 1e-9)
                muestras = [(l * f_l, u * f_u) for l, u in zip(ls, us)]
            else:
                muestras = self._bootstrap(nombre, jug, d, n_boot, rng)
            # el bootstrap puede tener sesgo: se centra en la estimación puntual para que
            # el intervalo mida solo la dispersión (bootstrap "centrado" en escala log)
            ls_ = np.array([x[0] for x in muestras]); us_ = np.array([x[1] for x in muestras])
            ls_ *= lam[0] / np.median(ls_); us_ *= mu[0] / np.median(us_)
            muestras = list(zip(ls_, us_))
            mats_b = [a_matriz(l, u, rho) for l, u in muestras]
            res["modelos"][nombre] = {"lam": float(lam[0]), "mu": float(mu[0]), "matriz": m,
                                      "muestras": mats_b}
        w = self.consenso["pesos_ensamble"]
        ens = sum(w[n] * res["modelos"][n]["matriz"] for n in w)
        nmin = min(len(res["modelos"][n]["muestras"]) for n in w)
        ens_b = [sum(w[n] * res["modelos"][n]["muestras"][i] for n in w) for i in range(nmin)]
        res["modelos"]["Ensamble"] = {"lam": float(sum(w[n] * res["modelos"][n]["lam"] for n in w)),
                                      "mu": float(sum(w[n] * res["modelos"][n]["mu"] for n in w)),
                                      "matriz": ens, "muestras": ens_b}
        res["pesos_ensamble"] = w
        res["alerta_consenso"] = self.consenso.get("alerta", "")
        return res

    def _bootstrap(self, nombre, jug, d, n_boot, rng):
        """Reajusta el modelo con remuestreos de partidos y devuelve (λ, μ) por remuestreo."""
        cfg = self.cfg
        out = []
        # los modelos de árboles son más lentos: se limita su nº de remuestreos
        nb = n_boot if nombre != "GradientBoosting" else max(20, n_boot // 3)
        # los modelos remuestreados se ajustan una vez por sesión y se reutilizan en todos los partidos
        if not hasattr(self, "_cache_boot"):
            self._cache_boot = {}
        clave = (nombre, nb)
        if clave not in self._cache_boot:
            rng_b = np.random.default_rng(cfg.semilla + 1)
            modelos = []
            for b in range(nb):
                idx = rng_b.integers(0, len(jug), len(jug))
                muestra = jug.iloc[idx]
                if nombre == "DixonColes":
                    mismo = set(muestra["HomeTeam"]) | set(muestra["AwayTeam"]) == set(self.dc.equipos)
                    mdl = DixonColes(cfg).fit(muestra, x0=self.dc.params if mismo else None)
                elif nombre == "RegresionPoisson":
                    mdl = RegresionPoisson(cfg).fit(muestra, self.columnas)
                else:
                    mdl = GradientBoosting(cfg, semilla=b).fit(muestra, self.columnas)
                modelos.append(mdl)
            self._cache_boot[clave] = modelos
        for mdl in self._cache_boot[clave]:
            l, u = mdl.predict(d)
            l, u = self._ajustes_finales(nombre, d, np.asarray(l, float), np.asarray(u, float))
            out.append((l[0], u[0]))
        return out

    # --- informes
    def imprimir_resumen(self):
        modo = (f"jornada a jornada ({self.n_bloques} reentrenamientos)" if self.cfg.walk_forward
                else "modelo fijo")
        print(f"\nPartidos de entrenamiento: {self.n_train} | prueba: {self.n_test} | "
              f"corte: {self.fecha_corte.date()} | métrica de forma: {self.metrica} | evaluación: {modo}")
        tabla = pd.DataFrame(self.resumen).T
        cols = [c for c in ["ll_marcador", "ll_over25", "brier_over25", "ll_1x2", "rps_1x2", "n"] if c in tabla.columns]
        print("\nMétricas en el 30 % de prueba (más bajo = mejor):")
        print(tabla[cols].round(4).to_string())
        s = self.sesgo_over
        print(f"\nOver 2.5 en prueba: el ensamble esperaba {s['predicho']*100:.1f}% | ocurrió {s['real']*100:.1f}% "
              f"| sesgo {(s['predicho'] - s['real'])*100:+.1f} puntos")
        if self.metrica == "xg":
            a_p, a_f = self.xg_coef or (np.nan, np.nan)
            print(f"Fuente del xG por partido: {self.fuentes_xg} | xG aproximado = {a_p:.3f}·tiros a puerta "
                  f"+ {a_f:.3f}·tiros fuera")
        print("Factor del calibrador de goles (actual):",
              {k: round(v, 3) for k, v in self.factor_cal.items()})
        if len(self.vs_mercado):
            print("\nEnsamble contra el mercado en los mismos partidos (diferencia < 0 = el ensamble es mejor):")
            v = self.vs_mercado.copy()
            v["veredicto"] = np.where(v["p_wilcoxon"] >= 0.05, "sin diferencia significativa",
                                      np.where(v["diferencia"] < 0, "ENSAMBLE MEJOR", "mercado mejor"))
            print(v.round(4).to_string(index=False))
        c = self.consenso
        if "friedman" in c:
            print(f"\nFriedman: chi2 = {c['friedman']['chi2']:.2f}, p = {c['friedman']['p']:.4f}")
            print("Rango medio (1 = mejor):", {k: round(v, 2) for k, v in c["rango_medio"].items()})
        print("\nWilcoxon por parejas (Holm) y convergencia:")
        for par, v in c["pares"].items():
            print(f"  {par:40s} p={v['p_wilcoxon_holm']:.3f}  ΔP(over)={v['delta_over25_mediana']:.3f}  "
                  f"JS={v['js_media']:.4f}  {'CONVERGEN' if v['convergen'] else '-'}")
        print("\nGrupo de consenso:", c["grupo_consenso"], "| pesos:",
              {k: round(v, 3) for k, v in c["pesos_ensamble"].items()}, c.get("alerta", ""))
        print("\nCalibración del ensamble (over 2.5):")
        print(self.calibracion.round(3).to_string(index=False))


# ---------------------------------------------------------------------------
# SALIDA: texto, gráfica, archivos
# ---------------------------------------------------------------------------


def intervalo(vals, cfg):
    lo, hi = np.percentile(vals, cfg.intervalo)
    return float(lo), float(hi)


def informe_partido(res, cfg: Config, cuotas: dict | None = None) -> str:
    lineas = [f"\n=== {res['partido']} ({res['fecha']}) ==="]
    if "vivo" in res:
        v = res["vivo"]
        lineas.append(f"EN VIVO: minuto {v['minuto']}, marcador {v['marcador']}, rojas {v['rojas']}")
        lineas.append("(λ y μ son del partido completo; las probabilidades ya parten del marcador actual)")
    if res["alertas"]:
        lineas.append(f"Alertas: {res['alertas']}")
    if res.get("alerta_consenso"):
        lineas.append(f"Alerta: {res['alerta_consenso']} (no se recomienda apostar)")
    lineas.append(f"{'Modelo':18s} {'λ':>5s} {'μ':>5s}  {'Over 1.5':>17s} {'Over 2.5':>17s} {'Over 3.5':>17s}  1 / X / 2")
    for nombre, r in res["modelos"].items():
        mk = mercados(r["matriz"])
        mb = [mercados(x) for x in r["muestras"]]
        celdas = []
        for k in ("over_1.5", "over_2.5", "over_3.5"):
            lo, hi = intervalo([x[k] for x in mb], cfg) if mb else (np.nan, np.nan)
            celdas.append(f"{mk[k]*100:4.1f}% ({lo*100:4.1f}-{hi*100:4.1f})")
        lineas.append(f"{nombre:18s} {r['lam']:5.2f} {r['mu']:5.2f}  " + " ".join(celdas) +
                      f"  {mk['local']*100:.0f}/{mk['empate']*100:.0f}/{mk['visitante']*100:.0f}")
    ens = res["modelos"]["Ensamble"]
    m = ens["matriz"]
    top = sorted(((m[i, j], f"{i}-{j}") for i in range(m.shape[0]) for j in range(m.shape[1])), reverse=True)[:8]
    lineas.append("Marcadores más probables (ensamble): " + ", ".join(f"{s} {p*100:.1f}%" for p, s in top))
    lineas.append(f"Suma de la matriz del ensamble: {m.sum():.6f}")
    if cuotas:
        mk = mercados(m)
        mb = [mercados(x) for x in ens["muestras"]]
        if cuotas.get("over") and cuotas.get("under"):
            p_merc = prob_sin_margen([cuotas["over"], cuotas["under"]])
            lo, hi = intervalo([x["over_2.5"] for x in mb], cfg)
            for lado, p, c, pm in (("Over 2.5", mk["over_2.5"], cuotas["over"], p_merc[0]),
                                   ("Under 2.5", 1 - mk["over_2.5"], cuotas["under"], p_merc[1])):
                ev = p * c - 1
                fiable = (pm < lo or pm > hi) if lado == "Over 2.5" else (1 - pm < lo or 1 - pm > hi)
                lineas.append(f"{lado}: modelo {p*100:.1f}% | mercado sin margen {pm*100:.1f}% | cuota {c:.2f} "
                              f"(justa {1/p:.2f}) | VE {ev*100:+.1f}% | "
                              f"{'mercado FUERA del intervalo' if fiable else 'mercado dentro del intervalo: valor no fiable'}")
        if cuotas.get("ah") is not None and cuotas.get("ah_local") and cuotas.get("ah_visit"):
            e = ev_handicap(m, cuotas["ah"], cuotas["ah_local"], cuotas["ah_visit"])
            lineas.append(f"Hándicap asiático local {cuotas['ah']:+.2f}: VE local {e['ev_local']*100:+.1f}% | "
                          f"VE visitante {e['ev_visit']*100:+.1f}%")
    return "\n".join(lineas)


COLORES = {"DixonColes": "#2a78d6", "RegresionPoisson": "#eb6834", "GradientBoosting": "#1baf7a",
           "Bayesiano": "#eda100", "Ensamble": "#0b0b0b"}


def grafica_partido(res, cfg: Config, ruta: str, cuota_over=None, cuota_under=None):
    """Tres paneles: distribución de P(over 2.5) por modelo, top 10 marcadores con
    intervalo del 90 %, y mapa de calor de la matriz del ensamble."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tinta, tinta2, rejilla = "#0b0b0b", "#52514e", "#e4e3df"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": rejilla, "axes.labelcolor": tinta2,
                         "xtick.color": tinta2, "ytick.color": tinta2, "axes.spines.top": False,
                         "axes.spines.right": False})
    fig = plt.figure(figsize=(16, 5.6), facecolor="#fcfcfb")
    gs = fig.add_gridspec(1, 3, width_ratios=[1.1, 1.5, 0.9], wspace=0.28)

    # Panel 1: distribución de P(over 2.5)
    ax = fig.add_subplot(gs[0]); ax.set_facecolor("#fcfcfb")
    for nombre, r in res["modelos"].items():
        vals = np.array([mercados(x)["over_2.5"] for x in r["muestras"]]) * 100
        if len(vals) < 3 or np.std(vals) < 1e-6:
            ax.axvline(mercados(r["matriz"])["over_2.5"] * 100, color=COLORES[nombre], lw=2, label=nombre)
            continue
        kde = stats.gaussian_kde(vals)
        xs = np.linspace(vals.min() - 4, vals.max() + 4, 200)
        lw = 2.6 if nombre == "Ensamble" else 2
        ax.plot(xs, kde(xs), color=COLORES[nombre], lw=lw, label=nombre)
    if cuota_over and cuota_under:
        pm = prob_sin_margen([cuota_over, cuota_under])[0] * 100
        ax.axvline(pm, color=tinta2, lw=1.5, ls="--", label=f"Mercado sin margen ({pm:.1f}%)")
    ax.set_xlabel("P(over 2.5), %"); ax.set_ylabel("densidad")
    ax.set_title("Distribución de P(over 2.5) por modelo", loc="left", color=tinta, fontsize=11)
    ax.grid(axis="y", color=rejilla, lw=0.8)
    ax.legend(frameon=False, fontsize=8.5, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.14))

    # Panel 2: top 10 marcadores con intervalo
    ax = fig.add_subplot(gs[1]); ax.set_facecolor("#fcfcfb")
    m = res["modelos"]["Ensamble"]["matriz"]
    top = sorted(((m[i, j], i, j) for i in range(m.shape[0]) for j in range(m.shape[1])), reverse=True)[:10]
    nombres = list(res["modelos"])
    ancho = 0.8 / len(nombres)
    for k, nombre in enumerate(nombres):
        r = res["modelos"][nombre]
        p = np.array([r["matriz"][i, j] for _, i, j in top]) * 100
        bs = np.array([[x[i, j] for _, i, j in top] for x in r["muestras"]]) * 100 if r["muestras"] else None
        err = None
        if bs is not None and len(bs) > 2:
            lo, hi = np.percentile(bs, cfg.intervalo, axis=0)
            err = np.vstack([np.clip(p - lo, 0, None), np.clip(hi - p, 0, None)])
        xs = np.arange(len(top)) + (k - (len(nombres) - 1) / 2) * ancho
        ax.bar(xs, p, width=ancho * 0.9, color=COLORES[nombre], label=nombre, edgecolor="#fcfcfb", lw=1)
        if err is not None:
            ax.errorbar(xs, p, yerr=err, fmt="none", ecolor=tinta2, elinewidth=0.8, capsize=1.5)
    ax.set_xticks(np.arange(len(top)), [f"{i}-{j}" for _, i, j in top])
    ax.set_ylabel("probabilidad, %"); ax.grid(axis="y", color=rejilla, lw=0.8)
    ax.set_title("10 marcadores más probables, con intervalo del 90 %", loc="left", color=tinta, fontsize=11)
    ax.set_ylim(0, ax.get_ylim()[1] * 1.18)
    ax.legend(frameon=False, fontsize=8, ncol=5, loc="upper center", bbox_to_anchor=(0.5, 1.0),
              handlelength=1.0, columnspacing=0.8)

    # Panel 3: mapa de calor del ensamble (0-6)
    ax = fig.add_subplot(gs[2])
    n = 7
    sub = m[:n, :n] * 100
    ax.imshow(sub, cmap="Blues", origin="upper")
    for i in range(n):
        for j in range(n):
            ax.text(j, i, f"{sub[i, j]:.1f}", ha="center", va="center", fontsize=7.5,
                    color="#fcfcfb" if sub[i, j] > sub.max() * 0.55 else tinta)
    ax.set_xticks(range(n)); ax.set_yticks(range(n))
    ax.set_xlabel("goles visitante"); ax.set_ylabel("goles local")
    ax.set_title("Matriz del ensamble, %", loc="left", color=tinta, fontsize=11)
    for s in ax.spines.values():
        s.set_visible(False)

    titulo = res["partido"] + (f" · en vivo min {res['vivo']['minuto']}, {res['vivo']['marcador']}" if "vivo" in res else "")
    fig.suptitle(titulo, x=0.06, ha="left", fontsize=13, color=tinta, fontweight="bold")
    fig.savefig(ruta, dpi=130, bbox_inches="tight")
    plt.close(fig)


def guardar_matriz_csv(res, ruta):
    filas = []
    for nombre, r in res["modelos"].items():
        m = r["matriz"]
        for i in range(m.shape[0]):
            for j in range(m.shape[1]):
                filas.append({"partido": res["partido"], "fecha": res["fecha"], "modelo": nombre,
                              "goles_local": i, "goles_visitante": j, "probabilidad": m[i, j]})
    pd.DataFrame(filas).to_csv(ruta, index=False)


def nombre_archivo(texto):
    return "".join(ch if ch.isalnum() else "_" for ch in texto).strip("_")


# ---------------------------------------------------------------------------
# LÍNEA DE COMANDOS
# ---------------------------------------------------------------------------


def cmd_entrenar(a):
    df = cargar_datos(a.datos)
    aj = cargar_ajustes(a.ajustes)
    cfg = CONFIG
    if a.bootstrap:
        cfg.n_bootstrap = a.bootstrap
    metricas = ["goles", "xg"] if a.metrica == "ambas" else [a.metrica]
    sistemas = {}
    for met in metricas:
        print(f"\n######## Entrenando con métrica de forma: {met} ########")
        sistemas[met] = Sistema(cfg).entrenar(df, aj, met)
    if len(sistemas) > 1:
        ll = {m: s.resumen["Ensamble"]["ll_marcador"] for m, s in sistemas.items()}
        elegido = min(ll, key=ll.get)
        print(f"\nComparación goles vs xG (log-loss marcador, ensamble): {ll} -> se usa '{elegido}'")
    else:
        elegido = metricas[0]
    s = sistemas[elegido]
    s.rutas_datos, s.ruta_ajustes = a.datos, a.ajustes
    with open(a.modelo, "wb") as f:
        pickle.dump(s, f)
    informe = {"metrica": elegido,
               "fecha_entrenamiento": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
               "ultima_fecha_datos": str(s.ultima_fecha.date()),
               "n_partidos": int(len(s.jug_final)), "n_entreno": s.n_train, "n_prueba": s.n_test,
               "comparacion_metricas": ({m: x.resumen["Ensamble"] for m, x in sistemas.items()}),
               "resumen": s.resumen,
               "consenso": {k: v for k, v in s.consenso.items()},
               "calibracion": s.calibracion.astype(str).to_dict(orient="records"),
               "sesgo_over25": s.sesgo_over, "factor_calibrador_goles": s.factor_cal,
               "evolucion_factor_por_bloque": s.historial_factores,
               "ensamble_vs_mercado": s.vs_mercado.to_dict(orient="records")}
    Path("informe_entrenamiento.json").write_text(json.dumps(informe, indent=2, ensure_ascii=False, default=str),
                                                  encoding="utf-8")
    print(f"\nModelo guardado en {a.modelo}; informe en informe_entrenamiento.json")


def cargar_sistema(ruta) -> Sistema:
    with open(ruta, "rb") as f:
        return pickle.load(f)


def _ajustes_partido(sist: Sistema, ruta_aj, fecha, local, visitante):
    aj = cargar_ajustes(ruta_aj)
    if not len(aj):
        return None
    f = aj[(aj["HomeTeam"].str.strip() == local) & (aj["AwayTeam"].str.strip() == visitante) &
           (aj["Date"].dt.date == pd.Timestamp(fecha).date())]
    return f.iloc[0][COLS_AJUSTES].to_dict() if len(f) else None


def _salida(sist, res, a, cuotas, sufijo=""):
    """Imprime el informe, guarda gráfica y matrices y devuelve una fila para el resumen."""
    cfg = sist.cfg
    print(informe_partido(res, cfg, cuotas))
    base = nombre_archivo(res["partido"] + "_" + res["fecha"] + sufijo)
    Path(a.salida).mkdir(parents=True, exist_ok=True)
    png = Path(a.salida) / f"{base}.png"
    grafica_partido(res, cfg, str(png), cuotas.get("over"), cuotas.get("under"))
    guardar_matriz_csv(res, Path(a.salida) / f"{base}_matrices.csv")
    print(f"Gráfica: {png}")

    ens = res["modelos"]["Ensamble"]
    m = ens["matriz"]
    mk = mercados(m)
    lo, hi = intervalo([mercados(x)["over_2.5"] for x in ens["muestras"]], cfg) if ens["muestras"] else (np.nan, np.nan)
    i, j = np.unravel_index(np.argmax(m), m.shape)
    fila = {
        "partido": res["partido"], "fecha": res["fecha"],
        "goles_esperados": round(mk["goles_esperados"], 2),
        "p_over15": round(mk["over_1.5"], 4), "p_over25": round(mk["over_2.5"], 4),
        "p_over25_ic_bajo": round(lo, 4), "p_over25_ic_alto": round(hi, 4),
        "p_over35": round(mk["over_3.5"], 4),
        "p_local": round(mk["local"], 4), "p_empate": round(mk["empate"], 4), "p_visitante": round(mk["visitante"], 4),
        "marcador_mas_probable": f"{i}-{j}", "p_marcador": round(float(m[i, j]), 4),
        "cuota_over25": cuotas.get("over"), "cuota_under25": cuotas.get("under"),
        "mercado_over25_sin_margen": None, "ve_over25": None, "ve_under25": None,
        "alertas": ";".join(x for x in [res.get("alertas", ""), res.get("alerta_consenso", "")] if x),
        "grafica": png.name,
    }
    if cuotas.get("over") and cuotas.get("under"):
        fila["mercado_over25_sin_margen"] = round(prob_sin_margen([cuotas["over"], cuotas["under"]])[0], 4)
        fila["ve_over25"] = round(mk["over_2.5"] * cuotas["over"] - 1, 4)
        fila["ve_under25"] = round((1 - mk["over_2.5"]) * cuotas["under"] - 1, 4)
    return fila


def guardar_resumen(filas: list[dict], carpeta: str):
    """resumen.csv (para análisis) y resumen.md (tabla que GitHub muestra directamente)."""
    if not filas:
        return
    Path(carpeta).mkdir(parents=True, exist_ok=True)
    t = pd.DataFrame(filas)
    t.to_csv(Path(carpeta) / "resumen.csv", index=False)
    pct = lambda x: "" if x is None or pd.isna(x) else f"{x*100:.1f}%"
    lineas = ["| Partido | Fecha | Goles esp. | Over 2.5 (IC 90%) | Over 1.5 | Over 3.5 | 1 / X / 2 | Marcador más probable | Cuota over / under | Mercado over | VE over / under | Alertas |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in t.iterrows():
        cuota = "" if pd.isna(r["cuota_over25"]) else f"{r['cuota_over25']:.2f} / {r['cuota_under25']:.2f}"
        ve = "" if pd.isna(r["ve_over25"]) else f"{r['ve_over25']*100:+.1f}% / {r['ve_under25']*100:+.1f}%"
        lineas.append(
            f"| [{r['partido']}]({r['grafica']}) | {r['fecha']} | {r['goles_esperados']:.2f} | "
            f"{pct(r['p_over25'])} ({pct(r['p_over25_ic_bajo'])}–{pct(r['p_over25_ic_alto'])}) | {pct(r['p_over15'])} | "
            f"{pct(r['p_over35'])} | {r['p_local']*100:.0f} / {r['p_empate']*100:.0f} / {r['p_visitante']*100:.0f} | "
            f"{r['marcador_mas_probable']} ({pct(r['p_marcador'])}) | {cuota} | {pct(r['mercado_over25_sin_margen'])} | "
            f"{ve} | {str(r['alertas']).replace(';', ', ')} |")
    texto = ("# Predicciones\n\nProbabilidades del ensamble. El intervalo del 90 % mide la incertidumbre del modelo. "
             "Un VE positivo solo es fiable si el mercado queda fuera del intervalo. "
             "Haz clic en un partido para ver su gráfica.\n\n" + "\n".join(lineas) + "\n")
    (Path(carpeta) / "resumen.md").write_text(texto, encoding="utf-8")
    print(f"\nResumen: {Path(carpeta) / 'resumen.md'}")


def primera_valida(r, *cols):
    """Primer valor numérico válido (> 1 para cuotas) entre varias columnas de una fila."""
    for c in cols:
        v = r.get(c)
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(v) and v > 1.0:
            return v
    return None


def cmd_predecir(a):
    sist = cargar_sistema(a.modelo)
    if a.bootstrap:
        sist.cfg.n_bootstrap = a.bootstrap
    ruta_aj = a.ajustes or getattr(sist, "ruta_ajustes", None)
    if a.partidos:
        try:
            fx = pd.read_csv(a.partidos, encoding="utf-8-sig")
        except UnicodeDecodeError:
            fx = pd.read_csv(a.partidos, encoding="latin-1")
        fx.columns = [c.replace("ï»¿", "").strip() for c in fx.columns]
        if "Div" in fx.columns:
            fx = fx[fx["Div"] == "SP1"]
        fx["Date"] = pd.to_datetime(fx["Date"], dayfirst=True)
        filas = []
        for _, r in fx.iterrows():
            # la línea de hándicap puede ser 0 o negativa: no se filtra como cuota
            ah = pd.to_numeric(r.get("AHh"), errors="coerce") if r.get("AHh") is not None else np.nan
            cuotas = {"over": primera_valida(r, "Avg>2.5", "B365>2.5", "Max>2.5"),
                      "under": primera_valida(r, "Avg<2.5", "B365<2.5", "Max<2.5"),
                      "ah": None if pd.isna(ah) else float(ah),
                      "ah_local": primera_valida(r, "AvgAHH", "B365AHH"),
                      "ah_visit": primera_valida(r, "AvgAHA", "B365AHA")}
            res = sist.predecir(r["HomeTeam"], r["AwayTeam"], r["Date"],
                                _ajustes_partido(sist, ruta_aj, r["Date"], r["HomeTeam"], r["AwayTeam"]))
            filas.append(_salida(sist, res, a, cuotas))
        guardar_resumen(filas, a.salida)
    else:
        cuotas = {"over": a.cuota_over, "under": a.cuota_under, "ah": a.ah,
                  "ah_local": a.cuota_ah_local, "ah_visit": a.cuota_ah_visit}
        fecha = a.fecha or str(pd.Timestamp.today().date())
        res = sist.predecir(a.local, a.visitante, fecha, _ajustes_partido(sist, ruta_aj, fecha, a.local, a.visitante))
        _salida(sist, res, a, cuotas)


def cmd_vivo(a):
    sist = cargar_sistema(a.modelo)
    if a.bootstrap:
        sist.cfg.n_bootstrap = a.bootstrap
    gl, gv = (int(x) for x in a.marcador.split("-"))
    fecha = a.fecha or str(pd.Timestamp.today().date())
    res = sist.predecir(a.local, a.visitante, fecha,
                        _ajustes_partido(sist, a.ajustes or getattr(sist, "ruta_ajustes", None), fecha, a.local, a.visitante),
                        minuto=a.minuto, marcador=(gl, gv), rojas=(a.rojas_local, a.rojas_visit))
    cuotas = {"over": a.cuota_over, "under": a.cuota_under}
    _salida(sist, res, a, cuotas, sufijo=f"_min{a.minuto}")


def cmd_recalibrar(a):
    """Evalúa los partidos jugados desde el último entrenamiento con el modelo anterior,
    y después reentrena con todo (misma rutina que 'entrenar')."""
    viejo = cargar_sistema(a.modelo)
    df = cargar_datos(a.datos)
    nuevos = df[df["jugado"] & (df["Date"] > viejo.ultima_fecha)]
    if len(nuevos):
        filas = []
        for _, r in nuevos.iterrows():
            res = viejo.predecir(r["HomeTeam"], r["AwayTeam"], r["Date"], n_boot=1)
            fila = {"partido": res["partido"], "marcador": f"{int(r['FTHG'])}-{int(r['FTAG'])}"}
            for n in MODELOS + ["Ensamble"]:
                fila[n] = metricas_partido(res["modelos"][n]["matriz"], r["FTHG"], r["FTAG"])["ll_marcador"]
            filas.append(fila)
        t = pd.DataFrame(filas)
        print(f"\nJornada evaluada con el modelo anterior ({len(t)} partidos), log-loss del marcador:")
        print(t.round(3).to_string(index=False))
        print("\nMedia:", t[MODELOS + ["Ensamble"]].mean().round(3).to_dict())
    else:
        print("No hay partidos nuevos desde el último entrenamiento.")
    a.metrica = a.metrica or viejo.metrica
    a.ajustes = a.ajustes or getattr(viejo, "ruta_ajustes", None)
    cmd_entrenar(a)


def main(argv=None):
    p = argparse.ArgumentParser(description="Modelo de marcadores exactos LaLiga")
    sub = p.add_subparsers(dest="cmd", required=True)
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--modelo", default=ARCHIVO_MODELO)
    comun.add_argument("--ajustes", default=None)
    comun.add_argument("--bootstrap", type=int, default=None, help="nº de remuestreos para el intervalo")

    e = sub.add_parser("entrenar", parents=[comun])
    e.add_argument("--datos", nargs="+", required=True)
    e.add_argument("--metrica", choices=["goles", "xg", "ambas"], default="ambas")
    e.set_defaults(f=cmd_entrenar)

    r = sub.add_parser("recalibrar", parents=[comun])
    r.add_argument("--datos", nargs="+", required=True)
    r.add_argument("--metrica", choices=["goles", "xg", "ambas"], default=None)
    r.set_defaults(f=cmd_recalibrar)

    for nombre, fn in (("predecir", cmd_predecir), ("vivo", cmd_vivo)):
        q = sub.add_parser(nombre, parents=[comun])
        q.add_argument("--local"); q.add_argument("--visitante"); q.add_argument("--fecha")
        q.add_argument("--cuota-over", type=float); q.add_argument("--cuota-under", type=float)
        q.add_argument("--salida", default="predicciones")
        if nombre == "predecir":
            q.add_argument("--partidos", help="CSV con HomeTeam, AwayTeam, Date (p. ej. fixtures.csv)")
            q.add_argument("--ah", type=float, help="línea de hándicap asiático del local, p. ej. -0.75")
            q.add_argument("--cuota-ah-local", type=float); q.add_argument("--cuota-ah-visit", type=float)
        else:
            q.add_argument("--minuto", type=int, required=True)
            q.add_argument("--marcador", default="0-0")
            q.add_argument("--rojas-local", type=int, default=0)
            q.add_argument("--rojas-visit", type=int, default=0)
        q.set_defaults(f=fn)

    a = p.parse_args(argv)
    if a.cmd == "predecir" and not a.partidos and not (a.local and a.visitante):
        p.error("predecir necesita --partidos o --local y --visitante")
    if a.cmd == "vivo" and not (a.local and a.visitante):
        p.error("vivo necesita --local y --visitante")
    a.f(a)


if __name__ == "__main__":
    main()
