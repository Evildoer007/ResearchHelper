"""Display scales and real frozen content, never revised financial values."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import researchhelper.visual as visual
from researchhelper.layout import default_resource_root

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'result/acceptance/skill-host-financial-20261015-v2/result/researchhelper/rh_1cd38423decc4c6b9b46ea53ec82f05b/research-v1.json'


class FinancialChartDisplayTests(unittest.TestCase):
    def figure(self,unit='元',values=(90_703_260_964.48,),kind='comparison'):
        return dict(unit=unit,kind=kind,rows=[dict(value=value) for value in values])

    def test_yuan_display_uses_a_declared_yi_unit(self):
        result=visual.display_money_scale(self.figure())
        self.assertEqual(result,dict(unit='亿元',source_unit='元',divisor=100_000_000,monetary=True))

    def test_wanyuan_conversion_is_display_only(self):
        result=visual.display_money_scale(self.figure('万元',(20_000,)))
        self.assertEqual((result['unit'],result['divisor']),('亿元',10_000))

    def test_millionyuan_uses_the_corresponding_display_divisor(self):
        result=visual.display_money_scale(self.figure('百万元',(100,)))
        self.assertEqual((result['unit'],result['divisor']),('亿元',100))

    def test_negative_and_small_values_keep_their_sign_and_unit(self):
        negative=visual.display_money_scale(self.figure(values=(-90_703_260_964.48,1)))
        small=visual.display_money_scale(self.figure(values=(.000001,)))
        self.assertEqual(negative['unit'],'亿元')
        self.assertEqual((small['unit'],small['divisor']),('元',1))

    def test_zero_does_not_invent_a_different_unit(self):
        result=visual.display_money_scale(self.figure('百万元',(0,)))
        self.assertEqual((result['unit'],result['divisor']),('百万元',1))

    def test_fees_prices_and_observation_series_are_not_money_rebased(self):
        for figure in (self.figure('%/年',(.15,)),self.figure('元',(100_000,),kind='points'),
                       self.figure('元',(100_000,),kind='observations')):
            result=visual.display_money_scale(figure)
            self.assertFalse(result['monetary']);self.assertEqual(result['unit'],figure['unit'])
            self.assertEqual(result['divisor'],1)

    def test_prose_groups_only_explicit_money_tokens(self):
        text='600519.SH截至2026-06-30，营业收入90703260964.48元，利润率50.75%，差额−1234.5000万元。'
        expected='600519.SH截至2026-06-30，营业收入90,703,260,964.48元，利润率50.75%，差额−1,234.5000万元。'
        self.assertEqual(visual.display_prose_numbers(text),expected)
        self.assertEqual(visual.display_prose_numbers('已分组1,234.50元，编号600519，2026年。'),'已分组1,234.50元，编号600519，2026年。')

    def test_scale_choice_never_mutates_original_rows(self):
        original=self.figure(values=(-3_000_000_000,9_000_000_000))
        before=copy.deepcopy(original);visual.display_money_scale(original)
        self.assertEqual(original,before)

    def test_real_content_rerenders_without_changing_frozen_data(self):
        original=SOURCE.read_bytes();content=json.loads(original);before=copy.deepcopy(content)
        origin=getattr(visual,'__frozen_origin__',None)
        resources=Path(origin.split('!/PYZ.pyz/')[0]).parents[2] if origin else default_resource_root()
        html=visual.render(content,resources)
        self.assertEqual(content,before)
        self.assertEqual(SOURCE.read_bytes(),original)
        self.assertIn("argument-reading argument-comparisons",html)
        self.assertIn('90,703,260,964.48元',html)
        self.assertIn('图示单位亿元',html)
        self.assertIn('50.75%',html)
        if origin:
            directory=Path(tempfile.gettempdir()).resolve()
            self.assertTrue(directory.is_relative_to(ROOT/'result/acceptance'))
            (directory/'financial-chart-preview.html').write_text(html)
            (directory/'financial-chart-preview-source.sha256').write_text(hashlib.sha256(original).hexdigest())


if __name__=='__main__':unittest.main()
