"""Verify paired denominators and scoring failures do not create false gains."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from backbone_report import generate_report, parse_review


class BackboneEvaluationTest(unittest.TestCase):
    def test_invalid_or_unsupported_positive_score_is_rejected(self):
        for check in ({'met': 1, 'reason': 'yes', 'evidence': 'a:1'},
                      {'met': True, 'reason': 'yes', 'evidence': ''}):
            with self.assertRaises(ValueError):
                parse_review(json.dumps({'checks':[check], 'issues':[]}), 1)
        review = parse_review('{"checks":[{"met":false,"reason":"遗漏","evidence":""}],"issues":[]}', 1)
        self.assertFalse(review['checks'][0]['met'])

    def test_failed_pairs_and_unreviewed_answers_are_not_zero_scores(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            protocol = {'cases':[{'id':'case','checks':['correct']}],
                        'arms':{'code_only':'','optional':''},
                        'jobs':[{'case':'case','repeat':repeat,'arm':arm} for repeat in (1,2,3) for arm in ('code_only','optional')]}
            runs = []
            for repeat in (1,2,3):
                for arm in ('code_only','optional'):
                    r = {'id':f'{repeat}-{arm}', 'questionId':'case', 'repeat':repeat, 'arm':arm,
                         'status':'failed' if repeat == 2 and arm == 'optional' else 'completed',
                         'elapsedSeconds':10 if repeat == 1 else 100, 'toolCalls':2, 'toolErrors':0}
                    if repeat == 1:
                        r['review'] = {'status':'completed','checks':[{'met':arm=='optional','reason':'source','evidence':'file:1'}],'issues':[]}
                    runs.append(r)
            (root/'protocol.json').write_text(json.dumps(protocol))
            (root/'results.json').write_text(json.dumps(runs))
            generate_report(root)
            summary = json.loads((root/'summary.json').read_text())
            self.assertEqual(summary['completeBlocks'], 2)
            self.assertEqual(summary['reviewedBlocks'], 1)
            self.assertEqual(summary['aggregates']['optional']['possible'], 1)
            self.assertEqual(summary['aggregates']['code_only']['meanSeconds'], 55)
            self.assertIsNone(summary['pairs'][1]['scoreDelta'])

    def test_positive_score_must_quote_candidate_not_just_source(self):
        raw = json.dumps({'checks':[{'met':True,'reason':'source has it',
            'evidence':'file:23','candidateQuote':'checks bankCardId'}],'issues':[]})
        with self.assertRaises(ValueError):
            parse_review(raw, 1, 'only checks contractId')
        self.assertTrue(parse_review(raw, 1, 'also checks bankCardId')['checks'][0]['met'])

    def test_manual_review_overrides_model_review(self):
        from backbone_report import reviewed
        run = {'id':'one','questionId':'case','review':{'status':'completed',
               'checks':[{'met':True,'reason':'claimed','evidence':'file:1'}],'issues':[]}}
        score = reviewed(run, {'case':{'checks':['correct']}}, {'one':{'checks':[0],'issues':['wrong']}})
        self.assertEqual(score, (0,1,1,'复核记录'))


if __name__ == '__main__':
    unittest.main()
