"""Still-image preprocessing, matching, and simulated camera failure checks."""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from purifier.vision import still_identify as cli


class StillIdentifyTests(unittest.TestCase):
    def test_resize_largest_face_and_alignment_share_coordinates(self):
        small = np.zeros((480, 640, 3), dtype=np.uint8)
        large = np.zeros((1944, 2592, 3), dtype=np.uint8)
        faces = np.zeros((2, 15), dtype=np.float32)
        faces[0, 2:4] = [20, 20]
        faces[1, 2:4] = [100, 80]
        detector, recognizer = Mock(), Mock()
        detector.detect.return_value = (None, faces)
        recognizer.feature.return_value = np.array([[3, 4]], dtype=np.float32)
        with patch.object(cli.cv2.FaceDetectorYN, 'create', return_value=detector), \
                patch.object(cli.cv2.FaceRecognizerSF, 'create', return_value=recognizer):
            model = cli.FaceModel(Path('yunet'), Path('sface'))
        for image in (small, large):
            result = model.embedding(image)
            detected = detector.detect.call_args.args[0]
            aligned, selected = recognizer.alignCrop.call_args.args
            self.assertEqual(detected.shape, (480, 640, 3))
            self.assertIs(aligned, detected)
            np.testing.assert_array_equal(selected, faces[1])
            np.testing.assert_allclose(result, [.6, .8])
        detector.setInputSize.assert_called_with((640, 480))

    def test_no_face_diagnostics_and_mixed_burst_scoring(self):
        model = cli.FaceModel.__new__(cli.FaceModel)
        model.detector, model.recognizer = Mock(), Mock()
        model.detector.detect.return_value = (None, None)
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            vector = model.embedding(np.zeros((480, 640, 3), dtype=np.uint8),
                                     verbose=True, frame_number=2)
        self.assertIsNone(vector)
        self.assertIn('frame 2: 640x480 BGR, faces=0', output.getvalue())
        model.recognizer.feature.assert_not_called()
        # Best sample per user, then average across usable frames.
        samples = {'alice': [[1, 0], [0, 1]], 'bob': [[-1, 0]]}
        vectors = [v for v in (np.array([1., 0.]), vector, np.array([0., 1.]))
                   if v is not None]
        self.assertEqual(cli.identify(vectors, samples), ('alice', 1.0))
        self.assertEqual(cli.identify([], samples), ('no face detected', None))
        self.assertEqual(cli.identify(vectors, samples, 1.0)[0], 'unknown')
        self.assertEqual(cli.identify([np.array([-1., 0.])],
                                      {'alice': [[1, 0]]})[0], 'unknown')

    def test_default_threshold_is_strictly_above_point_six(self):
        samples = {'alice': [[1., 0.]]}
        self.assertEqual(cli.DEFAULT_THRESHOLD, .6)
        # Unit-length query vectors have cosine equal to the first component.
        for cosine, expected in ((.59, 'unknown'), (.6, 'unknown'), (.61, 'alice')):
            with self.subTest(cosine=cosine):
                query = np.array([cosine, np.sqrt(1 - cosine ** 2)])
                self.assertEqual(cli.identify([query], samples)[0], expected)

    def test_camera_format_count_and_failure_cleanup(self):
        for failure in (None, 'configure', 'start', 'capture_array', 'stop'):
            with self.subTest(failure=failure):
                camera = Mock()
                if failure:
                    getattr(camera, failure).side_effect = RuntimeError('camera fault')
                module = SimpleNamespace(Picamera2=Mock(return_value=camera))
                with patch.dict(sys.modules, {'picamera2': module}), \
                        patch.object(cli.time, 'sleep') as sleep:
                    if failure:
                        with self.assertRaisesRegex(RuntimeError, 'camera fault'):
                            cli.capture_burst(5, .4)
                    else:
                        self.assertEqual(len(cli.capture_burst(5, .4)), 5)
                        self.assertEqual(camera.capture_array.call_count, 5)
                        self.assertEqual(sleep.call_count, 5)
                camera.close.assert_called_once()
                camera.create_still_configuration.assert_called_once_with(
                    main={'size': (640, 480), 'format': 'RGB888'})

    def test_invalid_arguments_do_not_open_models_or_camera(self):
        base = ['recognize', '--store', 'unused', '--yunet', 'unused', '--sface', 'unused']
        for option in (['--count', '0'], ['--count', '11'], ['--delay', 'nan'],
                       ['--delay', '-1'], ['--threshold', 'inf'], ['--images']):
            with self.subTest(option=option), patch.object(cli, 'FaceModel') as model, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main(base + option)
                model.assert_not_called()

    def test_capture_failure_is_an_error_not_no_face(self):
        args = ['recognize', '--store', 'unused', '--yunet', 'unused', '--sface', 'unused']
        output = io.StringIO()
        with patch.object(cli, 'FaceModel'), \
                patch.object(cli, 'capture_burst', side_effect=RuntimeError('camera busy')), \
                contextlib.redirect_stderr(output):
            self.assertEqual(cli.main(args), 1)
        self.assertIn('camera busy', output.getvalue())
        self.assertNotIn('no face detected', output.getvalue())
