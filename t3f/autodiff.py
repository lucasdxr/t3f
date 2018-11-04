import tensorflow as tf

from t3f.tensor_train import TensorTrain
from t3f import shapes
from t3f import batch_ops
from t3f import decompositions
from t3f import riemannian


def _enforce_gauge_conditions(deltas, left):
  """Project deltas that define tangent space vec onto the gauge conditions."""
  proj_deltas = []
  for i in range(left.ndims()):
    if left.is_tt_matrix():
      r1, n, m, r2 = left.tt_cores[i].shape.as_list()
    else:
      r1, n, r2 = left.tt_cores[i].shape.as_list()
    q = tf.reshape(left.tt_cores[i], (-1, r2))
    if i < left.ndims() - 1:
      proj_delta = deltas[i]
      proj_delta = tf.reshape(proj_delta, (-1, r2))
      proj_delta -= tf.matmul(q, tf.matmul(tf.transpose(q), proj_delta))
      proj_delta = tf.reshape(proj_delta, left.tt_cores[i].shape)
    else:
      proj_delta = deltas[i]
    proj_deltas.append(proj_delta)
  return proj_deltas


def _is_invariant_to_input_transforms(f_value_1, f_value_2,
                                      name="check_autodiff_arguments"):
  """Returns an assert op that checks that the f_value_1 == f_value_2.

  Args:
    f_value_1: tf.Tensor, value of the function computed on x_1
    f_value_2: tf.Tensor, value of the function computed on x_2
    name: String, the name of the returned op
  
  Here we assume that as tensors x_1 == x_2, but their TT-cores are different,
  e.g. x_2 is a cores orthogonalization version of x_1.
  
  The function prints a warning about introducing overhead and returns an Assert
  op that checks that the two values are reasonably close to each other.

  Returns:
    tf.op, assertion operation.
  """
  print('Warning: debug mode of Riemannian autodiff is turned on which '
        'makes things a bit slower. It is advisable to keep debug=True untill '
        'actuall production usage, since debug mode does help to catch bugs.')
  rel_diff = tf.abs((f_value_1 - f_value_2) / f_value_1)
  err_msg = "The function passed to Riemannian autodiff returns different " \
            "values for two different versions of the same tensor. " \
            "The function values are"
  assert_op = tf.Assert(rel_diff < 1e-5, [err_msg, f_value_1, f_value_2],
                        name=name)
  return assert_op


def gradients(func, x, name='t3f_gradients', debug=True):
  """Riemannian autodiff: returns gradient projected on tangent space of TT.

  Computes projection of the gradient df/dx onto the tangent space of TT tensor
  at point x.

  Warning: this is experimental feature and it may not work for some function,
  e.g. ones that include QR or SVD decomposition (t3f.project, t3f.round) or
  for functions that work with TT-cores directly (in contrast to working with
  TT-object only via t3f functions). In this cases this function can silently
  return wrong results!

  Example:
      # Scalar product with some predefined tensor squared 0.5 * <x, t>**2.
      # It's gradient is <x, t> t and it's Riemannian gradient is
      #     t3f.project(<x, t> * t, x)
      f = lambda x: 0.5 * t3f.flat_inner(x, t)**2
      projected_grad = t3f.gradients(f, x) # t3f.project(t3f.flat_inner(x, t) * t, x)

  Args:
      func: function that takes TensorTrain object as input and outputs a number.
      x: point at which to compute the gradient and on which tangent space to
        project the gradient.
      name: string, name of the Op.
      debug: [True] whether to do a sanity check that the passed function is
        invariant to different TT representations (otherwise the Rieamnnian
        gradient doesn't even exist). It makes things slower, but helps catching
        bugs, so turn it off during production deployment.

  Returns:
      `TensorTrain`, projection of the gradient df/dx onto the tangent space at
      point x.

  See also:
      t3f.hessian_vector_product
  """
  with tf.name_scope(name, values=x.tt_cores):
    left = decompositions.orthogonalize_tt_cores(x)
    right = decompositions.orthogonalize_tt_cores(left, left_to_right=False)
    deltas = [right.tt_cores[0]]
    deltas += [tf.zeros_like(cc) for cc in right.tt_cores[1:]]
    x_projection = riemannian.deltas_to_tangent_space(deltas, x, left, right)
    function_value = func(x_projection)
    if debug:
      assert_op = _is_invariant_to_input_transforms(function_value, func(right))
    else:
      assert_op = None
    with tf.control_dependencies([assert_op]):
      cores_grad = tf.gradients(function_value, deltas)
    deltas = _enforce_gauge_conditions(cores_grad, left)
    return riemannian.deltas_to_tangent_space(deltas, x, left, right)


def hessian_vector_product(func, x, vector, name='t3f_hessian_vector_product',
                           debug=True):
  """P_x d^2f/dx^2 P_x vector, i.e. Riemannian hessian by vector product.

    Computes
      P_x d^2f/dx^2 P_x vector
    where P_x is projection onto the tangent space of TT at point x and
    d^2f/dx^2 is the Hessian of the function.

    Note that the true Riemannian hessian also includes the manifold curvature
    term which is ignored here.

    Warning: this is experimental feature and it may not work for some function,
    e.g. ones that include QR or SVD decomposition (t3f.project, t3f.round) or
    for functions that work with TT-cores directly (in contrast to working with
    TT-object only via t3f functions). In this cases this function can silently
    return wrong results!

    Example:
        # Quadratic form with matrix A: <x, A x>.
        # It's gradient is (A + A.T) x, it's Hessian is (A + A.T)
        # It's Riemannian Hessian by vector product is
        #     t3f.project(t3f.matmul(A + t3f.transpose(A), vector), x)
        f = lambda x: t3f.quadratic_form(A, x, x)
        res = t3f.hessian_vector_product(f, x, vector)

    Args:
        func: function that takes TensorTrain object as input and outputs a number.
        x: point at which to compute the Hessian and on which tangent space to
          project the gradient.
      vector: `TensorTrain` object which to multiply be the Hessian.
      name: string, name of the Op.
      debug: [True] whether to do a sanity check that the passed function is
        invariant to different TT representations (otherwise the Rieamnnian
        gradient doesn't even exist). It makes things slower, but helps catching
        bugs, so turn it off during production deployment.

    Returns:
        `TensorTrain`, projection of the gradient df/dx onto the tangent space at
        point x.

    See also:
        t3f.gradients
    """
  all_cores = list(x.tt_cores) + list(vector.tt_cores)
  with tf.name_scope(name, values=all_cores):
    left = decompositions.orthogonalize_tt_cores(x)
    right = decompositions.orthogonalize_tt_cores(left, left_to_right=False)
    deltas = [right.tt_cores[0]]
    deltas += [tf.zeros_like(cc) for cc in right.tt_cores[1:]]
    x_projection = riemannian.deltas_to_tangent_space(deltas, x, left, right)
    function_value = func(x_projection)
    if debug:
      assert_op = _is_invariant_to_input_transforms(function_value, func(right))
    else:
      assert_op = None
    with tf.control_dependencies([assert_op]):
      vector_projected = riemannian.project(vector, x)
    cores_grad = tf.gradients(function_value, deltas)
    vec_deltas = riemannian.tangent_space_to_deltas(vector_projected)
    products = [tf.reduce_sum(a * b) for a, b in zip(cores_grad, vec_deltas)]
    grad_times_vec = tf.add_n(products)
    second_cores_grad = tf.gradients(grad_times_vec, deltas)
    final_deltas = _enforce_gauge_conditions(second_cores_grad, left)
    return riemannian.deltas_to_tangent_space(final_deltas, x, left, right)


def _block_diag_hessian_vector_product(func, x, vector):
  # TODO:
  left = decompositions.orthogonalize_tt_cores(x)
  right = decompositions.orthogonalize_tt_cores(left, left_to_right=False)
  deltas = [right.tt_cores[0]] + [tf.zeros_like(cc) for cc in right.tt_cores[1:]]
  x_projection = riemannian.deltas_to_tangent_space(deltas, x, left, right)
  grad = _gradients(func, x, x_projection, left, right)
  vector_projected = riemannian.project(vector, x)
  vector_deltas = riemannian.tangent_space_to_deltas(vector_projected)
  grad_deltas = riemannian.tangent_space_to_deltas(grad)
  final_deltas = []
  for i in range(x.ndims()):
    h = tf.reduce_sum(grad_deltas[i] * vector_deltas[i])
    cores_grad = tf.gradients(h, x_projection.tt_cores[i])[0]
    if x.is_tt_matrix():
      r1, n, m, r2 = left.tt_cores[i].shape.as_list()
    else:
      r1, n, r2 = left.tt_cores[i].shape.as_list()
    q = tf.reshape(left.tt_cores[i], (-1, r2))
    if x.is_tt_matrix():
      if i == 0:
        curr_grad = cores_grad[:, :, :, :r2]
      elif i == w.ndims() - 1:
        curr_grad = cores_grad[r1:, :, :, :]
      else:
        curr_grad = cores_grad[r1:, :, :, :r2]
    else:
      if i == 0:
        curr_grad = cores_grad[:, :, :r2]
      elif i == x.ndims() - 1:
        curr_grad = cores_grad[r1:, :, :]
      else:
        curr_grad = cores_grad[r1:, :, :r2]
    if i < x.ndims() - 1:
      proj = tf.matmul(tf.eye(r1 * n) - q, tf.transpose(q))
      # TODO: multiply faster.
      delta = tf.matmul(proj, tf.reshape(curr_grad, (-1, r2)))
      delta = tf.reshape(delta, left.tt_cores[i].shape)
    else:
      delta = curr_grad
    final_deltas.append(delta)
  return riemannian.deltas_to_tangent_space(final_deltas, x, left, right)