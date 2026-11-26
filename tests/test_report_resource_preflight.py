"""Broken delivery resources are detected before billable research dispatch.

Only source-module callbacks are synthetic. No model, provider or native window
is opened. Missing business data and a missing optional PDF worker stay local.
"""
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import Mock,patch

from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError,charge,fingerprint
from researchhelper.engine import ResearchAgent


class RecordingModel:
    def __init__(self):self.calls=[]
    def reason(self,task,messages,**options):
        self.calls.append(options['phase'])
        charge(task['ledger'],'model_calls',1,delivery=options.get('delivery',False))
        return '{"data_requests":[]}' if options['phase']=='draft_plan' else '{"title":"合成研究检查","conclusion":"无新增资料，不推定市场事实。"}'


class ReportResourcePreflightTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='report-resource-',dir=ROOT/'result/acceptance')
        self.addCleanup(folder.cleanup)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        guard.start();self.addCleanup(guard.stop)
        self.root=Path(folder.name);self.resources=self.root/'installation'
        web=self.resources/'app/web';web.mkdir(parents=True)
        for name in ('report.css','publication.css','draft-figures.js','visual-library.js','visual-catalog-data.js'):
            shutil.copyfile(ROOT/'products/app/frontend'/name,web/name)
        shutil.copytree(ROOT/'modules/visualrenderer/assets',web/'report-design')
        vendor=self.resources/'assets/vendor';vendor.mkdir(parents=True)
        shutil.copyfile(ROOT/'assets/vendor/echarts.min.js',vendor/'echarts.min.js')
        self.archive=ResearchArchive(self.root/'store');self.model=RecordingModel()
        self.agent=ResearchAgent(self.archive,self.resources,offline=False,model=self.model,
            source_credentials={},draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        self.agent.collector.external=Mock(side_effect=ContractError('合成来源无数据'))
        self.task=self.agent.create('仅以实际取得的资料研究600519.SH，生成报告',objects=['600519.SH'])

    def test_missing_html_asset_fails_before_any_model_or_data_dispatch(self):
        (self.resources/'app/web/report.css').unlink()
        done=self.agent.start(self.task['id'])
        self.assertEqual(done['status'],'失败')
        self.assertIn('报告组件',done.get('error') or '')
        self.assertEqual(self.model.calls,[])
        self.agent.collector.external.assert_not_called()
        self.assertEqual(done['ledger']['used']['data_requests'],0)
        self.assertEqual(done['ledger']['used']['model_calls'],0)

    def test_empty_unreadable_or_redirected_asset_does_not_dispatch(self):
        path=self.resources/'app/web/report.css'
        original=path.read_bytes()
        outside=self.root/'other.css';outside.write_bytes(original)
        for invalid in ('empty','encoding','symlink'):
            with self.subTest(invalid=invalid):
                path.unlink()
                if invalid=='empty':path.write_bytes(b'   ')
                elif invalid=='encoding':path.write_bytes(b'\xff\xfe')
                else:path.symlink_to(outside)
                try:
                    done=self.agent.start(self.task['id'])
                    self.assertEqual(done['status'],'失败')
                    self.assertIn('报告组件',done.get('error') or '')
                    self.assertEqual(self.model.calls,[])
                    self.agent.collector.external.assert_not_called()
                finally:
                    path.unlink();path.write_bytes(original)
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        self.assertEqual(done['ledger']['used']['data_requests'],0)

    def test_repaired_resources_resume_the_same_task_without_resetting_ledger(self):
        path=self.resources/'app/web/report.css';path.unlink()
        failed=self.agent.start(self.task['id'])
        self.assertEqual(failed['ledger']['used']['model_calls'],0)
        before=fingerprint(failed['original_input'])
        shutil.copyfile(ROOT/'products/app/frontend/report.css',path)
        done=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=dict(title='原资料观察',conclusion='保留条件，不补造数值。')))
        self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(done['version'],failed['version'])
        self.assertEqual(done['batch'],2)
        self.assertEqual(fingerprint(done['original_input']),before)
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        self.assertEqual(done['ledger']['used']['data_requests'],0)

    def test_data_only_host_checkpoint_does_not_require_report_resources(self):
        shutil.rmtree(self.resources/'app/web')
        done=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=None),defer_delivery=True)
        self.assertEqual(done['status'],'待开始',done.get('error'))
        self.assertTrue(done.get('host_checkpoint'))
        self.assertEqual(done['ledger']['used']['model_calls'],0)
        self.agent.collector.external.assert_not_called()

    def test_optional_pdf_worker_does_not_block_html_delivery(self):
        self.assertFalse((self.resources/'app/tools/ResearchHelperHTMLPDF').exists())
        done=self.agent.start(self.task['id'],dict(data_requests=[],draft_report=dict(title='HTML交付',conclusion='无新增市场判断。')))
        self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertIn('<html',self.archive.report(done['id'],1)['html'].lower())
        self.assertEqual(done['ledger']['used']['model_calls'],0)


if __name__=='__main__':unittest.main()
