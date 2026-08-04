from app.cli.term import _add_word_terms_to_term_table, _dedupe_terms_by_db_key
from app.core.bean.term import Term


def test_dedupe_terms_by_db_key_skips_same_en_category_duplicates():
    terms, skipped_same, conflicts = _dedupe_terms_by_db_key([
        Term("Magic", "action", "魔法"),
        Term("Magic", "action", "魔法"),
        Term("Magic", "spell", "魔法"),
    ])

    assert [(term.en, term.category, term.cn) for term in terms] == [
        ("Magic", "action", "魔法"),
        ("Magic", "spell", "魔法"),
    ]
    assert skipped_same == 1
    assert conflicts == 0


def test_add_word_terms_dedupes_before_database_insert():
    class FakeDB:
        def __init__(self):
            self.inserted = []

        def get_all_term_entries(self):
            return []

        def get_word_terms_for_sync(self):
            return [
                Term("Magic", "action", "魔法"),
                Term("Magic", "action", "魔法"),
                Term("Magic", "spell", "魔法"),
            ]

        def put_term_ignore_duplicate(self, term, **_kwargs):
            self.inserted.append((term.en, term.category, term.cn))
            return 1

    db = FakeDB()

    _add_word_terms_to_term_table(db)

    assert db.inserted == [
        ("Magic", "action", "魔法"),
        ("Magic", "spell", "魔法"),
    ]


def test_add_word_terms_treats_existing_case_variants_as_duplicates():
    class FakeDB:
        def __init__(self):
            self.inserted = []

        def get_all_term_entries(self):
            return [
                {
                    "en": "a petrified mouse",
                    "category": "",
                    "cn": "一只被石化的老鼠",
                }
            ]

        def get_word_terms_for_sync(self):
            return [
                Term("A petrified mouse", "", "一只被石化的老鼠"),
            ]

        def put_term_ignore_duplicate(self, term, **_kwargs):
            self.inserted.append((term.en, term.category, term.cn))
            return 1

    db = FakeDB()

    _add_word_terms_to_term_table(db)

    assert db.inserted == []


def test_add_word_terms_treats_database_ignored_duplicate_as_skipped():
    class FakeDB:
        def __init__(self):
            self.inserted = []

        def get_all_term_entries(self):
            return []

        def get_word_terms_for_sync(self):
            return [
                Term("Joker (with ™)", "", "大小王（带™）"),
            ]

        def put_term_ignore_duplicate(self, term, **_kwargs):
            self.inserted.append((term.en, term.category, term.cn))
            return 0

    db = FakeDB()

    _add_word_terms_to_term_table(db)

    assert db.inserted == [
        ("Joker (with ™)", "", "大小王（带™）"),
    ]
