"""Extraction locking: one model is serialised, different models are not. No GLiNER needed."""
import threading
import time
import unittest

from resolve_pipeline.extract import extract


class FakeModel:
    def __init__(self):
        self.active = 0
        self.overlap = False
        self.guard = threading.Lock()

    def predict_entities(self, text, labels, threshold, flat_ner):
        with self.guard:
            self.active += 1
            self.overlap |= self.active > 1
        time.sleep(0.05)
        with self.guard:
            self.active -= 1
        return []


def run_concurrently(models):
    threads = [threading.Thread(target=extract, args=(m, "Apple", ["Organization"])) for m in models]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.monotonic() - t0


class ExtractLocking(unittest.TestCase):
    def test_one_model_is_serialised(self):
        model = FakeModel()
        run_concurrently([model] * 4)
        self.assertFalse(model.overlap)

    def test_different_models_run_in_parallel(self):
        self.assertLess(run_concurrently([FakeModel() for _ in range(4)]), 0.15)


if __name__ == "__main__":
    unittest.main()
