import pytest

from app.core.translator.json_generator import JsonGenerator
from app.core.utils.job import Job


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


def test_tag_only_sync_wraps_new_english_tags_and_updates_database():
    class FakeDictionary:
        def __init__(self):
            self.calls = []

        def get(self, en, tag=""):
            if en == "Magic" and tag == "action":
                return "魔法", True
            return None, False

        def update(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return True

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FakeDictionary()
    generator.done_jobs = [
        Job(
            uid="sentence",
            en_str=(
                "When you take the {@action Magic|XPHB} action, you cast a spell "
                "that requires a {@action Magic|XPHB} action."
            ),
            cn_str="当你执行Magic动作时，你施放一个需要Magic动作的法术。",
            tag="entries",
            is_proofread=True,
            sql_id=9,
            tag_sync_required=True,
            old_en_str=(
                "When you take the Magic action, you cast a spell "
                "that requires a Magic action."
            ),
        )
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ sentence}"]}
    )

    assert ok is True
    assert cn_obj == {
        "entries": [
            "当你执行{@action 魔法|XPHB}动作时，你施放一个需要{@action 魔法|XPHB}动作的法术。"
        ]
    }
    assert generator.dictionary.calls == [
        (
            (
                9,
                (
                    "When you take the {@action Magic|XPHB} action, you cast a spell "
                    "that requires a {@action Magic|XPHB} action."
                ),
                "当你执行{@action 魔法|XPHB}动作时，你施放一个需要{@action 魔法|XPHB}动作的法术。",
            ),
            {"proofread": True, "tag": "entries"},
        )
    ]


def test_tag_only_sync_replaces_changed_tag_type_using_old_cn_display():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []
    generator.dictionary = type(
        "FakeDictionary",
        (),
        {"get": lambda self, en, tag="": (None, False)},
    )()
    old_en = (
        "If the character has {@variantrule Passive Perception|XPHB} of 16 or "
        "higher, they are not {@table Surprise|RMR|Surprised}."
    )
    new_en = (
        "If the character has {@variantrule Passive Perception|XPHB} of 16 or "
        "higher, they are not {@status Surprised|XPHB}."
    )
    old_cn = (
        "如果角色拥有16或更高的{@variantrule 被动察觉|XPHB}，"
        "则他们不会被{@table 突袭|RMR|突袭}。"
    )

    result = generator._JsonGenerator__sync_tag_only_cn(
        old_cn,
        old_en,
        new_en,
        tag="adventure",
    )

    assert result == (
        "如果角色拥有16或更高的{@variantrule 被动察觉|XPHB}，"
        "则他们不会被{@status 突袭|XPHB}。"
    )


def test_tag_only_sync_keeps_quickref_metadata_index():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = [
        Job(uid="term", en_str="difficult terrain", cn_str="困难地形", tag="quickref")
    ]
    generator.dictionary = type(
        "FakeDictionary",
        (),
        {"get": lambda self, en, tag="": (None, False)},
    )()

    result = generator._JsonGenerator__sync_tag_only_cn(
        "视该空间为困难地形。",
        "The space is difficult terrain.",
        "The space is {@quickref difficult terrain||3}.",
        tag="entries",
    )

    assert result == "视该空间为{@quickref 困难地形||3}。"


def test_replace_jobs_keeps_context_specific_plain_translation():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = [
        Job(
            uid="light-domain",
            en_str="Light",
            cn_str="光明",
            tag="book",
            sql_id=8444,
            is_proofread=True,
        )
    ]
    generator.dictionary = type(
        "FakeDictionary",
        (),
        {
            "get": lambda self, en, tag="": ("轻型", True),
            "update": lambda self, *args, **kwargs: True,
        },
    )()

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ light-domain}"]}
    )

    assert ok is True
    assert cn_obj == {"entries": ["光明"]}
    assert generator.done_jobs[0].cn_str == "光明"


def test_replace_sub_jobs_restores_quickref_numeric_metadata():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("difficult terrain", "quickref"): "困难地形",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@quickref 困难地形||困难地形}",
        "{@quickref difficult terrain||3}",
    )

    assert success is True
    assert result == "{@quickref 困难地形||3}"


def test_replace_sub_jobs_restores_subclass_feature_level_metadata():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    translations = {
        ("Fast Hands", "subclassFeature"): "快手",
        ("Rogue", "subclassFeature"): "游荡者",
        ("Thief", "subclassFeature"): "盗贼",
    }

    def fake_process_value(value, tag=""):
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@subclassFeature 快手|游荡者||盗贼||快手}",
        "{@subclassFeature Fast Hands|Rogue||Thief||3}",
    )

    assert success is True
    assert result == "{@subclassFeature 快手|游荡者||盗贼||3}"


def test_json_generator_aligns_stale_cn_tags_when_en_was_already_updated():
    class FakeDictionary:
        def __init__(self):
            self.calls = []

        def update(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return True

        def get(self, en, tag=""):
            translations = {
                ("Passive Perception", "variantrule"): "被动察觉",
                ("Surprised", "status"): "突袭",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FakeDictionary()
    generator.done_jobs = [
        Job(
            uid="bqdd",
            en_str=(
                "If the character has {@variantrule Passive Perception|XPHB} of "
                "16 or higher, they are not {@status Surprised|XPHB}."
            ),
            cn_str=(
                "如果角色拥有16或更高的{@variantrule 被动察觉|XPHB}，"
                "则他们不会被{@table 突袭|RMR|突袭}。"
            ),
            tag="adventure",
            sql_id=528214,
            is_proofread=True,
        )
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ bqdd}"]}
    )

    assert ok is True
    assert cn_obj == {
        "entries": [
            "如果角色拥有16或更高的{@variantrule 被动察觉|XPHB}，"
            "则他们不会被{@status 突袭|XPHB}。"
        ]
    }
    assert generator.dictionary.calls == [
        (
            (
                528214,
                (
                    "If the character has {@variantrule Passive Perception|XPHB} of "
                    "16 or higher, they are not {@status Surprised|XPHB}."
                ),
                (
                    "如果角色拥有16或更高的{@variantrule 被动察觉|XPHB}，"
                    "则他们不会被{@status 突袭|XPHB}。"
                ),
            ),
            {"proofread": True, "tag": "adventure"},
        )
    ]


def test_replace_jobs_continues_sub_tag_replacement_after_alignment():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = type(
        "FakeDictionary",
        (),
        {"update": lambda self, *args, **kwargs: True},
    )()
    generator.done_jobs = [
        Job(
            uid="sentence",
            en_str="They are not {@status Surprised|XPHB}.",
            cn_str="他们不会被{@table 突袭|RMR|突袭}。",
            tag="entries",
            sql_id=1,
            is_proofread=True,
        )
    ]

    calls = []

    def fake_replace_sub_jobs(cn_str, en_str=None, tag=""):
        calls.append(cn_str)
        if len(calls) == 1:
            assert cn_str == "他们不会被{@table 突袭|RMR|突袭}。"
            return cn_str, False
        assert cn_str == "他们不会被{@status 突袭|XPHB}。"
        assert en_str == "They are not {@status Surprised|XPHB}."
        return "他们不会被{@status 突袭|XPHB}。OK", True

    generator._JsonGenerator__replace_sub_jobs = fake_replace_sub_jobs

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ sentence}"]}
    )

    assert ok is True
    assert cn_obj == {"entries": ["他们不会被{@status 突袭|XPHB}。OK"]}
    assert calls == [
        "他们不会被{@table 突袭|RMR|突袭}。",
        "他们不会被{@status 突袭|XPHB}。",
    ]


def test_replace_jobs_prefers_value_matching_over_position_alignment():
    class FakeDictionary:
        def get(self, en, tag=""):
            translations = {
                ("Condition", "variantrule"): "状态",
                ("Incapacitated", "condition"): "失能",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

        def update(self, *args, **kwargs):
            raise AssertionError("position alignment should not run first")

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FakeDictionary()
    generator.done_jobs = [
        Job(
            uid="sentence",
            en_str=(
                "The {@variantrule Condition|XPHB|condition} ends if the grappler "
                "has the {@condition Incapacitated|XPHB} condition."
            ),
            cn_str=(
                "若擒抱者陷入{@condition 失能|XPHB}，"
                "受擒{@variantrule 状态|XPHB|状态}将结束。"
            ),
            tag="entries",
            sql_id=1,
            is_proofread=True,
        )
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ sentence}"]}
    )

    assert ok is True
    assert cn_obj == {
        "entries": [
            "若擒抱者陷入{@condition 失能|XPHB}，"
            "受擒{@variantrule 状态|XPHB|状态}将结束。"
        ]
    }


def test_replace_jobs_recovers_tag_type_from_polluted_position_alignment():
    class FakeDictionary:
        def get(self, en, tag=""):
            translations = {
                ("Athletics", "skill"): "运动",
                ("Acrobatics", "skill"): "特技",
                ("Condition", "variantrule"): "状态",
                ("Incapacitated", "condition"): "失能",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

        def update(self, *args, **kwargs):
            return True

    en_str = (
        "A Grappled creature can use its action to make a Strength "
        "({@skill Athletics|XPHB}) or Dexterity ({@skill Acrobatics|XPHB}) check "
        "against the grapple's escape DC, ending the "
        "{@variantrule Condition|XPHB|condition} on itself on a success. The "
        "{@variantrule Condition|XPHB|condition} also ends if the grappler has "
        "the {@condition Incapacitated|XPHB} condition or if the distance between "
        "the Grappled target and the grappler exceeds the grapple's range."
    )
    polluted_cn = (
        "受擒生物可以用其动作进行一次力量（{@skill Athletics|XPHB}）或敏捷"
        "（{@skill Acrobatics|XPHB}）检定，对抗此次擒抱的逃脱DC，检定成功时，"
        "受擒{@condition condition|XPHB}将结束。若擒抱者陷入"
        "{@variantrule Condition|XPHB|Incapacitated}，或是擒抱者与受擒者之间的"
        "距离超出了此次擒抱的范围，受擒{@variantrule Condition|XPHB|condition}"
        "也会提前结束。"
    )

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FakeDictionary()
    generator.done_jobs = [
        Job(
            uid="sentence",
            en_str=en_str,
            cn_str=polluted_cn,
            tag="action",
            sql_id=336458,
            is_proofread=True,
        )
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ sentence}"]}
    )

    assert ok is True
    assert cn_obj == {
        "entries": [
            "受擒生物可以用其动作进行一次力量（{@skill 运动|XPHB}）或敏捷"
            "（{@skill 特技|XPHB}）检定，对抗此次擒抱的逃脱DC，检定成功时，"
            "受擒{@variantrule 状态|XPHB|状态}将结束。若擒抱者陷入"
            "{@condition 失能|XPHB}，或是擒抱者与受擒者之间的距离超出了"
            "此次擒抱的范围，受擒{@variantrule 状态|XPHB|状态}也会提前结束。"
        ]
    }


def test_tag_only_sync_wraps_known_translated_phrase_from_other_job():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = type(
        "FakeDictionary",
        (),
        {"get": lambda self, en, tag="": (None, False)},
    )()
    generator.done_jobs = [
        Job(uid="term", en_str="dogsled", cn_str="狗拉雪橇", tag="item")
    ]

    result = generator._JsonGenerator__sync_tag_only_cn(
        "最快的方式是乘坐狗拉雪橇。",
        "The fastest way is by dogsled.",
        "The fastest way is by {@item dogsled|IDRotF}.",
        tag="adventure",
    )

    assert result == "最快的方式是乘坐{@item 狗拉雪橇|IDRotF}。"
