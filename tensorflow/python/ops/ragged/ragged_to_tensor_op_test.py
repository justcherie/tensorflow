# Copyright 2018 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Tests for ragged.to_tensor."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import random
from absl.testing import parameterized
import numpy as np

from tensorflow.python.client import session
from tensorflow.python.framework import constant_op
from tensorflow.python.framework import dtypes

from tensorflow.python.framework import errors
from tensorflow.python.framework import ops
from tensorflow.python.framework import tensor_shape
from tensorflow.python.framework import test_util
from tensorflow.python.ops import array_ops
from tensorflow.python.ops.ragged import ragged_conversion_ops
from tensorflow.python.ops.ragged import ragged_factory_ops
from tensorflow.python.ops.ragged import ragged_tensor
from tensorflow.python.ops.ragged.ragged_tensor import RaggedTensor
from tensorflow.python.platform import benchmark
from tensorflow.python.platform import googletest
from tensorflow.python.util import nest


def make_placeholder(t):
  return array_ops.placeholder_with_default(t, None)


def rebuild_ragged_tensor_with_value_rowids(rt, feed_dict=None, sess=None):
  """Returns a copy of `rt`, built using `from_value_rowids`.

  This ensures that RaggedTensor._cached_value_rowids is populated, which
  triggers a different code-path for converting ragged tensors to tensors.

  If `feed_dict` and `sess` are specified, then build the new `RaggedTensor`
  using placeholder tensors, and populate a feed dictionary that can be used
  to feed the placeholders.

  Args:
    rt: The RaggedTensor to copy.
    feed_dict: If specified, then build the new `RaggedTensor` using
      placeholders, and populate this dict with entries to feed those
      placeholders.
    sess: A session used to evaluate tensors; required if feed_dict is
      specified.

  Returns:
    A copy of `rt`, built using `from_value_rowids`.
  """
  if isinstance(rt, ragged_tensor.RaggedTensor):
    values = rebuild_ragged_tensor_with_value_rowids(rt.values, feed_dict, sess)
    rowids = rt.value_rowids()
    nrows = rt.nrows()
    if feed_dict is not None:
      rowids_ph = make_placeholder(rowids)
      nrows_ph = make_placeholder(nrows)
      feed_dict[rowids_ph] = sess.run(rowids)
      feed_dict[nrows_ph] = sess.run(nrows)
      rowids, nrows = rowids_ph, nrows_ph
    return ragged_tensor.RaggedTensor.from_value_rowids(values, rowids, nrows)
  else:
    if feed_dict is not None:
      rt_ph = make_placeholder(rt)
      feed_dict[rt_ph] = sess.run(rt)
      rt = rt_ph
    return rt


@test_util.run_all_in_graph_and_eager_modes
class RaggedTensorToTensorOpTest(test_util.TensorFlowTestCase,
                                 parameterized.TestCase):

  def testDocStringExamples(self):
    """Example from ragged_to_tensor.__doc__."""
    rt = ragged_factory_ops.constant([[9, 8, 7], [], [6, 5], [4]])
    dt = rt.to_tensor()
    self.assertAllEqual(dt, [[9, 8, 7], [0, 0, 0], [6, 5, 0], [4, 0, 0]])

  @parameterized.parameters(
      {
          'rt_input': [],
          'ragged_rank': 1,
          'expected': [],
          'expected_shape': [0, 0],
      },
      {
          'rt_input': [[1, 2, 3], [], [4], [5, 6]],
          'expected': [[1, 2, 3], [0, 0, 0], [4, 0, 0], [5, 6, 0]]
      },
      {
          'rt_input': [[1, 2, 3], [], [4], [5, 6]],
          'default': 9,
          'expected': [[1, 2, 3], [9, 9, 9], [4, 9, 9], [5, 6, 9]]
      },
      {
          'rt_input': [[[1], [2], [3]], [], [[4]], [[5], [6]]],
          'ragged_rank':
              1,
          'default': [9],
          'expected': [[[1], [2], [3]], [[9], [9], [9]], [[4], [9], [9]],
                       [[5], [6], [9]]]
      },
      {
          'rt_input': [[[1, 2], [], [3, 4]], [], [[5]], [[6, 7], [8]]],
          'expected': [
              [[1, 2], [0, 0], [3, 4]],  #
              [[0, 0], [0, 0], [0, 0]],  #
              [[5, 0], [0, 0], [0, 0]],  #
              [[6, 7], [8, 0], [0, 0]],  #
          ]
      },
      {
          'rt_input': [[[1, 2], [], [3, 4]], [], [[5]], [[6, 7], [8]]],
          'default':
              9,
          'expected': [
              [[1, 2], [9, 9], [3, 4]],  #
              [[9, 9], [9, 9], [9, 9]],  #
              [[5, 9], [9, 9], [9, 9]],  #
              [[6, 7], [8, 9], [9, 9]],  #
          ]
      },
      {
          'rt_input': [[[1], [2], [3]]],
          'ragged_rank': 1,
          'default': 0,
          'expected': [[[1], [2], [3]]],
      },
      {
          'rt_input': [[[[1], [2]], [], [[3]]]],
          'default': 9,
          'expected': [[[[1], [2]], [[9], [9]], [[3], [9]]]],
      },
  )
  def testRaggedTensorToTensor(self,
                               rt_input,
                               expected,
                               ragged_rank=None,
                               default=None,
                               expected_shape=None):
    rt = ragged_factory_ops.constant(rt_input, ragged_rank=ragged_rank)
    dt = rt.to_tensor(default)
    self.assertIsInstance(dt, ops.Tensor)
    self.assertEqual(rt.dtype, dt.dtype)
    self.assertTrue(dt.shape.is_compatible_with(rt.shape))
    if expected_shape is not None:
      expected = np.ndarray(expected_shape, buffer=np.array(expected))
    self.assertAllEqual(dt, expected)

  @parameterized.parameters(
      {
          'rt_input': [[1, 2, 3]],
          'default': [0],
          'error': (ValueError, r'Shape \(1,\) must have rank at most 0'),
      },
      {
          'rt_input': [[[1, 2], [3, 4]], [[5, 6]]],
          'ragged_rank': 1,
          'default': [7, 8, 9],
          'error': (ValueError, r'Shapes \(3,\) and \(2,\) are incompatible'),
      },
      {
          'rt_input': [[1, 2, 3]],
          'default': 'a',
          'error': (TypeError, '.*'),
      },
  )
  def testError(self, rt_input, default, error, ragged_rank=None):
    rt = ragged_factory_ops.constant(rt_input, ragged_rank=ragged_rank)
    with self.assertRaisesRegexp(error[0], error[1]):
      rt.to_tensor(default)


# This covers the tests above, but with the new implementation.
@test_util.run_all_in_graph_and_eager_modes
class RaggedTensorToTensorOpNewTest(test_util.TensorFlowTestCase,
                                    parameterized.TestCase):

  def testDocStringExamples(self):
    """Example from ragged_to_tensor.__doc__."""
    rt = ragged_factory_ops.constant([[9, 8, 7], [], [6, 5], [4]])
    dt = ragged_conversion_ops.ragged_to_dense(rt)
    self.assertAllEqual(dt, [[9, 8, 7], [0, 0, 0], [6, 5, 0], [4, 0, 0]])

  @parameterized.parameters(
      {
          'rt_input': [],
          'ragged_rank': 1,
          'expected': [],
          'expected_shape': [0, 0],
      },
      {
          'rt_input': [[1, 2, 3], [], [4], [5, 6]],
          'expected': [[1, 2, 3], [0, 0, 0], [4, 0, 0], [5, 6, 0]]
      },
      {
          'rt_input': [[1, 2, 3], [], [4], [5, 6]],
          'default': 9,
          'expected': [[1, 2, 3], [9, 9, 9], [4, 9, 9], [5, 6, 9]]
      },
      {
          'rt_input': [[[1], [2], [3]], [], [[4]], [[5], [6]]],
          'ragged_rank':
              1,
          'default': [9],
          'expected': [[[1], [2], [3]], [[9], [9], [9]], [[4], [9], [9]],
                       [[5], [6], [9]]]
      },
      {
          'rt_input': [[[1, 2], [], [3, 4]], [], [[5]], [[6, 7], [8]]],
          'expected': [
              [[1, 2], [0, 0], [3, 4]],  #
              [[0, 0], [0, 0], [0, 0]],  #
              [[5, 0], [0, 0], [0, 0]],  #
              [[6, 7], [8, 0], [0, 0]],  #
          ]
      },
      {
          'rt_input': [[[1, 2], [], [3, 4]], [], [[5]], [[6, 7], [8]]],
          'default':
              9,
          'expected': [
              [[1, 2], [9, 9], [3, 4]],  #
              [[9, 9], [9, 9], [9, 9]],  #
              [[5, 9], [9, 9], [9, 9]],  #
              [[6, 7], [8, 9], [9, 9]],  #
          ]
      },
      {
          'rt_input': [[[1], [2], [3]]],
          'ragged_rank': 1,
          'default': 0,
          'expected': [[[1], [2], [3]]],
      },
      {
          'rt_input': [[[[1], [2]], [], [[3]]]],
          'default': 9,
          'expected': [[[[1], [2]], [[9], [9]], [[3], [9]]]],
      },
  )
  def testRaggedTensorToTensor(self,
                               rt_input,
                               expected,
                               ragged_rank=None,
                               default=None,
                               expected_shape=None):
    rt1 = ragged_factory_ops.constant(rt_input, ragged_rank=ragged_rank)
    dt1 = ragged_conversion_ops.ragged_to_dense(rt1, default_value=default)
    rt2 = rebuild_ragged_tensor_with_value_rowids(rt1)
    dt2 = ragged_conversion_ops.ragged_to_dense(rt2, default_value=default)

    for (rt, dt) in [(rt1, dt1), (rt2, dt2)]:
      self.assertIsInstance(dt, ops.Tensor)
      self.assertEqual(rt.dtype, dt.dtype)
      self.assertTrue(dt.shape.is_compatible_with(rt.shape))
      if expected_shape is not None:
        expected = np.ndarray(expected_shape, buffer=np.array(expected))
      self.assertAllEqual(dt, expected)

  @parameterized.parameters([
      {
          'rt_input': [[1, 2, 3]],
          'default': 'a',
          'error_type': TypeError,
          'error': r"Expected int32 passed to parameter 'default_value'|"
                   r"Cannot convert 'a' to EagerTensor of dtype int32",
      },
      {
          'rt_input': [[1, 2, 3]],
          'default': [0],
          'error': r'default_value\.shape=\[1\] and '
                   r'rt_input\.flat_values\.shape=\[3\] are incompatible: '
                   r'default_value\.rank = 1  must be less than '
                   r'rt_input\.flat_values\.rank = 1'
      },
      {
          'rt_input': [[[1, 2], [3, 4]], [[5, 6]]],
          'ragged_rank': 1,
          'default': [7, 8, 9],
          'error': r'default_value\.shape=\[3\] and '
                   r'rt_input\.flat_values\.shape=\[3,2\] are incompatible: '
                   r'default_value\.shape\[-1\] = 3 but '
                   r'rt_input\.flat_values\.shape\[-1\] = 2'
      },
      {
          'rt_input': [[1, 2, 3]],
          'shape': [3, 3, 3],
          'error': r'rt_input\.shape and shape=\[.,.,.\] are incompatible: '
                   r'rt_input\.rank = 2 but shape\.rank = 3'
      },
      {
          'rt_input': [[[1, 2, 3]]],
          'ragged_rank': 1,
          'shape': [1, 1, 4],
          'error': r'rt_input\.shape and shape=\[1,1,4\] are incompatible: '
                   r'rt_input\.shape\[2\] = 3 but shape\[2\] = 4'
      },
  ])
  def testError(self,
                rt_input,
                error,
                error_type=(ValueError, errors.InvalidArgumentError),
                default=None,
                ragged_rank=None,
                shape=None):

    rt = ragged_factory_ops.constant(rt_input, ragged_rank=ragged_rank)
    with self.assertRaisesRegexp(error_type, error):
      self.evaluate(
          ragged_conversion_ops.ragged_to_dense(
              rt, default_value=default, shape=shape))
    rt_placeholder = nest.map_structure(
        make_placeholder, rt, expand_composites=True)
    with self.assertRaisesRegexp(error_type, error):
      self.evaluate(
          ragged_conversion_ops.ragged_to_dense(
              rt_placeholder, default_value=default, shape=shape))


@test_util.run_all_in_graph_and_eager_modes
class RaggedToTensorOpAdditionalTests(test_util.TensorFlowTestCase):

  def _compare_to_reference(self,
                            ragged_tensor,
                            expected=None,
                            default_value=None):
    treatment = ragged_conversion_ops.ragged_to_dense(
        ragged_tensor, default_value=default_value)
    control = ragged_tensor.to_tensor(default_value=default_value)
    self.assertAllEqual(control, treatment)
    if expected is not None:
      self.assertAllEqual(expected, treatment)

  def test_already_dense_simple(self):
    """This studies a tensor initialized with value_rowids and nrows."""
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant([6, 7, 8, 9, 10, 11], dtype=dtypes.int64),
        value_rowids=constant_op.constant([0, 0, 0, 1, 1, 1],
                                          dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(input_data, [[6, 7, 8], [9, 10, 11]])

  def test_already_dense_with_dense_values_and_default(self):
    """This studies a tensor initialized with value_rowids and nrows."""
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant(
            [[6, 7], [8, 9], [10, 11], [12, 13], [14, 15], [16, 17]],
            dtype=dtypes.int64),
        value_rowids=constant_op.constant([0, 0, 0, 1, 1, 1],
                                          dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(
        input_data,
        [[[6, 7], [8, 9], [10, 11]], [[12, 13], [14, 15], [16, 17]]],
        default_value=constant_op.constant([31, 32], dtype=dtypes.int64))

  def test_already_dense_with_dense_values(self):
    """This studies a tensor initialized with value_rowids and nrows."""
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant(
            [[6, 7], [8, 9], [10, 11], [12, 13], [14, 15], [16, 17]],
            dtype=dtypes.int64),
        value_rowids=constant_op.constant([0, 0, 0, 1, 1, 1],
                                          dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(
        input_data,
        [[[6, 7], [8, 9], [10, 11]], [[12, 13], [14, 15], [16, 17]]])

  def test_ragged_with_dense_values_and_default(self):
    """This studies a tensor initialized with value_rowids and nrows."""
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant(
            [[6, 7], [8, 9], [10, 11], [12, 13], [14, 15]], dtype=dtypes.int64),
        value_rowids=constant_op.constant([0, 0, 0, 1, 1], dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(
        input_data, [[[6, 7], [8, 9], [10, 11]], [[12, 13], [14, 15], [2, 3]]],
        default_value=[2, 3])

  def test_ragged_with_dense_values_and_small_default(self):
    """This studies a tensor initialized with value_rowids and nrows."""
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant(
            [[6, 7], [8, 9], [10, 11], [12, 13], [14, 15]], dtype=dtypes.int64),
        value_rowids=constant_op.constant([0, 0, 0, 1, 1], dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(
        input_data, [[[6, 7], [8, 9], [10, 11]], [[12, 13], [14, 15], [2, 2]]],
        default_value=2)

  def test_already_dense_with_dense_values_string(self):
    """This studies a tensor initialized with value_rowids and nrows."""
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant(
            [[b'a', b'b'], [b'c', b'd'], [b'e', b'f'], [b'g', b'jalapeno'],
             [b'kangaroo', b'llama'], [b'manzana', b'nectar']],
            dtype=dtypes.string),
        value_rowids=constant_op.constant([0, 0, 0, 1, 1, 1],
                                          dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(input_data,
                               [[[b'a', b'b'], [b'c', b'd'], [b'e', b'f']],
                                [[b'g', b'jalapeno'], [b'kangaroo', b'llama'],
                                 [b'manzana', b'nectar']]])

  def test_already_dense_with_string(self):
    """This studies a tensor initialized with value_rowids and nrows."""
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant(
            ['a', 'b', 'c', 'd', 'e', 'antidisestablishmentarianism'],
            dtype=dtypes.string),
        value_rowids=constant_op.constant([0, 0, 0, 1, 1, 1],
                                          dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(
        input_data,
        [[b'a', b'b', b'c'], [b'd', b'e', b'antidisestablishmentarianism']])

  def test_already_dense(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2], [3, 4, 5]])
    self._compare_to_reference(input_data, [[0, 1, 2], [3, 4, 5]])

  def test_true_ragged(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2], [], [3]])
    self._compare_to_reference(input_data, [[0, 1, 2], [0, 0, 0], [3, 0, 0]])

  def test_true_ragged_default_3(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2], [], [3]])
    self._compare_to_reference(
        input_data, [[0, 1, 2], [3, 3, 3], [3, 3, 3]], default_value=3)

  def test_three_dimensional_ragged(self):
    input_data = ragged_factory_ops.constant([[[0, 1, 2], []], [], [[3]]])
    self._compare_to_reference(
        input_data, [[[0, 1, 2], [3, 3, 3]], [[3, 3, 3], [3, 3, 3]],
                     [[3, 3, 3], [3, 3, 3]]],
        default_value=3)

  def test_empty_tensor(self):
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant([], dtype=dtypes.int64),
        value_rowids=constant_op.constant([], dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(input_data, [[], []], default_value=3)

  def test_empty_last(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2], [], [3], []])
    self._compare_to_reference(input_data,
                               [[0, 1, 2], [0, 0, 0], [3, 0, 0], [0, 0, 0]])

  def test_shape_limit(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2, 3], [], [4], []])
    actual = ragged_conversion_ops.ragged_to_dense(input_data, shape=[2, 3])
    self.assertAllEqual(actual, [[0, 1, 2], [0, 0, 0]])
    self.assertEqual(actual.shape.as_list(), [2, 3])

  def test_shape_limit_tuple(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2, 3], [], [4], []])
    actual = ragged_conversion_ops.ragged_to_dense(input_data, shape=(2, 3))
    self.assertAllEqual(actual, [[0, 1, 2], [0, 0, 0]])
    self.assertEqual(actual.shape.as_list(), [2, 3])

  def test_shape_limit_tensor_shape(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2, 3], [], [4], []])
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, shape=tensor_shape.TensorShape([2, 3]))
    self.assertAllEqual(actual, [[0, 1, 2], [0, 0, 0]])
    self.assertEqual(actual.shape.as_list(), [2, 3])

  def test_shape_half_limit_tensor_shape(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2, 3], [], [4], []])
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, shape=tensor_shape.TensorShape([2, None]))
    self.assertAllEqual(actual, [[0, 1, 2, 3], [0, 0, 0, 0]])

  def test_skip_eager_shape_half_limit_tensor_shape(self):
    # Eager would produce a shape of [2, 4]
    input_data = ragged_factory_ops.constant([[0, 1, 2, 3], [], [4], []])
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, shape=tensor_shape.TensorShape([2, None]))
    result = actual.shape.as_list()
    # This is equal to [2, 4] in eager, or [2, None] in non-eager.
    self.assertEqual(result[0], 2)

  def test_shape_limit_shape_is_tensor_int64(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2, 3], [], [4], []])
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, shape=constant_op.constant([2, 3], dtype=dtypes.int64))
    self.assertAllEqual(actual, [[0, 1, 2], [0, 0, 0]])
    self.assertEqual(actual.shape.as_list(), [2, 3])

  def test_shape_limit_shape_is_tensor_int32(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2, 3], [], [4], []])
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, shape=constant_op.constant([2, 3], dtype=dtypes.int32))
    self.assertAllEqual(actual, [[0, 1, 2], [0, 0, 0]])
    self.assertEqual(actual.shape.as_list(), [2, 3])

  def test_shape_expand_first_dim(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2], [], [3]])
    actual = ragged_conversion_ops.ragged_to_dense(input_data, shape=[4, 4])
    self.assertAllEqual(
        actual, [[0, 1, 2, 0], [0, 0, 0, 0], [3, 0, 0, 0], [0, 0, 0, 0]])
    self.assertEqual(actual.shape.as_list(), [4, 4])

  def test_value_transposed(self):
    # This test tries to get a tensor in columnar format, where I am uncertain
    # as to whether the underlying op, which copies data in the raw format,
    # could fail.
    my_value = array_ops.transpose(
        constant_op.constant([[0, 1, 2, 3], [4, 5, 6, 7]]))
    input_data = RaggedTensor.from_value_rowids(
        values=my_value,
        value_rowids=constant_op.constant([0, 1, 2, 3], dtype=dtypes.int64),
        nrows=constant_op.constant(4, dtype=dtypes.int64),
        validate=True)
    self._compare_to_reference(input_data,
                               [[[0, 4]], [[1, 5]], [[2, 6]], [[3, 7]]])

  # This fails on the older version of to_tensor.
  def test_broadcast_default(self):
    # This test is commented out. The functionality here is not supported.
    # The dense dimension here is 2 x 2
    input_data = ragged_factory_ops.constant([[[[1, 2], [3, 4]]], []],
                                             ragged_rank=1)
    # This placeholder has a 2 x 1 dimension.
    default_value = make_placeholder([[5], [6]])
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, default_value=default_value)
    expected = [[[[1, 2], [3, 4]]], [[[5, 5], [6, 6]]]]
    self.assertAllEqual(actual, expected)

  # This fails on the older version of to_tensor.
  def test_broadcast_default_no_placeholder(self):
    # Again, this functionality is not supported. It fails more gracefully
    # when creating the op.
    input_data = ragged_factory_ops.constant([[[[1, 2], [3, 4]]], []],
                                             ragged_rank=1)
    # default_value has a 2 x 1 dimension.
    default_value = constant_op.constant([[5], [6]], shape=None)
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, default_value=default_value)
    expected = [[[[1, 2], [3, 4]]], [[[5, 5], [6, 6]]]]
    self.assertAllEqual(actual, expected)

  def test_shape_expand_second_dim(self):
    input_data = ragged_factory_ops.constant([[0, 1, 2], [], [3], []])
    actual = ragged_conversion_ops.ragged_to_dense(input_data, shape=[3, 4])
    self.assertAllEqual(actual, [[0, 1, 2, 0], [0, 0, 0, 0], [3, 0, 0, 0]])

  def test_empty_tensor_with_shape(self):
    input_data = RaggedTensor.from_value_rowids(
        values=constant_op.constant([], dtype=dtypes.int64),
        value_rowids=constant_op.constant([], dtype=dtypes.int64),
        nrows=constant_op.constant(2, dtype=dtypes.int64),
        validate=True)
    actual = ragged_conversion_ops.ragged_to_dense(
        input_data, default_value=3, shape=[2, 3])
    self.assertAllEqual(actual, [[3, 3, 3], [3, 3, 3]])


class RaggedToDenseBenchmark(googletest.Benchmark):

  # Configurations to test.  See `run_benchmark` for config param docs.
  CONFIGS = [
      {'shape': [10, 10]},
      {'shape': [10, 1000]},
      {'shape': [1000, 10]},
      {'shape': [1000, 10], 'fill': [1, 0.95]},  # Mostly full.
      {'shape': [1000, 10], 'fill': [1, 0.05]},  # Mostly empty.
      {'shape': [1000, 10], 'dtype': dtypes.string},
      {'shape': [1000, 10], 'dtype': dtypes.int64},
      {'shape': [100, 100]},
      {'shape': [50, 50, 32]},
      {'shape': [100, 100, 100], 'min_iters': 100},
      {'shape': [1000, 1000], 'min_iters': 100},
      {'shape': [10, 10, 10, 10, 10]},
      {'shape': [10, 10, 10, 10, 10], 'ragged_rank': 1},
      {'shape': [10, 10, 10, 10, 10], 'ragged_rank': 2},
      {'shape': [50, 50, 32], 'ragged_rank': 1, 'default_shape': [32]},
      {'shape': [200, 50, 32], 'ragged_rank': 1, 'default_shape': [32]}
  ]  # pyformat: disable

  def run_benchmark(self,
                    shape=(100, 100),
                    ragged_rank=None,
                    dtype=dtypes.float32,
                    fill=None,
                    default_shape=(),
                    output_shape=None,
                    min_iters=1000):
    """Run a benchmark with the specified configuraiton parameters.

    Args:
      shape: Bounding box for the input ragged tensor.
      ragged_rank: Ragged rank for the input ragged tensor.  Defauts to
        `len(shape)-1`.
      dtype: Data type for the input ragged tensor.
      fill: How full each dimension should be (0-1).  Corresponds 1:1 with
        `shape`.  Defaults to 0.8 for each dimension.
      default_shape: Shape for the default (padding) value.
      output_shape: Output shape -- ragged tensor will be padded or cropped to
        this shape.
      min_iters: Minimum iterations for benchmark.
    """
    if ragged_rank is None:
      ragged_rank = len(shape) - 1
    if fill is None:
      fill = [0.8 for _ in shape]

    # Build the inputs for the op.
    rt_input = self._generateRaggedTensor(shape, ragged_rank, dtype, fill)
    default_value = constant_op.constant(
        self._generateRaggedTensor(default_shape, 0, dtype), dtype=dtype)

    mbs = np.prod(shape) / (2**20)
    with session.Session(config=benchmark.benchmark_config()) as sess:
      extras = {
          'shape': shape,
          'ragged_rank': ragged_rank,
          'dtype': dtype,
          'fill': fill,
          'default_shape': default_shape
      }
      rt = ragged_factory_ops.constant(rt_input, dtype, ragged_rank=ragged_rank)

      # Inputs for with_splits:
      splits_rt_placeholder = ragged_factory_ops.placeholder(
          dtype, ragged_rank, shape[ragged_rank + 1:])
      splits_feed_dict = {splits_rt_placeholder: sess.run(rt)}

      # Inputs for with_rowids:
      rowids_feed_dict = {}
      rowids_rt_placeholder = rebuild_ragged_tensor_with_value_rowids(
          rt, rowids_feed_dict, sess)

      # Common arguments for benchmarks:
      run_op_benchmark_kwargs = dict(
          sess=sess,
          store_memory_usage=True,
          min_iters=min_iters,
          burn_iters=max(5, min_iters // 10),
          mbs=mbs,
          extras=extras)

      ragged_to_dense_with_splits = ragged_conversion_ops.ragged_to_dense(
          splits_rt_placeholder, default_value=default_value)
      self.run_op_benchmark(
          op_or_tensor=ragged_to_dense_with_splits.op,
          name='ragged_to_dense_with_splits',
          feed_dict=splits_feed_dict,
          **run_op_benchmark_kwargs)

      ragged_to_tensor_with_splits = splits_rt_placeholder.to_tensor(
          default_value=default_value)
      self.run_op_benchmark(
          op_or_tensor=ragged_to_tensor_with_splits.op,
          name='ragged_to_tensor_with_splits',
          feed_dict=splits_feed_dict,
          **run_op_benchmark_kwargs)

      ragged_to_dense_with_rowids = ragged_conversion_ops.ragged_to_dense(
          rowids_rt_placeholder, default_value=default_value)
      self.run_op_benchmark(
          op_or_tensor=ragged_to_dense_with_rowids.op,
          name='ragged_to_dense_with_rowids',
          feed_dict=rowids_feed_dict,
          **run_op_benchmark_kwargs)

      ragged_to_tensor_with_rowids = rowids_rt_placeholder.to_tensor(
          default_value=default_value)
      self.run_op_benchmark(
          op_or_tensor=ragged_to_tensor_with_rowids.op,
          name='ragged_to_tensor_with_rowids',
          feed_dict=rowids_feed_dict,
          **run_op_benchmark_kwargs)

  def _generateRaggedTensor(self, shape, ragged_rank, dtype, fill=None, axis=0):
    if axis == len(shape):
      value = random.random()
      if dtype == dtypes.string:
        value = str(value)
      if dtype.is_integer:
        value = int(value * 1000)
      return value
    if axis == 0 or axis > ragged_rank:
      slice_size = shape[axis]
    else:
      slice_size = (np.random.geometric(fill[axis], shape[axis]) == 1).sum()
    return [
        self._generateRaggedTensor(shape, ragged_rank, dtype, fill, axis + 1)
        for _ in range(slice_size)
    ]

  def benchmark_ragged_to_dense(self):
    random.seed(5)
    for config in self.CONFIGS:
      self.run_benchmark(**config)


if __name__ == '__main__':
  googletest.main()

