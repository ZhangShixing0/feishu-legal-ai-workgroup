from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(label: str, command: list[str], cwd: Path = ROOT) -> None:
    print(f"\n==> {label}")
    print("    " + " ".join(command))
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行项目发布前检查")
    parser.add_argument("--require-node", action="store_true", help="未安装 Node.js 时也视为失败")
    parser.add_argument("--skip-audit", action="store_true", help="跳过仓库敏感数据审计")
    args = parser.parse_args()

    if not args.skip_audit:
        run("仓库发布安全审计", [sys.executable, "tools/release_audit.py"])

    run(
        "诉状生成器与飞书编排测试",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
    )
    run(
        "材料提取器测试",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        ROOT / "案件材料提取填表工具",
    )

    node = shutil.which("node")
    if node:
        run("前端文本解析测试", [node, "tests/text_parser.test.js"])
    elif args.require_node:
        raise RuntimeError("Node.js 未安装，无法运行前端文本解析测试。")
    else:
        print("\n==> 未找到 Node.js；已跳过前端文本解析测试。")

    print("\n全部可用检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
