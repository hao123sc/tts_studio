"""发音词典与读法规则测试（FR-T-4/5/6，验收 9）。"""

from __future__ import annotations

from app.core.lexicon import Lexicon, RuleOptions, digits_to_chinese, int_to_chinese


def test_int_to_chinese():
    assert int_to_chinese(0) == "零"
    assert int_to_chinese(5000) == "五千"
    assert int_to_chinese(24067) == "二万四千零六十七"
    assert int_to_chinese(10) == "十"
    assert int_to_chinese(105) == "一百零五"


def test_digits_by_digit():
    assert digits_to_chinese("24067") == "二四〇六七"
    # 小数读法传 zero_char="零"（文档："2.0" 读"二点零"）
    assert digits_to_chinese("2.0", zero_char="零") == "二点零"


def test_dict_replacement():
    lex = Lexicon(entries={"乐至": "乐志", "缫丝": "骚丝"})
    assert lex.apply("乐至的缫丝工艺") == "乐志的骚丝工艺"


def test_longer_entry_wins():
    lex = Lexicon(entries={"蚕": "惨", "蚕茧": "残简"})
    assert lex.apply("蚕茧") == "残简"


def test_decimal_rule():
    lex = Lexicon()
    assert "二点零" in lex.apply("重量是 2.0 千克")


def test_int_unit_rule():
    lex = Lexicon()
    out = lex.apply("年产 5000吨生丝")
    assert "五千吨" in out


def test_standard_rule():
    lex = Lexicon()
    out = lex.apply("执行 GB/T 24067 标准")
    assert "GB/T 二四〇六七" in out


def test_abbrev_letter_reading_and_whitelist():
    lex = Lexicon()
    out = lex.apply("LCA 和 NASA 都要懂")
    assert "L C A" in out
    assert "NASA" in out
    assert "N A S A" not in out


def test_rule_switches():
    opt = RuleOptions(decimal=False, int_unit=False, standard=False, abbrev=False)
    lex = Lexicon(options=opt)
    assert lex.apply("2.0千克 GB/T 24067 LCA 5000吨") == "2.0千克 GB/T 24067 LCA 5000吨"


def test_dict_overrides_rules():
    """词典显式覆盖优先于自动规则（FR-T-5 可逐条覆盖）。"""
    lex = Lexicon(entries={"GB/T 24067": "国标二四〇六七"})
    assert "国标二四〇六七" in lex.apply("GB/T 24067")


def test_roundtrip_and_csv(tmp_path):
    lex = Lexicon(entries={"a": "b"})
    lex.options.abbrev = False
    p = tmp_path / "lex.json"
    lex.save(p)
    lex2 = Lexicon.load(p)
    assert lex2.entries == {"a": "b"} and lex2.options.abbrev is False

    csvf = tmp_path / "lex.csv"
    csvf.write_text("缫丝,骚丝\n乐至,乐志\n", encoding="utf-8")
    lex3 = Lexicon.import_csv(csvf)
    assert lex3.apply("缫丝乐至") == "骚丝乐志"
