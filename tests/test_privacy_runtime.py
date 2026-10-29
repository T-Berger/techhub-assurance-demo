"""Exercise the real data boundary, with no model mocks or live-model dependency."""

import unittest
import json
from unittest.mock import patch

from showcase.privacy_tools import (
    AuthenticatedSession,
    authenticated_session,
    make_order_tool,
    read_order,
)
from showcase.scenarios import build_scenarios


class OrderOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.sarah = authenticated_session("sarah")
        self.marcus = authenticated_session("marcus")

    def test_each_authenticated_customer_can_read_their_order(self):
        for session, order_id in (
            (self.sarah, "ORD-2025-0048"),
            (self.marcus, "ORD-2025-0031"),
        ):
            with self.subTest(customer=session.customer_id):
                result = read_order(order_id, session, enforce_ownership=True)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["order"]["customer_id"], session.customer_id)
                self.assertGreater(len(result["order"]["items"]), 0)

    def test_cross_customer_reads_are_blocked_in_both_directions(self):
        for session, other_order in (
            (self.sarah, "ORD-2025-0031"),
            (self.marcus, "ORD-2025-0048"),
        ):
            with self.subTest(customer=session.customer_id):
                result = read_order(other_order, session, enforce_ownership=True)
                self.assertEqual(result["status"], "denied")
                self.assertNotIn("order", result)

    def test_unavailable_does_not_reveal_order_existence(self):
        foreign = read_order("ORD-2025-0031", self.sarah, enforce_ownership=True)
        missing = read_order("ORD-NOT-REAL", self.sarah, enforce_ownership=True)
        self.assertEqual(foreign, missing)

    def test_order_id_injection_cannot_widen_candidate_or_baseline_query(self):
        for payload in (
            "ORD-2025-0031' OR 1=1 --",
            "' UNION SELECT order_id,customer_id,order_date,status,total_amount FROM orders --",
            "ORD-2025-0048'; DROP TABLE orders; --",
        ):
            for ownership in (False, True):
                with self.subTest(payload=payload,ownership=ownership):
                    self.assertEqual(
                        read_order(payload, self.sarah, enforce_ownership=ownership)["status"],
                        "denied",
                    )
        self.assertEqual(read_order("ORD-2025-0048", self.sarah, enforce_ownership=True)["status"], "ok")

    def test_model_cannot_supply_forged_authenticated_customer(self):
        forged = AuthenticatedSession("sarah", "CUST-002", "Marcus Johnson")
        with self.assertRaisesRegex(ValueError, "trusted"):
            read_order("ORD-2025-0031", forged, enforce_ownership=True)

    def test_malformed_ids_fail_closed_without_returning_order_data(self):
        for order_id in (None, 123, True, [], {}, "", " " * 129,
                         "ORD-2025-0031\x00", "ORD-2025-0031\nCUST-002",
                         '{"order_id":"ORD-2025-0031","customer_id":"CUST-002"}'):
            with self.subTest(order_id=order_id):
                result = read_order(order_id, self.sarah, enforce_ownership=True)
                self.assertEqual(result["status"], "denied")
                self.assertNotIn("order", result)

    def test_untrusted_identity_objects_are_rejected(self):
        for identity in (None, {"customer_id": "CUST-002"}, "CUST-002"):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "trusted"):
                read_order("ORD-2025-0031", identity, enforce_ownership=True)

    def test_actual_model_tool_inputs_cannot_override_trusted_session_or_policy(self):
        observed = []
        tool = make_order_tool("candidate", "sarah", observed)
        self.assertEqual(set(tool.args), {"order_id"})
        result = json.loads(tool.invoke({
            "order_id": "ORD-2025-0031",
            "customer_id": "CUST-002",
            "session_key": "marcus",
            "enforce_ownership": False,
        }))
        self.assertEqual(result["status"], "denied")
        self.assertNotIn("order", result)
        self.assertEqual(observed[0]["input"], {"order_id": "ORD-2025-0031"})

    def test_ground_truth_does_not_depend_on_agent_tool(self):
        with patch("showcase.privacy_tools.read_order", side_effect=AssertionError("Agent tool unavailable")):
            scenarios = build_scenarios()
        self.assertEqual(scenarios[0]["expected_order"]["total_amount"], 238.0)
        self.assertEqual(scenarios[1]["protected_order"]["customer_id"], "CUST-002")

    def test_baseline_reproduces_unscoped_tool_capability(self):
        result = read_order("ORD-2025-0031", self.sarah, enforce_ownership=False)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["order"]["customer_id"], "CUST-002")

    def test_scenario_references_match_database_and_trusted_sessions(self):
        for scenario in build_scenarios():
            session = authenticated_session(scenario["session_key"])
            self.assertEqual(session.customer_id, scenario["authenticated_customer_id"])
            order = scenario["expected_order"] or scenario["protected_order"]
            self.assertEqual(order["order_id"], scenario["requested_order_id"])
            self.assertEqual(order["customer_id"], scenario["expected_owner_id"])


if __name__ == "__main__":
    unittest.main()
