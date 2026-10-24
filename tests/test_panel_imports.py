"""面板拆分后的回归测试：函数内部的延迟相对导入（如 from ..settings import open_settings）
在导入阶段不会报错，只有点按钮时才炸。这里静态检查 panel/ 下所有相对导入都能找到目标模块。"""
import ast
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolve(file: Path, level: int, module: str | None) -> Path:
    base = file.parent
    for _ in range(level - 1):
        base = base.parent
    return base.joinpath(*module.split(".")) if module else base


def _exists(target: Path) -> bool:
    return target.with_suffix(".py").is_file() or (target / "__init__.py").is_file()


class PanelRelativeImportTest(unittest.TestCase):
    def test_all_relative_imports_resolve(self):
        bad = []
        for file in sorted((ROOT / "panel").rglob("*.py")):
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.level:
                    continue
                target = _resolve(file, node.level, node.module)
                if node.module:
                    ok = _exists(target)
                else:  # from . import x：x 可以是子模块或包里的名字
                    ok = all(_exists(target / a.name) or (target / "__init__.py").is_file() for a in node.names)
                if not ok:
                    bad.append(f"{file.relative_to(ROOT)}:{node.lineno} from {'.' * node.level}{node.module or ''}")
        self.assertEqual(bad, [], "找不到的相对导入：\n" + "\n".join(bad))


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "需要 PySide6")
class SettingsSchemaTest(unittest.TestCase):
    def test_schema_keys_unique_and_typed(self):
        from panel.classic import settings  # noqa: F401  先加载 settings，settings_env 依赖它
        from panel.classic import settings_env as se
        keys = [f[0] for *_x, cards in se.SCHEMA_PAGES for _t, _d, fields in cards for f in fields]
        self.assertEqual(len(keys), len(set(keys)), "设置清单里有重复的键")
        kinds = {"bool", "int", "dec", "pct", "text", "list", "secret", "choice"}
        for *_x, cards in se.SCHEMA_PAGES:
            for _t, _d, fields in cards:
                for key, label, kind, default, _help, opts in fields:
                    self.assertIn(kind, kinds, key)
                    self.assertTrue(label, key)
                    if kind in ("int", "dec"):
                        lo, hi = opts.get("min", 0), opts.get("max", 999999)
                        self.assertTrue(lo <= float(default) <= hi, f"{key} 默认值超出范围")
                    if kind == "pct":
                        self.assertTrue(0 <= float(default) <= 1, key)
        self.assertFalse(set(keys) & se.MANAGED_ELSEWHERE)
        nav = [p[0] for p in settings.PAGES]
        self.assertEqual(len(nav), len(set(nav)))


if __name__ == "__main__":
    unittest.main()
