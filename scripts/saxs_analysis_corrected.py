#!/usr/bin/env python3
"""
Comprehensive SAXS Analysis for Pt Nanoparticles
With correct q units (nm⁻¹) and polydisperse spheres model fitting
"""

import numpy as np
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.special import erf
from scipy.integrate import quad
import warnings
warnings.filterwarnings('ignore')

# Set up for Chinese characters
plt.rcParams['font.sans-serif'] = ['SimHei']
plt.rcParams['axes.unicode_minus'] = False

def read_saxs_data(filepath):
    """Read SAXS data from .dat file with YAML header"""
    with open(filepath, 'r') as f:
        lines = f.readlines()

    # Find where data starts (after CSV header)
    data_start = 0
    for i, line in enumerate(lines):
        if line.strip().startswith('q,') or line.strip() == 'q,intensity,sigma':
            data_start = i + 1
            break

    # Parse data
    q_data = []
    intensity_data = []
    sigma_data = []

    for line in lines[data_start:]:
        parts = line.strip().split(',')
        if len(parts) >= 3:
            try:
                q_data.append(float(parts[0]))
                intensity_data.append(float(parts[1]))
                sigma_data.append(float(parts[2]))
            except ValueError:
                continue

    return np.array(q_data), np.array(intensity_data), np.array(sigma_data)

def guinier_analysis(q, I, sigma, q_max_Rg=1.3):
    """
    Guinier analysis for Rg determination
    I(q) = I(0) * exp(-q²Rg²/3)
    q is in nm⁻¹, Rg will be in nm
    Valid for q*Rg < 1.3

    Uses simple approach: fit in progressively smaller q ranges until valid
    """
    # Filter valid data (positive intensities)
    valid = (I > 0) & (sigma > 0)
    q_valid = q[valid]
    I_valid = I[valid]
    sigma_valid = sigma[valid]

    if len(q_valid) < 10:
        return None, None, None, None, None, None, None, None

    # Try different q ranges and pick the best one
    best_Rg = None
    best_I0 = None
    best_r2 = -np.inf
    best_q_range = None
    best_I_range = None
    best_slope = None
    best_intercept = None

    # Try multiple q_max values
    for q_max in np.linspace(q_valid.min() * 2, q_valid.max() * 0.3, 20):
        q_range = q_valid[q_valid <= q_max]
        I_range = I_valid[q_valid <= q_max]

        if len(q_range) < 5:
            continue

        # Guinier fit: ln(I) vs q²
        q2 = q_range**2
        lnI = np.log(I_range)
        weights = I_range / sigma_valid[q_valid <= q_max]**2

        try:
            coeffs = np.polyfit(q2, lnI, 1, w=np.sqrt(weights))
            slope = coeffs[0]
            intercept = coeffs[1]

            if slope >= 0:  # Invalid - slope should be negative
                continue

            # Rg = sqrt(-3*slope)
            Rg = np.sqrt(-3 * slope)

            # Check validity: q_max*Rg should be < 1.3
            max_qRg = q_range.max() * Rg
            if max_qRg > q_max_Rg:
                continue

            # R² value
            lnI_fit = slope * q2 + intercept
            ss_res = np.sum((lnI - lnI_fit)**2)
            ss_tot = np.sum((lnI - np.mean(lnI))**2)
            r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

            # Prefer fits with more points and good R²
            score = r2 * np.sqrt(len(q_range))

            if score > best_r2:
                best_r2 = score
                best_Rg = Rg
                best_I0 = np.exp(intercept)
                best_q_range = q_range
                best_I_range = I_range
                best_slope = slope
                best_intercept = intercept

        except:
            continue

    if best_Rg is not None:
        max_qRg = best_q_range.max() * best_Rg

        # Recalculate R² properly
        q2 = best_q_range**2
        lnI = np.log(best_I_range)
        lnI_fit = best_slope * q2 + best_intercept
        ss_res = np.sum((lnI - lnI_fit)**2)
        ss_tot = np.sum((lnI - np.mean(lnI))**2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        return best_Rg, best_I0, max_qRg, r2, best_q_range, best_I_range, best_slope, best_intercept

    return None, None, None, None, None, None, None, None

def sphere_form_factor(q, R):
    """
    Form factor for a solid sphere
    P(q,R) = [3*(sin(qR) - qR*cos(qR)) / (qR)³]²
    """
    qr = q * R
    # Handle small qr
    mask = qr < 0.01
    result = np.ones_like(qr)

    # For larger qr
    qr_large = qr[~mask]
    result[~mask] = ((3 * (np.sin(qr_large) - qr_large * np.cos(qr_large)) / (qr_large**3))**2)

    return result

def polydisperse_sphere_intensity(q, R_mean, sigma_R, I0, bkg):
    """
    Intensity for polydisperse spheres with log-normal size distribution

    Parameters:
    q: scattering vector (nm⁻¹)
    R_mean: mean radius (nm)
    sigma_R: relative standard deviation (sigma_R/R_mean)
    I0: scale factor
    bkg: background
    """
    if sigma_R < 0.001:
        # Monodisperse limit
        return I0 * sphere_form_factor(q, R_mean) + bkg

    # Numerical integration over size distribution (log-normal)
    # Use log-normal distribution
    mu = np.log(R_mean) - 0.5 * np.log(1 + sigma_R**2)
    sigma = np.sqrt(np.log(1 + sigma_R**2))

    # Integration limits (3 sigma on each side)
    R_min = R_mean * np.exp(-3 * sigma)
    R_max = R_mean * np.exp(3 * sigma)

    n_points = 50
    R_values = np.linspace(max(R_min, 0.1), R_max, n_points)

    # Log-normal distribution
    P_R = (1 / (R_values * sigma * np.sqrt(2 * np.pi))) * \
          np.exp(-0.5 * ((np.log(R_values) - mu) / sigma)**2)

    # Weight by volume (R³)
    V_weights = R_values**3
    P_R_normalized = P_R * V_weights
    P_R_normalized = P_R_normalized / np.trapz(P_R_normalized, R_values)

    # Calculate intensity
    intensity = np.zeros_like(q)
    for i, R in enumerate(R_values):
        intensity += P_R_normalized[i] * sphere_form_factor(q, R)

    # Normalize and add background
    intensity = I0 * intensity / intensity[0] + bkg

    return intensity

def polydisperse_sphere_intensity_fast(q, R_mean, sigma_R, I0, bkg):
    """
    Faster implementation using Gaussian quadrature
    """
    if sigma_R < 0.001:
        return I0 * sphere_form_factor(q, R_mean) + bkg

    # Log-normal parameters
    mu = np.log(R_mean) - 0.5 * np.log(1 + sigma_R**2)
    sigma_ln = np.sqrt(np.log(1 + sigma_R**2))

    # Integrate numerically
    n_R = 30
    R_min = max(R_mean * 0.1, R_mean * np.exp(-3 * sigma_ln))
    R_max = R_mean * np.exp(3 * sigma_ln)

    R_grid = np.linspace(R_min, R_max, n_R)

    # Log-normal PDF weighted by volume
    pdf = (1 / (R_grid * sigma_ln * np.sqrt(2 * np.pi))) * \
          np.exp(-0.5 * ((np.log(R_grid) - mu) / sigma_ln)**2)

    vol_weights = R_grid**3
    weighted_pdf = pdf * vol_weights
    weighted_pdf = weighted_pdf / np.trapz(weighted_pdf, R_grid)

    # Calculate intensity
    I_total = np.zeros_like(q)
    for i, R in enumerate(R_grid):
        I_total += weighted_pdf[i] * sphere_form_factor(q, R)

    return I0 * I_total + bkg

def fit_polydisperse_spheres(q, I, sigma, Rg_initial):
    """
    Fit SAXS data with polydisperse sphere model
    Returns: R_mean (nm), sigma_R (relative), I0, bkg, fit_quality
    """
    # Initial guesses
    # R_mean ≈ Rg * sqrt(5/3) for solid sphere
    R_mean_init = Rg_initial * np.sqrt(5/3)  # in nm

    # Filter data - use only positive intensity values
    valid = (I > 0) & (sigma > 0) & (q > 0)
    q_fit = q[valid]
    I_fit = I[valid]
    sigma_fit = sigma[valid]

    # Use subset for speed (keep more points for better fit)
    step = max(1, len(q_fit) // 150)
    q_fit = q_fit[::step]
    I_fit = I_fit[::step]
    sigma_fit = sigma_fit[::step]

    # Initial parameters
    I0_init = I_fit[np.argmin(q_fit)]  # I at lowest q
    bkg_init = np.median(I_fit[-20:]) if len(I_fit) > 20 else I_fit[-1]
    sigma_R_init = 0.20  # 20% polydispersity

    def model(q, R_mean, sigma_R, I0, bkg):
        return polydisperse_sphere_intensity_fast(q, R_mean, sigma_R, I0, bkg)

    # Bounds - allow wider range for better fitting
    bounds = ([0.3, 0.01, I0_init * 0.1, 0],
              [R_mean_init * 5, 0.8, I0_init * 10, I0_init * 0.5])

    try:
        popt, pcov = curve_fit(model, q_fit, I_fit,
                               p0=[R_mean_init, sigma_R_init, I0_init, bkg_init],
                               sigma=sigma_fit,
                               absolute_sigma=True,
                               bounds=bounds,
                               maxfev=10000,
                               method='trf')

        R_mean, sigma_R, I0, bkg = popt

        # Calculate fit quality
        I_calc = model(q_fit, *popt)
        residuals = (I_fit - I_calc) / sigma_fit
        chi2 = np.sum(residuals**2) / (len(q_fit) - 4)

        # R²
        ss_res = np.sum((I_fit - I_calc)**2)
        ss_tot = np.sum((I_fit - np.mean(I_fit))**2)
        r2 = 1 - ss_res / ss_tot

        # Get parameter uncertainties
        perr = np.sqrt(np.diag(pcov))

        return R_mean, sigma_R, I0, bkg, r2, chi2

    except Exception as e:
        print(f"Fitting failed: {e}")
        return None, None, None, None, None, None

def kratky_analysis(q, I, Rg, I0=None):
    """
    Dimensionless Kratky plot analysis
    For spheres: peak at q*Rg ≈ 1.73, height ≈ 1.1

    Uses I0 from Guinier fit for proper normalization
    """
    if I0 is None:
        I0 = I[0]  # Use first point as approximation

    qRg = q * Rg
    I_norm = I / I0  # Normalize by I(0)

    # Dimensionless Kratky: (q*Rg)² * I(q)/I(0)
    kratky = (qRg**2) * I_norm

    # Find peak (in reasonable range)
    valid = ~np.isnan(kratky) & ~np.isinf(kratky) & (qRg > 0.5) & (qRg < 4)
    if valid.sum() > 0:
        peak_idx = np.nanargmax(kratky[valid])
        peak_pos = qRg[valid][peak_idx]
        peak_height = kratky[valid][peak_idx]
    else:
        peak_pos, peak_height = None, None

    return kratky, qRg, peak_pos, peak_height

def porod_analysis(q, I, sigma):
    """
    Porod analysis for surface/interface characterization
    I(q) ∝ q⁻⁴ for sharp interfaces at high q
    """
    # High-q region
    valid = (I > 0) & (sigma > 0) & (q > q.mean())

    if valid.sum() < 10:
        return None, None, None

    q_h = q[valid]
    I_h = I[valid]
    sigma_h = sigma[valid]

    # Log-log fit
    log_q = np.log(q_h)
    log_I = np.log(I_h)

    try:
        coeffs = np.polyfit(log_q, log_I, 1)
        slope = coeffs[0]

        # Porod constant
        if slope < -3:
            # Porod region
            porod_const = I_h[-1] * q_h[-1]**4
        else:
            porod_const = None

        return slope, porod_const, (q_h, I_h)
    except:
        return None, None, None

def check_aggregation(q, I):
    """
    Check for aggregation via low-q behavior
    Power law: I(q) ∝ q⁻ᵅ
    α ≈ -4 for mass fractals (aggregates)
    α ≈ -2 to -3 for branching/aggregation
    """
    # Low-q region
    q_min = q.min()
    q_threshold = q_min * 3

    low_q = q[q <= q_threshold]
    low_I = I[q <= q_threshold]

    valid = low_I > 0
    if valid.sum() < 5:
        return None, None

    log_q = np.log(low_q[valid])
    log_I = np.log(low_I[valid])

    try:
        coeffs = np.polyfit(log_q, log_I, 1)
        slope = coeffs[0]

        # Interpret
        if slope > -2:
            aggregation = "Unlikely"
        elif slope > -2.5:
            aggregation = "Possible weak aggregation"
        elif slope > -3:
            aggregation = "Moderate aggregation likely"
        else:
            aggregation = "Strong aggregation/fractal structure"

        return slope, aggregation
    except:
        return None, None

def analyze_polydispersity(fit_sigma_R):
    """
    Analyze polydispersity from fitted relative standard deviation
    """
    if fit_sigma_R is None:
        return "Could not determine", None

    # Coefficient of variation
    CV = fit_sigma_R * 100  # in percent

    if CV < 5:
        pd_status = "Highly monodisperse"
    elif CV < 10:
        pd_status = "Monodisperse"
    elif CV < 20:
        pd_status = "Moderately polydisperse"
    elif CV < 30:
        pd_status = "Polydisperse"
    else:
        pd_status = "Highly polydisperse"

    return pd_status, CV

def determine_shape(kratky_peak_pos, kratky_peak_height, porod_slope, low_q_slope):
    """
    Determine most likely shape from Kratky plot characteristics

    For solid spheres:
    - Kratky peak position: ~1.73
    - Kratky peak height: ~1.1

    For polydisperse systems, peak height is lower and broader
    """
    reasons = []

    # Check Kratky peak
    if kratky_peak_height is not None:
        if 0.8 < kratky_peak_height < 1.3:
            if kratky_peak_pos is not None and 1.4 < kratky_peak_pos < 2.0:
                reasons.append("Kratky peak position and height consistent with spherical/compact particles")
            else:
                reasons.append("Kratky peak height suggests compact/spherical particles (lower peak may indicate polydispersity)")
        elif kratky_peak_height > 1.3:
            reasons.append("Elevated Kratky peak may indicate elongated/rod-like particles")
        elif kratky_peak_height < 0.8:
            reasons.append("Low Kratky peak may indicate flexible/branched structures or high polydispersity")

    # Check Porod slope
    if porod_slope is not None:
        if -4.5 < porod_slope < -3.5:
            reasons.append("Porod slope ≈ -4 indicates sharp particle interfaces (well-defined particles)")
        elif -3.5 < porod_slope < -2.5:
            reasons.append("Intermediate Porod slope suggests surface roughness or non-spherical shape")
        elif porod_slope > -2.5:
            reasons.append("Shallow Porod slope may indicate mass fractal or branched structures")

    # Check low-q behavior
    if low_q_slope is not None:
        if low_q_slope < -2.5:
            reasons.append("Low-q behavior suggests some aggregation or larger structures")

    # Synthesis - updated logic
    if kratky_peak_height is not None:
        if 0.85 < kratky_peak_height < 1.3 and (kratky_peak_pos is None or 1.4 < kratky_peak_pos < 2.0):
            shape = "Spherical or slightly aspherical"
            confidence = "Medium-High"
        elif 0.6 < kratky_peak_height <= 0.85:
            shape = "Spherical with high polydispersity"
            confidence = "Medium"
        elif kratky_peak_height > 1.3:
            shape = "Elongated/rod-like"
            confidence = "Medium"
        elif kratky_peak_height < 0.6:
            shape = "Possibly flexible or branched"
            confidence = "Low-Medium"
        else:
            shape = "Unknown - insufficient data"
            confidence = "Low"
    else:
        shape = "Unknown - insufficient data"
        confidence = "Low"

    return shape, confidence, reasons

def check_data_quality(q, I, sigma):
    """
    Assess data quality issues
    """
    issues = []
    mean_snr = None

    # Check for negative intensities
    neg_count = np.sum(I < 0)
    neg_frac = neg_count / len(I)
    if neg_frac > 0.1:
        issues.append(f"High fraction of negative intensities ({neg_frac*100:.1f}%) - buffer over-subtraction likely")
    elif neg_frac > 0.01:
        issues.append(f"Some negative intensities ({neg_frac*100:.1f}%) - minor buffer subtraction issues")

    # Check signal-to-noise
    snr = I / sigma
    valid_snr = snr[snr > 0]
    if len(valid_snr) > 0:
        mean_snr = np.mean(valid_snr)
        if mean_snr < 3:
            issues.append(f"Low signal-to-noise ratio (mean SNR = {mean_snr:.1f})")

    # Check for jumps
    if len(I) > 1:
        dI = np.abs(np.diff(np.log(np.abs(I) + 1)))
        if len(dI) > 0 and np.max(dI) > 2:
            issues.append("Large intensity jumps detected - possible detector artifacts")

    # Check q-range
    q_range = q.max() / q.min()
    if q_range < 10:
        issues.append(f"Limited q-range ({q_range:.1f} decades)")

    if len(issues) == 0:
        issues.append("No major data quality issues detected")

    return issues, neg_frac, mean_snr

def main():
    import os

    # Data files
    data_dir = "/home/z/my-project/upload/"
    files = [
        "sub_Pt_NPs_insitu_110C_00061_sample.dat.txt",
        "sub_Pt_NPs_insitu_110C_00062_sample.dat.txt",
        "sub_Pt_NPs_insitu_110C_00063_sample.dat.txt"
    ]

    # Load and average data
    all_q = []
    all_I = []
    all_sigma = []

    for f in files:
        filepath = os.path.join(data_dir, f)
        q, I, sigma = read_saxs_data(filepath)
        all_q.append(q)
        all_I.append(I)
        all_sigma.append(sigma)

    # Average across datasets
    q = all_q[0]  # q values should be same
    I_avg = np.mean(all_I, axis=0)
    sigma_avg = np.sqrt(np.mean(np.array(all_sigma)**2, axis=0))  # Propagate errors

    print("="*60)
    print("SAXS Analysis for Pt Nanoparticles at 110°C")
    print("q units: nm⁻¹ (corrected)")
    print("="*60)

    # 1. Data Quality Check
    print("\n1. DATA QUALITY ASSESSMENT")
    print("-" * 40)
    issues, neg_frac, snr = check_data_quality(q, I_avg, sigma_avg)
    for issue in issues:
        print(f"  • {issue}")

    # 2. Guinier Analysis
    print("\n2. GUINIER ANALYSIS (Rg determination)")
    print("-" * 40)
    Rg, I0, max_qRg, r2, q_guinier, I_guinier, slope, intercept = guinier_analysis(q, I_avg, sigma_avg)

    if Rg is not None:
        print(f"  Radius of Gyration (Rg): {Rg:.2f} nm")
        print(f"  Rg in Angstroms: {Rg*10:.2f} Å")
        print(f"  Forward scattering I(0): {I0:.2e}")
        print(f"  Max q·Rg: {max_qRg:.2f} (valid if < 1.3)")
        print(f"  Guinier fit R²: {r2:.4f}")

        # Estimated diameter for solid sphere: D = 2*R = 2*Rg*sqrt(5/3)
        D_sphere = 2 * Rg * np.sqrt(5/3)
        print(f"  Estimated diameter (sphere): {D_sphere:.2f} nm = {D_sphere*10:.2f} Å")
        guinier_success = True
    else:
        print("  Guinier analysis failed!")
        print("  Note: Limited low-q range and/or high polydispersity can cause Guinier failure")
        Rg = None  # Will be estimated from polydisperse fit
        I0 = I_avg[0]  # Use first data point as approximation
        r2 = None
        max_qRg = None
        slope = None
        intercept = None
        q_guinier = None
        I_guinier = None
        guinier_success = False

    # 3. Initial Kratky Analysis (will be updated after polydisperse fit if needed)
    print("\n3. KRATKY PLOT ANALYSIS (Shape)")
    print("-" * 40)

    # If Rg is not available, skip initial Kratky analysis
    if Rg is not None:
        kratky, qRg, peak_pos, peak_height = kratky_analysis(q, I_avg, Rg, I0)

        if peak_height is not None:
            print(f"  Kratky peak position (q·Rg): {peak_pos:.2f}")
            print(f"  Kratky peak height: {peak_height:.2f}")
            print(f"  Reference: Sphere = 1.73, 1.10")
        else:
            peak_pos, peak_height = None, None
    else:
        print("  Kratky analysis skipped (awaiting Rg from polydisperse fit)")
        kratky = None
        qRg = None
        peak_pos = None
        peak_height = None

    # 4. Porod Analysis
    print("\n4. POROD ANALYSIS (Surface)")
    print("-" * 40)
    porod_slope, porod_const, porod_data = porod_analysis(q, I_avg, sigma_avg)

    if porod_slope is not None:
        print(f"  Porod exponent: {porod_slope:.2f}")
        print(f"  (Sharp interface: -4, Rough surface: -3 to -4)")
    else:
        print("  Porod analysis not applicable")

    # 5. Aggregation Check
    print("\n5. AGGREGATION ANALYSIS")
    print("-" * 40)
    low_q_slope, aggregation_status = check_aggregation(q, I_avg)

    if low_q_slope is not None:
        print(f"  Low-q power law exponent: {low_q_slope:.2f}")
        print(f"  Aggregation status: {aggregation_status}")
    else:
        aggregation_status = "Unknown"
        print("  Could not determine aggregation status")

    # 6. Polydisperse Sphere Fitting
    print("\n6. POLYDISPERSE SPHERE MODEL FITTING")
    print("-" * 40)

    # Use a reasonable initial Rg if Guinier failed
    Rg_for_fit = Rg if Rg is not None else 1.5  # Reasonable guess for small Pt nanoparticles

    R_mean, sigma_R, I0_fit, bkg, r2_fit, chi2 = fit_polydisperse_spheres(q, I_avg, sigma_avg, Rg_for_fit)

    if R_mean is not None:
        print(f"  Mean radius: {R_mean:.2f} nm = {R_mean*10:.2f} Å")
        print(f"  Mean diameter: {2*R_mean:.2f} nm = {2*R_mean*10:.2f} Å")
        print(f"  Relative polydispersity (σR/R): {sigma_R:.3f} ({sigma_R*100:.1f}%)")
        print(f"  Scale factor I0: {I0_fit:.2e}")
        print(f"  Background: {bkg:.2e}")
        print(f"  Fit R²: {r2_fit:.4f}")
        print(f"  Reduced χ²: {chi2:.2f}")

        # Calculate Rg from polydisperse fit (for solid sphere: Rg = R * sqrt(3/5))
        Rg_from_fit = R_mean * np.sqrt(3/5)
        print(f"  Rg from fit: {Rg_from_fit:.2f} nm ({Rg_from_fit*10:.2f} Å)")

        # If Guinier failed, use Rg from polydisperse fit and redo Kratky
        if not guinier_success:
            Rg = Rg_from_fit
            I0 = I0_fit
            print(f"  (Using Rg from polydisperse fit for subsequent analysis)")

            # Redo Kratky analysis with updated Rg
            print("\n  Updated Kratky Analysis (using Rg from fit):")
            kratky, qRg, peak_pos, peak_height = kratky_analysis(q, I_avg, Rg, I0)

            if peak_height is not None:
                print(f"    Kratky peak position (q·Rg): {peak_pos:.2f}")
                print(f"    Kratky peak height: {peak_height:.2f}")
                print(f"    Reference: Sphere = 1.73, 1.10")

        # Polydispersity assessment
        pd_status, CV = analyze_polydispersity(sigma_R)
        print(f"  Polydispersity assessment: {pd_status}")
    else:
        print("  Polydisperse sphere fitting failed!")
        pd_status, CV = "Unknown", None

    # 7. Shape Determination
    print("\n7. SHAPE DETERMINATION")
    print("-" * 40)
    shape, confidence, reasons = determine_shape(peak_pos, peak_height, porod_slope, low_q_slope)

    print(f"  Most likely shape: {shape}")
    print(f"  Confidence: {confidence}")
    print("  Supporting evidence:")
    for r in reasons:
        print(f"    • {r}")

    # 8. Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    if Rg is not None:
        print(f"  Rg = {Rg:.2f} nm ({Rg*10:.2f} Å)")
    if R_mean is not None:
        print(f"  Particle diameter = {2*R_mean:.2f} nm ({2*R_mean*10:.2f} Å)")
        print(f"  Polydispersity = {sigma_R*100:.1f}% ({pd_status})")
    print(f"  Shape: {shape}")
    print(f"  Aggregation: {aggregation_status}")

    # Save results for report
    results = {
        'Rg_nm': Rg,
        'R_mean_nm': R_mean,
        'sigma_R': sigma_R,
        'I0': I0_fit if I0_fit is not None else I0,
        'r2_guinier': r2,
        'r2_fit': r2_fit,
        'kratky_peak_pos': peak_pos,
        'kratky_peak_height': peak_height,
        'porod_slope': porod_slope,
        'low_q_slope': low_q_slope,
        'shape': shape,
        'aggregation': aggregation_status,
        'pd_status': pd_status,
        'CV_percent': CV,
        'data_quality_issues': issues,
        'neg_frac': neg_frac
    }

    # Generate plots
    print("\nGenerating analysis plots...")

    fig, axes = plt.subplots(2, 3, figsize=(16, 10))

    # Plot 1: I(q) vs q (log-log)
    ax1 = axes[0, 0]
    ax1.loglog(q, I_avg, 'b.', markersize=2, alpha=0.7, label='Data')

    # Add polydisperse sphere fit
    if R_mean is not None:
        I_fit = polydisperse_sphere_intensity_fast(q, R_mean, sigma_R, I0_fit, bkg)
        ax1.loglog(q, I_fit, 'r-', linewidth=2, label=f'Polydisperse sphere fit\nR={R_mean:.2f}nm, σR/R={sigma_R:.2f}')

    ax1.set_xlabel('q (nm⁻¹)')
    ax1.set_ylabel('I(q) (a.u.)')
    ax1.set_title('SAXS Intensity Profile')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Plot 2: Guinier plot
    ax2 = axes[0, 1]
    if guinier_success and I_guinier is not None:
        valid_guinier = I_guinier > 0
        ax2.plot(q_guinier[valid_guinier]**2, np.log(I_guinier[valid_guinier]), 'b.', markersize=3)

        if slope is not None:
            q2_fit = np.linspace(0, q_guinier.max()**2, 100)
            lnI_fit = slope * q2_fit + intercept
            ax2.plot(q2_fit, lnI_fit, 'r-', linewidth=2,
                    label=f'Rg = {Rg:.2f} nm\nR² = {r2:.4f}')
            ax2.axvline(x=max_qRg**2/Rg**2, color='g', linestyle='--', label=f'q·Rg = {max_qRg:.2f}')
        ax2.legend()
    else:
        # Show all low-q data when Guinier failed
        valid_lowq = (I_avg > 0) & (q < 1.5)
        ax2.plot(q[valid_lowq]**2, np.log(I_avg[valid_lowq]), 'b.', markersize=3)
        ax2.text(0.5, 0.5, 'Guinier analysis failed\n(limited low-q range)',
                transform=ax2.transAxes, ha='center', fontsize=10)

    ax2.set_xlabel('q² (nm⁻²)')
    ax2.set_ylabel('ln[I(q)]')
    ax2.set_title('Guinier Plot')
    ax2.grid(True, alpha=0.3)

    # Plot 3: Dimensionless Kratky plot
    ax3 = axes[0, 2]
    ax3.plot(qRg, kratky, 'b.', markersize=2, alpha=0.7)

    if peak_height is not None:
        ax3.axhline(y=1.1, color='g', linestyle='--', alpha=0.7, label='Sphere reference (1.1)')
        ax3.axvline(x=1.73, color='g', linestyle='--', alpha=0.7, label='Sphere qRg=1.73')
        ax3.plot(peak_pos, peak_height, 'ro', markersize=10, label=f'Peak: ({peak_pos:.2f}, {peak_height:.2f})')

    ax3.set_xlabel('q·Rg')
    ax3.set_ylabel('(q·Rg)² × I(q)/I(0)')
    ax3.set_title('Dimensionless Kratky Plot')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.set_xlim(0, 5)
    ax3.set_ylim(0, max(2, kratky[~np.isnan(kratky)].max() * 0.8))

    # Plot 4: Residuals from polydisperse sphere fit
    ax4 = axes[1, 0]
    if R_mean is not None:
        I_calc = polydisperse_sphere_intensity_fast(q, R_mean, sigma_R, I0_fit, bkg)
        residuals = (I_avg - I_calc) / sigma_avg

        ax4.semilogx(q, residuals, 'b.', markersize=2, alpha=0.7)
        ax4.axhline(y=0, color='r', linestyle='-', linewidth=1)
        ax4.axhline(y=3, color='g', linestyle='--', alpha=0.7)
        ax4.axhline(y=-3, color='g', linestyle='--', alpha=0.7)

        ax4.set_xlabel('q (nm⁻¹)')
        ax4.set_ylabel('Residuals / σ')
        ax4.set_title('Fit Residuals')
        ax4.grid(True, alpha=0.3)
    else:
        ax4.text(0.5, 0.5, 'Fitting failed', transform=ax4.transAxes, ha='center')

    # Plot 5: Porod plot
    ax5 = axes[1, 1]
    valid_porod = I_avg > 0
    ax5.loglog(q[valid_porod]**4, I_avg[valid_porod] * q[valid_porod]**4, 'b.', markersize=2, alpha=0.7)

    if porod_slope is not None:
        ax5.set_xlabel('q⁴ (nm⁻⁴)')
        ax5.set_ylabel('I(q) × q⁴ (a.u.)')
        ax5.set_title(f'Porod Plot (slope = {porod_slope:.2f})')
    else:
        ax5.set_title('Porod Plot')
    ax5.grid(True, alpha=0.3)

    # Plot 6: Size distribution
    ax6 = axes[1, 2]
    if R_mean is not None and sigma_R is not None:
        # Log-normal distribution
        mu = np.log(R_mean) - 0.5 * np.log(1 + sigma_R**2)
        sigma_ln = np.sqrt(np.log(1 + sigma_R**2))

        R_plot = np.linspace(R_mean * 0.3, R_mean * 2.5, 200)
        P_R = (1 / (R_plot * sigma_ln * np.sqrt(2 * np.pi))) * \
              np.exp(-0.5 * ((np.log(R_plot) - mu) / sigma_ln)**2)

        ax6.plot(R_plot, P_R, 'b-', linewidth=2)
        ax6.axvline(x=R_mean, color='r', linestyle='--', label=f'Mean R = {R_mean:.2f} nm')
        ax6.fill_between(R_plot, P_R, alpha=0.3)

        ax6.set_xlabel('Radius (nm)')
        ax6.set_ylabel('Probability density')
        ax6.set_title(f'Size Distribution (σR/R = {sigma_R*100:.1f}%)')
        ax6.legend()
        ax6.grid(True, alpha=0.3)
    else:
        ax6.text(0.5, 0.5, 'Size distribution unavailable', transform=ax6.transAxes, ha='center')

    plt.tight_layout()
    plt.savefig('/home/z/my-project/download/saxs_analysis_corrected.png', dpi=150, bbox_inches='tight')
    print("Plots saved to: /home/z/my-project/download/saxs_analysis_corrected.png")

    # Save results
    import json
    with open('/home/z/my-project/saxs_results.json', 'w') as f:
        # Convert numpy types to native Python types
        def convert(obj):
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, (np.float32, np.float64)):
                return float(obj)
            elif isinstance(obj, (np.int32, np.int64)):
                return int(obj)
            elif obj is None:
                return None
            return obj

        results_serializable = {k: convert(v) for k, v in results.items()}
        json.dump(results_serializable, f, indent=2)

    print("\nResults saved to: /home/z/my-project/saxs_results.json")

    return results

if __name__ == "__main__":
    results = main()

