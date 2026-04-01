import pytest

from app.core.translator.json_generator import JsonGenerator


def test_replace_sub_jobs_keeps_marker_mapping_when_order_differs():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = (
        "When you drink this potion, it removes any "
        "{@condition Exhaustion|XPHB} levels you have and ends the "
        "{@condition Poisoned|XPHB} condition on you. For the next 24 hours, "
        "you regain the maximum number of {@variantrule Hit Points|XPHB} for any "
        "{@variantrule Hit Point Dice|XPHB|Hit Point Die} you spend."
    )
    cn_str = (
        "当你饮下这瓶药水时，它将移除你具有的任何{@condition Exhaustion|XPHB}等级，并结束你的"
        "{@condition Poisoned|XPHB}状态。在接下来的24小时内，你消耗"
        "{@variantrule Hit Point Dice|XPHB|Hit Point Die}来恢复"
        "{@variantrule Hit Points|XPHB}时将直接取用最大值。"
    )

    # 避免依赖数据库，只验证{@tag value}配对与替换顺序
    # 顶层整句通常不会被直接替换成功（否则会覆盖cn_str），子项保持原值并返回成功
    def fake_process_value(value, tag=""):
        if value == en_str:
            return value, False
        return value, True

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == cn_str


def test_replace_sub_jobs_matches_already_translated_values_by_order():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = (
        "It removes {@condition Exhaustion|XPHB} and also "
        "{@condition Poisoned|XPHB}."
    )
    cn_str = (
        "它会移除{@condition 中毒|XPHB}并且移除{@condition 力竭|XPHB}。"
    )

    def fake_process_value(value, tag=""):
        if value == en_str:
            return value, False
        if tag == "condition":
            translations = {
                "Exhaustion": "力竭",
                "Poisoned": "中毒",
            }
            if value in translations:
                return translations[value], True
        return value, False

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == cn_str
