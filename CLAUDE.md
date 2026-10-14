# Claude Code en este repo

Las reglas completas de cada tarea están en `.claude/agents/`. Estas valen siempre:

- **`.env` no se lee, no se muestra y no se escanea.** Las claves las pone Miquel. Solo `.env.example`, que lleva valores de ejemplo.
  - Ejecutar desde un guion órdenes que mencionan `.env` (el hook las bloquea en la línea de órdenes) solo vale en **carpetas de prueba temporales, con valores inventados, sin mostrar ningún valor y avisando a Miquel antes**. Nunca en este repo ni con claves reales. Al terminar, se borra la carpeta de prueba.
- **Buscar texto:** solo `git grep` (archivos versionados) o `rg` sin `-u`, `--no-ignore` ni `--unrestricted` (respeta `.gitignore`, así que no entra en `.env`).
  - Nunca `grep -r` / `-R` / `--recursive`, `findstr /s`, `Select-String -Recurse`, `Get-ChildItem -Recurse | Select-String` ni `find … | xargs grep`: recorren `.env` y todo lo que ignora git e imprimen lo que encuentran.
  - El hook `.claude/hooks/pre_bash.py` las bloquea (`tests/unit/test_claude_hooks.py`).
- **Secretos en git:** solo `make secrets` (gitleaks sobre el historial, con `--redact`). Nunca `gitleaks dir` ni `--no-git`.
- **Nunca** `--no-verify`, `SKIP=`, `push --force` ni cambiar `core.hooksPath`. Push y PR, solo con el OK de Miquel.
