from app.core.translator.foundry_identifier_stabilizer import FoundryIdentifierStabilizer
from app.core.utils.file_work_info import FileWorkInfo
from app.core.utils.job import Job


def _file_info(json_path, source, translated, jobs=None):
    file_info = FileWorkInfo(jobs or [], source, json_path, json_path)
    file_info.cn_obj = translated
    return file_info


def test_generates_identifier_from_english_title_for_class_foundry():
    source = {
        "class": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {"type": "dice", "scale": {}},
                "title": "Brutal Strike",
            }],
        }],
    }
    translated = {
        "class": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {"type": "dice", "scale": {}},
                "title": "凶蛮打击",
            }],
        }],
    }
    file_info = _file_info("class/foundry.json", source, translated)

    result = list(FoundryIdentifierStabilizer().invoke([file_info]))

    assert result == [file_info]
    assert translated["class"][0]["advancement"][0]["configuration"]["identifier"] == "brutal-strike"


def test_preserves_explicit_source_identifier():
    source = {
        "subclass": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {"identifier": "sorcery-points"},
                "title": "Sorcery Points",
            }],
        }],
    }
    translated = {
        "subclass": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {"identifier": "术法点"},
                "title": "术法点",
            }],
        }],
    }
    file_info = _file_info("/tmp/data/class/foundry.json", source, translated)

    list(FoundryIdentifierStabilizer().invoke([file_info]))

    assert translated["subclass"][0]["advancement"][0]["configuration"]["identifier"] == "sorcery-points"


def test_resolves_analyser_placeholder_before_generating_identifier():
    uid = "class-name-barbarian-source-xphb-advancement-1-title-0"
    source = {
        "class": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {"type": "dice", "scale": {}},
                "title": f"{{!@ {uid}}}",
            }],
        }],
    }
    translated = {
        "class": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {"type": "dice", "scale": {}},
                "title": "凶蛮打击",
            }],
        }],
    }
    jobs = [Job(uid=uid, en_str="Brutal Strike", cn_str="凶蛮打击")]
    file_info = _file_info("class/foundry.json", source, translated, jobs)

    list(FoundryIdentifierStabilizer().invoke([file_info]))

    configuration = translated["class"][0]["advancement"][0]["configuration"]
    assert configuration["identifier"] == "brutal-strike"


def test_does_not_modify_other_foundry_files():
    source = {
        "race": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {},
                "title": "Breath Weapon",
            }],
        }],
    }
    translated = {
        "race": [{
            "advancement": [{
                "type": "ScaleValue",
                "configuration": {},
                "title": "吐息武器",
            }],
        }],
    }
    file_info = _file_info("foundry-races.json", source, translated)

    list(FoundryIdentifierStabilizer().invoke([file_info]))

    assert "identifier" not in translated["race"][0]["advancement"][0]["configuration"]


def test_english_slug_removes_apostrophes_without_splitting_words():
    assert FoundryIdentifierStabilizer._slugify_english_title("Hunter's Mark") == "hunters-mark"
