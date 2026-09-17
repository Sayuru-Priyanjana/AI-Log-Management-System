import unittest
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from pathlib import Path

import httpx

from app import InvestigationRequest, assemble_result, execute_tool, holmes_events, scope_filter, window_for


class HolmesAdapterTests(unittest.TestCase):
    def test_window_is_bounded(self):
        request = InvestigationRequest(system_id="cls-1", environment="prod", question="What happened?", duration="8d")
        with self.assertRaises(ValueError):
            window_for(request)

    def test_every_log_query_uses_selected_scope(self):
        request = InvestigationRequest(system_id="cls-1", environment="prod", question="What happened?")
        end = datetime.now(timezone.utc)
        filters = scope_filter(request, end - timedelta(hours=1), end, "api")
        self.assertIn({"term": {"system.id": "cls-1"}}, filters)
        self.assertIn({"term": {"environment": "prod"}}, filters)
        self.assertIn({"term": {"service.name": "api"}}, filters)
        self.assertTrue(any("@timestamp" in f.get("range", {}) for f in filters))

    def test_rollout_crashloop_without_exit_evidence_is_unconfirmed(self):
        request = InvestigationRequest(system_id="cls-1", environment="prod", question="Why did it crash?")
        end = datetime.now(timezone.utc)
        evidence = {"doc:events:1": {"message": "Deployment changed at 08:15"}}
        result = assemble_result(request, {"name": "demo"}, end - timedelta(hours=1), end,
            "Deployment caused the crashloop at 08:15. doc:events:1", evidence, [])
        self.assertEqual(result["answer"]["headline"], "Crashloop observed; container exit cause unconfirmed")
        self.assertLessEqual(result["answer"]["confidence"], 0.3)
        self.assertIsNone(result["answer"]["root_cause_service"])

    def test_uncited_cause_stays_unconfirmed(self):
        request = InvestigationRequest(system_id="cls-1", environment="prod", question="What caused it?")
        end = datetime.now(timezone.utc)
        result = assemble_result(request, {"name": "demo"}, end - timedelta(hours=1), end,
            "Database outage caused the errors.", {}, [])
        self.assertIn("unconfirmed", result["answer"]["headline"])
        self.assertEqual(result["plan"]["system_id"], "cls-1")
        self.assertEqual(result["engine"], "holmes")

    def test_result_matches_logintel_history_schema(self):
        request = InvestigationRequest(system_id="cls-1", environment="prod", question="What happened?")
        end = datetime.now(timezone.utc)
        result = assemble_result(request, {"name": "demo"}, end - timedelta(hours=1), end,
            "An error occurred. doc:logs:1", {"doc:logs:1": {
                "@timestamp": end.isoformat(), "log": {"message": "fatal startup error"}}}, [])
        root = Path(__file__).resolve().parent.parent
        check = subprocess.run([sys.executable, "-c",
            "import json,sys; from app.models.analysis import InvestigationResult; "
            "InvestigationResult.model_validate(json.load(sys.stdin))"],
            input=json.dumps(result), text=True, cwd=root / "langgraph-agent",
            capture_output=True, check=False)
        self.assertEqual(check.returncode, 0, check.stderr)


if __name__ == "__main__":
    unittest.main()


class HolmesToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_holmes_sse_final_answer_is_read(self):
        def handler(_request):
            return httpx.Response(200, text='event: ai_answer_end\ndata: {"analysis":"Done"}\n')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            events = [event async for event in holmes_events(client, {"ask": "test", "stream": True})]
        self.assertEqual(events, [("ai_answer_end", {"analysis": "Done"})])

    async def test_model_cannot_change_log_system_scope(self):
        seen = []

        def handler(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"hits": {"total": {"value": 0}, "hits": []}})

        request = InvestigationRequest(system_id="cls-1", environment="prod", question="Find errors")
        end = datetime.now(timezone.utc)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await execute_tool(client, "logintel_search_logs",
                {"system_id": "cls-2", "service": "api", "limit": 100000},
                request, end - timedelta(hours=1), end, ["api"])
        body = seen[0]
        filters = body["query"]["bool"]["filter"]
        self.assertIn({"term": {"system.id": "cls-1"}}, filters)
        self.assertNotIn({"term": {"system.id": "cls-2"}}, filters)
        self.assertEqual(body["size"], 50)

    async def test_promql_discards_other_system_results(self):
        def handler(request):
            if "logintel-promql-queries" in str(request.url):
                return httpx.Response(200, json={"_source": {
                    "expression": 'sum by (system_id) (rate(http_requests_total{system_id="{{system_id}}"}[5m]))'}})
            return httpx.Response(200, json={"status": "success", "data": {"result": [
                {"metric": {"system_id": "cls-2"}, "values": [[1, "10"]]}]}})

        request = InvestigationRequest(system_id="cls-1", environment="prod", question="Rate?")
        end = datetime.now(timezone.utc)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await execute_tool(client, "logintel_promql", {"query_id": "error_rate"},
                request, end - timedelta(hours=1), end, [])
        self.assertIn("selected system_id", result["error"])
