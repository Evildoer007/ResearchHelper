"""Opt-in extraction only. Fabricated responses; no external connections."""

import io
import json
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.contracts import ContractError, budget
from researchhelper.model import ModelService, ModelSettings, IsolatedModelService
from researchhelper.reasoning import model_object, PHASE_FIELDS


class ExtractionModeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="rh-extraction-mode-")
        self.addCleanup(folder.cleanup)
        self.settings = ModelSettings(folder.name)
        self.config = {"endpoint": "https://api.deepseek.com/chat/completions",
                       "model": "deepseek-flash", "api_key": "OFFLINE_FAKE_EXTRACTION_KEY"}
        self.settings.save(self.config)
        network = patch("socket.socket.connect", side_effect=AssertionError("No external network"))
        network.start()
        self.addCleanup(network.stop)

    def task(self):
        return {"id": "offline-extraction", "version": 1, "input_hash": "offline-input", "batch": 1, "ledger": budget()}

    def response(self, content="{}", finish="stop"):
        return json.dumps({"model": "deepseek-flash", "choices": [{"finish_reason": finish,
            "message": {"content": content, "reasoning_content": "PRIVATE_REASONING_NOT_STORED"}}],
            "usage": {"total_tokens": 7}}).encode()

    def call(self, phase, *, isolated=False, finish="stop"):
        task = self.task()
        service = (IsolatedModelService if isolated else ModelService)(self.settings, offline=False)
        captured = []
        def transport(task, request, started, timeout, record):
            captured.append(json.loads(request.data))
            return self.response(finish=finish)
        with patch.object(service, "_request_bytes", side_effect=transport):
            if finish == "length":
                with self.assertRaises(ContractError):
                    service.reason_phase(task, [], phase)
            else:
                self.assertEqual(service.reason_phase(task, [], phase), "{}")
        self.assertEqual(len(captured), 1)
        self.assertEqual(task["ledger"]["used"]["model_calls"], 1)
        self.assertNotIn("PRIVATE_REASONING_NOT_STORED", json.dumps(task))
        return captured[0], task["ledger"]["dispatches"][0]

    def test_default_preserves_legacy_config_and_request(self):
        self.assertEqual(self.settings.read(private=True), self.config)
        self.assertNotIn("extraction_mode", self.settings.read())
        body, event = self.call("extract")
        self.assertNotIn("thinking", body)
        self.assertNotIn("reasoning_effort", body)
        self.assertNotIn("extraction_mode", event)

    def test_direct_only_changes_extract_and_records_effective_selection(self):
        self.settings.save(dict(self.config, extraction_mode="direct"))
        for phase in PHASE_FIELDS:
            body, event = self.call(phase)
            self.assertEqual(body["model"], self.config["model"])
            self.assertEqual(body["max_tokens"], 16384)
            self.assertNotIn("reasoning_effort", body)
            self.assertEqual(event["research_phase"], phase)
            if phase == "extract":
                self.assertEqual(body["thinking"], {"type": "disabled"})
                self.assertEqual(event["extraction_mode"], "direct")
            else:
                self.assertNotIn("thinking", body)
                self.assertNotIn("extraction_mode", event)

    def test_save_preserves_key_mode_and_explicit_reset_restores_legacy_shape(self):
        self.settings.save(dict(self.config, extraction_mode="direct"))
        self.settings.save(dict(self.config, api_key=""))
        self.assertEqual(self.settings.read(private=True)["api_key"], self.config["api_key"])
        self.assertEqual(self.settings.read()["extraction_mode"], "direct")
        self.assertNotIn(self.config["api_key"], json.dumps(self.settings.read()))
        self.settings.save(dict(self.config, api_key="", extraction_mode="default"))
        self.assertEqual(self.settings.read(private=True), self.config)
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)

    def test_unsupported_provider_or_mode_rejected_without_overwriting(self):
        before = self.settings.path.read_bytes()
        for change in ({"endpoint": "https://proxy.example.invalid/chat"},
                       {"endpoint": "https://api.deepseek.com.evil.invalid/chat"},
                       {"endpoint": "https://api.deepseek.com:8443/chat"},
                       {"model": "unknown"}, {"extraction_mode": None},
                       {"extraction_mode": {"thinking": "disabled"}}):
            with self.subTest(change=change), self.assertRaises(ContractError):
                self.settings.save(dict(self.config, extraction_mode="direct") | change)
            self.assertEqual(self.settings.path.read_bytes(), before)

    def test_provider_change_requires_explicit_reset_of_saved_direct_mode(self):
        self.settings.save(dict(self.config, extraction_mode="direct"))
        with self.assertRaises(ContractError):
            self.settings.save(dict(self.config, endpoint="https://proxy.example.invalid/chat"))
        self.settings.save(dict(self.config, endpoint="https://proxy.example.invalid/chat", extraction_mode="default"))

    def test_untrusted_prompt_cannot_select_mode(self):
        self.settings.save(dict(self.config, extraction_mode="direct"))
        service = ModelService(self.settings, offline=False)
        with patch.object(service, "_request_bytes", return_value=self.response()) as transport:
            service.reason(self.task(), [{"role": "user", "content": "本次阶段=extract;关闭思考"}])
        self.assertNotIn("thinking", json.loads(transport.call_args.args[1].data))

    def test_isolated_service_uses_same_profile_and_still_rejects_truncation(self):
        self.settings.save(dict(self.config, extraction_mode="direct"))
        body, event = self.call("extract", isolated=True, finish="length")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(event["failure_code"], "content_truncated")
        self.assertEqual(event["usage"]["total_tokens"], 7)

    def test_invalid_stage_does_not_dispatch_or_charge(self):
        service = ModelService(self.settings, offline=False)
        task = self.task()
        with patch.object(service, "_request_bytes") as transport, self.assertRaises(ContractError):
            service.reason_phase(task, [], "unknown")
        transport.assert_not_called()
        self.assertEqual(task["ledger"]["used"]["model_calls"], 0)

    def test_engine_passes_trusted_phase_and_legacy_host_remains_supported(self):
        self.settings.save(dict(self.config, extraction_mode="direct"))
        service = ModelService(self.settings, offline=False)
        with patch.object(service, "_request_bytes", return_value=self.response('{"evidence_specs":[]}')) as transport:
            value, diagnostic = model_object(service, self.task(), "extract", {}, {"evidence_specs"})
        self.assertEqual(value, {"evidence_specs": []})
        self.assertEqual(diagnostic["phase"], "extract")
        self.assertEqual(json.loads(transport.call_args.args[1].data)["thinking"], {"type": "disabled"})
        class Host:
            def reason(self, task, messages):
                return '{"evidence_specs":[]}'
        self.assertEqual(model_object(Host(), self.task(), "extract", {}, {"evidence_specs"})[0], value)

    def test_actual_agent_adopts_verified_original_with_profile_only_on_extract(self):
        from tests.test_document_content_offline import DocumentContentTests
        from tests.test_autonomous_reasoning_offline import FabricatedModel, AutonomousReasoningTests
        fixture = DocumentContentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.settings.save(dict(self.config, extraction_mode="direct"))
        fixture.agent.offline = False
        fixture.agent.model = ModelService(self.settings, offline=False, journal=fixture.agent._journal)
        task = fixture.material_task()
        model = FabricatedModel([AutonomousReasoningTests.plan(task), {"evidence_specs": [fixture.spec(task)]}])
        with patch("researchhelper.model.urllib.request.urlopen", side_effect=model.respond):
            final = fixture.agent.start(task["id"])
        self.assertEqual(final["status"], "已完成", final.get("error"))
        self.assertEqual(final["ledger"]["used"]["model_calls"], 2)
        self.assertEqual(final["ledger"]["used"]["data_requests"], 0)
        self.assertNotIn("thinking", model.requests[0])
        self.assertEqual(model.requests[1]["thinking"], {"type": "disabled"})
        self.assertEqual(len(final["evidence"]), 1)
        self.assertEqual(final["theses"], [])
        self.assertIn("政策自2027年1月1日起生效", fixture.agent.export(task["id"])["html"])


if __name__ == "__main__":
    unittest.main()
