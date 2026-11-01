"""Exercise the new transport through both gates, calculation and saved report.

Synthetic close plus retained public NAV; authored proposals, not a model run.
"""
import json

try:
    from . import test_sse_nav_delivery_chain as chain
    from . import test_sse_etf_price_context as fixtures
except ImportError:
    import test_sse_nav_delivery_chain as chain
    import test_sse_etf_price_context as fixtures
from researchhelper.contracts import fingerprint
from researchhelper.flat_api_selection import POLICY
from researchhelper.output_schema import original_span_output_schema, structured_json_completion, ORIGINAL_SPAN_PROTOCOL
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import extractable_source_views


class FlatAPIDeliveryChainTests(chain.SSENavDeliveryChainTests):
    def setUp(self):
        super().setUp()
        self.task[POLICY] = 1
        self.task['api_need_compatibility_version'] = 2
        needs = ['同一估值日收盘价', '官方单位净值及所属日']
        self.task['questions'][0]['evidence_needs'] = needs
        self.task['questions'][0]['evidence_need_constraints'] = {text: dict(value_kind='number',
            unit_dimension='price', minimum_count=1, period_kind='point') for text in needs}
        self.task['input_hash'] = input_fingerprint(self.task)
        self.archive.save(self.task)

    def selections(self):
        views = extractable_source_views(self.task)
        schema = original_span_output_schema(self.task, views)
        wire = fixtures.literal(schema)
        wire.update(version=15, selections=[])
        for source, need in [(self.control.nav, 'n2'), (self.control.price, 'n1')]:
            ref = 'source_'+str(next(i for i, view in enumerate(views, 1) if view['id'] == source['id']))
            variants = [b for b in schema['properties']['selections']['items']['anyOf']
                        if b['properties']['source_ref']['enum'] == [ref]]
            branch = next((b for b in variants if b['properties'].get('additional_contexts', {})
                .get('items', {}).get('anyOf')), variants[0])
            item = fixtures.literal(branch)
            shape = branch['properties']['selection']['properties']['evidence_specs']['items']['anyOf'][0]
            choice = fixtures.literal(shape)
            choice['api_cell_selections'] = [shape['properties']['api_cell_selections']['items']['enum'][0]]
            choice['review'].update(need_links=[dict(question_id='q1', need_ids=[need])],
                allowed_natures=['已实现事实'], conditions=['仅离线同日原值比较，不批准历史或套利判断。'],
                reason='显式选择控制，不是模型自主研究。')
            item.pop('selection')
            item.update(choice)
            if source is self.control.price:
                item['additional_contexts'] = [fixtures.literal(
                    branch['properties']['additional_contexts']['items']['anyOf'][0])]
            wire['selections'].append(item)
        before = fingerprint(self.task)
        result = json.loads(structured_json_completion(json.dumps(wire), schema,
            protocol=ORIGINAL_SPAN_PROTOCOL, context=(self.task, views)))
        self.assertEqual(fingerprint(self.task), before)
        return result['evidence_specs']
