#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Descarga los CSV de LaLiga (SP1) de football-data.co.uk.

- Temporadas: las `--temporadas-atras` completas más la temporada en curso
  (la temporada empieza en julio). Ej. en octubre de 2026: 2324, 2425, 2526 y 2627.
- Reintenta si la página falla o limita las descargas (error 429).
- Si una descarga falla, conserva el archivo que ya está en la carpeta (respaldo).
- Opcional: guarda los próximos partidos de LaLiga con cuotas (fixtures.csv).

En GitHub Actions escribe en $GITHUB_OUTPUT:
  archivos      rutas de los CSV a usar, en orden, separadas por espacios
  cambio        true si algún CSV cambió (hay resultados nuevos)
  hay_proximos  true si se guardaron próximos partidos
  ultima_fecha  fecha del último partido jugado
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

BASE = "https://www.football-data.co.uk"
CABECERAS = {"User-Agent": "Mozilla/5.0 (modelo-laliga; descarga semanal)"}


def temporadas(hoy: date, atras: int) -> list[str]:
    inicio = hoy.year if hoy.month >= 7 else hoy.year - 1
    return [f"{(y) % 100:02d}{(y + 1) % 100:02d}" for y in range(inicio - atras, inicio + 1)]


def descargar(url: str, intentos: int = 3) -> bytes | None:
    espera = 10
    for i in range(1, intentos + 1):
        try:
            req = urllib.request.Request(url, headers=CABECERAS)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  {url}: no existe (404)")
                return None
            print(f"  {url}: error HTTP {e.code} (intento {i}/{intentos})")
        except Exception as e:  # red, tiempo de espera, etc.
            print(f"  {url}: {type(e).__name__}: {e} (intento {i}/{intentos})")
        if i < intentos:
            time.sleep(espera)
            espera *= 2
    return None


def leer_csv(contenido: bytes) -> pd.DataFrame | None:
    for cod in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(contenido), encoding=cod)
            df.columns = [c.replace("﻿", "").replace("ï»¿", "").strip() for c in df.columns]
            return df
        except Exception:
            continue
    return None


def valido(df: pd.DataFrame | None, columnas=("HomeTeam", "AwayTeam", "Date")) -> bool:
    return df is not None and all(c in df.columns for c in columnas) and len(df.dropna(subset=["HomeTeam"])) > 0


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def normalizar(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n").rstrip(b"\n") + b"\n"


def salida_github(**valores):
    ruta = os.environ.get("GITHUB_OUTPUT")
    for k, v in valores.items():
        print(f"{k}={v}")
        if ruta:
            with open(ruta, "a", encoding="utf-8") as f:
                f.write(f"{k}={v}\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--carpeta", default="datos")
    p.add_argument("--temporadas-atras", type=int, default=3)
    p.add_argument("--liga", default="SP1")
    p.add_argument("--proximos", default=None, help="ruta donde guardar los próximos partidos con cuotas")
    p.add_argument("--hoy", default=None, help="fecha de referencia AAAA-MM-DD (para pruebas)")
    a = p.parse_args()

    hoy = date.fromisoformat(a.hoy) if a.hoy else date.today()
    carpeta = Path(a.carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    usados, cambio, avisos = [], False, []

    for cod in temporadas(hoy, a.temporadas_atras):
        destino = carpeta / f"{a.liga}_{cod}.csv"
        url = f"{BASE}/mmz4281/{cod}/{a.liga}.csv"
        print(f"Temporada {cod}: {url}")
        contenido = descargar(url)
        df = leer_csv(contenido) if contenido else None
        if valido(df):
            # mismos saltos de línea que guarda git (LF), para no ver cambios donde no los hay
            contenido = normalizar(contenido)
            anterior = normalizar(destino.read_bytes()) if destino.exists() else b""
            if sha(anterior) != sha(contenido):
                destino.write_bytes(contenido)
                cambio = True
                print(f"  actualizado: {len(df)} partidos")
            else:
                print(f"  sin cambios: {len(df)} partidos")
            usados.append(destino)
        elif destino.exists():
            avisos.append(f"No se pudo descargar {cod}; se usa la copia guardada en {destino}.")
            print("  " + avisos[-1])
            usados.append(destino)
        else:
            avisos.append(f"Temporada {cod} no disponible y sin copia guardada; se omite.")
            print("  " + avisos[-1])

    if not usados:
        print("ERROR: no hay ningún archivo de datos para entrenar.", file=sys.stderr)
        sys.exit(1)

    # último partido jugado
    ultima = None
    for f in usados:
        df = leer_csv(f.read_bytes())
        if valido(df) and "FTHG" in df.columns:
            fechas = pd.to_datetime(df.loc[df["FTHG"].notna(), "Date"], dayfirst=True, errors="coerce").dropna()
            if len(fechas):
                ultima = max(ultima, fechas.max()) if ultima is not None else fechas.max()

    hay_proximos = False
    if a.proximos:
        print(f"Próximos partidos: {BASE}/fixtures.csv")
        contenido = descargar(f"{BASE}/fixtures.csv")
        fx = leer_csv(contenido) if contenido else None
        if valido(fx) and "Div" in fx.columns:
            fx = fx[fx["Div"] == a.liga]
            fx = fx[pd.to_datetime(fx["Date"], dayfirst=True, errors="coerce").dt.date >= hoy]
            if len(fx):
                Path(a.proximos).parent.mkdir(parents=True, exist_ok=True)
                fx.to_csv(a.proximos, index=False)
                hay_proximos = True
                print(f"  {len(fx)} partidos próximos de {a.liga} guardados en {a.proximos}")
            else:
                print(f"  no hay partidos próximos de {a.liga} en fixtures.csv")
        else:
            avisos.append("No se pudo descargar fixtures.csv (próximos partidos).")
            print("  " + avisos[-1])

    if avisos and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("### Avisos de la descarga de datos\n\n" + "\n".join(f"- {x}" for x in avisos) + "\n\n")

    salida_github(archivos=" ".join(str(f).replace("\\", "/") for f in usados),
                  cambio=str(cambio).lower(), hay_proximos=str(hay_proximos).lower(),
                  ultima_fecha=ultima.date().isoformat() if ultima is not None else "")


if __name__ == "__main__":
    main()
