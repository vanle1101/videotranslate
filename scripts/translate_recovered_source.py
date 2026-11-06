"""Checkpoint native-language translations; preserve source audit unchanged."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    sys.stdout.reconfigure(encoding='utf8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    from core.multilingual_translation import translate_and_review
    from core.multilingual_translation import VERSION
    from core.engines.translation.opencode_client import OpenCodeZenClient
    from config import settings
    from scripts.publish_multilingual_recovery import srt_text
    source=json.loads(args.input.read_text(encoding='utf8'))
    rows=[{**row,'text_zh':row.get('text_zh',row.get('text',''))} for row in source['segments']]
    directory=args.output.parent/(args.output.stem+'_batches')
    directory.mkdir(parents=True,exist_ok=True)
    size=4
    groups=[rows[i:i+size] for i in range(0,len(rows),size)]
    completed={}
    def save():
        result={**source,'segments':[completed.get(row['id'],row) for row in rows],
                'translation_completed':len(completed),'translation_total':len(rows)}
        pending=args.output.with_suffix('.pending.json')
        pending.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        pending.replace(args.output)
        args.output.with_suffix('.vi.srt').write_text(srt_text(result['segments']),encoding='utf-8-sig')
    def run(index,group):
        offset=index*size
        context=rows[max(0,offset-8):offset+size+8]
        key=hashlib.sha256(json.dumps({'version':VERSION,'group':group,'context':context},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        checkpoint=directory/f'{index:03d}_{key[:16]}.json'
        if checkpoint.exists():
            return json.loads(checkpoint.read_text(encoding='utf8'))
        client=OpenCodeZenClient(model=settings.OPENCODE_MODEL,timeout=settings.OPENCODE_TIMEOUT)
        # Include nearby source on both sides; the caller's original evidence
        # and uncertainty remain attached to each returned copy.
        def bounded_translate(part):
            try:
                return translate_and_review(part,language='mixed',client=client,context=context,
                                            keep_draft_on_review_failure=True)
            except Exception as error:
                # Smaller independent ownership avoids losing all siblings to
                # one schema/timeout failure. Authentication is never retried.
                if len(part)>1 and type(error).__name__ in {'StructuredResponseError',
                        'OpenCodeTimeoutError','OpenCodeResponseValidationError'}:
                    middle=len(part)//2
                    return bounded_translate(part[:middle])+bounded_translate(part[middle:])
                raise
        result=bounded_translate(group)
        checkpoint.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        return result
    errors=[]
    with ThreadPoolExecutor(max_workers=3) as executor:
        pending={executor.submit(run,index,group):index for index,group in enumerate(groups)}
        for future in as_completed(pending):
            index=pending[future]
            try:
                batch=future.result()
                completed.update({row['id']:row for row in batch})
                print(f'BATCH {index+1}/{len(groups)} saved; {len(completed)}/{len(rows)} translated',flush=True)
            except Exception as error:
                errors.append(index)
                print(f'BATCH {index+1} pending: {type(error).__name__}',flush=True)
            save()
    if errors:
        raise SystemExit(f'Pending batches: {errors}; rerun reuses all saved batches')

if __name__=='__main__':
    main()
