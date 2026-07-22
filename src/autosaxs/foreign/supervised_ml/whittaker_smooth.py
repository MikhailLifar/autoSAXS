import numpy as np
import scipy.sparse as sparse
from scipy.sparse.linalg import splu


def sparse_eye_diff(N, d, format='csc'):
    assert d >= 0, "d must be >= 0"
    shape = (N - d, N)
    diagonals = np.zeros(2 * d + 1)
    diagonals[d] = 1.0
    for _ in range(d):
        diff = diagonals[:-1] - diagonals[1:]
        diagonals = diff
    offsets = np.arange(d + 1)
    sparse_diff = sparse.diags(diagonals, offsets, shape, format=format)
    return sparse_diff


def whittaker_smooth(y, lmbd, d=2):
    N = len(y)
    E = sparse.eye(N, format='csc')
    D = sparse_eye_diff(N, d, format='csc')
    coefmat = E + lmbd * D.conj().T.dot(D)
    z = splu(coefmat).solve(y)
    return z
