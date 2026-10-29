"""A failed new comparison must never replace the last verified showcase."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from showcase import coordinator
from showcase import server


class PublishedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / ".showcase"
        self.run = self.state / "runs" / "new-attempt"
        self.run.mkdir(parents=True)
        self.latest = self.state / "latest.json"
        self.previous = {"run_dir": ".showcase/runs/previous-verified", "platform_status": "completed"}
        self.latest.write_text(json.dumps(self.previous))
        self.patches = [patch.object(coordinator, "ROOT", self.root), patch.object(coordinator, "STATE", self.state),
                        patch.object(coordinator, "STATUS", self.state / "status.json"), patch.object(coordinator, "LATEST", self.latest)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_failed_platform_handoff_keeps_the_previous_verified_comparison(self):
        with patch("showcase.platform.integrate_run", side_effect=RuntimeError("Worker unreachable")):
            with self.assertRaisesRegex(RuntimeError, "Worker unreachable"):
                coordinator.run_pipeline(run_dir=self.run)
        self.assertEqual(json.loads(self.latest.read_text()), self.previous)
        self.assertEqual(json.loads(coordinator.STATUS.read_text())["status"], "failed")

    def test_unfinished_native_run_is_not_published(self):
        with patch("showcase.platform.integrate_run", return_value={"status": "running"}):
            with self.assertRaises(RuntimeError):
                coordinator.run_pipeline(run_dir=self.run)
        self.assertEqual(json.loads(self.latest.read_text()), self.previous)

    def test_second_button_press_does_not_start_a_second_model_run(self):
        coordinator.save(coordinator.STATUS, {"status": "running", "pid": 42})
        with patch.object(coordinator, "alive", return_value=True), patch.object(coordinator.subprocess, "Popen") as spawn:
            with self.assertRaises(RuntimeError):
                coordinator.launch_run()
        spawn.assert_not_called()

    def test_revalidating_a_run_does_not_hide_its_published_evidence(self):
        published = self.state / "published" / "verified.json"
        coordinator.save(published, {"status": "completed", "client_scores_supplied": False})
        coordinator.save(self.run / "platform.json", {"status": "running"})
        coordinator.save(self.latest, {"run_dir": str(self.run.relative_to(self.root)),
                                      "platform_receipt": str(published.relative_to(self.root))})
        with patch.object(server, "ROOT", self.root), patch.object(server, "STATE", self.state), \
                patch.object(server, "LATEST", self.latest), patch.object(server, "STATUS", coordinator.STATUS):
            self.assertEqual(server.snapshot()["platform"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
