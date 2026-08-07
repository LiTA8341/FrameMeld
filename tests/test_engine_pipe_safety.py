from __future__ import annotations

import ast
import unittest
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1] / "src" / "engine"


class EnginePipeSafetyTests(unittest.TestCase):
    def test_engine_diagnostics_never_write_to_vspipe_stdout(self) -> None:
        unsafe: list[str] = []
        for path in ENGINE_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Name) or node.func.id != "print":
                    continue
                destination = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "file"),
                    None,
                )
                if not (
                    isinstance(destination, ast.Attribute)
                    and isinstance(destination.value, ast.Name)
                    and destination.value.id == "sys"
                    and destination.attr == "stderr"
                ):
                    unsafe.append(f"{path.relative_to(ENGINE_ROOT)}:{node.lineno}")

        self.assertEqual(
            unsafe,
            [],
            "VSPipe stdout must contain only Y4M bytes; send engine diagnostics to sys.stderr",
        )


if __name__ == "__main__":
    unittest.main()
