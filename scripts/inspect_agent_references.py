"""Read-only live probe for Stage B sources; no LLM calls or database writes."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.agent.reference_tools import ReferenceTools
from app.core.agent.buquanshu_search import BuquanshuSearcher
from app.core.database.term_reader import TermTableReader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, default=Path('app/core/crawler/forum_posts2129.txt'))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    reference = args.reference.resolve(strict=True)
    refs = ReferenceTools(max_pages=2)
    report = {'reference': str(reference)}
    report['load'] = refs.store.load_path(str(reference), (reference.parent,))
    reader = TermTableReader()
    terms = ['Fireball', 'Magic Missile', 'Arcana Domain', 'saving throw']
    report['raw_term_rows'] = {query: reader(query) for query in terms}
    with BuquanshuSearcher() as searcher:
        report['raw_search'] = searcher.search('Fireball', page_size=2)
    report['session_entities'] = refs.lookup_entities(['Battle Familiar', "Conjurer's Canopy"], limit_per_name=1)
    report['translation_candidates'] = refs.lookup_translations(['Fireball', 'Arcana Domain'])
    report['buquanshu_entity'] = refs.lookup_entities(['Fireball'], entity_type='spell', limit_per_name=1)
    report['audit'] = refs.audit
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
        print(json.dumps({'output': str(args.output), 'entities': report['load']['entities'],
                          'translations': report['load']['translations'],
                          'session_matches': len(report['session_entities']['results']),
                          'buquanshu_matches': len(report['buquanshu_entity']['results']),
                          'errors': report['translation_candidates']['errors'] + report['buquanshu_entity']['errors']}, ensure_ascii=False))
    else:
        print(text)


if __name__ == '__main__':
    main()
