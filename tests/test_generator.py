import sqlite3
import unittest

from generate_procurement import (
    DEFAULT_SEED,
    build_dataset,
    render_files,
    validate_dataset,
)


class ProcurementGeneratorTests(unittest.TestCase):
    def test_full_baseline_volumes_and_exception_coverage(self):
        dataset = build_dataset(DEFAULT_SEED, 1.0)
        summary = validate_dataset(dataset)
        self.assertEqual(len(dataset["tables"]["requesters"]), 300)
        self.assertEqual(len(dataset["tables"]["suppliers"]), 120)
        self.assertEqual(len(dataset["tables"]["purchase_requests"]), 1200)
        self.assertEqual(len(dataset["tables"]["rfqs"]), 500)
        self.assertEqual(len(dataset["tables"]["supplier_quotes"]), 1500)
        self.assertEqual(len(dataset["tables"]["purchase_orders"]), 900)
        self.assertEqual(summary["validation_status"], "passed")
        self.assertTrue({
            "po_issued_without_approval",
            "quote_expired_before_award",
            "selected_quote_not_cheapest_without_rationale",
            "inactive_supplier_awarded",
            "budget_overrun",
            "duplicate_request",
            "quote_currency_mismatch",
        }.issubset(summary["ground_truth_labels"]))
        for label in (
            "po_issued_without_approval",
            "quote_expired_before_award",
            "selected_quote_not_cheapest_without_rationale",
            "inactive_supplier_awarded",
            "budget_overrun",
            "duplicate_request",
            "quote_currency_mismatch",
        ):
            self.assertEqual(summary["ground_truth_labels"][label], 1)

    def test_starter_generation_is_deterministic(self):
        first = render_files(build_dataset(DEFAULT_SEED, 0.1))
        second = render_files(build_dataset(DEFAULT_SEED, 0.1))
        self.assertEqual(first, second)

    def test_operational_sql_loads_without_ground_truth(self):
        files = render_files(build_dataset(DEFAULT_SEED, 0.1))
        connection = sqlite3.connect(":memory:")
        connection.executescript(files["sql/schema.sql"].decode("utf-8"))
        connection.executescript(files["sql/seed.sql"].decode("utf-8"))
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0],
            90,
        )
        self.assertNotIn(b"ground_truth", files["sql/schema.sql"])
        self.assertNotIn(b"ground_truth", files["sql/seed.sql"])
        self.assertEqual(
            connection.execute("PRAGMA foreign_key_check").fetchall(),
            [],
        )
        connection.executescript(files["sql/ground_truth.sql"].decode("utf-8"))
        self.assertGreater(
            connection.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0],
            0,
        )
        connection.close()

    def test_invalid_scale_is_rejected(self):
        with self.assertRaises(ValueError):
            build_dataset(DEFAULT_SEED, 0)


if __name__ == "__main__":
    unittest.main()
