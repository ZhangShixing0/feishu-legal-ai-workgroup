from __future__ import annotations

from typing import Any

from .mapping import label_for


def material_review_card(
    case_id: str,
    result: dict[str, Any],
    review_url: str,
) -> dict[str, Any]:
    summary = result.get("summary") or {}
    warnings = result.get("warnings") or []
    warning_text = "\n".join(f"- {item}" for item in warnings[:3]) or "- 暂无系统警告"
    content = (
        f"**案件编号：** {case_id}\n"
        f"**已识别：** {summary.get('found', 0)} 项　"
        f"**缺失：** {summary.get('missing', 0)} 项　"
        f"**需核对：** {summary.get('review', 0)} 项　"
        f"**冲突：** {summary.get('conflicts', 0)} 项\n\n"
        f"**重点提示**\n{warning_text}\n\n"
        "请先打开核对页修改字段并点击“生成并下载 Excel”，再返回本卡片确认交接。"
    )
    return _card(
        title="AI 材料专员｜提取完成，等待核对",
        template="blue",
        content=content,
        actions=[
            _url_button("打开完整核对页", review_url),
            _callback_button(
                "确认并交给诉状专员",
                {"action": "approve_step1", "case_id": case_id},
                primary=True,
            ),
        ],
    )


def supplement_card(case_id: str, missing_ids: list[str], form_url: str) -> dict[str, Any]:
    missing_text = "、".join(label_for(item) for item in missing_ids) or "无"
    content = (
        f"**案件编号：** {case_id}\n\n"
        "材料专员已完成交接。第一步字段已经带入诉状生成台。\n\n"
        f"**仍需确认或补充：** {missing_text}\n\n"
        "打开补充页核对起诉状专用字段，提交后系统将生成 Word 并发回本群。"
    )
    return _card(
        title="AI 诉状专员｜请补充起诉信息",
        template="orange",
        content=content,
        actions=[_url_button("打开补充信息页", form_url, primary=True)],
    )


def generated_card(case_id: str, warnings: list[str]) -> dict[str, Any]:
    warning_text = "\n".join(f"- {item}" for item in warnings[:5]) or "- 暂无系统警告"
    return _card(
        title="AI 诉状专员｜Word 草稿已生成",
        template="green",
        content=(
            f"**案件编号：** {case_id}\n\n"
            "Word 草稿已发送到群内。请律师对主体、债权转让关系、基准日、金额和计息规则进行最终审查。\n\n"
            f"**生成提示**\n{warning_text}"
        ),
        actions=[],
    )


def _card(
    title: str,
    template: str,
    content: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": content}}
    ]
    if actions:
        elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title},
        },
        "elements": elements,
    }


def _url_button(label: str, url: str, primary: bool = False) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "primary" if primary else "default",
        "url": url,
    }


def _callback_button(
    label: str,
    value: dict[str, str],
    primary: bool = False,
) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label},
        "type": "primary" if primary else "default",
        "value": value,
    }
