import os
import sys
from typing import Optional, Union, Tuple, Dict

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import seaborn as sns

from collections import defaultdict

import ase
from ase.geometry import get_distances

# from .utils import SUPERVISED_ML_DIR
# sys.path.append(SUPERVISED_ML_DIR)
# from supervised_ml.plot_util import *
from .foreign.supervised_ml.plot_util import *

from .utils import (
    calc_chi2,
    calculate_atoms_density_and_isosurface,
    calculate_shape_density_and_isosurface
)


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
    def view_mask(*args, **kwargs):
        pass

    @staticmethod
    def view_curves(*args, **kwargs):
        pass

    @staticmethod
    def plot_structure_and_scattering(*args, **kwargs):
        pass

    @staticmethod
    def plot_3d_views_and_scattering(*args, **kwargs):
        pass


class PLTViewer(Viewer):
    def __init__(self):
        setPlotDefaults()

    @staticmethod
    def show(duration: Optional[float] = None):
        """
        Unified non-blocking show helper.
        If duration is None, do nothing (caller only saves/updates figures).
        Otherwise, show for `duration` seconds and close all figures.
        """
        if duration is None:
            return
        plt.show(block=False)
        plt.pause(duration)
        plt.close("all")

    @staticmethod
    def view_center(img_data, tiff_path, center_y_px, center_x_px, clusters,
                    fig_axs=None, show_duration: Optional[float] = None):
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
        axs[1].plot(center_x_px, center_y_px, 'r*')
        axs[1].set_title(f"Apparent rings and the center")
        axs[1].set_xlabel("Pixel X")
        axs[1].set_ylabel("Pixel Y")

        if fig_axs is None:
            PLTViewer.show(show_duration)

        return fig, axs
    
    @staticmethod
    def view_rings(img_data, tiff_path, rings, fig_axs=None, show_duration: Optional[float] = None):
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

        if fig_axs is None:
            PLTViewer.show(show_duration)

        return fig, axs
    
    @staticmethod
    def view_refined_curve(curve_calibrated, theoretical_peaks,
                           fig_axs=None, show_duration: Optional[float] = None):
        if fig_axs is None:
            fig, axs = plt.subplots(figsize=(10, 6))
            axs = np.array([axs, ])
        else:
            fig, axs = fig_axs
        
        q_cal, i_cal, sigma_cal = curve_calibrated
        # sigma_cal *= 10  # debug

        color = '#1f77b4'
        # log(I) vs q (coordinates): plot log10(I) values directly on a linear axis.
        # Filter out very small intensities: keep log10(I) >= -4 (i.e., I >= 1e-4).
        eps = np.finfo(float).tiny
        i_pos = np.where(i_cal > 0, i_cal, np.nan)
        log_i = np.log10(i_pos)
        keep = np.isfinite(log_i) & (log_i >= -4.0)
        q_plot = q_cal[keep]
        i_plot = i_pos[keep]
        sigma_plot = sigma_cal[keep]

        # Keep the uncertainty band within the same visibility threshold.
        # Otherwise, (I - σ) can go to ~0 and produce huge negative logs.
        lower_i = np.maximum(i_plot - sigma_plot, 1e-4)
        upper_i = np.maximum(i_plot + sigma_plot, 1e-4)
        log_i_plot = np.log10(i_plot)
        lower_log = np.log10(lower_i)
        upper_log = np.log10(upper_i)

        cal_plot = axs[0].plot(q_plot, log_i_plot, label="Calibrated Curve")
        axs[0].fill_between(q_plot, lower_log, upper_log, color=color, alpha=0.5)
        axs[0].grid(True, alpha=0.3)

        # Plot theoretical peak positions
        for q_val in theoretical_peaks:
            axs[0].axvline(x=q_val, color='r', linestyle='--', label='Theoretical Peaks')

        axs[0].set_xlim(0, np.max(q_cal))
        axs[0].set_xlabel("q (nm^-1)")
        axs[0].set_ylabel("log10(Intensity)")
        axs[0].set_title("Calibration Result (log I vs q)")
        
        # Create a legend with unique labels
        handles, labels = axs[0].get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        axs[0].legend(by_label.values(), by_label.keys())
        
        axs[0].grid(True)

        if fig_axs is None:
            PLTViewer.show(show_duration)
    
    @staticmethod
    def view_calibration(
        *,
        img_data, tiff_path, 
        center_y_px, center_x_px, clusters,
        rings, curve_calibrated, theoretical_peaks, 
        fig_axs=None, show_duration: Optional[float] = None, plotFilePath=None,
        **kwargs):
        
        if fig_axs is None:
            fig, axs = plt.subplots(2, 2, figsize=(32, 24))
        else:
            fig, axs = fig_axs
        
        PLTViewer.view_center(
            img_data,
            tiff_path,
            center_y_px=center_y_px,
            center_x_px=center_x_px,
            clusters=clusters,
            fig_axs=(fig, axs[0]),
        )

        axs[1, 0].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
        scatter_data = pd.DataFrame(data=rings, columns=['y', 'x', 'ring_number'])
        sns.scatterplot(data=scatter_data, y='y', x='x', hue='ring_number', ax=axs[1, 0],
                        palette=get_bright_fire_cmap()[1])
        axs[1, 0].set_title(f"Apparent rings, refined")
        axs[1, 0].set_xlabel("Pixel X")
        axs[1, 0].set_ylabel("Pixel Y")

        PLTViewer.view_refined_curve(
            curve_calibrated,
            theoretical_peaks,
            fig_axs=(fig, axs[[1, ], [1, ]]),
        )

        if plotFilePath is not None:
            fig.savefig(plotFilePath)
        PLTViewer.show(show_duration)
        if fig_axs is None and show_duration is None:
            # No external figure management and no timed show – close to avoid leaks
            plt.close(fig)
    
    @staticmethod
    def view_mask(
        img_data, mask, *, 
        tiff_path, fig_axs=None, show_duration: Optional[float] = None, plotFilePath=None):

        if fig_axs is None:
            fig, axs = plt.subplots(2, 2, figsize=(32, 24))
        else:
            fig, axs = fig_axs
        
        img_data = img_data.astype('float')
        img_data = img_data - min(np.min(img_data), 0)
        
        axs[0, 0].imshow(np.log1p(img_data), cmap='viridis', origin='lower')
        axs[0, 0].set_title(f"2D SAXS Data: {os.path.basename(tiff_path)}")
        axs[0, 0].set_xlabel("Pixel X")
        axs[0, 0].set_ylabel("Pixel Y")

        # pyFAI convention: True means masked.
        mask_vis_masked = np.asarray(mask, dtype=bool)
        # Display panel now follows pyFAI convention too: 1=masked, 0=unmasked.
        mask_pyfai_convention = mask_vis_masked.astype(np.uint8)
        axs[0, 1].imshow(mask_pyfai_convention, cmap='grey', origin='lower', vmin=0, vmax=1)
        axs[0, 1].set_title("Mask (pyFAI convention: 1=masked, 0=unmasked)")
        axs[0, 1].set_xlabel("Pixel X")
        axs[0, 1].set_ylabel("Pixel Y")

        img_data_masked = np.copy(img_data)
        img_data_masked[mask_vis_masked] = 0.0
        axs[1, 0].imshow(np.log1p(img_data_masked), cmap='viridis', origin='lower')
        axs[1, 0].set_title(f"Masked 2D SAXS Data, masked as zeros")
        axs[1, 0].set_xlabel("Pixel X")
        axs[1, 0].set_ylabel("Pixel Y")

        img_data_masked[mask_vis_masked] = np.nan
        axs[1, 1].imshow(np.log1p(img_data_masked), cmap='viridis', origin='lower')
        axs[1, 1].set_title(f"Masked 2D SAXS Data, masked as missing")
        axs[1, 1].set_xlabel("Pixel X")
        axs[1, 1].set_ylabel("Pixel Y")

        if plotFilePath is not None:
            fig.savefig(plotFilePath)
        PLTViewer.show(show_duration)
        if fig_axs is None and show_duration is None:
            # No external figure management and no timed show – close to avoid leaks
            plt.close(fig)
    
    @staticmethod
    def view_curves(
        *args, show_duration: Optional[float] = None, 
        sigmas=None, grid=True,
        plotFilePath=None, savefigArgs=None,
        **kwargs):
        kw = dict(xlabel='$q (nm^-1)$', ylabel='I (a.u.)')
        kw.update(kwargs)

        fig, ax = plotLines(*args, **kw)
        if sigmas is not None:
            assert len(sigmas) <= int(len(args) // 3) and all(isinstance(s, np.ndarray) or s is None for s in sigmas)
            for i, s in enumerate(sigmas):
                if s is None:
                    continue
                x, y, fmt = args[3*i:3*(i+1)]
                color = None
                if fmt is None:
                    fmt = {}
                if isinstance(fmt, str):
                    fmt = {'label': fmt}
                for k in ('c', 'color'):
                    if k in fmt:
                        color = fmt[k]
                        break
                ax.fill_between(x, y - s, y + s, color=color, alpha=0.5*fmt.get('alpha', 1.0))
        if grid:
            ax.grid(True, alpha=0.3)
        if plotFilePath is not None:
            if savefigArgs is None:
                savefigArgs = {}
            fig.savefig(plotFilePath, **savefigArgs)
        if show_duration is not None:
            PLTViewer.show(show_duration)
        else:
            plt.close(fig)
    
    @staticmethod
    def plot_structure_and_scattering(atoms, q, I, sigma, I_fit, fig_axs=None,
                                      plotFilePath=None):
        """
        Plot 2D projections of an ASE Atoms object and optional scattering data.

        Parameters:
        -----------
        atoms : ase.Atoms
            Atomic structure to visualize.
        q, I : array-like, optional
            Experimental scattering data (q-values and intensities).
        q_fit, I_fit : array-like, optional
            Fitted scattering curve.
        """
        positions = atoms.positions  # (N, 3)
        symbols = atoms.get_chemical_symbols()
        symbols_set = set(symbols)
        assert len(symbols_set) < 2, f'Expected one atom symbol for dummy model or empty model, but got atom symbols {list(symbols_set)}'
        
        # # Group atoms by element for coloring
        # atom_groups = defaultdict(list)
        # for sym, pos in zip(symbols, positions):
        #     atom_groups[sym].append(pos)
        # for sym in atom_groups:
        #     atom_groups[sym] = np.array(atom_groups[sym])

        # Create 2x2 subplots
        if fig_axs is None:
            fig, axs = plt.subplots(2, 2, figsize=(30, 24))
        else:
            fig, axs = fig_axs
        (ax_front, ax_side), (ax_top, ax_saxs) = axs

        # --- Projections ---
        for ax, (x, y, title) in zip(
            [ax_front, ax_side, ax_top],
            [
                (positions[:, 0], positions[:, 1], 'Front (x–y)'),
                (positions[:, 1], positions[:, 2], 'Side (y–z)'),
                (positions[:, 0], positions[:, 2], 'Top (x–z)')
            ]
        ):
            # for sym, coords in atom_groups.items():
            #     ax.scatter(coords[:, 0] if 'x' in title else coords[:, 1 if 'y' in title else 0],
            #                coords[:, 1 if 'y' in title else 2],
            #                label=sym, s=80, edgecolor='k', linewidth=0.5)
            ax.scatter(x, y, s=80, edgecolor='k', linewidth=0.5)
            ax.set_xlabel('x' if 'x' in title else 'y')
            ax.set_ylabel('y' if 'x–y' in title else 'z')
            ax.set_title(title)
            ax.set_aspect('equal', adjustable='datalim')

        # --- Scattering plot (subplot 1,1) ---
        ax_saxs.plot(q, I, 'o', label='Experimental', markersize=4, alpha=0.7)
        ax_saxs.plot(q, I_fit, '-', label='Fit', linewidth=2)
        ax_saxs.set_xlabel(r'$q$ (nm$^{-1}$)')
        ax_saxs.set_ylabel(r'$I(q)$ (a.u.)')
        ax_saxs.set_title(f'Experiment vs fit comparison\n$\\chi^2$: {calc_chi2(I, I_fit, sigma):.5f}')
        ax_saxs.set_yscale('log')
        ax_saxs.legend()

        # plt.tight_layout()
        # plt.show()

        if plotFilePath is not None:
            savefig(fig, plotFilePath)

    @staticmethod
    def plot_3d_views_and_scattering(
        structure: Union[ase.Atoms, Tuple[str, Dict[str, float]]], q, I, sigma, I_fit, 
        fig_axs=None, plotFilePath=None, r_max=30.0, grid_size=64, isosurface_sigma=1.5, 
        isosurface_level=None, alpha_transparency=0.8):
        """
        Plot 3D structure from front, side, and top views using isosurface visualization,
        colored by distance from center of mass, and overlay scattering data in the fourth subplot.

        Parameters:
        -----------
        structure : ase.Atoms or tuple
            Either an atomic structure (ase.Atoms) or a tuple (shape_name, shape_params_dict)
            where shape_name is a string from BODIES_SHAPES and shape_params_dict contains
            the shape parameters.
        q, I : array-like
            Experimental scattering data.
        sigma : array-like
            Uncertainties for experimental data.
        I_fit : array-like
            Fitted scattering curve.
        r_max : float, default 30.0
            Maximum distance (in Å) for color scale normalization.
        grid_size : int, default 64
            Grid resolution for isosurface calculation.
        isosurface_sigma : float, default 1.5
            Standard deviation for Gaussian kernel in isosurface calculation (only for atoms).
        isosurface_level : float, optional
            Density level for isosurface. If None, auto-calculated for atoms or 0.5 for shapes.
        alpha_transparency : float, default 0.8
            Transparency of the isosurface (0-1).
        """
        from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
        from skimage.measure import marching_cubes
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        from matplotlib import cm
        from matplotlib.colors import Normalize
        
        # Determine input type and calculate density accordingly
        if isinstance(structure, ase.Atoms):
            # Atoms structure
            density, isosurface_level, min_coords, max_coords = calculate_atoms_density_and_isosurface(
                structure, grid_size=grid_size, isosurface_sigma=isosurface_sigma,
                isosurface_level=isosurface_level
            )
            # Calculate center of mass for coloring
            com = structure.get_center_of_mass()
        elif isinstance(structure, tuple) and len(structure) == 2:
            # Shape tuple (shape_name, shape_params_dict)
            density, isosurface_level, min_coords, max_coords = calculate_shape_density_and_isosurface(
                structure, grid_size=grid_size, isosurface_level=isosurface_level
            )
            # For shapes, center is at origin
            com = np.array([0.0, 0.0, 0.0])
        else:
            raise TypeError(
                f"structure must be either ase.Atoms or tuple (shape_name, shape_params_dict), "
                f"got {type(structure)}"
            )
        
        # Extract isosurface (may fail if level is outside volume data range)
        norm = Normalize(vmin=0, vmax=r_max)
        cmap = cm.viridis
        verts = faces = face_colors = None
        try:
            verts, faces, _, _ = marching_cubes(density, level=isosurface_level)
            # Scale vertices back to original coordinate system
            scale = (max_coords - min_coords) / (np.array(density.shape) - 1)
            verts = verts * scale + min_coords
            # Calculate colors for each face based on average distance of its vertices from center
            face_colors = []
            for face in faces:
                face_verts = verts[face]
                avg_dist = np.mean(np.linalg.norm(face_verts - com, axis=1))
                face_colors.append(cmap(norm(avg_dist)))
        except ValueError:
            # Surface level outside volume data range; leave 3D axes empty, keep scattering plot
            pass
        
        # Create figure
        if fig_axs is not None:
            raise RuntimeError
        fig = plt.figure(figsize=(30, 24))
        # Three 3D plane views. (vertical axis, horizontal axis) with (top-to-down, left-to-right).
        # vert_transform maps (c0,c1,c2)=(x,y,z) to (ax.x=horizontal, ax.y=vertical, ax.z=depth). View along depth.
        # (0,0): (x top-to-down, y left-to-right) -> (x,y) plane. Plot (y, -x, z), view from +z (elev=90)
        # (0,1): (z left-to-right, y left-to-right) -> (z,y) plane. Plot (z, y, x), view from +x (elev=90)
        # (1,0): (x top-to-down, z left-to-right) -> (x,z) plane. Plot (z, -x, y), view from +y (elev=90)
        # For all, we look down plot's z (depth); elev=90, azim=-90 gives view from +z in plot coords.
        def transform_00(v):
            return np.column_stack([v[:, 1], -v[:, 0], v[:, 2]])   # horizontal=y, vertical=-x, depth=z
        def transform_01(v):
            return np.column_stack([v[:, 2], v[:, 1], v[:, 0]])    # horizontal=z, vertical=y, depth=x
        def transform_10(v):
            return np.column_stack([v[:, 2], -v[:, 0], v[:, 1]])   # horizontal=z, vertical=-x, depth=y
        view_plane = (90, -90)  # look down plot z (depth) so we see the plane
        views_3d = [
            (221, '(x, y) plane', transform_00,
             (min_coords[1], max_coords[1]), (-max_coords[0], -min_coords[0]), (min_coords[2], max_coords[2]),
             view_plane),
            (222, '(z, y) plane', transform_01,
             (min_coords[2], max_coords[2]), (min_coords[1], max_coords[1]), (min_coords[0], max_coords[0]),
             view_plane),
            (223, '(x, z) plane', transform_10,
             (min_coords[2], max_coords[2]), (-max_coords[0], -min_coords[0]), (min_coords[1], max_coords[1]),
             view_plane),
        ]

        for subplot_spec, title, vert_transform, xlim, ylim, zlim, (view_elev, view_azim) in views_3d:
            ax = fig.add_subplot(subplot_spec, projection='3d')
            if verts is not None and faces is not None and face_colors is not None:
                verts_plot = vert_transform(verts)
                mesh = Poly3DCollection(verts_plot[faces], alpha=alpha_transparency,
                                        facecolors=face_colors, edgecolor='k', linewidth=0.2)
                ax.add_collection3d(mesh)
            ax.view_init(elev=view_elev, azim=view_azim)
            ax.set_xlim(xlim[0], xlim[1])
            ax.set_ylim(ylim[0], ylim[1])
            ax.set_zlim(zlim[0], zlim[1])
            ax.set_title(title)
            ax.set_box_aspect([1, 1, 1])

        # Scattering subplot
        ax = fig.add_subplot(224)
        ax.plot(q, I, 'o', label='Experimental', markersize=4, alpha=0.7)
        if sigma is not None:
            ax.fill_between(q, I - sigma, I + sigma, alpha=0.5)
        ax.plot(q, I_fit, '-', label='Fit', linewidth=2)
        ax.set_xlabel(r'$q$ (nm$^{-1}$)')
        ax.set_ylabel(r'$I(q)$ (a.u.)')
        ax.set_yscale('log')
        ax.set_title(f'Experiment vs fit comparison\n$\\chi^2$: {calc_chi2(I, I_fit, sigma):.5f}')
        ax.legend()

        if plotFilePath is not None:
            savefig(fig, plotFilePath)


def get_bright_fire_cmap():
    cmap_name = 'bright_fire'
    colors = ['#FF4136', '#FF851B', '#FFDC00']  # Bright Red -> Bright Orange -> Bright Yellow
    bright_fire_cmap = mcolors.LinearSegmentedColormap.from_list(cmap_name, colors)
    return bright_fire_cmap, colors, cmap_name
