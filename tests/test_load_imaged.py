# Copyright (c) MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import itk
import nibabel as nib
import numpy as np
import torch
from parameterized import parameterized

from monai.data import ITKReader
from monai.data.meta_obj import set_track_meta
from monai.data.meta_tensor import MetaTensor
from monai.transforms import Compose, EnsureChannelFirstD, FromMetaTensord, LoadImaged, SaveImageD
from monai.transforms.meta_utility.dictionary import ToMetaTensord
from tests.utils import assert_allclose

KEYS = ["image", "label", "extra"]

TEST_CASE_1 = [{"keys": KEYS}, (128, 128, 128)]

TEST_CASE_2 = [{"keys": KEYS, "reader": "ITKReader", "fallback_only": False}, (128, 128, 128)]

TESTS_META = []
for track_meta in (False, True):
    TESTS_META.append([{"keys": KEYS}, (128, 128, 128), track_meta])
    TESTS_META.append([{"keys": KEYS, "reader": "ITKReader", "fallback_only": False}, (128, 128, 128), track_meta])


class TestLoadImaged(unittest.TestCase):
    @parameterized.expand([TEST_CASE_1, TEST_CASE_2])
    def test_shape(self, input_param, expected_shape):
        test_image = nib.Nifti1Image(np.random.rand(128, 128, 128), np.eye(4))
        test_data = {}
        with tempfile.TemporaryDirectory() as tempdir:
            for key in KEYS:
                nib.save(test_image, os.path.join(tempdir, key + ".nii.gz"))
                test_data.update({key: os.path.join(tempdir, key + ".nii.gz")})
            result = LoadImaged(**input_param)(test_data)

        for key in KEYS:
            self.assertTupleEqual(result[key].shape, expected_shape)

    def test_register(self):
        spatial_size = (32, 64, 128)
        test_image = np.random.rand(*spatial_size)
        with tempfile.TemporaryDirectory() as tempdir:
            filename = os.path.join(tempdir, "test_image.nii.gz")
            itk_np_view = itk.image_view_from_array(test_image)
            itk.imwrite(itk_np_view, filename)

            loader = LoadImaged(keys="img")
            loader.register(ITKReader())
            result = loader({"img": Path(filename)})
            self.assertTupleEqual(result["img"].shape, spatial_size[::-1])

    def test_channel_dim(self):
        spatial_size = (32, 64, 3, 128)
        test_image = np.random.rand(*spatial_size)
        with tempfile.TemporaryDirectory() as tempdir:
            filename = os.path.join(tempdir, "test_image.nii.gz")
            nib.save(nib.Nifti1Image(test_image, affine=np.eye(4)), filename)

            loader = LoadImaged(keys="img")
            loader.register(ITKReader(channel_dim=2))
            t = Compose([EnsureChannelFirstD("img"), FromMetaTensord("img")])
            result = t(loader({"img": filename}))
            self.assertTupleEqual(result["img"].shape, (3, 32, 64, 128))

    def test_no_file(self):
        with self.assertRaises(RuntimeError):
            LoadImaged(keys="img")({"img": "unknown"})
        with self.assertRaises(RuntimeError):
            LoadImaged(keys="img", reader="nibabelreader")({"img": "unknown"})


class TestConsistency(unittest.TestCase):
    def _cmp(self, filename, ch_shape, reader_1, reader_2, outname, ext):
        data_dict = {"img": filename}
        keys = data_dict.keys()
        xforms = Compose([LoadImaged(keys, reader=reader_1, ensure_channel_first=True)])
        img_dict = xforms(data_dict)  # load dicom with itk
        self.assertTupleEqual(img_dict["img"].shape, ch_shape)

        with tempfile.TemporaryDirectory() as tempdir:
            save_xform = SaveImageD(keys, output_dir=tempdir, squeeze_end_dims=False, output_ext=ext)
            save_xform(img_dict)  # save to nifti

            new_xforms = Compose(
                [
                    LoadImaged(keys, reader=reader_2),
                    EnsureChannelFirstD(keys),
                    FromMetaTensord(keys),
                    ToMetaTensord(keys),
                ]
            )
            out = new_xforms({"img": os.path.join(tempdir, outname)})  # load nifti with itk
            self.assertTupleEqual(out["img"].shape, ch_shape)

            def is_identity(x):
                return (x == torch.eye(x.shape[0])).all()

            if not is_identity(img_dict["img"].affine) and not is_identity(out["img"].affine):
                assert_allclose(img_dict["img"].affine, out["img"].affine, rtol=1e-3)
            assert_allclose(out["img"], img_dict["img"], rtol=1e-3)

    def test_dicom(self):
        img_dir = "tests/testing_data/CT_DICOM"
        self._cmp(img_dir, (1, 16, 16, 4), "itkreader", "itkreader", "CT_DICOM/CT_DICOM_trans.nii.gz", ".nii.gz")
        output_name = "CT_DICOM/CT_DICOM_trans.nii.gz"
        self._cmp(img_dir, (1, 16, 16, 4), "nibabelreader", "itkreader", output_name, ".nii.gz")
        self._cmp(img_dir, (1, 16, 16, 4), "itkreader", "nibabelreader", output_name, ".nii.gz")

    def test_multi_dicom(self):
        """multichannel dicom reading, saving to nifti, then load with itk or nibabel"""

        img_dir = ["tests/testing_data/CT_DICOM", "tests/testing_data/CT_DICOM"]
        self._cmp(img_dir, (2, 16, 16, 4), "itkreader", "itkreader", "CT_DICOM/CT_DICOM_trans.nii.gz", ".nii.gz")
        output_name = "CT_DICOM/CT_DICOM_trans.nii.gz"
        self._cmp(img_dir, (2, 16, 16, 4), "nibabelreader", "itkreader", output_name, ".nii.gz")
        self._cmp(img_dir, (2, 16, 16, 4), "itkreader", "nibabelreader", output_name, ".nii.gz")

    def test_png(self):
        """png reading with itk, saving to nifti, then load with itk or nibabel or PIL"""

        test_image = np.random.randint(0, 256, size=(256, 224, 3)).astype("uint8")
        with tempfile.TemporaryDirectory() as tempdir:
            filename = os.path.join(tempdir, "test_image.png")
            itk_np_view = itk.image_view_from_array(test_image, is_vector=True)
            itk.imwrite(itk_np_view, filename)
            output_name = "test_image/test_image_trans.png"
            self._cmp(filename, (3, 224, 256), "itkreader", "itkreader", output_name, ".png")
            self._cmp(filename, (3, 224, 256), "itkreader", "PILReader", output_name, ".png")
            self._cmp(filename, (3, 224, 256), "itkreader", "nibabelreader", output_name, ".png")


class TestLoadImagedMeta(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super(__class__, cls).setUpClass()
        cls.tmpdir = tempfile.mkdtemp()
        test_image = nib.Nifti1Image(np.random.rand(128, 128, 128), np.eye(4))
        cls.test_data = {}
        for key in KEYS:
            nib.save(test_image, os.path.join(cls.tmpdir, key + ".nii.gz"))
            cls.test_data.update({key: os.path.join(cls.tmpdir, key + ".nii.gz")})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir)
        super(__class__, cls).tearDownClass()

    @parameterized.expand(TESTS_META)
    def test_correct(self, input_param, expected_shape, track_meta):
        set_track_meta(track_meta)
        result = LoadImaged(**input_param)(self.test_data)

        # shouldn't have any extra meta data keys
        self.assertEqual(len(result), len(KEYS))
        for key in KEYS:
            r = result[key]
            self.assertTupleEqual(r.shape, expected_shape)
            if track_meta:
                self.assertIsInstance(r, MetaTensor)
                self.assertTrue(hasattr(r, "affine"))
                self.assertIsInstance(r.affine, torch.Tensor)
            else:
                self.assertIsInstance(r, torch.Tensor)
                self.assertNotIsInstance(r, MetaTensor)
                self.assertFalse(hasattr(r, "affine"))


if __name__ == "__main__":
    unittest.main()
