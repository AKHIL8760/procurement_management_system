import json
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from app import (
    APIError,
    ProcurementHandler,
    add_quote,
    award_rfq,
    create_request,
    create_rfq,
    decide_request,
    initialize_database,
    receive_delivery,
    connect_database,
)


class ProcurementAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "procurement.sqlite3"
        initialize_database(self.db_path)
        self.connection = connect_database(self.db_path)
        self.department = self.connection.execute(
            "SELECT department_id FROM departments ORDER BY department_id LIMIT 1"
        ).fetchone()[0]
        self.requester = self.connection.execute(
            "SELECT requester_id FROM requesters WHERE department_id=? AND role='requester' LIMIT 1",
            (self.department,),
        ).fetchone()[0]
        self.budget = self.connection.execute(
            "SELECT budget_id FROM budgets WHERE department_id=?", (self.department,)
        ).fetchone()[0]
        self.suppliers = [
            row[0] for row in self.connection.execute(
                "SELECT supplier_id FROM suppliers WHERE status='active' ORDER BY supplier_id LIMIT 3"
            ).fetchall()
        ]
        self.today = date.today()

    def tearDown(self):
        self.connection.close()
        self.temp_dir.cleanup()

    def test_request_to_purchase_order_and_partial_delivery(self):
        result = create_request(self.connection, {
            "requester_id": self.requester,
            "department_id": self.department,
            "budget_id": self.budget,
            "description": "Integration test equipment",
            "category": "IT Hardware",
            "quantity": "6",
            "estimated_unit_cost": "1000",
            "required_by": (self.today + timedelta(days=40)).isoformat(),
            "business_justification": "Test the complete purchasing lifecycle",
        })
        request_id = result["request_id"]
        self.assertEqual(result["status"], "pending_approval")
        decide_request(self.connection, request_id, {
            "decision": "approved",
            "reason": "Approved for the integration test",
        })

        rfq = create_rfq(self.connection, {
            "request_id": request_id,
            "competition_type": "competitive",
            "close_date": (self.today + timedelta(days=7)).isoformat(),
        })
        rfq_id = rfq["rfq_id"]
        for index, supplier_id in enumerate(self.suppliers):
            add_quote(self.connection, rfq_id, {
                "supplier_id": supplier_id,
                "currency": "USD",
                "unit_price": str(900 + index * 50),
                "shipping_cost": "30",
                "tax_amount": "12",
                "lead_time_days": str(10 + index),
                "valid_until": (self.today + timedelta(days=20)).isoformat(),
            })

        with self.assertRaises(APIError):
            award_rfq(self.connection, rfq_id, {
                "allocations": [{"quote_id": "missing", "quantity": 6}],
            })

        quote_id = self.connection.execute(
            """SELECT quote_id FROM supplier_quotes WHERE rfq_id=?
               ORDER BY landed_total LIMIT 1""",
            (rfq_id,),
        ).fetchone()[0]
        award = award_rfq(self.connection, rfq_id, {
            "allocations": [{"quote_id": quote_id, "quantity": 6}],
        })
        po_id = award["purchase_order_ids"][0]
        self.assertEqual(self.connection.execute(
            "SELECT status FROM purchase_orders WHERE po_id=?", (po_id,)
        ).fetchone()[0], "in_transit")
        delivery_id = self.connection.execute(
            "SELECT delivery_id FROM deliveries WHERE po_id=?", (po_id,)
        ).fetchone()[0]

        partial = receive_delivery(self.connection, delivery_id, {"quantity": 2})
        self.assertEqual(partial["status"], "partially_received")
        self.assertEqual(partial["remaining"], 4)
        complete = receive_delivery(self.connection, delivery_id, {"quantity": 4})
        self.assertEqual(complete["status"], "delivered")
        self.assertEqual(complete["purchase_order_status"], "delivered")
        self.assertGreater(
            self.connection.execute(
                "SELECT COUNT(*) FROM delivery_events WHERE delivery_id=?", (delivery_id,)
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )

    def test_competitive_award_requires_three_quotes(self):
        request = create_request(self.connection, {
            "requester_id": self.requester,
            "department_id": self.department,
            "budget_id": self.budget,
            "description": "One quote validation item",
            "category": "Office Supplies",
            "quantity": "1",
            "estimated_unit_cost": "100",
            "required_by": (self.today + timedelta(days=40)).isoformat(),
            "business_justification": "Test minimum quote competition",
        })
        rfq = create_rfq(self.connection, {
            "request_id": request["request_id"],
            "competition_type": "competitive",
            "close_date": (self.today + timedelta(days=7)).isoformat(),
        })
        with self.assertRaisesRegex(APIError, "at least three"):
            award_rfq(self.connection, rfq["rfq_id"], {
                "allocations": [{"quote_id": "not-yet-submitted", "quantity": 1}],
            })

    def test_single_source_requires_documented_reason(self):
        request = create_request(self.connection, {
            "requester_id": self.requester,
            "department_id": self.department,
            "budget_id": self.budget,
            "description": "Single source reason validation",
            "category": "Software",
            "quantity": "1",
            "estimated_unit_cost": "100",
            "required_by": (self.today + timedelta(days=40)).isoformat(),
            "business_justification": "Test single-source guard",
        })
        with self.assertRaisesRegex(APIError, "Single-source justification"):
            create_rfq(self.connection, {
                "request_id": request["request_id"],
                "competition_type": "single_source",
                "single_source_reason": "",
                "close_date": (self.today + timedelta(days=7)).isoformat(),
            })
        rfq = create_rfq(self.connection, {
            "request_id": request["request_id"],
            "competition_type": "single_source",
            "single_source_reason": "Unique software compatibility requirement",
            "close_date": (self.today + timedelta(days=7)).isoformat(),
        })
        quote = add_quote(self.connection, rfq["rfq_id"], {
            "supplier_id": self.suppliers[0],
            "currency": "EUR",
            "unit_price": "100",
            "shipping_cost": "0",
            "tax_amount": "0",
            "lead_time_days": "50",
            "valid_until": (self.today + timedelta(days=20)).isoformat(),
        })
        self.assertEqual(quote["status"], "excluded_currency")
        quote_row = self.connection.execute(
            "SELECT exclusion_reason FROM supplier_quotes WHERE quote_id=?",
            (quote["quote_id"],),
        ).fetchone()
        self.assertIn("Lead time misses", quote_row["exclusion_reason"])
        with self.assertRaisesRegex(APIError, "accepts one supplier quote"):
            add_quote(self.connection, rfq["rfq_id"], {
                "supplier_id": self.suppliers[1],
                "currency": "USD",
                "unit_price": "100",
                "shipping_cost": "0",
                "tax_amount": "0",
                "lead_time_days": "10",
                "valid_until": (self.today + timedelta(days=20)).isoformat(),
            })

    def test_read_only_server_rejects_writes_and_reports_demo_mode(self):
        previous_db_path = getattr(ProcurementHandler, "db_path", None)
        previous_read_only = ProcurementHandler.read_only
        ProcurementHandler.db_path = self.db_path
        ProcurementHandler.read_only = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), ProcurementHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            client = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            client.request("GET", "/api/health")
            health_response = client.getresponse()
            self.assertEqual(health_response.status, 200)
            self.assertTrue(json.loads(health_response.read())["read_only"])

            client.request("POST", "/api/requests", body="{}", headers={
                "Content-Type": "application/json",
            })
            write_response = client.getresponse()
            self.assertEqual(write_response.status, 403)
            self.assertIn("read-only", json.loads(write_response.read())["error"])
            client.close()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
            ProcurementHandler.read_only = previous_read_only
            if previous_db_path is not None:
                ProcurementHandler.db_path = previous_db_path


if __name__ == "__main__":
    unittest.main()
