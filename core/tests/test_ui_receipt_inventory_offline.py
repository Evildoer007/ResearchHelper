"""A completed settings workflow must not disappear from the UI release gate."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
NODE='/Users/haoranxu/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'


class BrowserReceiptInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='researchhelper-ui-proof-')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)

    def proof(self,name,count,status='passed'):
        target=self.base/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps({'status':status,'checks':[f'check-{i}' for i in range(count)]}))

    def run_reader(self):
        reader=ROOT/'products/app/tests/ui_receipts.mjs'
        program=("import {pathToFileURL} from 'node:url';"
            "const {browserProofs,summarizeProofs}=await import(pathToFileURL(process.argv[1]));"
            "console.log(JSON.stringify(summarizeProofs(await browserProofs(process.argv[2]))));")
        return subprocess.run([NODE,'--input-type=module','-e',program,str(reader),str(self.base)],
            cwd=ROOT,capture_output=True,text=True,timeout=15)

    def test_settings_proof_is_counted_and_identical_pointers_are_not(self):
        self.proof('settings/runtime-settings.json',5)
        self.proof('login/per-run/receipt.json',52)
        self.proof('login/browser.json',52)
        # A runtime configuration is not an acceptance proof or something to log.
        self.proof('store/model-settings.json',99)
        before={str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        result=self.run_reader()
        self.assertEqual(result.returncode,0,result.stderr)
        record=json.loads(result.stdout)
        self.assertEqual(record['unique_context_count'],2)
        self.assertEqual(record['checks'],57)
        self.assertEqual(record['raw_receipt_check_sum'],109)
        self.assertEqual(len(record['contexts']),3)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.base.rglob('*') if p.is_file()})

    def test_failed_settings_proof_rejects_the_gate(self):
        self.proof('settings/runtime-settings.json',5,status='failed')
        self.assertNotEqual(self.run_reader().returncode,0)

    def test_structured_login_checks_are_read_without_rewriting_them(self):
        target=self.base/'receipt.json'
        target.write_text(json.dumps({'status':'passed','checks':[
            {'name':'keyboard focus restored','passed':True,'detail':{'font':13}}]}))
        before=target.read_bytes()
        result=self.run_reader()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['checks'],1)
        self.assertEqual(target.read_bytes(),before)

    def test_false_structured_check_cannot_borrow_a_passed_overall_status(self):
        (self.base/'receipt.json').write_text(json.dumps({'status':'passed',
            'checks':[{'name':'keyboard focus restored','passed':False}]}))
        self.assertNotEqual(self.run_reader().returncode,0)

    def test_viewport_outcomes_use_their_actual_status_contract(self):
        target=self.base/'receipt.json'
        target.write_text(json.dumps({'status':'passed','checks':[
            {'width':390,'height':844,'theme':'dark','kind':'field','status':'passed'}]}))
        result=self.run_reader()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['checks'],1)
        target.write_text(json.dumps({'status':'passed','checks':[
            {'width':390,'height':844,'theme':'dark','kind':'field','status':'failed'}]}))
        self.assertNotEqual(self.run_reader().returncode,0)

    def test_retained_assertion_receipts_keep_their_original_contract(self):
        target=self.base/'acceptance.json'
        target.write_text(json.dumps({'checks':['composer assertions completed'],'errors':[]}))
        result=self.run_reader()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['checks'],1)
        target.write_text(json.dumps({'checks':['composer assertions completed'],'errors':['page failed']}))
        self.assertNotEqual(self.run_reader().returncode,0)

    def test_missing_status_in_other_formats_is_not_accepted(self):
        (self.base/'receipt.json').write_text(json.dumps({'checks':['unknown assertion'],'errors':[]}))
        self.assertNotEqual(self.run_reader().returncode,0)
