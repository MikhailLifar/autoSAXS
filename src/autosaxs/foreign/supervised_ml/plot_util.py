"""
Library development principles:
  * Modularity
    Each(?) plot function acts like a separate module which could be easily stacked
    with another function from the library (scatter + line, map + scatter, etc).
    This is achieved through each function accepting and returning ax or figax.
  * One plot - one function
    For each plot there should be a function in the library. Functions which create more than one plot are possible,
    but they should internally utilize one-plot functions from the library for each plot with full access
    to the arguments of these one-plot functions.
  * Different function types:
    ** Level 0. One-plot functions, which accept and return ax.
       Should be wrapped with @wrap_fig+@wrap_ax or @wrap_ax
    ** Level 1. Complex-plot functions, which accept figax and return, this is necessary
       if there should be more than one axes, for instance, when plotting colorbar.
       Should be wrapped with @wrap_fig
    ** Level 3. Specific functions: since due to wrappers there are plenty of arguments in each wrapped function,
       sometimes it makes sense to write a specific function which set's some of the parameters
       based on data which should be plotted.
       These functions could be put in a separate module.
       These functions should allow for modifications in default parameters.
    ** Wrapper functions. Wrapper functions absorb all the functionality, common for plots of different types.
       Wrapper functions: wrap_fig, wrap_ax
  * Direct access to all matplotlib/seaborn function arguments
    Achieved through propagating **kwargs from the highest to the lowest level (library) functions.
  * Each parameter is set only once in one function
    Achieved through accurately distributing functionality between Level 0 functions, Level 1 functions
    and wrapper functions.
  * Parameters saving
    Saving is a general operation required for all the plots. It is reasonable to do it
    in some general way applicable to all the plot types. This could be easily achieved by saving
    the parameters simultaneously with a plot and by saving all the parameters. This may look excessive,
    but that also allows for quick replot - since all the parameters are saved, we can just call
    the same plot function with them again. If we wanna alter something we can just read the parameters,
    alter what we want and call the function. The only problem is that this does not allow for full saving if
    there is several successive plot functions. This is not a large problem, though, since if some
    sequence of plot functions is used rel.-ly often, they are most likely wrapped with @wrap_fig.
  * Discarded - Data/Plot separation (?)
    No function saves its data. The functions from this library are only responsible for creating
    and saving the plots, data creation and saving is the responsibility of a user or functions of other modules.
    [Very questionable since it is not very convenient to care about data and plot saving separately]

  Useful information
  Standard figure widths:
  * minimal: 354px/85pt/30mm/1.18inches
  * single column: 1063px/255pt/90mm/3.54inches
  * 1.5: 1654px/397pt/140mm/5.51inches
  * full (two-column): 2244px/539pt/190mm/7.48inches
  Default matplotlib parameters:
  * figsize: 6.4x4.8
  * font: DejaVu Sans, 10

"""
import copy
import json
import os
import shutil
import itertools

import numpy as np
import pandas as pd
import matplotlib
import sklearn.metrics

import matplotlib.pyplot as plt
import matplotlib.pylab as pl
import seaborn as sns
import squarify

from matplotlib import ticker

from sklearn.metrics import ConfusionMatrixDisplay

from .util import *
# from pyfitit import plotting
#from pyfitit import descriptor


matplotlib.rcParams.update({
    'mathtext.default': 'regular',
})

DEFAULT_MPL_FONT = {'font': 'DejaVu Sans', 'size': 10}
DEFAULT_MPL_FIGSIZE = (6.4, 4.8)
DOUBLE_MPL_FIGSIZE = (12.8, 9.6)
DEFAULT_MPL_COLORS = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']

DEFAULT_FONTSIZE = 18


def setPlotDefaults():
    # matplotlib.use('Agg')
    plt.rc('figure', figsize=DOUBLE_MPL_FIGSIZE)
    plt.rc('font', size=2 * DEFAULT_FONTSIZE)  # controls default text sizes
    plt.rc('axes', titlesize=2 * DEFAULT_FONTSIZE)  # fontsize of the axes title
    plt.rc('axes', labelsize=2 * DEFAULT_FONTSIZE)  # fontsize of the x and y labels
    plt.rc('xtick', labelsize=int(2 * DEFAULT_FONTSIZE * 0.75))  # fontsize of the tick labels
    plt.rc('ytick', labelsize=int(2 * DEFAULT_FONTSIZE * 0.75))  # fontsize of the tick labels
    plt.rc('legend', fontsize=int(2 * DEFAULT_FONTSIZE * 0.5))  # legend fontsize
    plt.rc('figure', titlesize=2 * DEFAULT_FONTSIZE)  # fontsize of the figure title


def savefig(fig, destpath, **kwargs):
    """
    most common kwargs: dpi; bbox_inches(None or 'tight')
    """
    fig.savefig(destpath, **kwargs)
    plt.close(fig)


def setFonts(ax, fontsize=None, fontsizes:dict=None):
    if fontsize is not None:
        for item in ([ax.title, ax.xaxis.label, ax.yaxis.label] +
                     ax.get_xticklabels() + ax.get_yticklabels()):
            item.set_fontsize(fontsize)
        # Set legend font size if legend exists
        legend = ax.get_legend()
        if legend is not None:
            # A Legend object doesn't have set_fontsize, so we must
            # iterate through its text components.
            for text in legend.get_texts():
                text.set_fontsize(fontsize)
            # Also set the font size for the legend title
            legend.get_title().set_fontsize(fontsize)
    if fontsizes is not None:
        if 'title' in fontsizes:
            ax.title.set_fontsize(fontsizes['title'])
        if 'ax_labels' in fontsizes:
            ax.xaxis.label.set_fontsize(fontsizes['ax_labels'])
            ax.yaxis.label.set_fontsize(fontsizes['ax_labels'])
        if 'tick_labels' in fontsizes:
            for item in (ax.get_xticklabels() + ax.get_yticklabels()):
                item.set_fontsize(fontsizes['tick_labels'])
        if 'legend' in fontsizes:
            legend = ax.get_legend()
            if legend is not None:
                    # A Legend object doesn't have set_fontsize, so we must
                    # iterate through its text components.
                    for text in legend.get_texts():
                        text.set_fontsize(fontsizes['legend'])
                    # Also set the font size for the legend title
                    legend.get_title().set_fontsize(fontsizes['legend'])


# TODO very convenient, but could generate a lot of garbage
# TODO also there is a problem with tuple or list like objects (like xlim, colorbounds)
# TODO consider revision
# def wrap_save_arrlike(plotFunc):
#     def newFunc(*args,
#                 save_args=True, save_kwargs=True, savePath=None,
#                 exclude_args=None, exclude_kwargs=None,
#                 **kwargs):
#
#         if savePath is not None:
#             data = pd.DataFrame()
#             if save_args:
#                 for i, arg in enumerate(args):
#                     if i in exclude_args:
#                         continue
#                     if isinstance(arg, pd.Series):
#                         data[arg.name] = arg
#                         continue
#                     try:
#                         arg = np.array(arg)
#                         data[f'arg{i}'] = arg
#                     except ValueError:
#                         pass
#             if save_kwargs:
#                 for i, arg in enumerate(args):
#                     if i in exclude_args:
#                         continue
#                     if isinstance(arg, pd.Series):
#                         data[arg.name] = arg
#                         continue
#                     try:
#                         arg = np.array(arg)
#                         data[f'arg{i}'] = arg
#                     except ValueError:
#                         pass
#         return plotFunc(*args, **kwargs)
#
#     return newFunc


def saveParams(*args, plotFilePath, **kwargs):
    # TODO consider also the lighter and faster option - saving to pickle
    dirPath, fname = os.path.split(plotFilePath)
    fname, _ = os.path.splitext(fname)
    dataDir = f'{dirPath}/{fname}_data'
    if os.path.exists(dataDir):
        assert (os.path.expanduser('~') in dataDir
                or (dataDir[0] not in '/\\') and (dataDir[:2] not in ('C:', 'D:'))), 'Be careful with deletion'
        shutil.rmtree(dataDir)
    os.makedirs(dataDir, exist_ok=False)
    json_types = (bool, int, float, str, list, tuple, dict)

    argDict = dict()
    for i, arg in enumerate(args):
        # TODO still dangerous since iterables can contain non-jsons
        if arg is None or isinstance(arg, json_types):
            argDict[i] = arg
        elif isinstance(arg, (pd.Series, pd.DataFrame)):
            arg.to_csv(f'{dataDir}/{i}.csv', index=True, index_label='index__')
        elif isinstance(arg, np.ndarray):
            np.save(f'{dataDir}/{i}.npy', arg)

    for k, v in kwargs.items():
        if v is None or isinstance(v, json_types):
            argDict[k] = v
        elif isinstance(v, (pd.Series, pd.DataFrame)):
            v.to_csv(f'{dataDir}/{k}.csv', index=True, index_label='index__')
        elif isinstance(v, np.ndarray):
            np.save(f'{dataDir}/{k}.npy', v)

    with open(f'{dataDir}/args__.json', 'w') as fwrite:
        json.dump(argDict, fwrite)


def readParams(dataDir):
    argDict = dict()
    for fname in os.listdir(dataDir):
        fname, ext = os.path.splitext(fname)
        if ext == '.npy':
            arg = np.load(f'{dataDir}/{fname}{ext}')
        elif ext == '.csv':
            arg = pd.read_csv(f'{dataDir}/{fname}{ext}')
        elif fname == 'args__' and ext == '.json':
            with open(f'{dataDir}/{fname}{ext}', 'r') as fread:
                json_args = json.load(fread)
                for k, v in json_args.items():
                    if k.isdigit():
                        argDict[int(k)] = v
                    else:
                        argDict[k] = v
        if ext in ('.npy', '.csv'):
            if fname.isdigit():
                argDict[int(fname)] = arg
            else:
                argDict[fname] = arg

    if any(isinstance(k, int) for k in argDict):
        args = [None, ] * (np.max([k for k in argDict if isinstance(k, int)]) + 1)
    else:
        args = []
    kwargs = dict()
    for k in argDict:
        if isinstance(k, int):
            args[k] = argDict[k]
        else:
            kwargs[k] = argDict[k]

    return args, kwargs


def wrap_fig(plotFunc):
    def newFunc(*args,
                plotFilePath=None, figax=None,
                subplotsArgs=None, savefigArgs=None, save=True,
                **kwargs):
        if figax is None:
            if subplotsArgs is None:
                subplotsArgs = dict()
            fig, ax = plt.subplots(**subplotsArgs)
        else:
            fig, ax = figax

        plotFunc(*args, figax=(fig, ax), **kwargs)

        if plotFilePath is not None:
            dirPath, _ = os.path.split(plotFilePath)
            if dirPath:
                os.makedirs(dirPath, exist_ok=True)
            if savefigArgs is None:
                savefigArgs = dict()
            savefig(fig, plotFilePath, **savefigArgs)
            # ideal place to save params is where the figure is saved
            if save:
                saveParams(*args, plotFilePath=plotFilePath, **kwargs)

        return fig, ax

    return newFunc


def wrap_ax(plotFunc):
    def newFunc(*args,
                figax=None,
                xlim=None, ylim=None, xlabel=None, ylabel=None, title=None,
                xticks: dict = None, yticks: dict = None,
                fontsize=None, fontsizes=None, legend=False, legendArgs=None,
                **kwargs):
        fig, ax = figax
        plotFunc(*args, ax=ax, **kwargs)

        if xlim is not None:
            ax.set_xlim(*xlim)
        if ylim is not None:
            ax.set_ylim(*ylim)

        if xticks is not None:
            ax.set_xticks(**xticks)
        if yticks is not None:
            ax.set_yticks(**yticks)

        if xlabel is not None:
            ax.set_xlabel(xlabel)
        if ylabel is not None:
            ax.set_ylabel(ylabel)
        if title is not None:
            ax.set_title(title)

        if legend:
            if legendArgs is None:
                legendArgs = dict()
            ax.legend(**legendArgs)

        setFonts(ax, fontsize=fontsize, fontsizes=fontsizes)

        return fig, ax

    return newFunc


def color_arr_to_correct_rgb(colorParam, cmap, isColorDiscrete, vmin=None, vmax=None):
    colorParam = np.array(colorParam)
    if vmin is None:
        vmin = np.min(colorParam)
    if vmax is None:
        vmax = np.max(colorParam)
    norm = plt.Normalize(vmin, vmax)
    if cmap is None:
        cmap = 'plasma'
    if isinstance(cmap, str):
        if isColorDiscrete:
            cmap = matplotlib.cm.get_cmap(cmap, len(np.unique(colorParam)))
        else:
            cmap = matplotlib.cm.get_cmap(cmap)
    colorParam = [cmap(norm(val)) for val in colorParam]
    return colorParam, cmap


@wrap_fig
@wrap_ax
def basic_plot(x, y, ax, **kwargs):
    ax.plot(x, y, **kwargs)


@wrap_fig
@wrap_ax
def basic_scatter(x, y, ax, **kwargs):
    ax.scatter(x, y, **kwargs)


@wrap_fig
@wrap_ax
def basic_contourf(xx, yy, color, ax, **kwargs):
    ax.contourf(xx, yy, color, **kwargs)


@wrap_fig
@wrap_ax
def basic_map(data, ax, **kwargs):
    sns.heatmap(data, ax=ax, **kwargs)


@wrap_fig
@wrap_ax
def basic_treemap(sizes, label, color, ax, cmap, isColorDiscrete, **kwargs):
    color = color_arr_to_correct_rgb(color, cmap, isColorDiscrete)
    squarify.plot(sizes, label=label, color=color, ax=ax, **kwargs)


@wrap_fig
@wrap_ax
def basic_hist(x, ax, **kwargs):
    ax.hist(x, **kwargs)


@wrap_fig
@wrap_ax
def basic_imshow(img, ax, **kwargs):
    ax.imshow(img, **kwargs)


@wrap_fig
@wrap_ax
def basic_bar(x, y, ax, annot=None, color_label=None, color=None,
              annot_height_threshold=None,
              text_args=None, segment_text_args=None, total_text_args=None,
              **kwargs):
    if annot and text_args is None:
        text_args = dict()

    # 2d y case
    if isinstance(y, np.ndarray) and len(y.shape) == 2:
        assert color_label is not None
        if color is None:
            color = [None, ] * len(y)

        totals = np.zeros_like(x)
        bar_returns = []
        for row, c, l in zip(y, color, color_label):
            bar_returns.append(ax.bar(x, row, bottom=totals, label=l, color=c))
            totals += row

        if annot:
            if segment_text_args is None:
                segment_text_args = dict()
            if total_text_args is None:
                total_text_args = dict()
            if annot_height_threshold is None:
                annot_height_threshold = -np.inf

            segment_text_args_1 = dict(ha='center', va='center')
            segment_text_args_1.update(segment_text_args)

            total_text_args_1 = dict(ha='center', va='bottom', fontweight='bold')
            total_text_args_1.update(total_text_args)

            for i, bars in enumerate(bar_returns):
                for bar, total in zip(bars, totals):
                    height = bar.get_height()
                    bottom = bar.get_y()
                    if height > annot_height_threshold:
                        ax.text(bar.get_x() + bar.get_width() / 2.,
                                bottom + height / 2,
                                f'{height}',
                                **segment_text_args_1)
                    if i == len(bar_returns) - 1:
                        ax.text(bar.get_x() + bar.get_width() / 2.,
                                total + 0.5, f'{total}', **total_text_args_1)

        return

    # 1d y case
    bars = ax.bar(x, y, **kwargs)
    if annot:
        text_args_1 = dict(ha='center', va='bottom', )
        text_args_1.update(text_args)
        for bar in bars:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f'{bar.get_height():.2f}', **text_args_1)


@wrap_fig
@wrap_ax
def basic_barplot(data, ax, annot=None, **kwargs):
    # color='#FFD700'
    ax = sns.barplot(data, ax=ax, **kwargs)
    # if annot is not None and isinstance(annot, dict):
    #     ax.bar_label(ax.containers[0], fontsize=annot['fontsize'])
    # elif annot is True:
    #     ax.bar_label(ax.containers[0])


@wrap_fig
@wrap_ax
def basic_histplot(*args, ax, **kwargs):
    sns.histplot(*args, ax=ax, **kwargs)


def colorBar(fig, ax, cmap, ticksVals=None, ticks=None, location='right', **kwargs):
    cbar = fig.colorbar(plt.cm.ScalarMappable(norm=matplotlib.colors.Normalize(vmin=0, vmax=1), cmap=cmap),
                        location=location,
                        ax=ax, **kwargs)
    if ticks is not None:
        if location in ('left', 'right'):
            cbar.ax.set_yticks(ticks, labels=ticksVals)
        elif location in ('top', 'bottom'):
            cbar.ax.set_xticks(ticks, labels=ticksVals)
    return cbar


@wrap_ax
def plotLines_ax_part_(*args, ax, colorParam=None, commonFormat=None,
                       plot_kind='plot'):
    assert len(args) % 3 == 0, 'Number of arguments should be divisible by 3'
    assert plot_kind in ('plot', 'scatter'), f'Unsupported plot kind: {plot_kind}'
    if commonFormat is None:
        commonFormat = dict()
    for i in range(0, len(args), 3):
        x, y, fmt = args[i], args[i + 1], args[i + 2]
        if fmt is None:
            fmt = dict()
        if isinstance(fmt, str):
            fmt = {'label': fmt}
        if colorParam is not None:
            assert 'color' not in fmt
            fmt['color'] = colorParam[i // 3]
        if plot_kind == 'plot':
            ax.plot(x, y, **fmt, **commonFormat)
        else:
            ax.scatter(x, y, **fmt, **commonFormat)


@wrap_fig
def plotLines(*args, figax, colorParam=None, cmap=None,
              isColorDiscrete=False, contBarNVals=10,
              addColorBar=True,
              colorbarArgs=None,
              **kwargs):
    colorParam_saved = colorParam
    if colorParam is not None:
        colorParam_saved = np.copy(colorParam)
        colorParam, cmap = color_arr_to_correct_rgb(colorParam, cmap, isColorDiscrete)

    fig, ax = figax
    plotLines_ax_part_(*args, figax=(fig, ax), colorParam=colorParam, **kwargs)

    if colorParam is not None and addColorBar:
        if colorbarArgs is None:
            colorbarArgs = dict()
        if isColorDiscrete:
            nVals = len(np.unique(colorParam_saved))
            ticks = [(2 * i + 1) / (2 * nVals) for i in range(nVals)]
            params_ = dict(extend='both', ticks=ticks)
            params_.update(colorbarArgs)
            colorBar(fig, ax=ax, cmap=cmap, **params_)
        else:
            ticks = np.linspace(0., 1., contBarNVals).tolist()
            ticksVals = [t * (np.max(colorParam_saved) - np.min(colorParam_saved)) + np.min(colorParam_saved)
                         for t in ticks]
            ticksVals = [f'{t:.3f}' for t in ticksVals]
            params_ = dict(extend='max', ticks=ticks,
                           ticksVals=ticksVals,
                           )
            params_.update(colorbarArgs)
            colorBar(fig, ax=ax, cmap=cmap, **params_)


@wrap_fig
@wrap_ax
def basic_lineplot(data, ax, **kwargs):
    sns.lineplot(data, ax=ax, **kwargs)


def plot_values_distribution(vector: pd.Series, destDir, **kwargs):
    assert isinstance(vector, pd.Series)
    basic_histplot(vector, **kwargs, xlabel=f'Value of {vector.name}', ylabel='Count',
                   title=f'{vector.name} distribution\ntotal number of values: {len(vector)}',
                   plotFilePath=f'{destDir}/{vector.name}.png')


@wrap_fig
def map_with_defaults(data, figax, xticks=None, yticks=None,
                      barFontArgs=None,
                      **kwargs):

    ybounds = xbounds = None
    if isinstance(xticks, dict):
        xbounds = [xticks['min'], xticks['max']]
        xticks = {'ticks': [], 'labels': None}
    if isinstance(yticks, dict):
        ybounds = [yticks['min'], yticks['max']]
        yticks = {'ticks': [], 'labels': None}

    if not isinstance(data, pd.DataFrame):
        data = pd.DataFrame(data=data,
                            )

    fig, ax = figax

    basic_map(data, figax=(fig, ax), xticks=xticks, yticks=yticks, **kwargs)
    # sns.heatmap(data, ax=ax, vmin=cbounds[0], vmax=cbounds[1],
    #             cbar_kws={'label': kwargs.get('color_ax_label', None)},
    #             **(kwargs.get('map_kwargs', dict())))

    if barFontArgs is not None:
        setFonts(fig.axes[-1], **barFontArgs)

    if xbounds is not None:
        xmin, xmax = xbounds
        ax.xaxis.set_major_locator(ticker.MultipleLocator(data.shape[1] / 10))
        ax.xaxis.set_major_formatter(lambda x, pos: f'{x / data.shape[1] * (xmax - xmin) + xmin:.2f}')
    if ybounds is not None:
        ymin, ymax = ybounds
        ax.yaxis.set_major_locator(ticker.MultipleLocator(data.shape[0] / 10))
        ax.yaxis.set_major_formatter(lambda y, pos: f'{y / data.shape[0] * (ymax - ymin) + ymin:.2f}')
    setFonts(ax, fontsize=kwargs.get('fontsize', None), fontsizes=kwargs.get('fontsizes', None))


def draw_heatmaps(optimizer, grid_spec,
                  features_pair=None,
                  target_name=None,
                  bayes=False,
                  classif_or_regr='regr',
                  value_to_int=None,
                  points=None,
                  destDir=None,
                  **plotMapArgs
                  ):
    """

    :param bayes:
    :param optimizer:
    :param grid_spec: ([value] or (low, high, number_of_points))
    :param features_pair: ((name1, col_index1), (name2, col_index2))
    :param target_name:
    :param destDir:
    :param color_bounds: tuple(3), tuple[i] = None or (low, high)
    :return:
    """

    coord_v, coord_h = list(i for i, op in enumerate(grid_spec) if len(op) == 3)
    assert coord_v == features_pair[0][1]
    assert coord_h == features_pair[1][1]
    resolution_v, resolution_h = grid_spec[coord_v][2], grid_spec[coord_h][2]
    x_dim = len(grid_spec)

    # make grid
    grid_to_opt = np.zeros((resolution_v * resolution_h, x_dim))
    # fill constant
    for i in range(x_dim):
        if i not in (coord_v, coord_h):
            grid_to_opt[:, i] = grid_spec[i][0]
    # fill vertical
    arr = np.repeat(np.linspace(*grid_spec[coord_v]).reshape(-1, 1), resolution_h, axis=1)
    grid_to_opt[:, coord_v] = arr.flatten()
    # fill horizontal
    arr = np.repeat(np.linspace(*grid_spec[coord_h]).reshape(1, -1), resolution_v, axis=0)
    grid_to_opt[:, coord_h] = arr.flatten()

    name_v, name_h = features_pair[0][0], features_pair[1][0]

    uniq_v, uniq_h = np.linspace(*grid_spec[coord_v]), np.linspace(*grid_spec[coord_h])

    def reshape_to_matrix(functional):
        return functional.reshape(resolution_v, resolution_h)

    preds = mu = sigma = utility = None
    if bayes:
        mu, sigma, utility = optimizer.predict(grid_to_opt, return_std=True, return_utility=True)
        mu = reshape_to_matrix(mu)
        sigma = reshape_to_matrix(sigma)
        utility = reshape_to_matrix(utility)
    else:
        preds = optimizer.predict(grid_to_opt)
        preds = reshape_to_matrix(preds)

    # # plot surface
    # X, Y = grid
    # fig, ax = plt.subplots(subplot_kw={"projection": "3d"})
    # # Plot the surface.
    # surf = ax.plot_surface(X, Y, mu, cmap=cm.coolwarm,
    #                        linewidth=0, antialiased=False)
    # fig.colorbar(surf, shrink=0.5, aspect=5)
    # plt.show()

    minmax_h = (np.min(uniq_h), np.max(uniq_h))
    minmax_v = (np.min(uniq_v), np.max(uniq_v))
    xlabels = np.array([f'{i1 * (minmax_h[1] - minmax_h[0]) + minmax_h[0]:.3f}' for i1 in np.linspace(0., 1., uniq_h.size)])
    ylabels = np.array([f'{i1 * (minmax_v[1] - minmax_v[0]) + minmax_v[0]:.3f}' for i1 in np.linspace(0., 1., uniq_v.size)])

    # def plot_show_save_map_(data, xticks, yticks, filepath, show: bool = False,
    #                        xlabel='?', ylabel='?', cbounds=None):
    #     if cbounds is None:
    #         cbounds = [None, None]
    #     fig, ax = plt.subplots()
    #     data_to_heatmap = pd.DataFrame(data=data,
    #                                    index=yticks,
    #                                    columns=xticks,
    #                                    )
    #
    #     if classif_or_regr == 'regr':
    #         cmap = 'YlGnBu'
    #     else:
    #         cmap = pl.cm.get_cmap('YlGnBu', len(value_to_int))
    #     ax = sns.heatmap(data_to_heatmap, ax=ax, vmin=cbounds[0], vmax=cbounds[1],
    #                 cbar_kws={'label': target_name}, cmap=cmap)
    #     if classif_or_regr == 'classif':
    #         num_classes = len(value_to_int)
    #         colorbar = ax.collections[0].colorbar
    #         r = colorbar.vmax - colorbar.vmin
    #         colorbar.set_ticks([colorbar.vmin + r / num_classes * (0.5 + i) for i in range(num_classes)])
    #         int_to_value = {i: k for k, i in value_to_int.items()}
    #         colorbar.set_ticklabels([int_to_value[i] for i in range(num_classes)])
    #
    #     ax.set_xlabel(xlabel)
    #     ax.set_ylabel(ylabel)
    #
    #     # xticks_float = xticks.astype('float')
    #     # yticks_float = yticks.astype('float')
    #     xmin, xmax = np.min(xticks.astype('float')), np.max(xticks.astype('float'))
    #     ymin, ymax = np.min(yticks.astype('float')), np.max(yticks.astype('float'))
    #     ax.xaxis.set_major_locator(ticker.MultipleLocator(data.shape[1] / 10))
    #     ax.yaxis.set_major_locator(ticker.MultipleLocator(data.shape[0] / 10))
    #     ax.xaxis.set_major_formatter(lambda x, pos: f'{x / data.shape[1] * (xmax - xmin) + xmin:.3f}')
    #     ax.yaxis.set_major_formatter(lambda y, pos: f'{y / data.shape[0] * (ymax - ymin) + ymin:.3f}')
    #
    #     # if points is not None:
    #     #     points[1] = (points[1] - xmin) / (xmax - xmin) * data.shape[0]
    #     #     points[0] = (points[0] - ymin) / (ymax - ymin) * data.shape[1]
    #     #     # points[0] = np.ones_like(points[0]) * data.shape[1]
    #     #     # points[1] = np.ones_like(points[1]) * data.shape[0]
    #     #     plotting.scatter(
    #     #         points[1], points[0], color=points[2],
    #     #         fig_ax=(fig, ax),
    #     #         marker_text=points[3],
    #     #         min_marker_size=7, max_marker_size=12,
    #     #         edgecolor='r',
    #     #         cmap=cmap,
    #     #         cbar=False
    #     #     )
    #
    #     if show:
    #         plt.show()
    #     savefig(fig, filepath)

    # if color_bounds is None:
    #     if bayes:
    #         color_bounds = (None, None, None)

    show_maps = False

    if bayes:
        # plot_show_save_map_(mu, xlabels, ylabels, f'{destDir}/mu_map_{name_h}x{name_v}.png', show=show_maps,
        #                     xlabel=name_h, ylabel=name_v, cbounds=color_bounds[0])
        # plot_show_save_map_(sigma, xlabels, ylabels, f'{destDir}/sigma_map_{name_h}x{name_v}.png', show=show_maps,
        #                     xlabel=name_h, ylabel=name_v, cbounds=color_bounds[1])
        # plot_show_save_map_(utility, xlabels, ylabels, f'{destDir}/utility_map_{name_h}x{name_v}.png', show=show_maps,
        #                     xlabel=name_h, ylabel=name_v, cbounds=color_bounds[2])
        raise NotImplementedError
    else:
        if destDir is not None:
            plotMapArgs['plotFilePath'] = plotMapArgs.get('plotFilePath', f'{destDir}/map_{name_h}x{name_v}.png')
        fig, ax = map_with_defaults(preds, xticks={'min': minmax_h[0], 'max': minmax_h[1]},
                                    yticks={'min': minmax_v[0], 'max': minmax_v[1]},
                                    xlabel=name_h, ylabel=name_v,
                                    **plotMapArgs)
        return fig, ax


def draw_in_all_dimensions(optimizer,
                           features,
                           foldpath='./plot_folder',
                           color_bounds=None,
                           max_plot_number=20,
                           data: pd.DataFrame = None,
                           target_col: str = None,
                           id_col: str = None,
                           **kwargs):
    """

    :param optimizer:
    :param features: ({name1, value1, (low, high, number_of_points)_1}, ...)
    :param foldpath:
    :param color_bounds:
    :return:
    """

    idxs = list(range(len(features)))
    plot_number = 0
    for c in itertools.combinations(idxs, 2):
        grid_spec = [0] * len(idxs)
        features_pair = []
        for i in idxs:
            if i in c:
                grid_spec[i] = features[i]['grid']
                features_pair.append((features[i]['name'], i))
            else:
                grid_spec[i] = [features[i]['value']]
        points = None
        if data is not None:
            points = [data[features_pair[0][0]], data[features_pair[1][0]], data[target_col], data[id_col]]
        draw_heatmaps(optimizer, grid_spec, features_pair, destDir=foldpath, color_bounds=color_bounds,
                      points=points,
                      **kwargs)
        plot_number += 1
        if plot_number >= max_plot_number:
            break


def draw_in_all_dimensions_2(data, features, model_class=None, model_regr=None,
                             labels=None,
                             labelMaps=None,
                             foldpath='./plot_folder',):
    """

    :param optimizer:
    :param features: ({name1, value1, (low, high, number_of_points)_1}, ...)
    :param foldpath:
    :param color_bounds:
    :return:
    """

    for comb in itertools.combinations(features, 2):
        descriptor.plotDescriptors2d(data, descriptorNames=comb, labelNames=labels, labelMaps=labelMaps,
                                     folder_prefix=foldpath,
                                     unknown=None, textColumn=None,
                                     cv_count=data.shape[0], cmap='viridis',
                                     model_class=model_class, model_regr=model_regr)


def pairwise_scatters(X: pd.DataFrame, Y: pd.Series, id_col,
                      foldpath, extend_path=True,
                      log_the_target=False,
                      labels=None):
    assert isinstance(Y, pd.Series)
    features_names = X.columns.to_list()
    target_name = Y.name

    if log_the_target:
        Y = np.log(Y + 1.e-3)

    features_pairs = filter(lambda pair: features_names.index(pair[0]) > features_names.index(pair[1]),
                            itertools.product(features_names, features_names))

    if extend_path:
        foldpath = f'{foldpath}/{target_name}'
        os.makedirs(foldpath, exist_ok=True)

    for p in features_pairs:
        feature1, feature2 = add_noise_to_vectors(X[p[0]], X[p[1]], noise_level=0.05)
        fig, ax = plt.subplots()
        plotting.scatter(feature1.to_numpy(), feature2.to_numpy(), color=Y.to_numpy(),
                         fig_ax=(fig, ax),
                         title=f'target: {target_name}\nfeatures: {p[0]} x {p[1]}', xlabel=p[0], ylabel=p[1],
                         marker_text=id_col,
                         class_labels=labels)
        setFonts(ax, 18)
        savefig(fig, f'{foldpath}/{target_name}_{p[0]}X{p[1]}.png', bbox_inches='tight')


def true_pred_scatter(names_to_plot=None,
                      data=None,
                      foldpath='.', filename='true_predicted.xlsx',
                      plot_path=None,
                      exp_marker=None,
                      metrics_to_show=None,
                      ):
    if data is None:
        data = pd.read_excel(f'{foldpath}/{filename}')
    if names_to_plot is None:
        names_to_plot = [c[:c.rfind('_pred')] for c in data.columns if '_pred' in c]
    if plot_path is not None:
        assert len(names_to_plot) == 1

    for name in names_to_plot:
        vals = data[[f'{name}_true', f'{name}_pred']]
        vals = vals.to_numpy()

        min_v, max_v = np.min(vals), np.max(vals)
        padding = (max_v - min_v) * 0.1
        bounds = np.tile([min_v - padding,
                          max_v + padding], (2, 1))

        def plot_more_func(ax):
            ax.set_xlim(bounds[0])
            ax.set_ylim(bounds[0])
            ax.plot(bounds[0], bounds[1], 'r--')

        title = f'true-predicted scatter for label "{name}"'
        if metrics_to_show is not None:
            title += '\n'
            for metric_name, metric in metrics_to_show.items():
                title += f'{metric_name}: {metric(data[f"{name}_true"], data[f"{name}_pred"]):.3f}; '

        plot_path_temp = plot_path
        if plot_path_temp is None:
            plot_path_temp = f'{foldpath}/{name}_{os.path.splitext(filename)[0]}.png'

        # TODO crutchy part
        fig, axs = plt.subplots()
        plotting.scatter(vals[:, 0], vals[:, 1],
                         fig_ax=(fig, axs),
                         title=title, xlabel=f'true {name}', ylabel=f'predicted {name}',
                         marker_text=exp_marker,
                         plotMoreFunction=plot_more_func,
                         min_marker_size=12,
                         max_marker_size=15,
                         color='r',
                         )
        setFonts(axs, 18)
        plot_foldpath, _ = os.path.split(plot_path_temp)
        os.makedirs(plot_foldpath, exist_ok=True)
        savefig(fig, plot_path_temp, bbox_inches='tight')


@wrap_fig
@wrap_ax
def confusion_matrix(y_true, y_pred, ax, text_kw=None, **kwargs):
    CM = ConfusionMatrixDisplay.from_predictions(y_true, y_pred,
                                                 text_kw=text_kw,
                                                 **kwargs
                                                 )
    CM.plot(ax=ax, cmap='Blues', colorbar=False, text_kw=text_kw)


def confusion_matrices(names_to_plot=None,
                       data=None,
                       dataPath=None,
                       plotPath=None,
                       label_vals=None,
                       metrics_to_show=None,
                       **kwargs
                       ):
    dataDir = fileName = None
    if dataPath is not None:
        dataDir = os.path.dirname(dataPath)
        fileName = os.path.split(dataPath)[1]
    if data is None:
        data = pd.read_excel(dataPath)
    if names_to_plot is None:
        names_to_plot = [c[:c.rfind('_pred')] for c in data.columns if '_pred' in c]
    if plotPath is not None:
        assert len(names_to_plot) == 1
    for name in names_to_plot:

        title = f'Confusion matrix for label "{name}"'
        if metrics_to_show is not None:
            title += '\n'
            for metric_name, metric in metrics_to_show.items():
                title += f'{metric_name}: {metric(data[f"{name}_true"], data[f"{name}_pred"]):.3f}; '

        plot_path_temp = plotPath
        if plot_path_temp is None:
            plot_path_temp = f'{dataDir}/{name}_{os.path.splitext(fileName)[0]}.png'

        confusion_matrix(data[f'{name}_true'], data[f'{name}_pred'],
                         labels=label_vals,
                         title=title, xlabel=f'Predicted {name}', ylabel=f'True {name}',
                         plotFilePath=plot_path_temp, **kwargs
                         )

        # fig, ax = plt.subplots(figsize=(16, 9))
        # confusion_matrix(data[f'{name}_true'], data[f'{name}_pred'],
        #                  labels=label_vals,
        #                  title=title, xlabel=f'Predicted {name}', ylabel=f'True {name}',
        #                  figax=(fig, ax), plotFilePath=plot_path_temp, **kwargs
        #                  )
        # plt.close(fig)


LABEL_TO_COLOR = {0: 'r', 1: 'b', 2: 'purple',
                  3: 'y', 4: 'black'}


def plot_linear_boundaries(clsfr, ax, xlim, ylim, num_classes,
                           grid_resolution=400, alpha=0.5):
    X1, X2 = np.meshgrid(np.linspace(*xlim, grid_resolution),
                         np.linspace(*ylim, grid_resolution))
    data = np.hstack((X1.ravel().reshape(-1, 1), X2.ravel().reshape(-1, 1)))
    Y_pred = np.apply_along_axis(lambda x: clsfr.predict(x), 1, data)

    levels = np.arange(-0.5, num_classes, 1.)
    colors = [LABEL_TO_COLOR[i] for i in range(num_classes)]
    ax.contourf(X1, X2,
                Y_pred.reshape(grid_resolution, grid_resolution),
                levels=levels, colors=colors, alpha=alpha)

