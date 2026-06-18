import detector
from detector import build_automaton, exact_match
from normalizer import normalize


def setup_function(_):
    # 每个用例前重置全局自动机，避免用例之间互相污染
    detector._automaton = None


def test_no_automaton_returns_empty():
    # 未构建自动机时，exact_match 应安全返回空列表
    assert exact_match("任何文本") == []


def test_basic_exact_match():
    build_automaton(["违规", "敏感词"])
    hits = exact_match(normalize("这是一段违规的内容"))
    words = [h["word"] for h in hits]
    assert "违规" in words
    assert len(hits) == 1


def test_multiple_matches():
    build_automaton(["违规", "敏感词"])
    hits = exact_match(normalize("既违规又含敏感词"))
    words = {h["word"] for h in hits}
    assert words == {"违规", "敏感词"}


def test_dedup_repeated_word():
    # 同一个词在文本里出现多次，只返回一条
    build_automaton(["违规"])
    hits = exact_match(normalize("违规违规违规"))
    assert len(hits) == 1
    assert hits[0]["word"] == "违规"


def test_reason_field():
    # 命中项的 reason 固定为"精确匹配"
    build_automaton(["违规"])
    hits = exact_match(normalize("违规"))
    assert hits[0]["reason"] == "精确匹配"


def test_short_word_filtered():
    # 归一化后长度不足 2 的词会被 build_automaton 过滤掉，不参与匹配
    build_automaton(["A", "违规"])  # "A" 归一化为 "a"，长度 1，应被丢弃
    hits = exact_match(normalize("a 违规"))
    words = {h["word"] for h in hits}
    assert words == {"违规"}


def test_match_after_normalization_traditional():
    # 词库以繁体加入，文本是繁体；两边都经 normalize 后应能命中
    build_automaton(["違規"])  # 繁体词库
    hits = exact_match(normalize("這是違規內容"))  # 繁体文本
    assert len(hits) == 1
    # 命中返回的是归一化后的简体形态
    assert hits[0]["word"] == "违规"


def test_match_evasion_with_zero_width():
    # 安全语义闭环：文本里塞零宽字符想绕过，归一化后仍被精确命中
    build_automaton(["违规"])
    evasive = "这是违​规内容"  # 违规 中间插入零宽空格
    hits = exact_match(normalize(evasive))
    assert any(h["word"] == "违规" for h in hits)


def test_no_match():
    build_automaton(["违规", "敏感词"])
    hits = exact_match(normalize("一段干净的正常文本"))
    assert hits == []


def test_empty_wordlist():
    build_automaton([])
    assert exact_match(normalize("违规")) == []
