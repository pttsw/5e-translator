import json
import csv
import os
from concurrent.futures import ThreadPoolExecutor
from app.core.database import DBDictionary, RedisDB
from app.core.bean.term import Term
import re

try:
    import docx2txt
except ImportError:
    docx2txt = None

db = DBDictionary()

def compare_term(term_path: str):
    with open(term_path, 'r') as f:
        lines = f.readlines()
        res:dict[str, str] = {}
        
        # 定义并发处理单行的函数
        def process_line(line):
            term = line.strip()
            cn, ok = db.get(term, load_from_sql=True)
            return term, cn if ok else ""
        
        # 使用线程池并发处理所有行
        with ThreadPoolExecutor() as executor:
            results = executor.map(process_line, lines)
            for term, cn in results:
                res[term] = cn
                print(f"{term} -> {cn}")
        
    with open("term.json", 'w') as f:
        f.write(json.dumps(res, ensure_ascii=False, indent=2))
        
def _add_word_terms_to_term_table(mysql_db: DBDictionary):
    existing_terms = {
        (row['en'], row.get('category') or ''): row['cn']
        for row in mysql_db.get_all_term_entries()
    }
    inserted = 0
    skipped = 0
    conflicts = 0

    for term in sorted(
        mysql_db.get_word_terms_for_sync(),
        key=lambda item: (item.en.lower(), item.category or '', item.cn),
    ):
        key = (term.en, term.category or '')
        existing_cn = existing_terms.get(key)
        if existing_cn is not None:
            if existing_cn != term.cn:
                conflicts += 1
                print(
                    f"Term conflict, kept existing value: "
                    f"{term.en} [{term.category or ''}] {existing_cn} != {term.cn}"
                )
            else:
                skipped += 1
            continue

        inserted_ok = mysql_db.put_term(
            term,
            source='words',
            modified_by=1,
            modified_reson='term add from words',
        )
        if not inserted_ok:
            print(f"Failed to insert term: {term.en} [{term.category or ''}] -> {term.cn}")
            continue
        existing_terms[key] = term.cn
        inserted += 1

    print(
        f"Synced words to term table: inserted={inserted}, "
        f"skipped={skipped}, conflicts={conflicts}"
    )


def add_confirmed_term(mysql_db: DBDictionary, en: str, cn: str, category="", source=""):
    category = category or ''
    same_category_rows = [
        row for row in mysql_db.get_term_entries(en)
        if (row.get('category') or '') == category
    ]
    if same_category_rows:
        existing_cn = same_category_rows[0]['cn']
        if existing_cn != cn:
            print(
                f"Term conflict, kept existing value: "
                f"{en} [{category}] {existing_cn} != {cn}"
            )
            return False
        return True

    ok = mysql_db.put_term(
        Term(en, category, cn),
        source=source,
        modified_by=1,
        modified_reson='term analyze confirmed',
    )
    if ok:
        print(f"Added confirmed term: {en} [{category}] -> {cn}")
    else:
        print(f"Failed to insert confirmed term: {en} [{category}] -> {cn}")
    return ok


def add_mysql_terms_to_redis():
    mysql_db = DBDictionary()
    _add_word_terms_to_term_table(mysql_db)
    redis_db = RedisDB(db=1)
    mysql_terms = mysql_db.get_all_term()
    redis_db.clean()
    for term in mysql_terms:
        redis_db.put(term.en, term.cn, term.category)
        # print(f"{term.en} -> {term.cn}")
    print(f"Added {len(mysql_terms)} terms to Redis")
        
def combine_temp_terms_to_csv():
    mysql_db = DBDictionary()
    mysql_terms = mysql_db.get_all_term()
    print(len(mysql_terms))
    with open("temp_terms.csv", 'w') as f:
        for term in mysql_terms:
            if ',' in term.en or ',' in term.cn:
                continue
            f.write(f"{term.en},{term.cn},{term.category}\n")


def _collect_term_input_files(input_path: str):
    file_paths = []
    if os.path.isfile(input_path):
        return [input_path]

    for root, _, files in os.walk(input_path):
        for file in files:
            if not file.lower().endswith(('.txt', '.csv', '.docx')):
                continue
            file_paths.append(os.path.join(root, file))
    return sorted(file_paths)


def _prompt_sync_action(en: str, new_cn: str, existing_rows: list[dict]):
    print(f"\n术语冲突: {en}")
    print(f"新中文: {new_cn}")
    for index, row in enumerate(existing_rows, 1):
        print(f"{index}. {row['cn']} (category={row.get('category')}, source={row.get('source')})")

    if len(existing_rows) == 1:
        prompt = "选择操作: [o] 覆盖现有记录, [a] 改新分类新增, [s] 跳过: "
    else:
        prompt = "选择操作: 输入序号覆盖对应记录, 或输入 [a] 改新分类新增, [s] 跳过: "

    while True:
        resp = input(prompt).strip().lower()
        if resp in ('a', 'add'):
            return 'add', None
        if resp in ('s', 'skip', ''):
            return 'skip', None
        if resp in ('o', 'overwrite'):
            if len(existing_rows) == 1:
                return 'overwrite', existing_rows[0]
            print("当前有多条冲突记录，请输入要覆盖的序号。")
            continue
        if resp.isdigit():
            selected_index = int(resp) - 1
            if 0 <= selected_index < len(existing_rows):
                return 'overwrite', existing_rows[selected_index]
        print("输入无效，请重新输入。")


def _prompt_new_category(en: str, current_category: str):
    while True:
        new_category = input(
            f"为 {en} 输入新的 category（当前冲突 category={current_category!r}，留空则取消新增）: "
        ).strip()
        if new_category == "":
            return None
        if new_category == current_category:
            print("新 category 不能与当前冲突 category 相同。")
            continue
        return new_category


def _sync_term_pairs(mysql_db: DBDictionary, terms: list[Term], stats: dict, source_label: str):
    stats['parsed_terms'] += len(terms)
    for term in terms:
        en = term.en
        cn = term.cn
        category = term.category or ''
        existing_rows = mysql_db.get_term_entries(en)
        same_category_rows = [row for row in existing_rows if (row.get('category') or '') == category]
        exact_match = next((row for row in existing_rows if row['cn'] == cn), None)
        if exact_match is not None:
            print(
                f"跳过已存在术语: {en} -> {cn} "
                f"(existing_category={exact_match.get('category') or ''}, category={category})"
            )
            stats['skipped_same'] += 1
            continue

        if not same_category_rows:
            mysql_db.put_term(Term(en, category, cn), source=source_label, modified_by=1, modified_reson='term sync insert')
            print(f"新增术语: {en} -> {cn} (category={category})")
            stats['inserted'] += 1
            continue

        action, selected_row = _prompt_sync_action(en, cn, same_category_rows)
        if action == 'add':
            new_category = _prompt_new_category(en, category)
            if new_category is None:
                print(f"取消新增术语: {en} -> {cn}")
                stats['skipped_manual'] += 1
                continue
            mysql_db.put_term(
                Term(en, new_category, cn),
                source=source_label,
                modified_by=1,
                modified_reson='term sync insert with new category'
            )
            print(f"新增术语: {en} -> {cn} (category={new_category})")
            stats['inserted'] += 1
            continue
        if action == 'overwrite' and selected_row is not None:
            selected_category = selected_row.get('category') or ''
            ok = mysql_db.update_term(
                en,
                selected_row['cn'],
                cn,
                selected_category,
                source=source_label,
                modified_by=1,
                modified_reson='term sync overwrite'
            )
            if ok:
                print(f"已覆盖 term: {en} {selected_row['cn']} -> {cn} (category={selected_category})")
                stats['updated'] += 1
            else:
                print(f"覆盖失败 term: {en} {selected_row['cn']} -> {cn} (category={selected_category})")
            continue

        print(f"跳过冲突术语: {en} -> {cn} (category={category})")
        stats['skipped_manual'] += 1


def _load_term_pairs_from_words(mysql_db: DBDictionary):
    term_pairs = []
    for term in sorted(mysql_db.get_word_terms_for_sync(), key=lambda item: (item.en.lower(), item.cn)):
        term_pairs.append(Term(term.en, term.category or '', term.cn))
    return term_pairs


def _load_terms_from_file(file_path: str):
    return [
        Term(en, '', cn)
        for en, cn in load_term_from_text(file_path).items()
        if not (en or '').lstrip().startswith('+')
    ]


def sync_terms_to_mysql(input_path: str):
    mysql_db = DBDictionary(conn_num=1)
    stats = {
        'inserted': 0,
        'updated': 0,
        'skipped_same': 0,
        'skipped_manual': 0,
        'parsed_terms': 0,
        'files': 0,
    }

    normalized_input = (input_path or '').strip()
    if normalized_input.lower() == 'words':
        print("\nsyncing words table terms using add-mode source rules")
        terms = _load_term_pairs_from_words(mysql_db)
        if not terms:
            print("未从 words 表中提取到可同步术语")
            return
        stats['files'] = 1
        _sync_term_pairs(mysql_db, terms, stats, source_label='words-sync')
        print(
            "\n同步完成: "
            f"files={stats['files']} "
            f"parsed_terms={stats['parsed_terms']} "
            f"inserted={stats['inserted']} "
            f"updated={stats['updated']} "
            f"skipped_same={stats['skipped_same']} "
            f"skipped_manual={stats['skipped_manual']}"
        )
        return

    if not normalized_input:
        normalized_input = '/data/5e-translator/app/core/crawler/valda'

    file_paths = _collect_term_input_files(normalized_input)
    if not file_paths:
        print(f"未找到可同步的文件: {normalized_input}")
        return

    for file_path in file_paths:
        print(f"\nsyncing {file_path}")
        terms = _load_terms_from_file(file_path)
        if not terms:
            continue

        stats['files'] += 1
        _sync_term_pairs(mysql_db, terms, stats, source_label=os.path.basename(file_path))

    print(
        "\n同步完成: "
        f"files={stats['files']} "
        f"parsed_terms={stats['parsed_terms']} "
        f"inserted={stats['inserted']} "
        f"updated={stats['updated']} "
        f"skipped_same={stats['skipped_same']} "
        f"skipped_manual={stats['skipped_manual']}"
    )

def _read_text_lines(file_path: str):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.readlines()
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='gbk') as f:
            return f.readlines()


def _read_docx_lines(file_path: str):
    if docx2txt is None:
        raise ImportError("未安装 docx2txt，无法解析 .docx 文件")

    text = docx2txt.process(file_path) or ""
    return text.splitlines()

def _load_term_from_csv(file_path: str):
    res: dict[str, str] = {}
    try:
        try:
            with open(file_path, 'r', encoding='utf-8', newline='') as f:
                rows = list(csv.reader(f))
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='gbk', newline='') as f:
                rows = list(csv.reader(f))

        for row_number, row in enumerate(rows, 1):
            if len(row) < 2:
                print(f"行 {row_number}: CSV 列数不足，跳过: {row}")
                continue

            english_part = row[0].strip()
            chinese_part = row[1].strip()
            if not english_part:
                print(f"行 {row_number}: 英文术语为空，跳过: {row}")
                continue
            if not chinese_part:
                print(f"行 {row_number}: 中文术语为空，跳过: {row}")
                continue
            if english_part in res:
                continue

            res[english_part] = chinese_part
            print(f"行 {row_number}: 已解析 CSV -> {english_part} -> {chinese_part}")

        print(f"总共从 CSV 解析了 {len(res)} 个术语")
        return res
    except FileNotFoundError:
        print(f"错误: 文件 '{file_path}' 不存在")
        return {}
    except Exception as e:
        print(f"处理 CSV 文件时发生错误: {str(e)}")
        return {}

def load_term_from_text(file_path: str):
    """
    从文本、CSV 或 DOCX 文件中加载术语。

    文本格式支持多种场景：
    1. 中文在前英文在后直接相连（如"桑缟Songal"）
    2. 中文在前英文在后，中间有空格（如"结阵魔法 Circle Magic"）
    3. 可能包含标点符号（如"什么是结阵法术？ What is a Circle Spell？"）
    4. 支持英文中的所有格和缩写形式（如"法师的法术书 Wizard's Spellbook"）
    5. 支持多词英文术语（如"咒火风暴 Spellfire Storm"）
    
    Args:
        file_path: 包含术语的文本文件路径
        
    Returns:
        dict: 以英文为key，中文为value的术语字典
    """
    try:
        file_ext = os.path.splitext(file_path)[1].lower()
        if file_ext == '.csv':
            return _load_term_from_csv(file_path)
        if file_ext == '.docx':
            lines = _read_docx_lines(file_path)
        else:
            # 尝试使用UTF-8编码打开文件，如果失败则尝试GBK
            lines = _read_text_lines(file_path)

        res: dict[str, str] = {}
        
        # 改进的正则表达式，支持中文在前英文在后的多种格式
        # 1. 中文和英文直接相连的格式，支持撇号
        pattern1 = re.compile(r'(?P<chinese>[一-鿿·]+)(?P<english>[a-zA-Z\'\-]+)')
        
        # 2. 中文在前英文在后，中间可能有空格和标点的格式，支持多词术语和撇号
        # 优先匹配包含多个大写单词的专业术语格式
        pattern2 = re.compile(r'(?P<chinese>[一-鿿]+[^一-鿿·]*?)(?P<english>([A-Z][a-z]+\s?)+(Storm|Spell|Magic|Wizard|Sorcerer|Cleric|Druid|Paladin|Ranger|Warlock|Bard|Fighter|Rogue|Monk|Barbarian))')
        
        # 3. 通用格式，匹配任何英文内容
        pattern3 = re.compile(r'(?P<chinese>[一-鿿·]+[^一-鿿·]*?)(?P<english>[a-zA-Z][a-zA-Z\'\-\s]+[a-zA-Z])')
        
        patterns = [pattern2, pattern3, pattern1]  # 按优先级排序
        
        for line_number, line in enumerate(lines, 1):
            # 去除行首尾空白字符
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 尝试用多个正则表达式匹配，按优先级顺序
            matched = False
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    chinese_part = match.group('chinese').strip().rstrip('：:？?(（｜')
                    english_part = match.group('english').strip().replace('  ',' ')
                    
                    # 英文做key，中文做value存入字典
                    if english_part in res:
                        continue
                    res[english_part] = chinese_part
                    print(f"行 {line_number}: 已解析 '{line}' -> {english_part} -> {chinese_part}")
                    matched = True
                    break
            
            if not matched:
                # 记录未匹配的行
                print(f"行 {line_number}: 未匹配到中文在前英文在后的格式: '{line}'")
        
        print(f"总共解析了 {len(res)} 个术语")
        for english, chinese in res.items():
            print(f"{english}: {chinese}")
        return res
    except FileNotFoundError:
        print(f"错误: 文件 '{file_path}' 不存在")
        return {}
    except Exception as e:
        print(f"处理文件时发生错误: {str(e)}")
        return {}
