#!/usr/bin/env bash
# Guarda y sube los cambios hechos por un workflow. Uso: git_subir.sh "mensaje" ruta1 ruta2 ...
# Solo se incluyen las rutas indicadas que existan. Si no hay nada nuevo, igualmente sube
# cualquier commit que haya quedado pendiente.
set -euo pipefail
mensaje="$1"; shift
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

rutas=()
for r in "$@"; do [ -e "$r" ] && rutas+=("$r"); done
if [ ${#rutas[@]} -gt 0 ]; then
  git add -A -- "${rutas[@]}"
fi
if git diff --cached --quiet; then
  echo "Sin cambios nuevos que guardar."
else
  git commit --quiet -m "$mensaje"
  echo "Commit: $mensaje"
fi

for intento in 1 2 3; do
  if git pull --rebase --autostash --quiet && git push --quiet; then
    echo "Repositorio al día."
    exit 0
  fi
  echo "Reintentando la subida ($intento)..."
  sleep $((intento * 5))
done
echo "No se pudieron subir los cambios." >&2
exit 1
