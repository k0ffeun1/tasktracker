import importlib.util
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor


spec = importlib.util.spec_from_file_location('tracker', Path(__file__).parents[1] / 'server.py')
tracker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tracker)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        tracker.DB_PATH = Path(self.folder.name) / 'tasks.db'
        tracker.init_db()

    def tearDown(self):
        self.folder.cleanup()

    def test_checkbox_survives_reopening_database_and_initialization(self):
        task = tracker.create_task('Task', 'Aleksandr')
        tracker.update_task(task['id'], {'done': 1})
        tracker.init_db()
        saved = tracker.list_tasks()[0]
        self.assertEqual(saved['done'], 1)
        self.assertGreater(saved['completed_at'], 0)
        self.assertEqual(saved['assignee'], 'Aleksandr')

    def test_two_clients_update_different_fields_without_losing_either(self):
        task = tracker.create_task('Task', '')
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(tracker.update_task, task['id'], fields)
                       for fields in [{'done': 1}, {'assignee': 'Aza'}]]
            for future in futures:
                future.result()
        saved = tracker.list_tasks()[0]
        self.assertEqual(saved['done'], 1)
        self.assertEqual(saved['assignee'], 'Aza')


if __name__ == '__main__':
    unittest.main()
