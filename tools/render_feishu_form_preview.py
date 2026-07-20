from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from feishu_workgroup.mapping import extraction_to_generator_fields
from feishu_workgroup.store import CaseStore
from feishu_workgroup.web import render_form


def main() -> None:
    parser = argparse.ArgumentParser(description="渲染飞书第二步补充页预览")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    extraction = {
        "fields": [
            {"header": "贷款合同编号", "value": "HT-2026-001", "missing": False},
            {"header": "借款人姓名", "value": "示例借款人", "missing": False},
            {"header": "未偿本金余额", "value": "100000.00", "missing": False},
            {"header": "未偿利息余额", "value": "3600.00", "missing": False},
            {"header": "合计", "value": "103600.00", "missing": False},
            {"header": "贷款金额", "value": "100000.00", "missing": False},
            {"header": "贷款发放日", "value": "2025-01-01", "missing": False},
            {"header": "贷款到期日", "value": "2026-01-01", "missing": False},
            {"header": "截至基准日执行利率", "value": "3.6", "missing": False},
            {
                "header": "贷款发放机构全称",
                "value": "某银行股份有限公司某支行",
                "missing": False,
            },
        ]
    }
    with tempfile.TemporaryDirectory() as directory:
        store = CaseStore(Path(directory))
        case = store.create_case("preview-chat", "preview-user")
        case = store.save_generator_fields(
            case.id, extraction_to_generator_fields(extraction), "preview-user"
        )
        content = render_form(case, case.access_token)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding="utf-8")
    print(args.output.resolve())


if __name__ == "__main__":
    main()
