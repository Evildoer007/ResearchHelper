"""Isolated identity and rollback checks; no real App or native UI writes."""
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests import ROOT
sys.path.insert(0,str(ROOT/'maintenance'))
import install_from_verified_package as upgrade


class ObservedAppUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(dir=ROOT/'result/acceptance')
        self.addCleanup(self.temporary.cleanup);self.folder=Path(self.temporary.name)
        self.app=self.folder/'ResearchHelper.app';self.create(self.app,'old')

    def create(self,path,value):
        (path/'Contents').mkdir(parents=True)
        (path/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='io.researchhelper.app',
            CFBundleName='ResearchHelper',CFBundleExecutable='ResearchHelper',
            CFBundleShortVersionString='0.0.0',CFBundleVersion='0.0.0')))
        (path/'Contents/value').write_text(value)

    def observe(self):
        with patch.object(upgrade.native,'signature'):return upgrade.observe_bundle(self.app)

    def test_observed_bytes_are_not_fabricated_release_provenance(self):
        value=self.observe()
        self.assertFalse(value['release_origin_verified'])
        self.assertTrue(value['signature_verified'])
        self.assertEqual(value['bundle_identifier'],'io.researchhelper.app')
        self.assertEqual(set(value['files']),{'Contents/Info.plist','Contents/value'})

    def test_wrong_product_or_invalid_signature_is_rejected(self):
        with patch.object(upgrade.native,'signature',side_effect=RuntimeError('invalid_signature')):
            with self.assertRaisesRegex(RuntimeError,'invalid_signature'):upgrade.observe_bundle(self.app)
        (self.app/'Contents/Info.plist').write_bytes(plistlib.dumps(dict(CFBundleIdentifier='other.app')))
        with self.assertRaisesRegex(RuntimeError,'不是ResearchHelper'):self.observe()

    def test_changes_after_observation_are_not_overwritten(self):
        value=self.observe();(self.app/'Contents/value').write_text('user change')
        with patch.object(upgrade.native,'signature'):
            with self.assertRaisesRegex(RuntimeError,'不一致'):
                upgrade.require_observed(self.app,value['inventory_sha256'])
        self.assertEqual((self.app/'Contents/value').read_text(),'user change')

    def test_atomic_promotion_preserves_exact_observed_old_bundle(self):
        previous=self.observe();stage=self.folder/'stage.app';self.create(stage,'new')
        desired=dict(version='0.0.0',files=upgrade.native.bundle_files(stage));recovery=self.folder/'recovery.app'
        upgrade.native.promote(stage,self.app,desired,previous,recovery,lambda:None)
        self.assertEqual((self.app/'Contents/value').read_text(),'new')
        upgrade.native.verify_bundle(recovery,previous)
        self.assertFalse(stage.exists())

    def test_promotion_failure_restores_the_old_bundle_and_retains_stage(self):
        previous=self.observe();stage=self.folder/'stage.app';self.create(stage,'new')
        desired=dict(version='0.0.0',files=upgrade.native.bundle_files(stage));recovery=self.folder/'recovery.app'
        original=upgrade.os.replace
        def replace(source,target):
            if Path(source)==stage:raise OSError('injected promotion failure')
            return original(source,target)
        with patch.object(upgrade.os,'replace',side_effect=replace):
            with self.assertRaises(OSError):
                upgrade.native.promote(stage,self.app,desired,previous,recovery,lambda:None)
        upgrade.native.verify_bundle(self.app,previous)
        self.assertTrue(stage.exists());self.assertFalse(recovery.exists())

    def test_running_preflight_stops_before_recovery_or_replacement(self):
        previous=self.observe();stage=self.folder/'stage.app';self.create(stage,'new')
        desired=dict(version='0.0.0',files=upgrade.native.bundle_files(stage));recovery=self.folder/'recovery.app'
        def running():raise RuntimeError('running')
        with self.assertRaisesRegex(RuntimeError,'running'):
            upgrade.native.promote(stage,self.app,desired,previous,recovery,running)
        upgrade.native.verify_bundle(self.app,previous)
        self.assertFalse(recovery.exists())


if __name__=='__main__':unittest.main()
