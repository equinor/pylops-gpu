import torch
import numpy as np

from pytorch_complex_tensor import ComplexTensor
from pylops import LinearOperator as pLinearOperator
from pylops_gpu.utils.complex import conj
#from pylops_gpu.optimization.leastsquares import cg


class LinearOperator(pLinearOperator):
    """Common interface for performing matrix-vector products.

    This class is an overload of the
    :py:class:`pylops.LinearOperator` class. It adds
    functionalities for operators on GPUs; specifically, it allows users
    specifying when to move model and data from the host to the device and
    viceversa.

    Compared to its equivalent PyLops class :class:`pylops.LinearOperator`, it
    requires input model and data to be :obj:`torch.Tensor` objects.

    .. note:: End users of PyLops should not use this class directly but simply
      use operators that are already implemented. This class is meant for
      developers and it has to be used as the parent class of any new operator
      developed within PyLops-gpu. Find more details regarding
      implementation of new operators at :ref:`addingoperator`.

    Parameters
    ----------
    Op : :obj:`pylops.LinearOperator`
        Operator
    explicit : :obj:`bool`
        Operator contains a matrix that can be solved explicitly
        (``True``) or not (``False``)
    device : :obj:`str`, optional
        Device to be used
    togpu : :obj:`tuple`, optional
        Move model and data from cpu to gpu prior to applying ``matvec`` and
        ``rmatvec``, respectively (only when ``device='gpu'``)
    tocpu : :obj:`tuple`, optional
        Move data and model from gpu to cpu after applying ``matvec`` and
        ``rmatvec``, respectively (only when ``device='gpu'``)

    """
    def __init__(self, shape, dtype, Op=None, explicit=False, device='cpu',
                 togpu=(False, False), tocpu=(False, False)):
        super().__init__(Op=Op, explicit=explicit)
        self.shape = shape
        self.dtype = dtype
        if Op is None:
            self.Op = None
        self.device = device
        self.togpu = togpu
        self.tocpu = tocpu

    def matvec(self, x):
        # convert x to torch.Tensor
        if not isinstance(x, torch.Tensor):
            _tonumpy = True
            x = torch.from_numpy(x)
        else:
            _tonumpy = False
        # matvec, possibly moving x to gpu and y back to cpu
        if self.device != 'cpu' and self.togpu[0]:
            x = x.to(self.device)
        if self.Op is None:
            y = self._matvec(x)
        else:
            y = self.Op._matvec(x)
        if self.device != 'cpu' and self.tocpu[0]:
            y = y.to('cpu')
        # convert y to numpy when input was numpy
        if _tonumpy:
            y = y.numpy()
        return y

    def rmatvec(self, x):
        # convert x to torch.Tensor
        if not isinstance(x, torch.Tensor):
            _tonumpy = True
            x = torch.from_numpy(x)
        else:
            _tonumpy = False
        # rmatvec, possibly moving x to gpu and y back to cpu
        if self.device != 'cpu' and self.togpu[1]:
            x = x.to(self.device)
        if self.Op is None:
            y = self._rmatvec(x)
        else:
            y = self.Op._rmatvec(x)
        if self.device != 'cpu' and self.tocpu[1]:
            y = y.to('cpu')
        # convert y to numpy when input was numpy
        if _tonumpy:
            y = y.numpy()
        return y

    def __call__(self, x):
        return self * x

    def __mul__(self, x):
        return self.dot(x)

    def dot(self, x):
        """Matrix-vector multiplication.

        Parameters
        ----------
        x : array_like
            1-d or 2-d array, representing a vector or matrix.

        Returns
        -------
        Ax : array
            1-d or 2-d array (depending on the shape of x) that represents
            the result of applying this linear operator on x.

        """
        if isinstance(x, LinearOperator):
            return _ProductLinearOperator(self, x)
        elif np.isscalar(x):
            return _ScaledLinearOperator(self, x)
        else:
            ndim = x.ndimension()
            if isinstance(x, ComplexTensor):
                ndim -= 1
            if ndim == 1 or ndim == 2 and x.shape[1] == 1:
                return self.matvec(x)
            elif ndim == 2:
                return self.matmat(x)
            else:
                raise ValueError('expected 1-d or 2-d array or matrix, got %r'
                                 % x)

    def __matmul__(self, other):
        if np.isscalar(other):
            raise ValueError("Scalar operands are not allowed, "
                             "use '*' instead")
        return self.__mul__(other)

    def __rmatmul__(self, other):
        if np.isscalar(other):
            raise ValueError("Scalar operands are not allowed, "
                             "use '*' instead")
        return self.__rmul__(other)

    def __rmul__(self, x):
        if np.isscalar(x):
            return _ScaledLinearOperator(self, x)
        else:
            return NotImplemented

    def __pow__(self, p):
        if np.isscalar(p):
            return _PowerLinearOperator(self, p)
        else:
            return NotImplemented

    def __add__(self, x):
        if isinstance(x, LinearOperator):
            return _SumLinearOperator(self, x)
        else:
            return NotImplemented

    def __neg__(self):
        return _ScaledLinearOperator(self, -1)

    def __sub__(self, x):
        return self.__add__(-x)

    def __repr__(self):
        M, N = self.shape
        if self.dtype is None:
            dt = 'unspecified dtype'
        else:
            dt = 'dtype=' + str(self.dtype)

        return '<%dx%d %s with %s>' % (M, N, self.__class__.__name__, dt)

    def adjoint(self):
        """Hermitian adjoint.

        Returns the Hermitian adjoint. Can be abbreviated self.H instead
        of self.adjoint().

        """
        return self._adjoint()

    H = property(adjoint)

    def _adjoint(self):
        """Default implementation of _adjoint; defers to rmatvec."""
        shape = (self.shape[1], self.shape[0])
        print('shape', shape)
        return _CustomLinearOperator(shape, matvec=self.rmatvec,
                                     rmatvec=self.matvec,
                                     dtype=self.dtype, explicit=self.explicit,
                                     device=self.device, tocpu=self.tocpu,
                                     togpu=self.togpu)

    def div(self, y, niter=100, tol=1e-4):
        r"""Solve the linear problem :math:`\mathbf{y}=\mathbf{A}\mathbf{x}`.

        Overloading of operator ``/`` to improve expressivity of `Pylops_gpu`
        when solving inverse problems.

        Parameters
        ----------
        y : :obj:`torch.Tensor`
            Data
        niter : :obj:`int`, optional
            Number of iterations (to be used only when ``explicit=False``)
        tol : :obj:`int`
            Residual norm tolerance

        Returns
        -------
        xest : :obj:`np.ndarray`
            Estimated model

        """
        xest = self.__truediv__(y, niter=niter, tol=tol)
        return xest

    def __truediv__(self, y, niter=100, tol=1e-4):
        xest = None #cg(self, y, niter=niter, tol=tol)[0]
        return xest


class _CustomLinearOperator(LinearOperator):
    """Linear operator defined in terms of user-specified operations."""

    def __init__(self, shape, matvec, rmatvec=None, matmat=None, dtype=None,
                 explicit=None, device='cpu', togpu=(False, False),
                 tocpu=(False, False)):
        super(_CustomLinearOperator, self).__init__(shape=shape,
                                                    dtype=dtype, Op=None,
                                                    explicit=explicit,
                                                    device=device,
                                                    togpu=togpu,
                                                    tocpu=tocpu)

        self.args = ()

        self.__matvec_impl = matvec
        self.__rmatvec_impl = rmatvec
        self.__matmat_impl = matmat

        self._init_dtype()

    def _matmat(self, X):
        if self.__matmat_impl is not None:
            return self.__matmat_impl(X)
        else:
            return super(_CustomLinearOperator, self)._matmat(X)

    def _matvec(self, x):
        return self.__matvec_impl(x)

    def _rmatvec(self, x):
        func = self.__rmatvec_impl
        if func is None:
            raise NotImplementedError("rmatvec is not defined")
        return self.__rmatvec_impl(x)

    def _adjoint(self):
        return _CustomLinearOperator(shape=(self.shape[1], self.shape[0]),
                                     matvec=self.__rmatvec_impl,
                                     rmatvec=self.__matvec_impl,
                                     dtype=self.dtype)


class _SumLinearOperator(LinearOperator):
    def __init__(self, A, B):
        if not isinstance(A, pLinearOperator) or \
                not isinstance(B, LinearOperator):
            raise ValueError('both operands have to be a LinearOperator')
        if A.shape != B.shape:
            raise ValueError('cannot add %r and %r: shape mismatch'
                             % (A, B))
        self.args = (A, B)
        super(_SumLinearOperator, self).__init__(shape=A.shape,
                                                 dtype=A.dtype, Op=None,
                                                 explicit=A.explicit,
                                                 device=A.device,
                                                 togpu=A.togpu,
                                                 tocpu=A.tocpu)

    def _matvec(self, x):
        return self.args[0].matvec(x) + self.args[1].matvec(x)

    def _rmatvec(self, x):
        return self.args[0].rmatvec(x) + self.args[1].rmatvec(x)

    def _matmat(self, x):
        return self.args[0].matmat(x) + self.args[1].matmat(x)

    def _adjoint(self):
        A, B = self.args
        return A.H + B.H


class _ProductLinearOperator(LinearOperator):
    def __init__(self, A, B):
        if not isinstance(A, pLinearOperator) or \
                not isinstance(B, LinearOperator):
            raise ValueError('both operands have to be a LinearOperator')
        if A.shape[1] != B.shape[0]:
            raise ValueError('cannot multiply %r and %r: shape mismatch'
                             % (A, B))
        super(_ProductLinearOperator, self).__init__(shape=A.shape,
                                                     dtype=A.dtype, Op=None,
                                                     explicit=A.explicit,
                                                     device=A.device,
                                                     togpu=A.togpu,
                                                     tocpu=A.tocpu)
        self.args = (A, B)

    def _matvec(self, x):
        return self.args[0].matvec(self.args[1].matvec(x))

    def _rmatvec(self, x):
        return self.args[1].rmatvec(self.args[0].rmatvec(x))

    def _matmat(self, x):
        return self.args[0].matmat(self.args[1].matmat(x))

    def _adjoint(self):
        A, B = self.args
        return B.H * A.H


class _ScaledLinearOperator(LinearOperator):
    def __init__(self, A, alpha):
        if not isinstance(A, pLinearOperator):
            raise ValueError('LinearOperator expected as A')
        if not np.isscalar(alpha):
            raise ValueError('scalar expected as alpha')
        super(_ScaledLinearOperator, self).__init__(shape=A.shape,
                                                    dtype=A.dtype, Op=None,
                                                    explicit=A.explicit,
                                                    device=A.device,
                                                    togpu=A.togpu,
                                                    tocpu=A.tocpu)
        self.args = (A, alpha)

    def _matvec(self, x):
        return self.args[1] * self.args[0].matvec(x)

    def _rmatvec(self, x):
        return np.conj(self.args[1]) * self.args[0].rmatvec(x)

    def _matmat(self, x):
        return self.args[1] * self.args[0].matmat(x)

    def _adjoint(self):
        A, alpha = self.args
        return A.H * np.conj(alpha)


class _PowerLinearOperator(LinearOperator):
    def __init__(self, A, p):
        if not isinstance(A, pLinearOperator):
            raise ValueError('LinearOperator expected as A')
        print('A.shape', A.shape)
        if A.shape[0] != A.shape[1]:
            raise ValueError('square LinearOperator expected, got %r' % A)
        if not np.issubdtype(type(p), int) or p < 0:
            raise ValueError('non-negative integer expected as p')
        super(_PowerLinearOperator, self).__init__(shape=A.shape,
                                                   dtype=A.dtype, Op=None,
                                                   explicit=A.explicit,
                                                   device=A.device,
                                                   togpu=A.togpu,
                                                   tocpu=A.tocpu)
        self.args = (A, p)

    def _power(self, fun, x):
        res = x.clone()
        if isinstance(x, ComplexTensor):
            res = ComplexTensor(res)
        for i in range(self.args[1]):
            res = fun(res)
        return res

    def _matvec(self, x):
        return self._power(self.args[0].matvec, x)

    def _rmatvec(self, x):
        return self._power(self.args[0].rmatvec, x)

    def _matmat(self, x):
        return self._power(self.args[0].matmat, x)

    def _adjoint(self):
        A, p = self.args
        return A.H ** p


class MatrixMult(LinearOperator):
    r"""Matrix multiplication.

    Simple wrapper to :py:func:`torch.matmul` for
    an input matrix :math:`\mathbf{A}`.

    Parameters
    ----------
    A : :obj:`torch.Tensor` or :obj:`pytorch_complex_tensor.ComplexTensor`
        Matrix.
    dims : :obj:`tuple`, optional
        Number of samples for each other dimension of model
        (model/data will be reshaped and ``A`` applied multiple times
        to each column of the model/data).
    device : :obj:`str`, optional
        Device to be used
    togpu : :obj:`tuple`, optional
        Move model and data from cpu to gpu prior to applying ``matvec`` and
        ``rmatvec``, respectively (only when ``device='gpu'``)
    tocpu : :obj:`tuple`, optional
        Move data and model from gpu to cpu after applying ``matvec`` and
        ``rmatvec``, respectively (only when ``device='gpu'``)
    dtype : :obj:`torch.dtype`, optional
        Type of elements in input array.

    Attributes
    ----------
    shape : :obj:`tuple`
        Operator shape
    explicit : :obj:`bool`
        Operator contains a matrix that can be solved explicitly
        (``True``) or not (``False``)

    """
    def __init__(self, A, dims=None, device='cpu',
                 togpu=(False, False), tocpu=(False, False),
                 dtype=torch.float32):
        self.Op = None
        self.A = A
        if dims is None:
            self.reshape = False
            self.shape = A.shape
        else:
            if isinstance(dims, int):
                dims = (dims, )
            self.reshape = True
            self.dims = np.array(dims, dtype=np.int)
            self.shape = (A.shape[0]*np.prod(self.dims),
                          A.shape[1]*np.prod(self.dims))
        self.complex = True if isinstance(A, ComplexTensor) else False
        if self.complex:
            self.Ac = conj(A).t()
        self.explicit = True
        self.device = device
        self.togpu = togpu
        self.tocpu = tocpu
        self.dtype = dtype

    def _matvec(self, x):
        if self.reshape:
            x = torch.reshape(x, np.insert([np.prod(self.dims)], 0,
                                           self.A.shape[1]))
        if self.complex:
            y = self.A.mm(x.t()).t()
        else:
            y = self.A.matmul(x)
        if self.reshape:
            y = y.ravel()
        return y

    def _rmatvec(self, x):
        if self.reshape:
            x = np.reshape(x, np.insert([np.prod(self.dims)], 0,
                                        self.A.shape[0]))
        if self.complex:
            y = self.Ac.mm(x.t()).t()
        else:
            y = self.A.t().matmul(x)
        if self.reshape:
            y = y.ravel()
        return y

    def inv(self):
        r"""Return the inverse of :math:`\mathbf{A}`.

        Returns
        ----------
        Ainv : :obj:`torch.Tensor`
            Inverse matrix.

        """
        Ainv = torch.inverse(self.A)
        return Ainv


def aslinearoperator(A, device='cpu'):
    """Return A as a LinearOperator.

    ``A`` may be already a :class:`pylops_gpu.LinearOperator` or a
    :obj:`torch.Tensor`.

    """
    if isinstance(A, LinearOperator):
        return A
    else:
        return MatrixMult(A, device=device)