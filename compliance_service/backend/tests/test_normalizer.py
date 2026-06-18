from normalizer import normalize


def test_traditional_to_simplified():
    # 繁体应转成简体
    assert normalize("測試繁體字") == "测试繁体字"


def test_nfkc_fullwidth_to_halfwidth():
    # 全角字母数字经 NFKC 归一化为半角
    assert normalize("ＡＢＣ１２３") == "abc123"


def test_nfkc_compatibility_chars():
    # NFKC 把兼容字符拆开，例如全角括号、罗马数字
    # 全角括号 -> 半角括号
    assert normalize("（测试）") == "(测试)"


def test_lowercase():
    # 英文统一转小写
    assert normalize("HeLLo WORLD") == "hello world"


def test_strip_whitespace():
    # 首尾空白被 strip
    assert normalize("  内容  ") == "内容"


def test_remove_zero_width_chars():
    # 零宽字符（常用于绕过敏感词检测）应被剔除
    # ​ 零宽空格, ‌ 零宽非连接符, ‍ 零宽连接符, ﻿ BOM
    raw = "敏​感‌词‍测﻿试"
    assert normalize(raw) == "敏感词测试"


def test_zero_width_breaks_evasion():
    # 关键安全语义：插入零宽字符的"敏感词"归一化后等于原词，
    # 从而能被精确匹配命中，挡住这种绕过手法。
    assert normalize("违​规") == normalize("违规")


def test_combined_traditional_fullwidth_upper():
    # 繁体 + 全角 + 大写 混合，逐项都该被处理
    assert normalize("測試ＡＢＣ") == "测试abc"


def test_idempotent():
    # 归一化应是幂等的：对已归一化结果再跑一次不变
    once = normalize("測試ＡＢＣ  ")
    assert normalize(once) == once


def test_empty_string():
    # 空串与纯空白
    assert normalize("") == ""
    assert normalize("   ") == ""
