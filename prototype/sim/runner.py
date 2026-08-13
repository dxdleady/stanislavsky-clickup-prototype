"""make demo — прогоняет все sim/scenarios/s0N_*.py на memory-адаптере,
печатает нарратив, ненулевой exit при провале (docs/04_test_plan.md, критерий сдачи).

Запуск: python -m sim.runner
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

SCENARIOS_DIR = Path(__file__).parent / "scenarios"


def _discover() -> list[str]:
    names = sorted(p.stem for p in SCENARIOS_DIR.glob("s*.py") if p.stem != "__init__")
    return [f"sim.scenarios.{name}" for name in names]


def main() -> int:
    failures = 0
    for module_name in _discover():
        module = importlib.import_module(module_name)
        label = module.__doc__.strip().splitlines()[0] if module.__doc__ else module_name
        try:
            module.run()
        except Exception:  # noqa: BLE001 — нарратив демо-прогона, не production-обработка
            print(f"❌ {module_name} — {label}")
            traceback.print_exc()
            failures += 1
        else:
            print(f"✅ {module_name} — {label}")

    total = len(_discover())
    print(f"\n{total - failures}/{total} сценариев зелёные")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
