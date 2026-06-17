# test_utils.py
import unittest
from app.core.utils.utils import only_has_format
import pytest
from app.core.utils.utils import check_skip_key, get_tag_display_text, normalize_tagless_text, parse_foundry_items_uuid_format

class TestOnlyHasFormat(unittest.TestCase):
    def test_only_format_no_english(self):
        """Test the case where there are only {@tag xxx} formats and no English"""
        text = "{@item Apple}{@spell Fireball}"
        self.assertTrue(only_has_format(text))
    
    def test_mixed_format_with_english(self):
        """Test the case of mixing {@tag xxx} and English"""
        text = "{@item Book} and {@spell Lightning}"
        self.assertFalse(only_has_format(text))
    
    def test_no_format_only_english(self):
        """Test the case where there is no format and only English"""
        text = "Hello World"
        self.assertFalse(only_has_format(text))
    
    def test_no_format_no_english(self):
        """Test the case where there is no format and no English"""
        text = "{@item Banana}{@spell Frost}"
        self.assertTrue(only_has_format(text))
    
    def test_nested_format_tags(self):
        """Test nested format tags"""
        text = "{@item {@nested Nested}}"
        self.assertTrue(only_has_format(text))
    
    def test_unclosed_format_tag(self):
        """Test unclosed format tags"""
        text = "{@item Unclosed"
        self.assertFalse(only_has_format(text))
    
    def test_empty_string(self):
        """Test an empty string"""
        text = ""
        self.assertTrue(only_has_format(text))
    
    def test_format_with_numbers(self):
        """Test format tags containing numbers"""
        text = "{@item 123}"
        self.assertTrue(only_has_format(text))
    
    def test_mixed_chinese_and_format(self):
        """Test the mix of Chinese and format tags (replaced with English)"""
        text = "Prefix{@item Orange}Suffix"
        self.assertFalse(only_has_format(text))
    
    def test_complex_nested_formats(self):
        """Test complex multi - level nested formats"""
        text = "{@a {@b {@c Deepest}}}Outer{@d Other}"
        self.assertFalse(only_has_format(text))



@pytest.mark.parametrize("input_text, expected_tags, expected_values, expected_valid", [
    # 正常匹配场景
    ("@spell[fireball|3rd_level]", ["spell","spell"], ["fireball","3rd_level"], True),
    # 多个标签匹配
    ("@item[sword|+1] @monster[goblin|CR1]", ["item","item", "monster","monster"], ["sword", "+1", "goblin", "CR1"], True),
    # 无匹配场景
    ("普通文本没有标签", [], [], False),
    # 特殊字符处理
    ("@tag[包含|竖线和!@#特殊字符]", ["tag","tag"], ["包含","竖线和!@#特殊字符"], True),
    # 无效格式（缺少闭合括号）
    ("@invalid[tag", [], [], False),
    # 无效格式（错误的括号位置）
    ("@tag]invalid[format", [], [], False),
    # 混合有效和无效格式
    ("@valid[value] @invalid[tag", ["valid"], ["value"], True),
])
def test_parse_foundry_items_uuid_format(input_text, expected_tags, expected_values, expected_valid):
    tags, values, is_valid = parse_foundry_items_uuid_format(input_text)
    assert tags == expected_tags
    assert values == expected_values
    assert is_valid == expected_valid


@pytest.mark.parametrize("old_text, new_text", [
    (
        "Auril's decision to live among mortals is explained in {@adventure appendix C|IDRotF|21}.",
        "{@filter Auril's|bestiary|source=IDRotF|search=Auril} decision to live among mortals is explained in {@adventure appendix C|IDRotF|21}.",
    ),
    (
        "Rules for extreme cold appear in the {@book Dungeon Master's Guide|DMG} but are repeated here.",
        "Rules for {@hazard extreme cold} appear in the {@book Dungeon Master's Guide|DMG|5|Wilderness Survival} but are repeated here.",
    ),
    (
        "Ythryn",
        "{@adventure Ythryn|IDRotF|17}",
    ),
])
def test_normalize_tagless_text_handles_5etools_link_metadata(old_text, new_text):
    assert normalize_tagless_text(old_text) == normalize_tagless_text(new_text)


def test_quickref_uses_first_value_unless_explicit_display_exists():
    assert get_tag_display_text("difficult terrain||3", "quickref") == "difficult terrain"
    assert normalize_tagless_text("{@quickref difficult terrain||3}") == "difficult terrain"
    assert get_tag_display_text("cover||3||total cover", "quickref") == "total cover"


def test_subclass_feature_uses_name_not_level_as_display_text():
    value = "Fast Hands|Rogue||Thief||3"

    assert get_tag_display_text(value, "subclassFeature") == "Fast Hands"
    assert normalize_tagless_text(f"{{@subclassFeature {value}}}") == "Fast Hands"


def test_skip_key_path_ignores_list_qualifiers():
    assert check_skip_key("group", "supplement", "/adventure[0]")
    assert check_skip_key("group", "supplement", "/adventure[id=NRH-ASS;source=NRH-ASS]")


def test_no_skip_path_still_overrides_skip_key_path_with_qualifiers():
    assert not check_skip_key("tags", "tag value", "/type[id=abc]")
    
if __name__ == '__main__':
    unittest.main()
