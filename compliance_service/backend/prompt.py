SYSTEM_PROMPT = """你是专业的内容合规编辑。
你的职责只有一个：将【违规内容】中列出的词汇进行替换改写。
严格遵守以下规则：
1. 只处理【违规内容】中明确列出的词汇，使用近义词、委婉表达或重新组织句子进行替换
2. 不得自行判断其他任何内容是否违规
3. 不得修改任何未在【违规内容】中列出的内容
4. 不得在输出中添加任何说明、注释或改写理由
5. 直接输出改写后的完整文本"""


def build_prompt(
    original_text: str,
    violations: list[dict],
    rules: list[dict],
    cached_strategy: dict | None = None,
) -> str:
    v_lines = "\n".join(
        f"- {v['word']}（{v['reason']}）" for v in violations
    )
    r_lines = "\n".join(r["summary"] for r in rules[:2])

    strategy_section = ""
    if cached_strategy:
        s_lines = "\n".join(
            f"- {word} → 建议替换为：{suggestion}"
            for word, suggestion in cached_strategy.items()
        )
        strategy_section = f"""
【历史改写参考】（仅供参考，请结合上下文判断是否采用）
{s_lines}
"""

    return f"""请严格按照以下要求改写文本，只替换【违规内容】中列出的词汇，其余内容保持原样不动。
{strategy_section}
【违规内容】（只处理这些词，不得自行添加其他判断）
{v_lines}

【平台规则要点】
{r_lines}

【原文】
{original_text}

【改写结果】（只替换违规词，其余原样保留）"""
