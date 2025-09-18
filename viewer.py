import os
import sys

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.append(os.path.expanduser('~/SupervisedML/repos'))
from supervised_ml.plot_util import *


class Viewer:
    @staticmethod
    def view_center(*args, **kwargs):
        raise NotImplementedError
    
    @staticmethod
    def view_rings(*args, **kwargs):
        raise NotImplementedError
    
    @staticmethod
    def view_refined_curve(*args, **kwargs):
        raise NotImplementedError
    
    @staticmethod
    def view_calibration(*args, **kwargs):
        pass

    @staticmethod
    def view_curves(*args, **kwargs):
        pass


class PLTViewer(Viewer):
    @staticmethod
    def view_center(img_data, tiff_path, center_y, center_x, clusters, 
                    fig_axs=None):
        show = fig_axs is None
        if fig_axs is None:
            fig, axs = plt.subplots(1, 2, figsize=(16, 6))
        else:
            fig, axs = fig_axs
        
        im = axs[0].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
        # plt.colorbar(im, ax=axs[0], label='Log(Intensity + 1)')
        axs[0].set_title(f"2D SAXS Data: {os.path.basename(tiff_path)}")
        axs[0].set_xlabel("Pixel X")
        axs[0].set_ylabel("Pixel Y")

        axs[1].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
        scatter_data = pd.DataFrame(data=clusters, columns=['y', 'x', 'cluster'])
        sns.scatterplot(data=scatter_data, y='y', x='x', hue='cluster', ax=axs[1],
                        palette=get_bright_fire_cmap()[0])
        axs[1].plot(center_x, center_y, 'r*')
        axs[1].set_title(f"Apparent rings and the center")
        axs[1].set_xlabel("Pixel X")
        axs[1].set_ylabel("Pixel Y")

        if show:
            plt.show()

        return fig, axs
    
    @staticmethod
    def view_rings(img_data, tiff_path, rings, fig_axs=None):
        show = fig_axs is None
        if fig_axs is None:
            fig, axs = plt.subplots(1, 2, figsize=(16, 6))
        else:
            fig, axs = fig_axs
        
        im = axs[0].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
        # plt.colorbar(im, ax=axs[0], label='Log(Intensity + 1)')
        axs[0].set_title(f"2D SAXS Data: {os.path.basename(tiff_path)}")
        axs[0].set_xlabel("Pixel X")
        axs[0].set_ylabel("Pixel Y")

        axs[1].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
        scatter_data = pd.DataFrame(data=rings, columns=['y', 'x', 'ring_number'])
        sns.scatterplot(data=scatter_data, y='y', x='x', hue='ring_number', ax=axs[1],
                        palette=get_bright_fire_cmap()[0])
        axs[1].set_title(f"Apparent rings, refined")
        axs[1].set_xlabel("Pixel X")
        axs[1].set_ylabel("Pixel Y")

        if show:
            plt.show()

        return fig, axs
    
    @staticmethod
    def view_refined_curve(curve_calibrated, theoretical_peaks, 
                           fig_axs=None):
        show = fig_axs is None
        if fig_axs is None:
            fig, axs = plt.subplots(figsize=(10, 6))
            axs = np.array([axs, ])
        else:
            fig, axs = fig_axs
        
        q_cal, i_cal = curve_calibrated

        cal_plot = axs[0].plot(q_cal, i_cal, label="Calibrated Curve")

        # Plot theoretical peak positions
        for q_val in theoretical_peaks:
            axs[0].axvline(x=q_val, color='r', linestyle='--', label='Theoretical Peaks')

        axs[0].set_xlim(0, np.max(q_cal))
        axs[0].set_xlabel("q (nm^-1)")
        axs[0].set_ylabel("Intensity")
        axs[0].set_title("Calibration Result")
        
        # Create a legend with unique labels
        handles, labels = axs[0].get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        axs[0].legend(by_label.values(), by_label.keys())
        
        axs[0].grid(True)
        if show:
            plt.show()
    
    @staticmethod
    def view_calibration(
        *,
        img_data, tiff_path, 
        center_y, center_x, clusters,
        rings, curve_calibrated, theoretical_peaks, 
        fig_axs=None, show=True, plotFilePath=None,
        **kwargs):
        
        if fig_axs is None:
            fig, axs = plt.subplots(2, 2, figsize=(16, 12))
        else:
            fig, axs = fig_axs
        
        PLTViewer.view_center(img_data, tiff_path, fig_axs=(fig, axs[0]),
                              center_y=center_y, center_x=center_x, clusters=clusters)

        axs[1, 0].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
        scatter_data = pd.DataFrame(data=rings, columns=['y', 'x', 'ring_number'])
        sns.scatterplot(data=scatter_data, y='y', x='x', hue='ring_number', ax=axs[1, 0],
                        palette=get_bright_fire_cmap()[1])
        axs[1, 0].set_title(f"Apparent rings, refined")
        axs[1, 0].set_xlabel("Pixel X")
        axs[1, 0].set_ylabel("Pixel Y")

        PLTViewer.view_refined_curve(curve_calibrated, theoretical_peaks,
                                     fig_axs=(fig, axs[[1, ], [1, ]]))

        if show:
            plt.show()
        if plotFilePath is not None:
            fig.savefig(plotFilePath)
    
    @staticmethod
    def view_curves(*args, **kwargs):
        kw = dict(xlabel='q (nm^-1)', ylabel='I (a.u.)')
        kw.update(kwargs)
        plotLines(*args, **kw)


def get_bright_fire_cmap():
    cmap_name = 'bright_fire'
    colors = ['#FF4136', '#FF851B', '#FFDC00']  # Bright Red -> Bright Orange -> Bright Yellow
    bright_fire_cmap = mcolors.LinearSegmentedColormap.from_list(cmap_name, colors)
    return bright_fire_cmap, colors, cmap_name
