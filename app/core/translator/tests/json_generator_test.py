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


def test_replace_sub_jobs_uses_generic_dictionary_for_variantrule_parts():
    class FakeDictionary:
        def get(self, en, tag=""):
            translations = {
                ("Sphere [Area of Effect]", ""): "球状 [效应区域]",
                ("Sphere", ""): "球状",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []
    generator.dictionary = FakeDictionary()
    generator._JsonGenerator__build_lookup_cache()

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@variantrule Sphere [Area of Effect]|XPHB|Sphere}",
        "{@variantrule Sphere [Area of Effect]|XPHB|Sphere}",
    )

    assert success is True
    assert result == "{@variantrule 球状 [效应区域]|XPHB|球状}"


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


def test_tag_only_sync_matches_reordered_cn_tags_by_translated_display():
    class FakeDictionary:
        def get(self, en, tag=""):
            translations = {
                ("Fellow Students", "adventure"): "同学",
                ("chapter 3", "adventure"): "第三章",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []
    generator.dictionary = FakeDictionary()

    old_en = (
        'If a character is Friends with Zanther Bowen (see the "'
        '{@adventure Fellow Students|SCC-CK|0|Fellow Students}" section in '
        '{@adventure chapter 3|SCC-CK|0}), these students eagerly help.'
    )
    new_en = (
        'If a character is Friends with Zanther Bowen (see the "'
        '{@book Fellow Students|SCC|3|Fellow Students}" section in '
        '{@adventure chapter 3|SCC-CK|0}), these students eagerly help.'
    )
    old_cn = (
        "如果角色与赞瑟·鲍文是朋友（参见{@adventure 第三章|SCC-CK|0}中的"
        "“{@adventure 同学|SCC-CK|0|同学}”部分），这些学生会热切地提供帮助。"
    )

    result = generator._JsonGenerator__sync_tag_only_cn(
        old_cn,
        old_en,
        new_en,
        tag="adventure",
    )

    assert result == (
        "如果角色与赞瑟·鲍文是朋友（参见{@adventure 第三章|SCC-CK|0}中的"
        "“{@book 同学|SCC|3|Fellow Students}”部分），这些学生会热切地提供帮助。"
    )


def test_tag_only_sync_matches_book_anchor_when_table_suffix_moves_outside_tag():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []
    generator.dictionary = type(
        "FakeDictionary",
        (),
        {"get": lambda self, en, tag="": (None, False)},
    )()

    old_en = (
        "Creatures from the {@book Elemental Earth Encounters table|BGG|3|"
        "Elemental Earth Encounters} arrive (see {@book chapter 3|BGG|3})."
    )
    new_en = (
        "Creatures from the {@table Elemental Earth Encounters|BGG} table "
        "arrive (see {@book chapter 3|BGG|3})."
    )
    old_cn = (
        "来自{@book Elemental Earth Encounters table|BGG|3|元素土遭遇表}中的"
        "生物出现了（见{@book chapter 3|BGG|3}）。"
    )

    result = generator._JsonGenerator__sync_tag_only_cn(
        old_cn,
        old_en,
        new_en,
        tag="book",
    )

    assert result == (
        "来自{@table 元素土遭遇表|BGG}中的生物出现了"
        "（见{@book chapter 3|BGG|3}）。"
    )


def test_replace_jobs_migrates_book_encounter_link_to_table_with_child_jobs():
    old_en = (
        "The {@creature unicorn} and her {@creature centaur} subjects fight "
        "{@creature Displacer Beast||displacer beasts} and creatures from the "
        "{@book Elemental Earth Encounters table|BGG|3|Elemental Earth Encounters} "
        "(see {@book chapter 3|BGG|3})."
    )
    new_en = (
        "The {@creature unicorn} and her {@creature centaur} subjects fight "
        "{@creature Displacer Beast||displacer beasts} and creatures from the "
        "{@table Elemental Earth Encounters|BGG} table "
        "(see {@book chapter 3|BGG|3})."
    )
    old_cn = (
        "{@creature unicorn}和其{@creature centaur}臣子对抗"
        "{@creature Displacer Beast||移位兽}和来自"
        "{@book Elemental Earth Encounters table|BGG|3|元素土遭遇表}中的生物"
        "（见{@book chapter 3|BGG|3}）。"
    )

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = type(
        "FakeDictionary",
        (),
        {
            "get": lambda self, en, tag="": (None, False),
            "update": lambda self, *args, **kwargs: True,
        },
    )()
    generator.update_dictionary_during_generation = False
    generator.done_jobs = [
        Job(
            uid="encounter",
            en_str=new_en,
            cn_str=old_cn,
            tag="book",
            sql_id=265051,
            tag_sync_required=True,
            old_en_str=old_en,
        ),
        Job(uid="encounter.creature[0].0", en_str="unicorn", cn_str="独角兽", tag="creature"),
        Job(uid="encounter.creature[1].0", en_str="centaur", cn_str="人马", tag="creature"),
        Job(uid="encounter.creature[2].0", en_str="Displacer Beast", cn_str="移位兽", tag="creature"),
        Job(uid="encounter.creature[2].2", en_str="displacer beasts", cn_str="移位兽", tag="creature"),
        Job(uid="encounter.table[3].0", en_str="Elemental Earth Encounters", cn_str="土元素遭遇", tag="table"),
        Job(uid="encounter.book[4].0", en_str="chapter 3", cn_str="第三章", tag="book"),
        Job(uid="encounter.book[4].1", en_str="BGG", cn_str="BGG", tag="book"),
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ encounter}"]}
    )

    assert ok is True
    assert cn_obj == {
        "entries": [
            "{@creature 独角兽}和其{@creature 人马}臣子对抗"
            "{@creature 移位兽||移位兽}和来自{@table 土元素遭遇|BGG}中的生物"
            "（见{@book 第三章|BGG|3}）。"
        ]
    }


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


def test_replace_sub_jobs_keeps_empty_creature_source_segment():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Skeleton", "creature"): "骷髅",
            ("skeletons", "creature"): "骷髅们",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@creature 骷髅|BGDIA|骷髅们}",
        "{@creature Skeleton||skeletons}",
    )

    assert success is True
    assert result == "{@creature 骷髅||骷髅们}"


def test_replace_sub_jobs_keeps_mixed_case_creature_source_after_reordered_tags():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("githzerai uniter", "creature"): "吉斯泽莱统念者",
            ("Zaythir", "creature"): "扎伊西尔",
            ("Morte's Planar Parade", "book"): "莫提的位面游记",
            ("Zaythir", "adventure"): "扎伊西尔",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    en_str = (
        "The Fiends have kept the castle's caretaker, a neutral "
        "{@creature githzerai uniter|MPP} (see {@book Morte's Planar Parade|MPP}) "
        "named {@creature Zaythir|ToFW}, in this chamber. "
        "{@creature Zaythir|ToFW} is eager to be free and evict the devils from her home. "
        'She is further detailed in the "{@adventure Zaythir|ToFW|2|Zaythir}" section.'
    )
    cn_str = (
        "邪魔们将城堡的看护者，一个名叫{@creature 扎伊西尔|MPP}的绝对中立吉斯"
        "{@creature 吉斯泽莱统念者|MPP}（见《{@book 莫提的位面游记|MPP}》）"
        "关押在这个房间里。{@creature 扎伊西尔|ToFW}渴望自由并希望将邪魔们驱逐出她的家。"
        '她在"{@adventure 扎伊西尔|ToFW|2|扎伊西尔}"一节中有更详细的描述。'
    )

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert "{@creature 扎伊西尔|MPP}的绝对中立吉斯" not in result
    assert "{@creature 扎伊西尔|ToFW}的绝对中立吉斯" in result


def test_replace_sub_jobs_keeps_format_tag_from_stealing_creature_match():
    generator = JsonGenerator.__new__(JsonGenerator)
    base_uid = "$.entries[0]"
    generator.done_jobs = [
        Job(
            uid=f"{base_uid}.b[0].0",
            en_str="Darkmantles",
            cn_str="暗幕魔兽",
            tag="b",
        ),
        Job(
            uid=f"{base_uid}.creature[1].0",
            en_str="Darkmantle",
            cn_str="暗幕魔兽",
            tag="creature",
        ),
        Job(
            uid=f"{base_uid}.creature[1].2",
            en_str="darkmantles",
            cn_str="暗幕魔兽",
            tag="creature",
        ),
    ]
    generator._JsonGenerator__build_lookup_cache()

    def fake_process_value(value, tag=""):
        translations = {
            ("Darkmantles", "b"): "暗幕魔兽",
            ("Darkmantle", "creature"): "暗幕魔兽",
            ("darkmantles", "creature"): "暗幕魔兽",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@b 暗罩蕈}。钟乳石悬挂在高达30尺的天花板上。两只伪装成钟乳石的"
        "{@creature Darkmantle||暗幕魔兽}保持静止，直到它们发起攻击。",
        "{@b Darkmantles}. Stalactites hang from the 30-foot-high ceiling. Two "
        "{@creature Darkmantle||darkmantles} disguised as stalactites remain motionless until they attack.",
        tag="adventure",
        base_uid=base_uid,
    )

    assert success is True
    assert result == (
        "{@b 暗幕魔兽}。钟乳石悬挂在高达30尺的天花板上。两只伪装成钟乳石的"
        "{@creature 暗幕魔兽||暗幕魔兽}保持静止，直到它们发起攻击。"
    )


def test_replace_sub_jobs_keeps_format_tag_from_stealing_area_match():
    generator = JsonGenerator.__new__(JsonGenerator)
    base_uid = "$.entries[0]"
    generator.done_jobs = [
        Job(
            uid=f"{base_uid}.b[0].0",
            en_str="Crystal Stalactite",
            cn_str="水晶钟乳石",
            tag="b",
        ),
        Job(
            uid=f"{base_uid}.creature[1].0",
            en_str="githyanki gish",
            cn_str="吉斯洋基吉什精锐",
            tag="creature",
        ),
        Job(
            uid=f"{base_uid}.creature[1].1",
            en_str="MTF",
            cn_str="MTF",
            tag="creature",
        ),
        Job(
            uid=f"{base_uid}.area[3].0",
            en_str="Crystal Stalactites",
            cn_str="水晶钟乳石",
            tag="area",
        ),
    ]
    generator._JsonGenerator__build_lookup_cache()

    def fake_process_value(value, tag=""):
        translations = {
            ("Crystal Stalactite", "b"): "水晶石笋",
            ("githyanki gish", "creature"): "吉斯洋基吉什精锐",
            ("appendix A", "adventure"): "附录A",
            ("Crystal Stalactites", "area"): "水晶钟乳石",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@b 水晶钟乳石}. 一根10尺长的水晶钟乳石悬挂在一个充满银色雾气的坑上方。"
        "水晶钟乳石内部封着伊尔梅，一位女性{@creature 吉斯洋基战士|MTF}"
        "（见{@adventure 附录A|WDMM|25}以获取具体数值；另见"
        '"{@area 水晶钟乳石|832|x}"）。',
        "{@b Crystal Stalactite}. A 10-foot-long crystal stalactite hangs above a pit "
        "filled with silvery mist. Encased in the stalactite is Ilmei, a female "
        "{@creature githyanki gish|MTF} (see {@adventure appendix A|WDMM|25} "
        'for statistics; see also "{@area Crystal Stalactites|832|x}").',
        tag="adventure",
        base_uid=base_uid,
    )

    assert success is True
    assert "{@b 水晶钟乳石}" in result
    assert "{@creature 吉斯洋基吉什精锐|MTF}" in result
    assert '"{@area 水晶钟乳石|832|x}"' in result


def test_replace_sub_jobs_uses_matched_english_index_for_reordered_same_tag_jobs():
    generator = JsonGenerator.__new__(JsonGenerator)
    base_uid = "$.entry"
    generator.done_jobs = [
        Job(
            uid=f"{base_uid}.creature[0].0",
            en_str="mind flayer",
            cn_str="夺心魔",
            tag="creature",
        ),
        Job(
            uid=f"{base_uid}.creature[1].0",
            en_str="grimlock",
            cn_str="幽邃盲族",
            tag="creature",
        ),
        Job(
            uid=f"{base_uid}.creature[1].2",
            en_str="grimlocks",
            cn_str="幽邃盲族",
            tag="creature",
        ),
        Job(
            uid=f"{base_uid}.creature[2].0",
            en_str="troll",
            cn_str="巨魔",
            tag="creature",
        ),
        Job(
            uid=f"{base_uid}.creature[2].2",
            en_str="trolls",
            cn_str="巨魔",
            tag="creature",
        ),
    ]
    generator._JsonGenerator__build_lookup_cache()

    def fake_process_value(value, tag=""):
        translations = {
            ("mind flayer", "creature"): "夺心魔",
            ("grimlock", "creature"): "幽邃盲族",
            ("grimlocks", "creature"): "幽邃盲族",
            ("troll", "creature"): "巨魔",
            ("trolls", "creature"): "巨魔",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "协调不同种类生物努力的人，例如使用{@creature 幽邃盲族|MM|幽邃盲族}和"
        "{@creature 巨魔|MM|巨魔}作为代理人的{@creature 巨魔}主谋。",
        "Someone who coordinates the efforts of different kinds of creatures, such as a "
        "{@creature mind flayer} mastermind using {@creature grimlock|MM|grimlocks} "
        "and {@creature troll|MM|trolls} as agents.",
        tag="deck",
        base_uid=base_uid,
    )

    assert success is True
    assert result == (
        "协调不同种类生物努力的人，例如使用{@creature 幽邃盲族|MM|幽邃盲族}和"
        "{@creature 巨魔|MM|巨魔}作为代理人的{@creature 夺心魔}主谋。"
    )


def test_replace_sub_jobs_uses_position_when_mixed_tag_sequence_matches():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Commoner", "creature"): "平民",
            ("Utilize", "action"): "操作",
            ("15", "dc"): "15",
            ("Sleight of Hand", "skill"): "巧手",
            ("Thieves' Tools", "item"): "盗贼工具",
            ("Athletics", "skill"): "运动",
            ("damage threshold", "variantrule"): "伤害阈值",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    en_str = (
        "Groth kept this gnome named Gooster ({@creature Commoner|XMM}) as a prisoner. "
        "To free him, the character must take the {@action Utilize|XPHB} action and "
        "succeed on a {@dc 15} Dexterity ({@skill Sleight of Hand|XPHB}) check using "
        "{@item Thieves' Tools|XPHB}. A character can also succeed on a {@dc 15} "
        "Strength ({@skill Athletics|XPHB}) check. The chains have a "
        "{@variantrule damage threshold|XPHB} of 5."
    )
    cn_str = (
        "格罗斯将这位名叫古斯特的侏儒（{@creature 平民|XMM}）囚禁起来。"
        "若要解救他，角色必须执行{@action 操作|XPHB}动作，使用{@dc 盗贼工具}"
        "并成功通过一次{@skill 15|XPHB}敏捷（{@item 巧手|XPHB}）检定。"
        "角色也可以成功通过一次{@dc 15}力量（{@skill 运动|XPHB}）检定。"
        "锁链具有{@variantrule 伤害阈值|XPHB} 5。"
    )

    result, success = generator._JsonGenerator__replace_sub_jobs(
        cn_str,
        en_str,
        tag="adventure",
    )

    assert success is True
    assert "{@dc 15}" in result
    assert "{@skill 巧手|XPHB}" in result
    assert "{@item 盗贼工具|XPHB}" in result
    assert "{@variantrule 伤害阈值|XPHB}" in result


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


def test_replace_sub_jobs_uses_generic_dictionary_for_card_parts():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag="", allow_generic_fallback=False):
        translations = {
            ("Artifact", ""): "神器",
            ("Tarokka Deck", ""): "塔罗卡牌组",
        }
        translated = translations.get((value, tag))
        if translated is None and allow_generic_fallback:
            translated = translations.get((value, ""))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@card Artifact|Tarokka Deck|CoS}",
        "{@card Artifact|Tarokka Deck|CoS}",
    )

    assert success is True
    assert result == "{@card 神器|塔罗卡牌组|CoS}"


def test_replace_sub_jobs_splits_table_name_on_semicolon():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag="", allow_generic_fallback=False):
        translations = {
            ("Alignment and Personality", ""): "阵营与个性",
            ("Personality Traits by Alignment", ""): "根据阵营的个性特征",
            ("table of brief personality traits", ""): "简要性格特征表",
        }
        translated = translations.get((value, tag))
        if translated is None and allow_generic_fallback:
            translated = translations.get((value, ""))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@table Alignment and Personality; Personality Traits by Alignment|XPHB|table of brief personality traits}",
        "{@table Alignment and Personality; Personality Traits by Alignment|XPHB|table of brief personality traits}",
    )

    assert success is True
    assert result == "{@table 阵营与个性; 根据阵营的个性特征|XPHB|简要性格特征表}"


def test_replace_sub_jobs_prefers_whole_table_name_before_semicolon_split():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag="", allow_generic_fallback=False):
        translations = {
            ("Consider the Ideal Climax; Adventure Climax", ""): "安排理想的高潮; 冒险高潮",
            ("Adventure Climax", ""): "冒险高潮",
            ("Adventure Climax table", ""): "冒险高潮表",
        }
        translated = translations.get((value, tag))
        if translated is None and allow_generic_fallback:
            translated = translations.get((value, ""))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@table Consider the Ideal Climax; Adventure Climax||Adventure Climax table}",
        "{@table Consider the Ideal Climax; Adventure Climax||Adventure Climax table}",
    )

    assert success is True
    assert result == "{@table 安排理想的高潮; 冒险高潮||冒险高潮表}"


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


def test_replace_sub_jobs_keeps_subclass_source_metadata():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    def fake_process_value(value, tag=""):
        translations = {
            ("Alchemist", "subclass"): "炼金师",
            ("Artificer", "subclass"): "奇械师",
            ("EFA", "subclass"): "艾伯伦：熔炉奇械",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated is not None else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(
        "{@subclass 炼金师|奇械师|EFA|炼金师}",
        "{@subclass Alchemist|Artificer|EFA|EFA}",
    )

    assert success is True
    assert result == "{@subclass 炼金师|奇械师|EFA|EFA}"


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


def test_exact_translation_preserves_prose_but_translates_retained_tags():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = None
    parent_uid = "$.entry"
    generator.done_jobs = [
        Job(
            parent_uid,
            "Use {@spell Find Familiar|XPHB} and track {@variantrule Hit Points|XPHB}.",
            "人工正文：使用{@spell Find Familiar|XPHB}并记录{@variantrule Hit Points|XPHB}。",
            tag="entries",
            exact_translation=True,
        ),
        Job(f"{parent_uid}.spell[0].0", "Find Familiar", "寻获魔宠", tag="spell"),
        Job(f"{parent_uid}.variantrule[1].0", "Hit Points", "生命值", tag="variantrule"),
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entry": f"{{!@ {parent_uid}}}"}
    )

    assert ok is True
    assert cn_obj == {
        "entry": "人工正文：使用{@spell 寻获魔宠|XPHB}并记录{@variantrule 生命值|XPHB}。"
    }


def test_exact_translation_translates_retained_tag_without_restoring_omitted_tag():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = None
    generator.done_jobs = [
        Job(
            "$.entry",
            "{@condition Poisoned|XPHB}, then {@condition Frightened|XPHB}.",
            "仅保留{@condition Poisoned|XPHB}。",
            tag="entries",
            exact_translation=True,
        ),
        Job("poisoned", "Poisoned", "中毒", tag="condition"),
        Job("frightened", "Frightened", "恐慌", tag="condition"),
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entry": "{!@ $.entry}"}
    )

    assert ok is True
    assert cn_obj == {"entry": "仅保留{@condition 中毒|XPHB}。"}


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


def test_tag_only_sync_wraps_new_duplicate_tag_after_existing_same_tag():
    class FakeDictionary:
        def get(self, en, tag=""):
            if en == "Magic" and tag == "action":
                return "魔法", True
            return None, False

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []
    generator.dictionary = FakeDictionary()

    old_en = (
        "Finally, a creature in Limbo can take a {@action Magic|XPHB} action "
        "to make a {@dc 20} Intelligence check to stabilize an area within a "
        "30-foot-radius {@variantrule Sphere [Area of Effect]|XPHB|Sphere} "
        "centered on a point it can see on the plane. On a successful check, "
        "the creature prevents the area from being altered by the plane for "
        "24 hours or until the creature takes this Magic action again."
    )
    new_en = (
        "Finally, a creature in Limbo can take a {@action Magic|XPHB} action "
        "to make a {@dc 20} Intelligence check to stabilize an area within a "
        "30-foot-radius {@variantrule Sphere [Area of Effect]|XPHB|Sphere} "
        "centered on a point it can see on the plane. On a successful check, "
        "the creature prevents the area from being altered by the plane for "
        "24 hours or until the creature takes this {@action Magic|XPHB} action again."
    )
    old_cn = (
        "最后，一名位于混沌海中的生物可以用一个{@action Magic|XPHB}动作"
        "进行一次{@dc 20} 的智力检定来稳定以位于位面上其可见的一点为中心的 "
        "30 尺半径{@variantrule Sphere [Area of Effect]|XPHB|Sphere}区域。"
        "若检定成功，则该生物将避免该区域被位面所改变，持续 24 小时或者直到"
        "该生物再次使用该魔法动作为止。"
    )

    result = generator._JsonGenerator__sync_tag_only_cn(
        old_cn,
        old_en,
        new_en,
        tag="book",
    )

    assert "再次使用该{@action 魔法|XPHB}动作为止" in result
    assert result.count("{@") == new_en.count("{@")


def test_replace_jobs_syncs_stale_cn_when_database_en_was_already_updated():
    class FakeDictionary:
        def __init__(self):
            self.calls = []

        def get(self, en, tag=""):
            translations = {
                ("Magic", "action"): "魔法",
                ("Sphere [Area of Effect]", ""): "球状 [效应区域]",
                ("Sphere", ""): "球状",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

        def update(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return True

    en_str = (
        "Finally, a creature in Limbo can take a {@action Magic|XPHB} action "
        "to make a {@dc 20} Intelligence check to stabilize an area within a "
        "30-foot-radius {@variantrule Sphere [Area of Effect]|XPHB|Sphere} "
        "centered on a point it can see on the plane. On a successful check, "
        "the creature prevents the area from being altered by the plane for "
        "24 hours or until the creature takes this {@action Magic|XPHB} action again."
    )
    stale_cn = (
        "最后，一名位于混沌海中的生物可以用一个{@action Magic|XPHB}动作"
        "进行一次{@dc 20} 的智力检定来稳定以位于位面上其可见的一点为中心的 "
        "30 尺半径{@variantrule Sphere [Area of Effect]|XPHB|Sphere}区域。"
        "若检定成功，则该生物将避免该区域被位面所改变，持续 24 小时或者直到"
        "该生物再次使用该魔法动作为止。"
    )

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FakeDictionary()
    generator.update_dictionary_during_generation = True
    generator.done_jobs = [
        Job(
            uid="xdmg",
            en_str=en_str,
            cn_str=stale_cn,
            tag="book",
            sql_id=349418,
            is_proofread=True,
        )
    ]

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ xdmg}"]}
    )

    assert ok is True
    assert "再次使用该{@action 魔法|XPHB}动作为止" in cn_obj["entries"][0]
    assert cn_obj["entries"][0].count("{@") == en_str.count("{@")
    assert generator.dictionary.calls


def test_replace_sub_jobs_preserves_reordered_book_links_by_value_shape():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = (
        'Each character gains a charm of heroism (see "{@book Supernatural '
        'Gifts|DMG|7|Charms}" in the {@book Dungeon Master\'s Guide|DMG}).'
    )
    cn_str = (
        "每个角色获得英雄护咒（参见{@book 城主指南|DMG}中的"
        "\"{@book 超自然赠礼|DMG|7|护咒}\"）。"
    )

    def fake_process_value(value, tag=""):
        translations = {
            ("Supernatural Gifts", "book"): "超自然赠礼",
            ("Charms", "book"): "护咒",
            ("Dungeon Master's Guide", "book"): "城主指南",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == cn_str


def test_replace_sub_jobs_preserves_reordered_book_links_after_repeated_area_tags():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []
    generator.dictionary = None
    generator._JsonGenerator__build_lookup_cache()

    en_str = (
        "It contains a tiny portrait of the baron's wife, whom the characters "
        "might recognize from the larger framed portrait in {@area area C17|10f|x}. "
        "If this cameo is reunited with the baron (whose remains are interred in "
        "{@area area C9|0f4|x}), each character responsible for this act gains a "
        "charm of heroism (see \"{@book Supernatural Gifts|DMG|7|Charms}\" in "
        "the {@book Dungeon Master's Guide|DMG})."
    )
    cn_str = (
        "它包含男爵夫人的小肖像，角色们可能会从{@area 区域C17|10f|x}中的"
        "大型裱框肖像中认出她。如果这个浮雕与男爵（其遗骸安葬在"
        "{@area 区域C9|0f4|x}）重聚，每个为此事负责的角色将获得一个"
        "英雄魅力（参见{@book 城主指南|DMG}中的“{@book 超自然馈赠|DMG|7|魅力}”）。"
    )

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == cn_str


def test_replace_sub_jobs_preserves_reordered_same_shape_creature_links():
    generator = JsonGenerator.__new__(JsonGenerator)
    generator.done_jobs = []

    en_str = (
        "They visit {@creature Traxigor|BGDIA}, a wizard who can use the "
        "{@spell plane shift} spell. At this wizard's tower, they meet a "
        "{@creature hollyphant|BGDIA} named {@creature Lulu|BGDIA}."
    )
    cn_str = (
        "他们去拜访{@creature 特拉西戈尔|BGDIA}，一名可以使用"
        "{@spell 平面转移}法术的法师。在这位法师的法师塔中，"
        "他们将遇到一只名为{@creature 露露|BGDIA}的"
        "{@creature 霍利芬特|BGDIA}。"
    )

    def fake_process_value(value, tag=""):
        translations = {
            ("Traxigor", "creature"): "特拉西戈尔",
            ("plane shift", "spell"): "平面转移",
            ("hollyphant", "creature"): "霍利芬特",
            ("Lulu", "creature"): "露露",
        }
        translated = translations.get((value, tag))
        return (translated, True) if translated else (value, False)

    generator._JsonGenerator__process_value = fake_process_value

    result, success = generator._JsonGenerator__replace_sub_jobs(cn_str, en_str)

    assert success is True
    assert result == cn_str


def test_replace_jobs_continues_after_position_alignment_without_parent_cache_reset():
    class FakeDictionary:
        def get(self, en, tag=""):
            translations = {
                ("Lorehold apprentice", "creature"): "衡鉴学徒",
                ("chapter 3", "book"): "第三章",
                ("Greta Gorunn", "book"): "格丽塔·戈伦",
                ("belt of dwarvenkind", "item"): "矮人腰带",
            }
            value = translations.get((en, tag))
            return (value, True) if value is not None else (None, False)

        def update(self, *args, **kwargs):
            return True

    en_str = (
        "Further, Greta Gorunn (chaotic good dwarf "
        "{@creature Lorehold apprentice|SCC}; see "
        "{@book chapter 3|SCC|3|Greta Gorunn}) has offered a "
        "{@item belt of dwarvenkind} as the prize for this contest."
    )
    stale_cn = (
        "此外，Greta Gorunn（混乱善良矮人{@creature Lorehold apprentice|SCC}; "
        "见{@adventure 第3章|SCC-CK|0|Greta Gorunn}）提供了一条"
        "{@item 矮人族腰带}作为这次比赛的奖品。"
    )

    generator = JsonGenerator.__new__(JsonGenerator)
    generator.dictionary = FakeDictionary()
    generator.update_dictionary_during_generation = False
    generator.done_jobs = [
        Job(
            uid="contest",
            en_str=en_str,
            cn_str=stale_cn,
            tag="adventure",
            sql_id=223739,
            is_proofread=False,
        )
    ]
    generator.done_jobs[0].old_en_str = (
        "Further, Greta Gorunn (chaotic good dwarf "
        "{@creature Lorehold apprentice|SCC}; see "
        "{@adventure chapter 3|SCC-CK|0|Greta Gorunn}) has offered a "
        "{@item belt of dwarvenkind} as the prize for this contest."
    )
    generator.done_jobs[0].tag_sync_required = True

    cn_obj, ok = generator._JsonGenerator__replace_jobs(
        {"entries": ["{!@ contest}"]}
    )

    assert ok is True
    assert cn_obj == {
        "entries": [
            "此外，Greta Gorunn（混乱善良矮人{@creature 衡鉴学徒|SCC}; "
            "见{@book 第三章|SCC|3|格丽塔·戈伦}）提供了一条"
            "{@item 矮人腰带}作为这次比赛的奖品。"
        ]
    }
