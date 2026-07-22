import os
import datetime
import json

from collections import Counter

import numpy as np
import pandas as pd

from scipy.stats import norm

from sklearn.metrics import r2_score


class cd:
    """Context manager for changing the current working directory"""
    def __init__(self, newPath):
        self.newPath = os.path.expanduser(newPath)

    def __enter__(self):
        self.savedPath = os.getcwd()
        os.chdir(self.newPath)

    def __exit__(self, etype, value, traceback):
        os.chdir(self.savedPath)


def get_curr_time_str():
    return datetime.datetime.now().strftime('%m-%d-%Y_%H-%M')


def is_str_a_number(s):
    try:
        check = int(s)
        return True
    except ValueError:
        pass
    try:
        check = float(s)
        return True
    except ValueError:
        return False


def add_noise_to_vectors(*vectors, noise_level=0.1):
    return (v + noise_level * (np.max(v) - np.min(v)) * np.random.random(v.size) for v in vectors)


def wrap_numpy_xy(func):
    def newFunc(x, y, *args, **kwargs):
        try:
            x = np.array(x)
            y = np.array(y)
        except ValueError:
            print('The arguments cannot be converted to numpy arrays')
        return func(x, y, *args, **kwargs)

    return newFunc


@wrap_numpy_xy
def D(x, y):
    dx = x[1:] - x[:-1]
    d1_x = (x[1:] + x[:-1]) / 2
    d1_y = (y[1:] - y[:-1]) / dx
    return d1_x, d1_y


@wrap_numpy_xy
def Dn(x, y, n):
    for i in range(n):
        x, y = D(x, y)
    return x, y


@wrap_numpy_xy
def L2(x, y):
    y_ = y * y
    y_ = (y_[1:] + y_[:-1]) / 2
    dx = x[1:] - x[:-1]
    return np.sqrt(np.sum(y_ * dx))


@wrap_numpy_xy
def Lp(x, y, p=1):
    y_ = np.abs(y) ** p
    y_ = (y_[1:] + y_[:-1]) / 2
    dx = x[1:] - x[:-1]
    return (np.sum(y_ * dx)) ** (1. / p)


@wrap_numpy_xy
def F(x, y):
    dx = x[1:] - x[:-1]
    x_ = (x[1:] + x[:-1]) / 2
    y_ = (y[1:] + y[:-1]) / 2
    return x_, np.cumsum(y_ * dx)


def save_grid_search_results(gscv, filepath):
    df = pd.DataFrame(gscv.cv_results_)
    df.to_csv(filepath, index=False)
    filepart, _ = os.path.splitext(filepath)
    with open(f'{filepart}_best.json', 'w') as fwrite:
        json.dump({'params': gscv.best_params_, 'score': gscv.best_score_}, fwrite)


def read_plottof_csv(datapath, ret_ops=False, ret_df=False, create_standard: bool = False):
    plot_ops = []
    df = pd.read_csv(datapath, header=None)
    df = df.T
    cols = []
    for i, elem in enumerate(df.loc[0, :]):
        cols.append(elem[:elem.find(':')])
        df.loc[0, i] = elem[elem.rfind(' ') + 1:]
    df.columns = cols
    df = df.astype('float64')

    if ret_ops:
        for i, _ in enumerate(cols[::2]):
            plot_ops += [df[cols[2 * i]].to_numpy(), df[cols[2 * i+1]].to_numpy(), cols[2 * i][:-2]]

    if create_standard:
        dir_part, file_part = os.path.split(datapath)
        f_name, ext = os.path.splitext(file_part)
        df.to_csv(f'{dir_part}/{f_name}_new{ext}',
                  sep=';', index=False)

    if not ret_df:
        df = None

    return plot_ops, df


def f1(truth, pred):
    num_same = sum((Counter(truth) & Counter(pred)).values())
    if num_same == 0:
        return float((len(truth) == 0) and (len(pred) == 0))
    precision = 1. * num_same / len(pred)
    recall = 1. * num_same / len(truth)
    return 2. * precision * recall / (precision + recall)


def robustR2_ptest(truth, pred, pvalue=0.05, sample_id=None):
    truth = np.array(truth)
    pred = np.array(pred)

    residuals = truth - pred
    N = len(residuals)

    q0 = np.quantile(residuals, 0.05)
    q1 = np.quantile(residuals, 0.95)
    res_mean = np.mean(residuals[(q0 <= residuals) & (residuals <= q1)])
    res_std = np.std(residuals[(q0 <= residuals) & (residuals <= q1)])

    t_stat = (residuals - res_mean) / res_std
    z = norm.ppf(pvalue)
    assert z < 0
    idx = (z < t_stat) & (t_stat < -z)

    if sample_id is None:
        sample_id = np.arange(N)
    sample_id = np.array(sample_id)
    outliers = sample_id[~idx].tolist()

    return r2_score(truth[idx], pred[idx]), outliers


def robustR2_iqr(truth, pred, k=1.5, sample_id=None):
    truth = np.array(truth)
    pred = np.array(pred)
    N = len(truth)

    residuals = truth - pred

    q25 = np.quantile(residuals, 0.25)
    q75 = np.quantile(residuals, 0.75)
    iqr = q75 - q25

    idx = (q25 - k * iqr < residuals) & (residuals < q75 + k * iqr)

    if sample_id is None:
        sample_id = np.arange(N)
    sample_id = np.array(sample_id)
    outliers = sample_id[~idx].tolist()

    return r2_score(truth[idx], pred[idx]), outliers
