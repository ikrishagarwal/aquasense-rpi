import unittest

from purifier.vision.pipeline import CapturePermission, Pipeline


class PipelineTests(unittest.TestCase):
    def test_three_ephemeral_frames_one_inference_and_shutter(self):
        class Camera:
            count = 0
            closed = False
            def capture(self):
                self.count += 1
                return self.count
            def close(self):
                self.closed = True
        class Model:
            inferences = 0
            def quality(self, frame):
                return frame
            def embedding(self, frame):
                self.inferences += 1
                return [1, 0]
        camera, model = Camera(), Model()
        pipeline = Pipeline(camera, model, lambda: CapturePermission(True, True, True), .8, .1)
        self.assertEqual(pipeline.identify({'person': [[1, 0]]}).user_id, 'person')
        self.assertEqual(camera.count, 3)
        self.assertEqual(model.inferences, 1)
        self.assertTrue(camera.closed)
        camera = Camera()
        pipeline = Pipeline(camera, model, lambda: CapturePermission(True, True, False), .8, .1)
        self.assertEqual(pipeline.identify({}).status, 'privacy_blocked')
        self.assertEqual(camera.count, 0)

    def test_enrollment_shutter_closure_never_writes_partial_embeddings(self):
        from purifier.vision.enrollment import EnrollmentService
        class Camera:
            count = 0
            closed = False
            def capture(self):
                self.count += 1
                return self.count
            def close(self):
                self.closed = True
        class Model:
            def quality(self, frame):
                return 1.0
            def embedding(self, frame):
                return [1.0, 0.0]
        class Store:
            saved = False
            def enroll(self, user_id, vectors):
                self.saved = True
        from uuid import uuid4
        camera, store = Camera(), Store()
        service = EnrollmentService(store, camera, Model(), lambda: camera.count < 2)
        with self.assertRaises(PermissionError):
            service.enroll(str(uuid4()))
        self.assertFalse(store.saved)
        self.assertTrue(camera.closed)
