import pytest

from app.core.translator.json_generator import JsonGenerator
from app.core.utils.file_work_info import FileWorkInfo
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


def test_invoke_does_not_mutate_source_json_obj():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.ok = True
    generator.dictionary = None
    generator.thread_num = 1

    file_info = FileWorkInfo(
        job_list=[Job(uid="entry", en_str="Hello world", cn_str="你好，世界")],
        json_obj={"entries": ["{!@ entry}"]},
        json_path="book/example.json",
        out_path="book/example.json",
    )

    result = list(generator.invoke([file_info], config={"metadata": {}}))

    assert result == [file_info]
    assert file_info.json_obj == {"entries": ["{!@ entry}"]}
    assert file_info.cn_obj == {"entries": ["你好，世界"]}


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


def test_replace_sub_jobs_keeps_item_display_text_from_source():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = "{@item Enspelled Staff (Level 3 Spell)|XDMG|3}"
    cn_str = "{@item 魔咒法杖（3环）|XDMG|3环}"

    def fake_process_value(value, tag=""):
        if value == "3":
            return value, True
        translations = {
            "Enspelled Staff (Level 3 Spell)": "魔咒法杖（3环）",
        }
        if tag == "item" and value in translations:
            return translations[value], True
        return value, False

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == "{@item 魔咒法杖（3环）|XDMG|3}"


def test_replace_sub_jobs_translates_item_display_text_from_jobs():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = "{@item Ink (1-ounce bottle)|phb|bottle of ink}"
    cn_str = "{@item 墨水(1盎司/瓶)|phb|bottle of ink}"

    def fake_process_value(value, tag=""):
        translations = {
            "Ink (1-ounce bottle)": "墨水(1盎司/瓶)",
            "bottle of ink": "一瓶墨水",
        }
        if tag == "item" and value in translations:
            return translations[value], True
        return value, False

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == "{@item 墨水(1盎司/瓶)|phb|一瓶墨水}"


def test_replace_sub_jobs_prefers_item_display_text_from_uid_job():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = "{@item Armor of Resistance (Fire)|XDMG|Fire}"
    cn_str = "{@item 火焰抗性护甲|XDMG|火焰}"

    def fake_process_value(value, tag=""):
        translations = {
            "Armor of Resistance (Fire)": "火焰抗性护甲",
            "Fire": "火",
        }
        if tag == "item" and value in translations:
            return translations[value], True
        return value, False

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == "{@item 火焰抗性护甲|XDMG|火焰}"


def test_replace_jobs_uses_child_uid_for_item_display_text():
    generator = JsonGenerator.__new__(JsonGenerator)
    parent_uid = "$.itemGroup[name=Armor-of-Resistance;source=XDMG;page=231].entries[1].rows[2][1]"
    generator.done_jobs = [
        Job(uid=parent_uid, en_str="{@item Armor of Fire Resistance|XDMG|Fire}", cn_str="{@item Armor of Fire Resistance|XDMG|Fire}", tag="item"),
        Job(uid=f"{parent_uid}.item[0].0", en_str="Armor of Fire Resistance", cn_str="火焰抗性护甲", tag="item"),
        Job(uid=f"{parent_uid}.item[0].1", en_str="XDMG", cn_str="XDMG", tag="item"),
        Job(uid=f"{parent_uid}.item[0].2", en_str="Fire", cn_str="火焰", tag="item"),
    ]
    generator.dictionary = None
    generator._JsonGenerator__build_lookup_cache()

    result, success = generator._JsonGenerator__replace_jobs(f"{{!@ {parent_uid}}}")

    assert success is True
    assert result == "{@item 火焰抗性护甲|XDMG|火焰}"


def test_replace_sub_jobs_does_not_reprocess_translated_plain_job_with_tag():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = [
        Job(uid="card", en_str="Rogue", cn_str="浪客", tag="item"),
        Job(uid="class", en_str="Rogue", cn_str="游荡者", tag="item"),
    ]
    generator.dictionary = None
    generator._JsonGenerator__build_lookup_cache()

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "游荡者",
        "Rogue",
        tag="item",
    )

    assert success is True
    assert result == "游荡者"


def test_replace_sub_jobs_keeps_variantrule_source_metadata():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Hit Points", "variantrule"): "生命值",
            ("XPHB", "variantrule"): "经验值",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@variantrule 生命值|经验值|生命值}",
        "{@variantrule Hit Points|XPHB|Hit Points}",
    )

    assert success is True
    assert result == "{@variantrule 生命值|XPHB|生命值}"


def test_replace_sub_jobs_translates_filter_search_value_with_page_context():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Quori", "bestiary"): "梦灵",
            ("Quori", None): "梦灵语",
            ("Quori", "language"): "梦灵语",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@filter 梦灵语|bestiary|source=ERLW|search=梦灵语}",
        "{@filter Quori|bestiary|source=ERLW|search=Quori}",
    )

    assert success is True
    assert result == "{@filter 梦灵|bestiary|source=ERLW|search=梦灵}"


def test_replace_sub_jobs_keeps_total_skip_prefix_filter_condition():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Common", "items"): "普通",
            ("common", ""): "普通",
            ("common", "item"): "通用语",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@filter 普通|items|rarity=common}",
        "{@filter Common|items|rarity=common}",
    )

    assert success is True
    assert result == "{@filter 普通|items|rarity=common}"


def test_replace_sub_jobs_normalizes_translated_tag_values_from_dictionary():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = (
        "Choose two from among {@skill Deception}, {@skill Insight}, "
        "{@skill Persuasion}, and {@skill Stealth}"
    )
    cn_str = (
        "从{@skill 欺骗}，{@skill 洞察}，{@skill 说服}，以及{@skill 潜行}中选择两项"
    )

    def fake_process_value(value, tag=""):
        if value == en_str:
            return value, False
        translations = {
            ("Deception", "skill"): "欺瞒",
            ("Insight", "skill"): "洞悉",
            ("Persuasion", "skill"): "游说",
            ("Stealth", "skill"): "隐匿",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == (
        "从{@skill 欺瞒}，{@skill 洞悉}，{@skill 游说}，以及{@skill 隐匿}中选择两项"
    )


def test_generator_get_uses_job_lookup_cache_before_dictionary():
    class FailingDictionary:
        def get(self, *args, **kwargs):
            raise AssertionError("dictionary should not be queried")

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FailingDictionary()
    generator.done_jobs = [
        Job(uid="skill", en_str="Deception", cn_str="欺瞒", tag="skill"),
        Job(uid="unique", en_str="Arcane Study", cn_str="奥术书房", tag="name"),
    ]
    generator._JsonGenerator__build_lookup_cache()

    assert generator._JsonGenerator__get("Deception", "skill") == ("欺瞒", True)
    assert generator._JsonGenerator__get("deception", "skill") == ("欺瞒", True)
    assert generator._JsonGenerator__get("Arcane Study", "entries") == ("奥术书房", True)


def test_replace_sub_jobs_skips_dictionary_for_already_translated_plain_text():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fail_process_value(value, tag=""):
        raise AssertionError("plain translated text should not be looked up again")

    generator._JsonGenerator__process_value = fail_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "已经翻译的描述文本。",
        "Already translated description text.",
    )

    assert success is True
    assert result == "已经翻译的描述文本。"


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


def test_replace_sub_jobs_keeps_filter_rewards_page_metadata():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("New Chwinga Charms", "rewards"): "新的怯蕴阿灵护咒",
            ("rewards", "filter"): "大功告成",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@filter 新的怯蕴阿灵护咒|大功告成|source=IDRotF}",
        "{@filter New Chwinga Charms|rewards|source=IDRotF}",
    )

    assert success is True
    assert result == "{@filter 新的怯蕴阿灵护咒|rewards|source=IDRotF}"


def test_replace_sub_jobs_keeps_card_source_metadata():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Obo'laka", "card"): "奥博娜卡",
            ("Omu's Trickster Gods", "card"): "奥姆城诡神",
            ("ToA", "card"): "墓碑",
            ("Obo'laka's card", "card"): "奥博娜卡的卡片",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@card 奥博娜卡|奥姆城诡神|墓碑|奥博娜卡的卡片}",
        "{@card Obo'laka|Omu's Trickster Gods|ToA|Obo'laka's card}",
    )

    assert success is True
    assert result == "{@card 奥博娜卡|奥姆城诡神|ToA|奥博娜卡的卡片}"


def test_replace_sub_jobs_translates_recipe_display_text_as_plain_text():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Faerie Fire", "recipe"): "妖火",
            ("PaF", "recipe"): "PaF",
            ("this page", "recipe"): "妖火",
            ("this page", ""): "本页",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@recipe 妖火|PaF|妖火}",
        "{@recipe Faerie Fire|PaF|this page}",
    )

    assert success is True
    assert result == "{@recipe 妖火|PaF|本页}"


def test_replace_sub_jobs_keeps_class_source_and_level_metadata():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("fighter", "class"): "战士",
            ("Battle Master", "class"): "战斗大师",
            ("phb", "class"): "玩家手册",
            ("2-0", "class"): "战士",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@class 战士|玩家手册|战斗大师|战斗大师|玩家手册|战士}",
        "{@class fighter|phb|Battle Master|Battle Master|phb|2-0}",
    )

    assert success is True
    assert result == "{@class 战士|phb|战斗大师|战斗大师|phb|2-0}"


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

    def fake_replace_sub_jobs(cn_str, en_str=None, tag="", **_kwargs):
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


def test_replace_jobs_keeps_same_tag_items_in_order_when_old_terms_changed():
    class FakeDictionary:
        def get(self, en, tag=""):
            translations = {
                ("spell scroll", "item"): "法术卷轴",
                ("lesser restoration", "spell"): "次等复原术",
                ("Potion of Greater Healing", "item"): "高等治疗药水",
                ("potion of healing (greater)", "item"): "治疗药水（高等）",
                ("chime of opening", "item"): "开门钟琴",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

        def update(self, *args, **kwargs):
            return True

    en_str = (
        "The grave-robbers had gathered grave goods, candlesticks, metal fixtures, "
        "and other art objects worth a total of 440 gp, plus a {@item spell scroll} "
        "of {@spell lesser restoration}, a {@item Potion of Greater Healing||potion "
        "of healing (greater)}, and a {@item chime of opening}."
    )
    old_cn = (
        "盗墓者收集了墓葬品、烛台、金属装置和其他艺术品，总价值为440金币，"
        "外加一张{@item 法术卷轴}上的{@spell 次级复原}法术，"
        "一瓶{@item 强效治疗药水||治疗药水（强效）}，以及一个{@item 开锁铃铛}。"
    )

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FakeDictionary()
    generator.done_jobs = [
        Job(
            uid="treasure",
            en_str=en_str,
            cn_str=old_cn,
            tag="adventure",
            sql_id=185450,
            is_proofread=False,
        )
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ treasure}"]}
    )

    assert ok is True
    assert cn_obj == {
        "entries": [
            "盗墓者收集了墓葬品、烛台、金属装置和其他艺术品，总价值为440金币，"
            "外加一张{@item 法术卷轴}上的{@spell 次等复原术}法术，"
            "一瓶{@item 高等治疗药水||治疗药水（强效）}，以及一个{@item 开门钟琴}。"
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
