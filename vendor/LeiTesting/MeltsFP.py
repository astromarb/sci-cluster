from thermoengine import core, phases, model, equilibrate
import time
import copy

import xml.etree.ElementTree as ET
import locale
from openpyxl import Workbook
from io import BytesIO
from PIL import Image as PILImage
from openpyxl.utils import get_column_letter
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # ensure non-interactive backend for reliable image rendering
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import signal
import threading
import logging
from functools import wraps
try:
    from futureproof import TaskManager, ThreadPoolExecutor as FutureproofThreadPoolExecutor
    from futureproof.task_manager import ErrorPolicyEnum
except Exception:
    # futureproof is optional; this fallback keeps behavior working with stdlib futures.
    FutureproofThreadPoolExecutor = ThreadPoolExecutor

    class ErrorPolicyEnum:
        RAISE = "RAISE"

    class TaskManager:
        def __init__(self, executor, error_policy=None):
            self._executor = executor
            self._tasks = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, exc_tb):
            self._executor.shutdown(wait=True)

        def submit(self, func, *args, **kwargs):
            task = self._executor.submit(func, *args, **kwargs)
            self._tasks.append(task)
            return task

        def as_completed(self):
            for task in as_completed(self._tasks):
                yield task


def _task_result(task):
    result_attr = getattr(task, "result", None)
    if callable(result_attr):
        return result_attr()
    if result_attr is not None:
        return result_attr
    return task


ZERO_EPSILON = 1e-10


def _sanitize_numeric_for_melts(value):
    """Return float value for MELTS, preserving blanks and nudging true zeros."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.isna(numeric):
        return None
    numeric = float(numeric)
    return ZERO_EPSILON if numeric == 0.0 else numeric


def _extract_composition_dict(series):
    """Build a composition dict from the first 15 oxide rows without zero-filling blanks."""
    out = {}
    numeric = pd.to_numeric(series.iloc[:15], errors="coerce")
    for oxide, value in numeric.items():
        clean = _sanitize_numeric_for_melts(value)
        if clean is not None:
            out[str(oxide)] = clean
    return out


def _parse_required_float(series, key, *, zero_to_epsilon=False):
    if key not in series.index:
        raise ValueError(f"Missing required parameter row: {key}")
    clean = _sanitize_numeric_for_melts(series[key])
    if clean is None:
        raise ValueError(f"Parameter '{key}' is blank/invalid.")
    if not zero_to_epsilon and clean == ZERO_EPSILON:
        return 0.0
    return float(clean)

# Configure logging for MELTS calculations
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler('melts_calculations.log'),
        logging.StreamHandler()
    ]
)

class TimeoutError(Exception):
    """Custom timeout exception"""
    pass
def timeout_handler(func, timeout_duration=30):
    """
    Timeout wrapper for thermoengine equilibrium calculations.
    Uses threading to provide robust timeout protection.
    
    Args:
        func: Function to execute with timeout protection
        timeout_duration: Maximum time to wait in seconds (default: 30)
    
    Returns:
        Function result or raises TimeoutError
    """
    def wrapper(*args, **kwargs):
        result = [None]
        exception = [None]
        
        def target():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                exception[0] = e
        
        thread = threading.Thread(target=target)
        thread.daemon = True
        thread.start()
        thread.join(timeout_duration)
        
        if thread.is_alive():
            # Force thread termination (not ideal but necessary for hanging calculations)
            logging.warning(f"Timeout occurred in {func.__name__} after {timeout_duration} seconds")
            raise TimeoutError(f"Function {func.__name__} timed out after {timeout_duration} seconds")
        
        if exception[0]:
            raise exception[0]
        
        return result[0]
    
    return wrapper

def safe_equilibrium_execute(equil, T, P, timeout=30, **kwargs):
    """
    Safe wrapper for equil.execute() with timeout protection and better error handling.
    
    Args:
        equil: Equilibrium object
        T: Temperature in Kelvin
        P: Pressure in bars
        timeout: Timeout in seconds (default: 30)
        **kwargs: Additional arguments for equil.execute()
    
    Returns:
        Equilibrium state or None if timeout/error occurs
    """
    try:
        # Create timeout-protected version of execute
        protected_execute = timeout_handler(equil.execute, timeout)
        
        # Log the calculation attempt
        logging.debug(f"Attempting equilibrium calculation: T={T:.1f}K, P={P:.1f}bar")
        
        # Execute with timeout protection
        state = protected_execute(T, P, **kwargs)
        
        # Validate the result
        if state is None:
            raise RuntimeError("Equilibrium calculation returned None")
            
        return state
        
    except TimeoutError as e:
        logging.warning(f"Equilibrium calculation timed out: T={T:.1f}K, P={P:.1f}bar - {str(e)}")
        return None
        
    except Exception as e:
        logging.warning(f"Equilibrium calculation failed: T={T:.1f}K, P={P:.1f}bar - {str(e)}")
        return None


def _build_model_database():
    """Compatibility wrapper for classic thermoengine and ThermoEngineLite."""
    try:
        return model.Database(liq_mod='v1.0')
    except TypeError:
        return model.Database(database_name='MELTS_v1_0')


def _get_phase_compat(model_db, phase_code: str, fallback_code: str | None = None):
    try:
        return model_db.get_phase(phase_code)
    except Exception:
        if fallback_code is None:
            raise
        return model_db.get_phase(fallback_code)


def _build_water_phase(model_db):
    try:
        return model_db.get_phase('H2O')
    except Exception:
        return phases.PurePhase('WaterMelts', 'H2O', calib=False)


def _safe_state_property(state, phase_name, prop_name, default=np.nan):
    try:
        return state.properties(phase_name=phase_name, props=prop_name)
    except Exception:
        return default


def _safe_state_oxide_comp(state, phase_name):
    try:
        return state.oxide_comp(phase_name)
    except Exception:
        return {}

def parabola(x, a, b, c):
    return a * x**2 + b * x + c

def _excel_io_from_filename_or_wb(filename=None, wb=None):
    """
    Normalize Excel input to something pandas can read.

    If wb is an openpyxl Workbook, convert it to an in-memory file-like
    object. Otherwise, return the filename/path unchanged.
    """
    if wb is None:
        return filename
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

def pressure_calc(filename=None, wb=None, residual_threshold=5.0, embed_plot=True):
    """
    Estimate pressure from Excel data using residual curves and parabolic fitting.

    Parameters:
        filename (str): Excel file with sheets: 'quartz', 'feldspar', 'feldspar_1', 'init_cond'.
        residual_threshold (float): Max acceptable residual to accept pressure estimate.
        show_plot (bool): Whether to show TP grids and residual plots.

    Returns:
        Tuple (P2, P3): each a tuple (P_est, residual_min, (a, b, c))
    """
    # Load Excel data (support filename or openpyxl Workbook)
    phases = ['quartz', 'feldspar', 'feldspar_1', 'init_cond']
    io_obj = _excel_io_from_filename_or_wb(filename=filename, wb=wb)
    xls = pd.ExcelFile(io_obj, engine='openpyxl')
    dfs = {
        phase: pd.read_excel(xls, sheet_name=phase, header=None if phase == 'init_cond' else 0)
        for phase in phases
    }

    # Read pressure range from init_cond sheet
    p1 = dfs['init_cond'].iloc[4, 4]
    p2 = dfs['init_cond'].iloc[5, 4]
    dp = dfs['init_cond'].iloc[6, 4]
    pressures = np.arange(p2, p1 + 1, dp)

    # Build results DataFrame: T vs Pressure for each phase
    results = pd.DataFrame(index=pressures)
    for phase in ['quartz', 'feldspar', 'feldspar_1']:
        df = dfs[phase]
        temps = []
        for p in pressures:
            match = df[df['P (MPa)'] == p]
            if not match.empty:
                temps.append(match['T (C)'].iloc[0])
            else:
                temps.append(np.nan)
        results[phase] = temps

    results.index.name = 'Pressure'

    delta_2, delta_3 = [], []
    for p in pressures:
        temps = results.loc[p].dropna().values
        sorted_temps = np.sort(temps)[::-1]

        delta_3.append(sorted_temps[0] - sorted_temps[2] if len(sorted_temps) >= 3 else np.nan)
        delta_2.append(sorted_temps[0] - sorted_temps[1] if len(sorted_temps) >= 2 else np.nan)

    delta_2 = np.array(delta_2)
    delta_3 = np.array(delta_3)

    def fit_and_plot(P, residuals, title, subplot_ax=None):
        if np.nanmin(residuals) > residual_threshold:
            print(f"Residual at minimum too high for {title.lower()}: {np.nanmin(residuals):.2f} °C")
            return (None, None, (np.nan, np.nan, np.nan))

        idx_min = np.nanargmin(residuals)
        idxs = np.arange(max(0, idx_min - 2), min(len(P), idx_min + 3))
        if len(idxs) < 3:
            print(f"Not enough points for fit for {title.lower()}")
            return (None, None, (np.nan, np.nan, np.nan))

        P_fit = P[idxs]
        R_fit = residuals[idxs]

        try:
            popt, _ = curve_fit(parabola, P_fit, R_fit)
            a, b, c = popt
            P_est = -b / (2 * a)
            R_min = parabola(P_est, *popt)

            if embed_plot and subplot_ax is not None:
                subplot_ax.plot(P, residuals, 'o-', label='Residuals')
                x_fit = np.linspace(P_fit.min() - 5, P_fit.max() + 5, 300)
                y_fit = parabola(x_fit, *popt)
                subplot_ax.plot(x_fit, y_fit, 'r--', label='Parabola fit')
                subplot_ax.axvline(P_est, color='red', linestyle='--', label=f'P_est = {P_est:.1f} MPa')
                subplot_ax.axhline(R_min, color='gray', linestyle='--', label=f'Residual = {R_min:.1f} °C')
                subplot_ax.plot(P_fit, R_fit, 'bo', label='Fit points')
                subplot_ax.plot(P_est, R_min, 'go', label='Estimated min')
                subplot_ax.set_ylabel('Residual (°C)')
                subplot_ax.set_title(f'{title} Residuals and Fit')
                subplot_ax.grid(True)
                subplot_ax.legend()

            return (P_est, R_min, (a, b, c))

        except Exception as e:
            print(f"Fit failed for {title.lower()}: {e}")
            return (None, None, (np.nan, np.nan, np.nan))

    if embed_plot:
        fig, axs = plt.subplots(2, 2, figsize=(12, 10), sharex=True)

        # Top left: 2-phase TP grid
        axs[0, 0].set_title('2-Phase Temperature-Pressure')
        axs[0, 0].set_ylabel('Temperature (°C)')
        for col in results.columns[:2]:
            axs[0, 0].plot(pressures, results[col], label=col)
        axs[0, 0].legend()
        axs[0, 0].grid(True)

        # Bottom left: 2-phase residuals
        P2 = fit_and_plot(pressures, delta_2, "2-Phase", axs[1, 0])

        # Top right: 3-phase TP grid
        axs[0, 1].set_title('3-Phase Temperature-Pressure')
        for col in results.columns[:3]:
            axs[0, 1].plot(pressures, results[col], label=col)
        axs[0, 1].legend()
        axs[0, 1].grid(True)

        # Bottom right: 3-phase residuals
        P3 = fit_and_plot(pressures, delta_3, "3-Phase", axs[1, 1])

        axs[1, 0].set_xlabel('Pressure (MPa)')
        axs[1, 1].set_xlabel('Pressure (MPa)')
        plt.tight_layout()
        plt.show()
    else:
        P2 = fit_and_plot(pressures, delta_2, "2-Phase")
        P3 = fit_and_plot(pressures, delta_3, "3-Phase")
    # Add a new worksheet for pressure analysis results
    pressure_sheet = wb.create_sheet("Pressure Analysis")
    
    # Set up headers
    headers = [
        "Phase System", "Estimated Pressure (MPa)", "Minimum Residual (°C)", 
        "a (quadratic)", "b (linear)", "c (constant)"
    ]
    for col, header in enumerate(headers, 1):
        pressure_sheet.cell(row=1, column=col, value=header)
    
    # Write 2-phase results
    pressure_sheet.cell(row=2, column=1, value="2-Phase")
    if P2[0] is not None:  # Check if fit was successful
        pressure_sheet.cell(row=2, column=2, value=P2[0])
        pressure_sheet.cell(row=2, column=3, value=P2[1])
        pressure_sheet.cell(row=2, column=4, value=P2[2][0])
        pressure_sheet.cell(row=2, column=5, value=P2[2][1])
        pressure_sheet.cell(row=2, column=6, value=P2[2][2])
    else:
        pressure_sheet.cell(row=2, column=2, value="Fit Failed")
    
    # Write 3-phase results  
    pressure_sheet.cell(row=3, column=1, value="3-Phase")
    if P3[0] is not None:  # Check if fit was successful
        pressure_sheet.cell(row=3, column=2, value=P3[0])
        pressure_sheet.cell(row=3, column=3, value=P3[1])
        pressure_sheet.cell(row=3, column=4, value=P3[2][0])
        pressure_sheet.cell(row=3, column=5, value=P3[2][1])
        pressure_sheet.cell(row=3, column=6, value=P3[2][2])
    else:
        pressure_sheet.cell(row=3, column=2, value="Fit Failed")
    
    # If plots were generated, save them to the workbook
    if embed_plot:
        from openpyxl.drawing.image import Image as XLImage
        fig = plt.gcf()
        # Ensure the figure is fully rendered
        try:
            fig.canvas.draw()
        except Exception:
            pass
        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        # Reopen via PIL and normalize mode to avoid transparency issues
        pil_img = PILImage.open(buf)
        if pil_img.mode in ('RGBA', 'P'):
            pil_img = pil_img.convert('RGB')
        
        # Save PIL image to BytesIO buffer for openpyxl
        img_buf = BytesIO()
        pil_img.save(img_buf, format='PNG')
        img_buf.seek(0)
        
        # Create openpyxl Image from buffer
        img = XLImage(img_buf)
        pressure_sheet.add_image(img, 'A5')
        plt.close(fig)
    return wb, P2, P3

# find_wet_liquidus_xml
# function to calculate wet liquidus of a composition at a given pressure. Function utilizes binary search. 
# earlier xml version (deprecated) from equilibrate
# params:
#   T1: max temperature
#   T2: min temperature
#   P: pressure of interest
#   n: maximum number of iterations before giving up
#   composition: composition of system
#   MELTS_Model: version of MELTS used in calculations

def find_wet_liquidus_xml(equil, T1, T2, P, n, composition, MELTS_Model):
    melts_liquidus = equilibrate.MELTSmodel(MELTS_Model)
    feasible = melts_liquidus.set_bulk_composition(composition)
    b = melts_liquidus.get_phase_inclusion_status()
    melts_liquidus.set_phase_inclusion_status({'Nepheline':False, 'Biotite':False})
    a = melts_liquidus.get_phase_inclusion_status()
    for phase in b.keys():
        if b[phase] != a[phase]:
            print ("{0:<15s} Before: {1:<5s} After: {2:<5s}".format(phase, repr(b[phase]), repr(a[phase])))
    current_T = round((T1+T2)/2)
    i = 0
    print(current_T, P)
    while (T1 > T2) and i < 50:
        output = melts_liquidus.equilibrate_tp(current_T, P)
        (_, _, _, xmlout1) = output[0]
        phases = melts_liquidus.get_list_of_phases_in_assemblage(xmlout1)
        if 'Water' in phases:
            min_phases = 2
        else:
            min_phases = 1
        if(len(phases) > min_phases):
            (_, _, _, xmlout2) = melts_liquidus.equilibrate_tp(current_T+1, P)[0]
            phases = melts_liquidus.get_list_of_phases_in_assemblage(xmlout2)
            if (len(phases) == min_phases):
                print(phases)
                return current_T
            temp = current_T+1
            current_T = round((temp+T1)/2)
            T2 = temp
        else:
            temp = current_T-1
            current_T = round((temp+T2)/2)
            T1 = temp
        i = i + 1
    return T1

# find_wet_liquidus
# function to calculate wet liquidus of a composition at a given pressure. Function utilizes binary search
# params:
#   T1: max temperature
#   T2: min temperature
#   P: pressure of interest
#   n: maximum number of iterations before giving up
#   composition: composition of system
#   f02_offset: initial f02 value to be constrained
def find_wet_liquidus(equil, T1, T2, P, n, composition, fO2_offset, verbose=False):
    if T1 < T2:
        T_temp = T1
        T1 = T2
        T2 = T_temp
    current_T = round((T1+T2)/2)
    i = 0
    max_iterations = 50
    timeout = 10
    dbg = 0
    if verbose:
        print(f"Finding liquidus: T range {T2}-{T1}°C, P={P} MPa")
        print("Initial composition:")
        dbg = 1
 
            
    # compute initial state, and perform same checks as loop below
    try:
        if verbose:
            logging.info(f"Computing initial state at T={current_T}°C, P={P} MPa")
        state = safe_equilibrium_execute(equil, current_T+273.15, P*10, timeout=timeout, 
                                       bulk_comp=composition, con_deltaNNO=fO2_offset, debug=dbg)
        
        if state is None:
            logging.error(f"Initial equilibrium calculation failed or timed out at T={current_T}°C")
            return T1  # Return fallback temperature
            
    except Exception as e:
        logging.error(f"Initial calculation error: {e}")
        return T1

    phases = state.properties()
    phases = [i for i in phases.keys() if phases[i] == 'stable']
    phases.remove('System')
    if 'Water' in phases:
        min_phases = 2
    else:
        min_phases = 1
    if(len(phases) > min_phases):
        try:

            if verbose: 
                logging.info(f"Computing initial state at T={current_T}°C, P={P} MPa")
            state = safe_equilibrium_execute(equil, current_T+1+273.15, P*10, timeout=timeout, 
                                        state=state, con_deltaNNO=fO2_offset, debug=dbg)
            
            if state is None:
                logging.error(f"Initial equilibrium calculation failed or timed out at T={current_T}°C")
                return T1  # Return fallback temperature
                
        except Exception as e:
            logging.error(f"Initial calculation error: {e}")
            return T1
        
        phases = state.properties()
        phases = [i for i in phases.keys() if phases[i] == 'stable']
        if (len(phases) == min_phases):
            print(phases)
            return current_T
        temp = current_T+1
        current_T = round((temp+T1)/2)
        T2 = temp
    else:
        temp = current_T-1
        current_T = round((temp+T2)/2)
        T1 = temp

    while (T1 > T2) and i < 50:

        try:
            if verbose:
                logging.info(f"Computing state at T={current_T}°C, P={P} MPa")
            state = safe_equilibrium_execute(equil, current_T+273.15, P*10, timeout=timeout, 
                                        state=state, con_deltaNNO=fO2_offset, debug=dbg)
            
            if state is None:
                logging.error(f"Equilibrium calculation failed or timed out at T={current_T}°C")
                current_T = current_T+25  # continue with higher temperature
                continue
            #state = equil.execute(current_T+273.15, P*10, state=state, con_deltaNNO=fO2_offset, debug=1)
            phases = state.properties()
            phases = [i for i in phases.keys() if phases[i] == 'stable']
            phases.remove('System')
            if 'Water' in phases:
                min_phases = 2
            else:
                min_phases = 1
            if(len(phases) > min_phases):
                state = safe_equilibrium_execute(equil, current_T+1+273.15, P*10, state=state,timeout=timeout, 
                                         con_deltaNNO=fO2_offset, debug=dbg)
            
                if state is None:
                    logging.error(f"Equilibrium calculation failed or timed out at T={current_T}°C")
                    return T1  # Return fallback temperature
                #state = equil.execute(current_T+1+273.15, P*10, state=state, con_deltaNNO=fO2_offset, debug=1)
                phases = state.properties()
                phases = [i for i in phases.keys() if phases[i] == 'stable']
                phases.remove('System')
                if (len(phases) == min_phases):
                    return current_T+1
                temp = current_T+1
                current_T = round((temp+T1)/2)
                T2 = temp
            else:
                temp = current_T-1
                current_T = round((temp+T2)/2)
                T1 = temp
            i = i + 1
        except Exception as e:
            print(e)
            return T1
    return T1
    
# call_melts_tp_sequence_xml
# function to calculate TP sequence or QFP Calc using xml version of equilibrate (deprecated)
# params:
#   T1: max temperature
#   T2: min temperature
#   delta_T: temperature change
#   P1: max pressure
#   P2: min pressure
#   delta_P: pressure change
#   const_f02: boolean if f02 is constrained
#   f02Path: what regime to be used (currently equilibrate only uses NNO)
#   f02_offset: initial f02 value to be constrained
#   find_liq: boolean to specify if wet liquidus should be found
#   liq_fr: liquid fraction that should be reched before calculations conclude
#   params: list of values required for writing (includes initial conditions, compositions)
#   wb: excel workbook that will be written to
#   index: current index of excel workbook so that continuity may be maintained as pressure changes (specific to QFP_calc)
#   composition: composition of system in % oxides
def call_melts_tp_sequence_xml(equil, T1, T2, delta_T, P1, P2, delta_P, const_fO2, fO2Path, f02_offset, find_liq, liq_fr, params, wb, index, composition):
    if T1 < T2:
        T_temp = T1
        T1 = T2
        T2 = T_temp

    if P1 < P2:
        P_temp = P1
        P1 = P2
        P2 = P_temp
        
    N_runs_T = abs(T1 - T2) / delta_T
    N_runs_P = abs(P1 - P2) / delta_P
    if N_runs_P > N_runs_T:
        N_runs = N_runs_P 
    else:
        N_runs = N_runs_T
    N_runs = int(N_runs)
    print('# runs:', N_runs)
    
    calc_time = 0
    write_time = 0
    unused = 0
    temp = T1
    pressure = P1
    if composition != None:
        melts_cur = equilibrate.MELTSmodel(params[10])
        feasible = melts_cur.set_bulk_composition(composition)
        b = melts_cur.get_phase_inclusion_status()
        melts_cur.set_phase_inclusion_status({'Nepheline':False, 'Biotite':False})
        a = melts_cur.get_phase_inclusion_status()
        for phase in b.keys():
            if b[phase] != a[phase]:
                print ("{0:<15s} Before: {1:<5s} After: {2:<5s}".format(phase, repr(b[phase]), repr(a[phase])))
    else:
        melts_cur = self
    solidus = False
    if find_liq == True:
        temp = find_wet_liquidus_xml(melts_cur, T1, T2, P1, 50, composition, params[10])
    while solidus == False and temp >= T2:
        t0 = time.time()
        current = melts_cur.equilibrate_tp(temp, pressure)
        t1 = time.time()
        calc_time = calc_time + (t1-t0)
        (status, t, p, xmlout) = current[0]
        t0 = time.time()
        system_mass = melts_cur.get_property_of_phase(xmlout, "System", "Mass")-melts_cur.get_property_of_phase(xmlout, "Water", "Mass")
        liquid_mass = melts_cur.get_property_of_phase(xmlout, "Liquid", "Mass")
        if liquid_mass/system_mass < liq_fr:
            unused = unused + temp-700
            solidus = True
        update_melts_excel_output_workbook_xml(melts_cur, wb, xmlout, index, status, params)
        t1 = time.time()
        write_time = write_time +(t1-t0)
        index = index+1
        temp = temp - abs(T2-T1)/N_runs
        pressure = pressure - abs(P2-P1)/N_runs
    return (index, unused, calc_time, write_time)

# call_melts_tp_sequence
# function to calculate TP sequence or QFP Calc
# params:
#   T1: max temperature
#   T2: min temperature
#   delta_T: temperature change
#   P1: max pressure
#   P2: min pressure
#   delta_P: pressure change
#   const_f02: boolean if f02 is constrained
#   f02Path: what regime to be used (currently equilibrate only uses NNO)
#   f02_offset: initial f02 value to be constrained
#   find_liq: boolean to specify if wet liquidus should be found
#   liq_fr: liquid fraction that should be reched before calculations conclude
#   params: list of values required for writing (includes initial conditions, compositions)
#   wb: excel workbook that will be written to
#   index: current index of excel workbook so that continuity may be maintained as pressure changes (specific to QFP_calc)\
#   blk_cmp: bulk composition in moles required for calculation
#   comp_dict: composition represent in % oxide for writing
def call_melts_tp_sequence(equil, T1, T2, delta_T, P1, P2, delta_P, const_fO2, fO2Path, fO2_offset, find_liq, liq_fr, params, wb, index, blk_cmp, comp_dict):

    if T1 < T2:
        T_temp = T1
        T1 = T2
        T2 = T_temp

    if P1 < P2:
        P_temp = P1
        P1 = P2
        P2 = P_temp
        
    N_runs_T = abs(T1 - T2) / delta_T
    N_runs_P = abs(P1 - P2) / delta_P
    if N_runs_P > N_runs_T:
        N_runs = N_runs_P 
    else:
        N_runs = N_runs_T
    N_runs = int(N_runs)
    print('# runs:', N_runs,find_liq)
    
    # working on this implementation
    calc_time = 0
    write_time = 0
    unused = 0
    temp = T1
    pressure = P1
    solidus = False
    if find_liq == True:
        temp = find_wet_liquidus(equil, T1, T2, P1, 50, blk_cmp, fO2_offset)
    # compute initial state
    t0 = time.time()
    state = equil.execute(temp+273.15, pressure*10, bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)
    print('Initial State')
    t1 = time.time()
    calc_time = calc_time + (t1-t0)
    print(calc_time)
    t0 = time.time()
    system_mass = state.properties(phase_name="System", props="Mass")-state.properties(phase_name="Water", props="Mass")
    liquid_mass = state.properties(phase_name="Liquid", props="Mass")
    if liquid_mass/system_mass < liq_fr:
        unused = unused + temp-700
        solidus = True
    params.append(comp_dict)
    update_melts_excel_output_workbook(state, wb, index, params)
    t1 = time.time()
    write_time = write_time +(t1-t0)
    index = index+1
    temp = temp - abs(T2-T1)/N_runs
    pressure = pressure - abs(P2-P1)/N_runs
    # loop through rest of states
    while solidus == False and temp >= T2:
        t0 = time.time()
        state = equil.execute(temp+273.15, pressure*10, state=state, con_deltaNNO=fO2_offset, debug=0)
        t1 = time.time()
        calc_time = calc_time + (t1-t0)
        t0 = time.time()
        system_mass = state.properties(phase_name="System", props="Mass")-state.properties(phase_name="Water", props="Mass")
        liquid_mass = state.properties(phase_name="Liquid", props="Mass")
        if liquid_mass/system_mass < liq_fr:
            unused = unused + temp-700
            solidus = True
        params.append(comp_dict)
        update_melts_excel_output_workbook(state, wb, index, params)
        t1 = time.time()
        write_time = write_time +(t1-t0)
        index = index+1
        temp = temp - abs(T2-T1)/N_runs
        pressure = pressure - abs(P2-P1)/N_runs
    return (index, unused, calc_time, write_time)

def extract_state_data(state, temperature, pressure):
    """Extract all relevant data from state object"""
    
    def get_phase_properties(phase_name):
        """Get all thermodynamic properties for a given phase"""
        try:
            return {
                'Mass': state.properties(phase_name=phase_name, props="Mass"),
                'Density': state.properties(phase_name=phase_name, props="Density"),
                'GibbsFreeEnergy': state.properties(phase_name=phase_name, props="GibbsFreeEnergy"),
                'Enthalpy': state.properties(phase_name=phase_name, props="Enthalpy"),
                'Entropy': state.properties(phase_name=phase_name, props="Entropy"),
                'HeatCapacity': state.properties(phase_name=phase_name, props="HeatCapacity"),
                'Volume': state.properties(phase_name=phase_name, props="Volume"),
                'DvDt': state.properties(phase_name=phase_name, props="DvDt"),
                'DvDp': state.properties(phase_name=phase_name, props="DvDp"),
                'D2vDt2': state.properties(phase_name=phase_name, props="D2vDt2"),
                'D2vDp2': state.properties(phase_name=phase_name, props="D2vDp2"),
                'D2vDtDp': state.properties(phase_name=phase_name, props="D2vDtDp"),
            }
        except:
            # Return None values if properties can't be accessed
            return {
                'Mass': None, 'Density': None, 'GibbsFreeEnergy': None,
                'Enthalpy': None, 'Entropy': None, 'HeatCapacity': None,
                'Volume': None, 'DvDt': None, 'DvDp': None,
                'D2vDt2': None, 'D2vDp2': None, 'D2vDtDp': None
            }
    
    def get_phase_composition(phase_name):
        """Get oxide composition for a phase if available"""
        try:
            return state.oxide_comp(phase_name)
        except:
            return {}
    
    # Get all stable phases (excluding System which is handled separately)
    all_phases = state.properties()
    stable_phases = [phase for phase, status in all_phases.items() if status == 'stable']
    
    # Build the data structure (always include System in assemblage)
    phase_assemblage = ['System'] + stable_phases if 'System' not in stable_phases else stable_phases
    data = {
        'temperature': temperature,
        'pressure': pressure,
        'phase_assemblage': phase_assemblage,
        'phases': {}
    }
    
    # Always include System properties (it's always present but not in the phase dictionary)
    data['phases']['System'] = {
        'properties': get_phase_properties('System'),
        'composition': {}  # System doesn't have oxide composition
    }
    
    # Extract data for all stable phases (excluding System since we handled it above)
    for phase_name in stable_phases:
        if phase_name != 'System':  # Skip System since we already handled it
            data['phases'][phase_name] = {
                'properties': get_phase_properties(phase_name),
                'composition': get_phase_composition(phase_name)
            }
    
    return data


# call_melts_tp_sequence
# function to calculate TP sequence or QFP Calc
# params:
#   T1: max temperature
#   T2: min temperature
#   delta_T: temperature change
#   P1: max pressure
#   P2: min pressure
#   delta_P: pressure change
#   const_f02: boolean if f02 is constrained
#   f02Path: what regime to be used (currently equilibrate only uses NNO)
#   f02_offset: initial f02 value to be constrained
#   find_liq: boolean to specify if wet liquidus should be found
#   liq_fr: liquid fraction that should be reched before calculations conclude
#   params: list of values required for writing (includes initial conditions, compositions)
#   wb: excel workbook that will be written to
#   index: current index of excel workbook so that continuity may be maintained as pressure changes (specific to QFP_calc)\
#   blk_cmp: bulk composition in moles required for calculation
#   comp_dict: composition represent in % oxide for writing
def call_melts_tp_sequence_parallel(equil, T1, T2, delta_T, P1, P2, delta_P, const_fO2, fO2Path, fO2_offset, find_liq, liq_fr, params, index, blk_cmp, comp_dict):

    if T1 < T2:
        T_temp = T1
        T1 = T2
        T2 = T_temp

    if P1 < P2:
        P_temp = P1
        P1 = P2
        P2 = P_temp
        
    N_runs_T = abs(T1 - T2) / delta_T
    N_runs_P = abs(P1 - P2) / delta_P
    if N_runs_P > N_runs_T:
        N_runs = N_runs_P 
    else:
        N_runs = N_runs_T
    N_runs = int(N_runs)
    print('# runs:', N_runs,find_liq)
    
    # working on this implementation
    states = []
    calc_time = 0
    write_time = 0
    unused = 0
    temp = T1
    pressure = P1
    solidus = False
    if find_liq == True:
        temp = find_wet_liquidus(equil, T1, T2, P1, 50, blk_cmp, fO2_offset)
    # compute initial state
    t0 = time.time()
    state = equil.execute(temp+273.15, pressure*10, bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)
    states.append(extract_state_data(state, temp, pressure))
    
    print('Initial State')
    t1 = time.time()
    calc_time = calc_time + (t1-t0)
    print(calc_time)
    t0 = time.time()
    system_mass = state.properties(phase_name="System", props="Mass")-state.properties(phase_name="Water", props="Mass")
    liquid_mass = state.properties(phase_name="Liquid", props="Mass")
    if liquid_mass/system_mass < liq_fr:
        unused = unused + temp-700
        solidus = True
    params.append(comp_dict)
    #update_melts_excel_output_workbook(state, wb, index, params)
    t1 = time.time()
    write_time = write_time +(t1-t0)
    index = index+1
    temp = temp - abs(T2-T1)/N_runs
    pressure = pressure - abs(P2-P1)/N_runs
    # loop through rest of states
    while solidus == False and temp >= T2:
        t0 = time.time()
        state = equil.execute(temp+273.15, pressure*10, state=state, con_deltaNNO=fO2_offset, debug=0)
        states.append(extract_state_data(state, temp, pressure))
        t1 = time.time()
        calc_time = calc_time + (t1-t0)
        t0 = time.time()
        system_mass = state.properties(phase_name="System", props="Mass")-state.properties(phase_name="Water", props="Mass")
        liquid_mass = state.properties(phase_name="Liquid", props="Mass")
        if liquid_mass/system_mass < liq_fr:
            unused = unused + temp-700
            solidus = True
        params.append(comp_dict)
        #update_melts_excel_output_workbook(state, wb, index, params)
        t1 = time.time()
        write_time = write_time +(t1-t0)
        index = index+1
        temp = temp - abs(T2-T1)/N_runs
        pressure = pressure - abs(P2-P1)/N_runs
    results = {'pressure': pressure, 'params': params, 'states': states}
    return results


def write_to_cell_in_sheet_row_col(ws, row, col, value, format='general'):

    if format == 'number':
        ws.cell(row=row, column=col, value=float(value)).number_format = '0.00'
    elif format == 'scientific':
        ws.cell(row=row, column=col, value=float(value)).number_format = '0.00E+00'
    else:
        ws.cell(row=row, column=col, value=value)

# update_melts_excel_output_workbook_xml
# function to update excel workbook using xml (deprecated)
# params:
#   wb: workbook object to be written to
#   root: xml object to be written from
#   iteration: index
#   status: if calculation succeeded or not
#   params: list containing initial condition information
def update_melts_excel_output_workbook_xml(melts_model, wb, root, iteration, status, params):
    if iteration == 0:
        
        condSh = wb["init_cond"]

        oxides = melts_model.get_oxide_names()
        bulk = melts_model.get_composition_of_phase(root)
        for i in range(0,len(oxides)):
            write_to_cell_in_sheet_row_col(condSh, i+1, 1, oxides[i])
            write_to_cell_in_sheet_row_col(condSh, i+1, 2, bulk.get(oxides[i]))

            write_to_cell_in_sheet_row_col(condSh, 1, 4,  "T1 (C)")
            write_to_cell_in_sheet_row_col(condSh, 2, 4,  "T2 (C)")
            write_to_cell_in_sheet_row_col(condSh, 3, 4,  "T step (C)")
            write_to_cell_in_sheet_row_col(condSh, 5, 4, "P1 (MPa)")
            write_to_cell_in_sheet_row_col(condSh, 6, 4, "P2 (MPa)")
            write_to_cell_in_sheet_row_col(condSh, 7, 4, "P step (MPa)")
            write_to_cell_in_sheet_row_col(condSh, 9, 4, "fO2 value")
            write_to_cell_in_sheet_row_col(condSh, 10, 4, "fO2 buffer")

            write_to_cell_in_sheet_row_col(condSh, 1, 5, params[0])
            write_to_cell_in_sheet_row_col(condSh, 2, 5, params[1])
            write_to_cell_in_sheet_row_col(condSh, 3, 5, params[2])
            write_to_cell_in_sheet_row_col(condSh, 5, 5, params[3])
            write_to_cell_in_sheet_row_col(condSh, 6, 5, params[4])
            write_to_cell_in_sheet_row_col(condSh, 7, 5, params[5])
            write_to_cell_in_sheet_row_col(condSh, 9, 5, params[6])
            write_to_cell_in_sheet_row_col(condSh, 10, 5, params[7])
            
            CalcType = params[9]
            MELTS_Model = params[10]
            
            if CalcType == 'HP_Sequence':
                write_to_cell_in_sheet_row_col(condSh, 12, 4, "H step (kJ)")
                write_to_cell_in_sheet_row_col(condSh, 12, 5, delta_H)

            if CalcType == "TV_Sequence":
                write_to_cell_in_sheet_row_col(condSh, 12, 4, "V step (cm3)")
                write_to_cell_in_sheet_row_col(condSh, 12, 5, delta_V)

            if CalcType == "SP_Sequence":
                write_to_cell_in_sheet_row_col(condSh, 12, 4, "S step (kJ)")
                write_to_cell_in_sheet_row_col(condSh, 12, 5, delta_S)

            write_to_cell_in_sheet_row_col(condSh, 1, 7, "Model")
            write_to_cell_in_sheet_row_col(condSh, 2, 7, "Calculation")
            write_to_cell_in_sheet_row_col(condSh, 1, 8, "rhyolite-Melts_v"+MELTS_Model[:-2]+".x")
            write_to_cell_in_sheet_row_col(condSh, 2, 8, CalcType)

            if (CalcType == "Draw_Phase_Diagram" and Include_P_Calc == True):
                write_to_cell_in_sheet_row_col(condSh, 2, 8, "QF_P_Calc")

    """ CREATE and UPDATE system TAB """
    
    if 'system' not in wb.sheetnames:
        sysSh = wb.create_sheet("system")
        write_to_cell_in_sheet_row_col(sysSh, 1, 1, "Index")
        write_to_cell_in_sheet_row_col(sysSh, 1, 2, "T (C) ")
        write_to_cell_in_sheet_row_col(sysSh, 1, 3, "P (MPa)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 4, "deltaQFM")
        write_to_cell_in_sheet_row_col(sysSh, 1, 5, "mass (g)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 6, "rho (g/cm3)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 7, "G (kJ)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 8, "H (kJ)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 9, "S (J/K)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 10, "Cp (J/K)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 11, "V (cm3)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 12, "alpha (1/K)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 13, "beta (1/bar)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 14, "d2V/dT2")
        write_to_cell_in_sheet_row_col(sysSh, 1, 15, "d2V/dP2")
        write_to_cell_in_sheet_row_col(sysSh, 1, 16, "d2V/dTdP")
        write_to_cell_in_sheet_row_col(sysSh, 1, 17, "Outcome")
    else:
        sysSh = wb["system"]
    
    Index = iteration + 1            
    Counter = sysSh.max_row + 1 # iteration + 2
    write_to_cell_in_sheet_row_col(sysSh, Counter, 1, Index)
    write_to_cell_in_sheet_row_col(sysSh, Counter, 2, locale.atof(root.find(".//Temperature").text))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 3, locale.atof(root.find(".//Pressure").text) * 1000) 
    # write_to_cell_in_sheet_row_col(sysSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 5, melts_model.get_property_of_phase(root, "System", "Mass"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 6, melts_model.get_property_of_phase(root, "System", "Density"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 7, melts_model.get_property_of_phase(root, "System", "GibbsFreeEnergy") / 1000)
    write_to_cell_in_sheet_row_col(sysSh, Counter, 8, melts_model.get_property_of_phase(root, "System", "Enthalpy") / 1000)
    write_to_cell_in_sheet_row_col(sysSh, Counter, 9, melts_model.get_property_of_phase(root, "System", "Entropy"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 10, melts_model.get_property_of_phase(root, "System", "HeatCapacity"))
    Temp_Vol = melts_model.get_property_of_phase(root, "System", "Volume")
    write_to_cell_in_sheet_row_col(sysSh, Counter, 11, melts_model.get_property_of_phase(root, "System", "Volume"))
    if (Temp_Vol != 0):
        write_to_cell_in_sheet_row_col(sysSh, Counter, 12, melts_model.get_property_of_phase(root, "System", "DvDt") / Temp_Vol)
        write_to_cell_in_sheet_row_col(sysSh, Counter, 13, melts_model.get_property_of_phase(root, "System", "DvDp") / Temp_Vol * (-1))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 14, melts_model.get_property_of_phase(root, "System", "D2vDt2"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 15, melts_model.get_property_of_phase(root, "System", "D2vDp2"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 16, melts_model.get_property_of_phase(root, "System", "D2vDtDp"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 17, status)
    
    systemMass = melts_model.get_property_of_phase(root, "System", "Mass")

    """ CREATE AND UPDATE tot_solids TAB """

    if 'tot_solids' not in wb.sheetnames:
        totsolSh = wb.create_sheet("tot_solids")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 1, "Index")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 2, "T (C) ")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 3, "P (MPa)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 4, "deltaQFM")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 5, "mass (g)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 6, "rho (g/cm3)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 7, "G (kJ)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 8, "H (kJ)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 9, "S (J/K)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 10, "Cp (J/K)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 11, "V (cm3)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 12, "alpha (1/K)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 13, "beta (1/bar)")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 14, "d2V/dT2")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 15, "d2V/dP2")
        write_to_cell_in_sheet_row_col(totsolSh, 1, 16, "d2V/dTdP")
    else:
        totsolSh = wb["tot_solids"]

    Counter = totsolSh.max_row + 1 # WorksheetFunction.Count(totsolSh.Range("A:A")) + 2
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 1, Index)
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 2, locale.atof(root.find(".//Temperature").text))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 3, locale.atof(root.find(".//Pressure").text) * 1000) 
    # write_to_cell_in_sheet_row_col(totsolSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 5, melts_model.get_property_of_phase(root, "TotalSolids", "Mass"))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 6, melts_model.get_property_of_phase(root, "totalSolids", "Density"))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 7, melts_model.get_property_of_phase(root, "totalSolids", "GibbsFreeEnergy") / 1000)
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 8, melts_model.get_property_of_phase(root, "totalSolids", "Enthalpy") / 1000)
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 9, melts_model.get_property_of_phase(root, "totalSolids", "Entropy"))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 10, melts_model.get_property_of_phase(root, "totalSolids", "HeatCapacity"))
    Temp_Vol = melts_model.get_property_of_phase(root, "TotalSolids", "Volume")
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 11, melts_model.get_property_of_phase(root, "totalSolids", "Volume"))
    # write_to_cell_in_sheet_row_col(totsolSh, Counter, 12, melts_model.get_property_of_phase(root, "totalSolids", "DvDt") / Temp_Vol)
    # write_to_cell_in_sheet_row_col(totsolSh, Counter, 13, melts_model.get_property_of_phase(root, "totalSolids", "DvDp") / Temp_Vol * (-1))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 14, melts_model.get_property_of_phase(root, "totalSolids", "D2vDt2"))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 15, melts_model.get_property_of_phase(root, "totalSolids", "D2vDp2"))
    write_to_cell_in_sheet_row_col(totsolSh, Counter, 16, melts_model.get_property_of_phase(root, "totalSolids", "D2vDtDp"))

    """ CREATE AND UPDATE liquid TAB """

    if 'liquid' not in wb.sheetnames:
        liqSh = wb.create_sheet("liquid")
        write_to_cell_in_sheet_row_col(liqSh, 1, 1, "Index")
        write_to_cell_in_sheet_row_col(liqSh, 1, 2, "T (C) ")
        write_to_cell_in_sheet_row_col(liqSh, 1, 3, "P (MPa)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 4, "deltaQFM")
        write_to_cell_in_sheet_row_col(liqSh, 1, 5, "mass (g)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 6, "rho (g/cm3)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 7, "G (kJ)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 8, "H (kJ)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 9, "S (J/K)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 10, "Cp (J/K)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 11, "V (cm3)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 12, "alpha (1/K)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 13, "beta (1/bar)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 14, "d2V/dT2")
        write_to_cell_in_sheet_row_col(liqSh, 1, 15, "d2V/dP2")
        write_to_cell_in_sheet_row_col(liqSh, 1, 16, "d2V/dTdP")
        write_to_cell_in_sheet_row_col(liqSh, 1, 17, "SiO2 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 18, "TiO2 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 19, "Al2O3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 20, "Fe2O3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 21, "Cr2O3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 22, "FeO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 23, "MnO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 24, "MgO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 25, "NiO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 26, "CoO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 27, "CaO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 28, "Na2O (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 29, "K2O (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 30, "P2O5 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 31, "H2O (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 32, "CO2 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 33, "SO3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 34, "Cl2O-1 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 35, "F2O -1 (wt%)")
    else:
        liqSh = wb["liquid"]

    Counter = liqSh.max_row + 1 # WorksheetFunction.Count(liqSh.Range("A:A")) + 2
    write_to_cell_in_sheet_row_col(liqSh, Counter, 1, Index)
    write_to_cell_in_sheet_row_col(liqSh, Counter, 2, locale.atof(root.find(".//Temperature").text))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 3, locale.atof(root.find(".//Pressure").text) * 1000) 
    # write_to_cell_in_sheet_row_col(liqSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 5, melts_model.get_property_of_phase(root, "Liquid", "Mass"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 6, melts_model.get_property_of_phase(root, "Liquid", "Density"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 7, melts_model.get_property_of_phase(root, "Liquid", "GibbsFreeEnergy") / 1000)
    write_to_cell_in_sheet_row_col(liqSh, Counter, 8, melts_model.get_property_of_phase(root, "Liquid", "Enthalpy") / 1000)
    write_to_cell_in_sheet_row_col(liqSh, Counter, 9, melts_model.get_property_of_phase(root, "Liquid", "Entropy"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 10, melts_model.get_property_of_phase(root, "Liquid", "HeatCapacity"))
    Temp_Vol = melts_model.get_property_of_phase(root, "Liquid", "Volume")
    write_to_cell_in_sheet_row_col(liqSh, Counter, 11, melts_model.get_property_of_phase(root, "Liquid", "Volume"))
    if (Temp_Vol != 0):
        write_to_cell_in_sheet_row_col(liqSh, Counter, 12, melts_model.get_property_of_phase(root, "Liquid", "DvDt") / Temp_Vol)
        write_to_cell_in_sheet_row_col(liqSh, Counter, 13, melts_model.get_property_of_phase(root, "Liquid", "DvDp") / Temp_Vol * (-1))
        
    write_to_cell_in_sheet_row_col(liqSh, Counter, 14, melts_model.get_property_of_phase(root, "Liquid", "D2vDt2"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 15, melts_model.get_property_of_phase(root, "Liquid", "D2vDp2"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 16, melts_model.get_property_of_phase(root, "Liquid", "D2vDtDp"))

    liquidMass = melts_model.get_property_of_phase(root, "Liquid", "Mass")
    
    ox_dict = melts_model.get_composition_of_phase(root, 'Liquid')
    if 'SiO2' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 17, ox_dict['SiO2'])
    if 'TiO2' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 18, ox_dict['TiO2'])
    if 'Al2O3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 19, ox_dict['Al2O3'])
    if 'Fe2O3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 20, ox_dict['Fe2O3'])
    if 'Cr2O3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 21, ox_dict['Cr2O3'])
    if 'FeO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 22, ox_dict['FeO'])
    if 'MnO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 23, ox_dict['MnO'])
    if 'MgO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 24, ox_dict['MgO'])
    if 'NiO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 25, ox_dict['NiO'])
    if 'CoO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 26, ox_dict['CoO'])
    if 'CaO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 27, ox_dict['CaO'])
    if 'Na2O' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 28, ox_dict['Na2O'])
    if 'K2O' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 29, ox_dict['K2O'])
    if 'P2O5' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 30, ox_dict['P2O5'])
    if 'H2O' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 31, ox_dict['H2O'])
    if 'CO2' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 32, ox_dict['CO2'])
    if 'SO3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 33, ox_dict['SO3'])
    if 'Cl2O-1' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 34, ox_dict['Cl2O-1'])
    if 'F2O -1' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 35, ox_dict['F2O -1'])
        
    """ CREATE AND UPDATE phases TABS """

    phases_present = melts_model.get_list_of_phases_in_assemblage(root)
    if 'Liquid' in phases_present:
        phases_present.remove('Liquid')

    for i in range(len(phases_present)):
        solid_name = phases_present[i]
        comp_dict = melts_model.get_composition_of_phase(root, phases_present[i], 'component')
        comp_names = [*comp_dict]

        if solid_name.lower() not in wb.sheetnames:
            solSh = wb.create_sheet(solid_name.lower())
            write_to_cell_in_sheet_row_col(solSh, 1, 1, "Index")
            write_to_cell_in_sheet_row_col(solSh, 1, 2, "T (C) ")
            write_to_cell_in_sheet_row_col(solSh, 1, 3, "P (MPa)")
            write_to_cell_in_sheet_row_col(solSh, 1, 4, "deltaQFM")
            write_to_cell_in_sheet_row_col(solSh, 1, 5, "mass (g)")
            write_to_cell_in_sheet_row_col(solSh, 1, 6, "rho (g/cm3)")
            write_to_cell_in_sheet_row_col(solSh, 1, 7, "G (kJ)")
            write_to_cell_in_sheet_row_col(solSh, 1, 8, "H (kJ)")
            write_to_cell_in_sheet_row_col(solSh, 1, 9, "S (J/K)")
            write_to_cell_in_sheet_row_col(solSh, 1, 10, "Cp (J/K)")
            write_to_cell_in_sheet_row_col(solSh, 1, 11, "V (cm3)")
            write_to_cell_in_sheet_row_col(solSh, 1, 12, "alpha (1/K)")
            write_to_cell_in_sheet_row_col(solSh, 1, 13, "beta (1/bar)")
            write_to_cell_in_sheet_row_col(solSh, 1, 14, "d2V/dT2")
            write_to_cell_in_sheet_row_col(solSh, 1, 15, "d2V/dP2")
            write_to_cell_in_sheet_row_col(solSh, 1, 16, "d2V/dTdP")
            write_to_cell_in_sheet_row_col(solSh, 1, 17, "SiO2 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 18, "TiO2 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 19, "Al2O3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 20, "Fe2O3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 21, "Cr2O3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 22, "FeO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 23, "MnO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 24, "MgO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 25, "NiO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 26, "CoO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 27, "CaO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 28, "Na2O (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 29, "K2O (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 30, "P2O5 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 31, "H2O (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 32, "CO2 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 33, "SO3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 34, "Cl2O-1 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 35, "F2O -1 (wt%)")
            if len(comp_names) > 1:
                for k in range(len(comp_names)):
                    write_to_cell_in_sheet_row_col(solSh, 1, 36 + k, comp_names[k])
            else:
                write_to_cell_in_sheet_row_col(solSh, 1, 36, solid_name.lower())
        else:
            solSh = wb[solid_name.lower()]

        Counter = solSh.max_row + 1 # WorksheetFunction.Count(liqSh.Range("A:A")) + 2
        write_to_cell_in_sheet_row_col(solSh, Counter, 1, Index)
        write_to_cell_in_sheet_row_col(solSh, Counter, 2, locale.atof(root.find(".//Temperature").text))
        write_to_cell_in_sheet_row_col(solSh, Counter, 3, locale.atof(root.find(".//Pressure").text) * 1000) 
        # write_to_cell_in_sheet_row_col(solSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
        write_to_cell_in_sheet_row_col(solSh, Counter, 5, melts_model.get_property_of_phase(root, solid_name, "Mass"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 6, melts_model.get_property_of_phase(root, solid_name, "Density"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 7, melts_model.get_property_of_phase(root, solid_name, "GibbsFreeEnergy") / 1000)
        write_to_cell_in_sheet_row_col(solSh, Counter, 8, melts_model.get_property_of_phase(root, solid_name, "Enthalpy") / 1000)
        write_to_cell_in_sheet_row_col(solSh, Counter, 9, melts_model.get_property_of_phase(root, solid_name, "Entropy"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 10, melts_model.get_property_of_phase(root, solid_name, "HeatCapacity"))
        Temp_Vol = melts_model.get_property_of_phase(root, "Liquid", "Volume")
        write_to_cell_in_sheet_row_col(solSh, Counter, 11, melts_model.get_property_of_phase(root, solid_name, "Volume"))
        if (Temp_Vol != 0):
            write_to_cell_in_sheet_row_col(solSh, Counter, 12, melts_model.get_property_of_phase(root, solid_name, "DvDt") / Temp_Vol)
            write_to_cell_in_sheet_row_col(solSh, Counter, 13, melts_model.get_property_of_phase(root, solid_name, "DvDp") / Temp_Vol * (-1))
        write_to_cell_in_sheet_row_col(solSh, Counter, 14, melts_model.get_property_of_phase(root, solid_name, "D2vDt2"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 15, melts_model.get_property_of_phase(root, solid_name, "D2vDp2"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 16, melts_model.get_property_of_phase(root, solid_name, "D2vDtDp"))

        ox_dict = melts_model.get_composition_of_phase(root, solid_name)
        if 'SiO2' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 17, ox_dict['SiO2'])
        if 'TiO2' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 18, ox_dict['TiO2'])
        if 'Al2O3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 19, ox_dict['Al2O3'])
        if 'Fe2O3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 20, ox_dict['Fe2O3'])
        if 'Cr2O3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 21, ox_dict['Cr2O3'])
        if 'FeO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 22, ox_dict['FeO'])
        if 'MnO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 23, ox_dict['MnO'])
        if 'MgO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 24, ox_dict['MgO'])
        if 'NiO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 25, ox_dict['NiO'])
        if 'CoO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 26, ox_dict['CoO'])
        if 'CaO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 27, ox_dict['CaO'])
        if 'Na2O' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 28, ox_dict['Na2O'])
        if 'K2O' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 29, ox_dict['K2O'])
        if 'P2O5' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 30, ox_dict['P2O5'])
        if 'H2O' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 31, ox_dict['H2O'])
        if 'CO2' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 32, ox_dict['CO2'])
        if 'SO3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 33, ox_dict['SO3'])
        if 'Cl2O-1' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 34, ox_dict['Cl2O-1'])
        if 'F2O -1' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 35, ox_dict['F2O -1'])
        
        if len(comp_names) > 1:
            for k in range(len(comp_names)):
                write_to_cell_in_sheet_row_col(solSh, Counter, 36 + k, comp_dict.get(comp_names[k]))
        else:
            write_to_cell_in_sheet_row_col(solSh, Counter, 36, 1)
            
    """ CREATE AND UPDATE affinities TAB """

    aff_dict = melts_model.get_dictionary_of_affinities(root, sort=True)
    system_phases = sorted(melts_model.get_phase_names())
    system_phases.remove('Liquid')

    if 'affinities (kJ)' not in wb.sheetnames:
        potsolSh = wb.create_sheet('affinities (kJ)')
        write_to_cell_in_sheet_row_col(potsolSh, 1, 1, "Index")
        write_to_cell_in_sheet_row_col(potsolSh, 1, 2, "T (C) ")
        write_to_cell_in_sheet_row_col(potsolSh, 1, 3, "P (MPa)")
        write_to_cell_in_sheet_row_col(potsolSh, 1, 4, "deltaQFM")
        for i in range(len(system_phases)):
            write_to_cell_in_sheet_row_col(potsolSh, 1, i + 5, system_phases[i])
    else:
        potsolSh = wb['affinities (kJ)']

    Counter = potsolSh.max_row + 1
    for i in range(len(system_phases)):
        write_to_cell_in_sheet_row_col(potsolSh, Counter, 1, Index)
        write_to_cell_in_sheet_row_col(potsolSh, Counter, 2, locale.atof(root.find(".//Temperature").text))
        write_to_cell_in_sheet_row_col(potsolSh, Counter, 3, locale.atof(root.find(".//Pressure").text) * 1000) 
        # write_to_cell_in_sheet_row_col(potsolSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
        if system_phases[i] in aff_dict.keys():
            (affinity, formula) = aff_dict[system_phases[i]]
            write_to_cell_in_sheet_row_col(potsolSh, Counter, i + 5, affinity / 1000)

# update_melts_excel_output_workbook
# function to update excel workbook
# params:
#   wb: workbook object to be written to
#   iteration: index
#   params: list containing initial condition information
def update_melts_excel_output_workbook(state, wb, iteration, params):
    """ CREATE init_cond and system TABS DURING 0TH ITERATION """
    
    if iteration == 0:
        
        condSh = wb["init_cond"]
        
        # NEED TO GET OXIDE COMPOSITION OF SYSTEM
        comp = params[len(params)-1]
        print(comp)
        oxides = [i for i in comp.keys()]
        for i in range(0,len(oxides)):
            write_to_cell_in_sheet_row_col(condSh, i+1, 1, oxides[i])
            write_to_cell_in_sheet_row_col(condSh, i+1, 2, comp.get(oxides[i]))

            write_to_cell_in_sheet_row_col(condSh, 1, 4,  "T1 (C)")
            write_to_cell_in_sheet_row_col(condSh, 2, 4,  "T2 (C)")
            write_to_cell_in_sheet_row_col(condSh, 3, 4,  "T step (C)")
            write_to_cell_in_sheet_row_col(condSh, 5, 4, "P1 (MPa)")
            write_to_cell_in_sheet_row_col(condSh, 6, 4, "P2 (MPa)")
            write_to_cell_in_sheet_row_col(condSh, 7, 4, "P step (MPa)")
            write_to_cell_in_sheet_row_col(condSh, 9, 4, "fO2 value")
            write_to_cell_in_sheet_row_col(condSh, 10, 4, "fO2 buffer")

            write_to_cell_in_sheet_row_col(condSh, 1, 5, params[0])
            write_to_cell_in_sheet_row_col(condSh, 2, 5, params[1])
            write_to_cell_in_sheet_row_col(condSh, 3, 5, params[2])
            write_to_cell_in_sheet_row_col(condSh, 5, 5, params[3])
            write_to_cell_in_sheet_row_col(condSh, 6, 5, params[4])
            write_to_cell_in_sheet_row_col(condSh, 7, 5, params[5])
            write_to_cell_in_sheet_row_col(condSh, 9, 5, params[8])
            write_to_cell_in_sheet_row_col(condSh, 10, 5, params[7])
            
            CalcType = params[10]
            MELTS_Model = params[9]
            
            if CalcType == 'HP_Sequence':
                write_to_cell_in_sheet_row_col(condSh, 12, 4, "H step (kJ)")
                write_to_cell_in_sheet_row_col(condSh, 12, 5, delta_H)

            if CalcType == "TV_Sequence":
                write_to_cell_in_sheet_row_col(condSh, 12, 4, "V step (cm3)")
                write_to_cell_in_sheet_row_col(condSh, 12, 5, delta_V)

            if CalcType == "SP_Sequence":
                write_to_cell_in_sheet_row_col(condSh, 12, 4, "S step (kJ)")
                write_to_cell_in_sheet_row_col(condSh, 12, 5, delta_S)

            write_to_cell_in_sheet_row_col(condSh, 1, 7, "Model")
            write_to_cell_in_sheet_row_col(condSh, 2, 7, "Calculation")
            write_to_cell_in_sheet_row_col(condSh, 1, 8, MELTS_Model)
            write_to_cell_in_sheet_row_col(condSh, 2, 8, CalcType)

            if (CalcType == "Draw_Phase_Diagram" and Include_P_Calc == True):
                write_to_cell_in_sheet_row_col(condSh, 2, 8, "QF_P_Calc")

    """ CREATE and UPDATE system TAB """
    
    if 'system' not in wb.sheetnames:
        sysSh = wb.create_sheet("system")
        write_to_cell_in_sheet_row_col(sysSh, 1, 1, "Index")
        write_to_cell_in_sheet_row_col(sysSh, 1, 2, "T (C) ")
        write_to_cell_in_sheet_row_col(sysSh, 1, 3, "P (MPa)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 4, "deltaQFM")
        write_to_cell_in_sheet_row_col(sysSh, 1, 5, "mass (g)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 6, "rho (g/cm3)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 7, "G (kJ)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 8, "H (kJ)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 9, "S (J/K)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 10, "Cp (J/K)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 11, "V (cm3)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 12, "alpha (1/K)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 13, "beta (1/bar)")
        write_to_cell_in_sheet_row_col(sysSh, 1, 14, "d2V/dT2")
        write_to_cell_in_sheet_row_col(sysSh, 1, 15, "d2V/dP2")
        write_to_cell_in_sheet_row_col(sysSh, 1, 16, "d2V/dTdP")
        write_to_cell_in_sheet_row_col(sysSh, 1, 17, "Outcome")
    else:
        sysSh = wb["system"]
    
    Index = iteration + 1            
    Counter = sysSh.max_row + 1 # iteration + 2
    write_to_cell_in_sheet_row_col(sysSh, Counter, 1, Index)
    write_to_cell_in_sheet_row_col(sysSh, Counter, 2, state.temperature-273.15)
    write_to_cell_in_sheet_row_col(sysSh, Counter, 3, state.pressure / 10) 
    # write_to_cell_in_sheet_row_col(sysSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 5, state.properties(phase_name="System", props="Mass"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 6, state.properties(phase_name="System", props="Density"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 7, state.properties(phase_name="System", props="GibbsFreeEnergy") / 1000)
    write_to_cell_in_sheet_row_col(sysSh, Counter, 8, state.properties(phase_name="System", props="Enthalpy") / 1000)
    write_to_cell_in_sheet_row_col(sysSh, Counter, 9, state.properties(phase_name="System", props="Entropy"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 10, state.properties(phase_name="System", props="HeatCapacity"))
    Temp_Vol = state.properties(phase_name="System", props="Volume")
    write_to_cell_in_sheet_row_col(sysSh, Counter, 11, state.properties(phase_name="System", props="Volume"))
    if (Temp_Vol != 0):
        write_to_cell_in_sheet_row_col(sysSh, Counter, 12, state.properties(phase_name="System", props="DvDt") / Temp_Vol)
        write_to_cell_in_sheet_row_col(sysSh, Counter, 13, state.properties(phase_name="System", props="DvDp") / Temp_Vol * (-1))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 14, state.properties(phase_name="System", props="D2vDt2"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 15, state.properties(phase_name="System", props="D2vDp2"))
    write_to_cell_in_sheet_row_col(sysSh, Counter, 16, state.properties(phase_name="System", props="D2vDtDp"))
    #write_to_cell_in_sheet_row_col(sysSh, Counter, 17, status)
    
    systemMass = state.properties(phase_name="System", props="Mass")

    """ CREATE AND UPDATE liquid TAB """
    if 'liquid' not in wb.sheetnames:
        liqSh = wb.create_sheet("liquid")
        write_to_cell_in_sheet_row_col(liqSh, 1, 1, "Index")
        write_to_cell_in_sheet_row_col(liqSh, 1, 2, "T (C) ")
        write_to_cell_in_sheet_row_col(liqSh, 1, 3, "P (MPa)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 4, "deltaQFM")
        write_to_cell_in_sheet_row_col(liqSh, 1, 5, "mass (g)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 6, "rho (g/cm3)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 7, "G (kJ)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 8, "H (kJ)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 9, "S (J/K)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 10, "Cp (J/K)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 11, "V (cm3)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 12, "alpha (1/K)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 13, "beta (1/bar)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 14, "d2V/dT2")
        write_to_cell_in_sheet_row_col(liqSh, 1, 15, "d2V/dP2")
        write_to_cell_in_sheet_row_col(liqSh, 1, 16, "d2V/dTdP")
        write_to_cell_in_sheet_row_col(liqSh, 1, 17, "SiO2 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 18, "TiO2 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 19, "Al2O3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 20, "Fe2O3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 21, "Cr2O3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 22, "FeO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 23, "MnO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 24, "MgO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 25, "NiO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 26, "CoO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 27, "CaO (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 28, "Na2O (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 29, "K2O (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 30, "P2O5 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 31, "H2O (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 32, "CO2 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 33, "SO3 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 34, "Cl2O-1 (wt%)")
        write_to_cell_in_sheet_row_col(liqSh, 1, 35, "F2O -1 (wt%)")
    else:
        liqSh = wb["liquid"]

    Counter = liqSh.max_row + 1 # WorksheetFunction.Count(liqSh.Range("A:A")) + 2
    write_to_cell_in_sheet_row_col(liqSh, Counter, 1, Index)
    write_to_cell_in_sheet_row_col(liqSh, Counter, 2, state.temperature-273.15)
    write_to_cell_in_sheet_row_col(liqSh, Counter, 3, state.pressure / 10) 
    # write_to_cell_in_sheet_row_col(sysSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 5, state.properties(phase_name="Liquid", props="Mass"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 6, state.properties(phase_name="Liquid", props="Density"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 7, state.properties(phase_name="Liquid", props="GibbsFreeEnergy") / 1000)
    write_to_cell_in_sheet_row_col(liqSh, Counter, 8, state.properties(phase_name="Liquid", props="Enthalpy") / 1000)
    write_to_cell_in_sheet_row_col(liqSh, Counter, 9, state.properties(phase_name="Liquid", props="Entropy"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 10, state.properties(phase_name="Liquid", props="HeatCapacity"))
    Temp_Vol = state.properties(phase_name="Liquid", props="Volume")
    write_to_cell_in_sheet_row_col(liqSh, Counter, 11, state.properties(phase_name="Liquid", props="Volume"))
    if (Temp_Vol != 0):
        write_to_cell_in_sheet_row_col(liqSh, Counter, 12, state.properties(phase_name="Liquid", props="DvDt") / Temp_Vol)
        write_to_cell_in_sheet_row_col(liqSh, Counter, 13, state.properties(phase_name="Liquid", props="DvDp") / Temp_Vol * (-1))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 14, state.properties(phase_name="Liquid", props="D2vDt2"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 15, state.properties(phase_name="Liquid", props="D2vDp2"))
    write_to_cell_in_sheet_row_col(liqSh, Counter, 16, state.properties(phase_name="Liquid", props="D2vDtDp"))
    #write_to_cell_in_sheet_row_col(liqSh, Counter, 17, status)

    liquidMass = state.properties(phase_name="Liquid", props="Mass")
    
    # EDIT THIS TO USE EQUILIBRATE
    ox_dict = state.oxide_comp('Liquid')
    if 'SiO2' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 17, ox_dict['SiO2'])
    if 'TiO2' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 18, ox_dict['TiO2'])
    if 'Al2O3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 19, ox_dict['Al2O3'])
    if 'Fe2O3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 20, ox_dict['Fe2O3'])
    if 'Cr2O3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 21, ox_dict['Cr2O3'])
    if 'FeO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 22, ox_dict['FeO'])
    if 'MnO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 23, ox_dict['MnO'])
    if 'MgO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 24, ox_dict['MgO'])
    if 'NiO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 25, ox_dict['NiO'])
    if 'CoO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 26, ox_dict['CoO'])
    if 'CaO' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 27, ox_dict['CaO'])
    if 'Na2O' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 28, ox_dict['Na2O'])
    if 'K2O' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 29, ox_dict['K2O'])
    if 'P2O5' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 30, ox_dict['P2O5'])
    if 'H2O' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 31, ox_dict['H2O'])
    if 'CO2' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 32, ox_dict['CO2'])
    if 'SO3' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 33, ox_dict['SO3'])
    if 'Cl2O-1' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 34, ox_dict['Cl2O-1'])
    if 'F2O -1' in ox_dict.keys():
        write_to_cell_in_sheet_row_col(liqSh, Counter, 35, ox_dict['F2O -1'])
        
    """ CREATE AND UPDATE phases TABS """
    # EDIT THIS TO USE EQUILIBRATE
    phases = state.properties()
    phases_present = [i for i in phases.keys() if phases[i] == 'stable']
    phases_present.remove('System')
    if 'Liquid' in phases_present:
        phases_present.remove('Liquid')

    for i in range(len(phases_present)):
        solid_name = phases_present[i]
        comp_dict = state.oxide_comp(phases_present[i])
        comp_names = [*comp_dict]

        if solid_name.lower() not in wb.sheetnames:
            solSh = wb.create_sheet(solid_name.lower())
            write_to_cell_in_sheet_row_col(solSh, 1, 1, "Index")
            write_to_cell_in_sheet_row_col(solSh, 1, 2, "T (C) ")
            write_to_cell_in_sheet_row_col(solSh, 1, 3, "P (MPa)")
            write_to_cell_in_sheet_row_col(solSh, 1, 4, "deltaQFM")
            write_to_cell_in_sheet_row_col(solSh, 1, 5, "mass (g)")
            write_to_cell_in_sheet_row_col(solSh, 1, 6, "rho (g/cm3)")
            write_to_cell_in_sheet_row_col(solSh, 1, 7, "G (kJ)")
            write_to_cell_in_sheet_row_col(solSh, 1, 8, "H (kJ)")
            write_to_cell_in_sheet_row_col(solSh, 1, 9, "S (J/K)")
            write_to_cell_in_sheet_row_col(solSh, 1, 10, "Cp (J/K)")
            write_to_cell_in_sheet_row_col(solSh, 1, 11, "V (cm3)")
            write_to_cell_in_sheet_row_col(solSh, 1, 12, "alpha (1/K)")
            write_to_cell_in_sheet_row_col(solSh, 1, 13, "beta (1/bar)")
            write_to_cell_in_sheet_row_col(solSh, 1, 14, "d2V/dT2")
            write_to_cell_in_sheet_row_col(solSh, 1, 15, "d2V/dP2")
            write_to_cell_in_sheet_row_col(solSh, 1, 16, "d2V/dTdP")
            write_to_cell_in_sheet_row_col(solSh, 1, 17, "SiO2 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 18, "TiO2 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 19, "Al2O3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 20, "Fe2O3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 21, "Cr2O3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 22, "FeO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 23, "MnO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 24, "MgO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 25, "NiO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 26, "CoO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 27, "CaO (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 28, "Na2O (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 29, "K2O (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 30, "P2O5 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 31, "H2O (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 32, "CO2 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 33, "SO3 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 34, "Cl2O-1 (wt%)")
            write_to_cell_in_sheet_row_col(solSh, 1, 35, "F2O -1 (wt%)")
            if len(comp_names) > 1:
                for k in range(len(comp_names)):
                    write_to_cell_in_sheet_row_col(solSh, 1, 36 + k, str(comp_names[k]))
            else:
                write_to_cell_in_sheet_row_col(solSh, 1, 36, solid_name.lower())
        else:
            solSh = wb[solid_name.lower()]

        Counter = solSh.max_row + 1 # WorksheetFunction.Count(liqSh.Range("A:A")) + 2
        write_to_cell_in_sheet_row_col(solSh, Counter, 1, Index)
        write_to_cell_in_sheet_row_col(solSh, Counter, 2, state.temperature-273.15)
        write_to_cell_in_sheet_row_col(solSh, Counter, 3, state.pressure / 10) 
        # write_to_cell_in_sheet_row_col(sysSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
        write_to_cell_in_sheet_row_col(solSh, Counter, 5, state.properties(phase_name=solid_name, props="Mass"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 6, state.properties(phase_name=solid_name, props="Density"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 7, state.properties(phase_name=solid_name, props="GibbsFreeEnergy") / 1000)
        write_to_cell_in_sheet_row_col(solSh, Counter, 8, state.properties(phase_name=solid_name, props="Enthalpy") / 1000)
        write_to_cell_in_sheet_row_col(solSh, Counter, 9, state.properties(phase_name=solid_name, props="Entropy"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 10, state.properties(phase_name=solid_name, props="HeatCapacity"))
        Temp_Vol = state.properties(phase_name=solid_name, props="Volume")
        write_to_cell_in_sheet_row_col(solSh, Counter, 11, state.properties(phase_name=solid_name, props="Volume"))
        if (Temp_Vol != 0):
            write_to_cell_in_sheet_row_col(solSh, Counter, 12, state.properties(phase_name=solid_name, props="DvDt") / Temp_Vol)
            write_to_cell_in_sheet_row_col(solSh, Counter, 13, state.properties(phase_name=solid_name, props="DvDp") / Temp_Vol * (-1))
        write_to_cell_in_sheet_row_col(solSh, Counter, 14, state.properties(phase_name=solid_name, props="D2vDt2"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 15, state.properties(phase_name=solid_name, props="D2vDp2"))
        write_to_cell_in_sheet_row_col(solSh, Counter, 16, state.properties(phase_name=solid_name, props="D2vDtDp"))
        
        ox_dict = state.oxide_comp(solid_name)
        if 'SiO2' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 17, ox_dict['SiO2'])
        if 'TiO2' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 18, ox_dict['TiO2'])
        if 'Al2O3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 19, ox_dict['Al2O3'])
        if 'Fe2O3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 20, ox_dict['Fe2O3'])
        if 'Cr2O3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 21, ox_dict['Cr2O3'])
        if 'FeO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 22, ox_dict['FeO'])
        if 'MnO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 23, ox_dict['MnO'])
        if 'MgO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 24, ox_dict['MgO'])
        if 'NiO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 25, ox_dict['NiO'])
        if 'CoO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 26, ox_dict['CoO'])
        if 'CaO' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 27, ox_dict['CaO'])
        if 'Na2O' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 28, ox_dict['Na2O'])
        if 'K2O' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 29, ox_dict['K2O'])
        if 'P2O5' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 30, ox_dict['P2O5'])
        if 'H2O' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 31, ox_dict['H2O'])
        if 'CO2' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 32, ox_dict['CO2'])
        if 'SO3' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 33, ox_dict['SO3'])
        if 'Cl2O-1' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 34, ox_dict['Cl2O-1'])
        if 'F2O -1' in ox_dict.keys():
            write_to_cell_in_sheet_row_col(solSh, Counter, 35, ox_dict['F2O -1'])
        
        if len(comp_names) > 1:
            for k in range(len(comp_names)):
                write_to_cell_in_sheet_row_col(solSh, Counter, 36 + k, comp_dict.get(comp_names[k]))
        else:
            write_to_cell_in_sheet_row_col(solSh, Counter, 36, 1)
            
    
    """ CREATE AND UPDATE affinities TAB """
'''
    system_phases = sorted([i for i in state.properties().keys()])
    system_phases.remove('Liquid')
    print(type(system_phases))

    if 'affinities (kJ)' not in wb.sheetnames:
        potsolSh = wb.create_sheet('affinities (kJ)')
        write_to_cell_in_sheet_row_col(potsolSh, 1, 1, "Index")
        write_to_cell_in_sheet_row_col(potsolSh, 1, 2, "T (C) ")
        write_to_cell_in_sheet_row_col(potsolSh, 1, 3, "P (MPa)")
        write_to_cell_in_sheet_row_col(potsolSh, 1, 4, "deltaQFM")
        for i in range(len(system_phases)):
            write_to_cell_in_sheet_row_col(potsolSh, 1, i + 5, system_phases[i])
    else:
        potsolSh = wb['affinities (kJ)']

    Counter = potsolSh.max_row + 1
    for i in range(len(system_phases)):
        aff_dict = state.affinities(phase_name=system_phases[i])
        print("here")
        print(aff_dict)
        write_to_cell_in_sheet_row_col(potsolSh, Counter, 1, Index)
        write_to_cell_in_sheet_row_col(potsolSh, Counter, 2, state.temperature-273)
        write_to_cell_in_sheet_row_col(potsolSh, Counter, 3, state.pressure / 10) 
        # write_to_cell_in_sheet_row_col(potsolSh, Counter, 4, locale.atof(root.find(".//deltaFMQ").text))
        if system_phases[i] in aff_dict.keys():
            (affinity, formula) = aff_dict[system_phases[i]]
            write_to_cell_in_sheet_row_col(potsolSh, Counter, i + 5, affinity / 1000)'''

# PARALLEL PROCESSING FUNCTIONS FOR MELTS CALCULATIONS

def create_equilibrate_object():
    """Create a new equilibrate object for worker processes"""
    # Recreate the system in worker process
    modelDB = _build_model_database()
    Liquid = modelDB.get_phase('Liq')
    Feldspar = modelDB.get_phase('Fsp')
    Quartz = modelDB.get_phase('Qz')
    Spinel = modelDB.get_phase('SplS')
    Opx = modelDB.get_phase('Opx')
    RhomOx = _get_phase_compat(modelDB, 'Rhom', fallback_code='Mag')
    Water = _build_water_phase(modelDB)
    
    elm_sys = ['H','O','Na','Mg','Al','Si','P','K','Ca','Ti','Cr','Mn','Fe','Co','Ni']
    phs_sys = [Liquid, Feldspar, Water, Quartz, Spinel, Opx, RhomOx]
    
    def muH2O(t, p, state):
        return Water.gibbs_energy(t, p)
    
    equil = equilibrate.Equilibrate(elm_sys, phs_sys)#, lagrange_l=[({'H':2.0,'O':1.0},muH2O)])
    return equil, elm_sys

def run_single_pressure_step(args):
    """
    Worker function to run a single pressure step calculation.
    This function can be pickled because it's in a module, not __main__.
    Now properly collects MELTS calculation data for writing to Excel.
    """
    try:
        # Unpack arguments
        (T1, T2, delta_T, pressure, const_fO2, fO2Path, fO2_offset, liq_fr, blk_cmp, comp_dict, params_list,verbose) = args
        
        # Import required modules in worker process
        import numpy as np
        import time
        from thermoengine import core, phases, model, equilibrate
        
        # Recreate the thermodynamic system in this worker process
        modelDB = _build_model_database()
        Liquid = modelDB.get_phase('Liq')
        Feldspar = modelDB.get_phase('Fsp')
        Quartz = modelDB.get_phase('Qz')
        Spinel = modelDB.get_phase('SplS')
        Opx = modelDB.get_phase('Opx')
        RhomOx = _get_phase_compat(modelDB, 'Rhom', fallback_code='Mag')
        Water = _build_water_phase(modelDB)
        
        elm_sys_local = ['H','O','Na','Mg','Al','Si','P','K','Ca','Ti','Cr','Mn','Fe','Co','Ni']
        phs_sys_local = [Liquid, Feldspar, Water, Quartz, Spinel, Opx, RhomOx]
        
        def muH2O_local(t, p, state):
            return Water.gibbs_energy(t, p)
        
        # Create equilibrate object
        equil = equilibrate.Equilibrate(elm_sys_local, phs_sys_local)#, lagrange_l=[({'H':2.0,'O':1.0},muH2O_local)])
        state = equilibrate.EquilState(equil.element_list,equil.phase_list)
        omni_phase = state.omni_phase()
        state.set_phase_comp(omni_phase,blk_cmp,input_as_elements=True)

        ox = equil.kc_ferric_ferrous(T1+273.15,pressure*10,state.phase_d['Liquid']['moles'],compute='oxides',deltaNNO=fO2_offset,debug=0)
        
        moles_end,oxide_res = Liquid.calc_endmember_comp(
            mol_oxide_comp=ox, method='intrinsic', output_residual=True)
        if not Liquid.test_endmember_comp(moles_end):
            print ("Calculated composition is infeasible!")
        # order oxides in bulk composition for calculations
        mol_elm = Liquid.convert_endmember_comp(moles_end, output='moles_elements')
        blk_cmp = np.array([mol_elm[core.chem.PERIODIC_ORDER.tolist().index(elm)] for elm in elm_sys_local])
        
        # Calculate number of temperature steps for this pressure
        N_runs_T = abs(T1 - T2) / delta_T
        N_runs = int(N_runs_T)
        
        # Start calculation
        temp = T1
        calc_time = 0
        unused = 0
        solidus = False
        results_data = []
        
        temp = find_wet_liquidus(equil, T1, T2, pressure, 50, blk_cmp, fO2_offset, verbose)
        # Ensure at least one state is sampled at/above T2 for downstream workbook writes.
        temp = max(temp, T2)
        # Run temperature sequence for this pressure
        for step in range(N_runs + 1):
            if temp < T2 or solidus:
                break
                
            t0 = time.time()
            try:
                # Execute MELTS calculation

                state = equil.execute(temp + 273.15, pressure * 10, bulk_comp=blk_cmp, con_deltaNNO=fO2_offset, debug=0)

                t1 = time.time()
                calc_time += (t1 - t0)
                
                # Check liquid fraction
                system_mass = _safe_state_property(state, "System", "Mass", default=0.0)
                water_mass = _safe_state_property(state, "Water", "Mass", default=0.0)
                liquid_mass = _safe_state_property(state, "Liquid", "Mass", default=0.0)
                dry_system_mass = system_mass - water_mass

                if dry_system_mass > 0 and (liquid_mass / dry_system_mass) < liq_fr:
                    unused += temp - 700
                    solidus = True
                
                # Collect thermodynamic data for this step
                step_data = {
                    'pressure': pressure,
                    'temperature': temp,
                    'step_index': step,
                    'calc_time': t1 - t0,
                    'system_properties': {
                        'Mass': _safe_state_property(state, "System", "Mass"),
                        'Density': _safe_state_property(state, "System", "Density"),
                        'GibbsFreeEnergy': _safe_state_property(state, "System", "GibbsFreeEnergy"),
                        'Enthalpy': _safe_state_property(state, "System", "Enthalpy"),
                        'Entropy': _safe_state_property(state, "System", "Entropy"),
                        'HeatCapacity': _safe_state_property(state, "System", "HeatCapacity"),
                        'Volume': _safe_state_property(state, "System", "Volume"),
                        'DvDt': _safe_state_property(state, "System", "DvDt"),
                        'DvDp': _safe_state_property(state, "System", "DvDp"),
                        'D2vDt2': _safe_state_property(state, "System", "D2vDt2"),
                        'D2vDp2': _safe_state_property(state, "System", "D2vDp2"),
                        'D2vDtDp': _safe_state_property(state, "System", "D2vDtDp"),
                    },
                    'liquid_properties': {
                        'Mass': _safe_state_property(state, "Liquid", "Mass"),
                        'Density': _safe_state_property(state, "Liquid", "Density"),
                        'GibbsFreeEnergy': _safe_state_property(state, "Liquid", "GibbsFreeEnergy"),
                        'Enthalpy': _safe_state_property(state, "Liquid", "Enthalpy"),
                        'Entropy': _safe_state_property(state, "Liquid", "Entropy"),
                        'HeatCapacity': _safe_state_property(state, "Liquid", "HeatCapacity"),
                        'Volume': _safe_state_property(state, "Liquid", "Volume"),
                        'DvDt': _safe_state_property(state, "Liquid", "DvDt"),
                        'DvDp': _safe_state_property(state, "Liquid", "DvDp"),
                        'D2vDt2': _safe_state_property(state, "Liquid", "D2vDt2"),
                        'D2vDp2': _safe_state_property(state, "Liquid", "D2vDp2"),
                        'D2vDtDp': _safe_state_property(state, "Liquid", "D2vDtDp"),
                    },
                    'liquid_composition': _safe_state_oxide_comp(state, 'Liquid'),
                    'composition_dict': comp_dict
                }
                
                # Add solid phase data if present
                phases = state.properties()
                phases_present = [i for i in phases.keys() if phases[i] == 'stable']
                if verbose:
                    print(f"  T={temp:.0f}°C: All phases = {phases}")
                if verbose:
                    print(f"  T={temp:.0f}°C: Stable phases = {phases_present}")
                
                if 'System' in phases_present:
                    phases_present.remove('System')
                if 'Liquid' in phases_present:
                    phases_present.remove('Liquid')
                
                if verbose:
                    print(f"  T={temp:.0f}°C: Solid phases = {phases_present}")
                step_data['solid_phases'] = {}
                for solid_name in phases_present:
                    if verbose:
                        print(f"    Extracting data for solid phase: {solid_name}")
                    try:
                        solid_data = {
                            'Mass': _safe_state_property(state, solid_name, "Mass"),
                            'Density': _safe_state_property(state, solid_name, "Density"),
                            'GibbsFreeEnergy': _safe_state_property(state, solid_name, "GibbsFreeEnergy"),
                            'Enthalpy': _safe_state_property(state, solid_name, "Enthalpy"),
                            'Entropy': _safe_state_property(state, solid_name, "Entropy"),
                            'HeatCapacity': _safe_state_property(state, solid_name, "HeatCapacity"),
                            'Volume': _safe_state_property(state, solid_name, "Volume"),
                            'DvDt': _safe_state_property(state, solid_name, "DvDt"),
                            'DvDp': _safe_state_property(state, solid_name, "DvDp"),
                            'D2vDt2': _safe_state_property(state, solid_name, "D2vDt2"),
                            'D2vDp2': _safe_state_property(state, solid_name, "D2vDp2"),
                            'D2vDtDp': _safe_state_property(state, solid_name, "D2vDtDp"),
                            'composition': _safe_state_oxide_comp(state, solid_name),
                        }
                        step_data['solid_phases'][solid_name] = solid_data
                        if verbose:
                            print(f"    ✓ Successfully extracted data for {solid_name}")
                            print(f"      Mass: {solid_data['Mass']}, Volume: {solid_data['Volume']}")
                            print(f"      Composition keys: {list(solid_data['composition'].keys())}")
                    except Exception as e:
                        print(f"    ✗ Error extracting data for {solid_name}: {e}")
                        # Don't add this phase to solid_phases if data extraction fails
                
                # Final check: what actually got stored?
                if verbose:
                    print(f"    Final step_data['solid_phases'] contains: {list(step_data['solid_phases'].keys())}")
                    if step_data['solid_phases']:
                        for phase_name in step_data['solid_phases'].keys():
                            print(f"      {phase_name}: Mass={step_data['solid_phases'][phase_name]['Mass']}")
                
                results_data.append(step_data)
                
            except Exception as e:
                print(f"Error at T={temp}, P={pressure}: {e}")
                # Continue to next temperature step
                pass
            
            # Move to next temperature
            temp -= delta_T
        
        return {
            'pressure': pressure,
            'num_steps': len(results_data),
            'total_calc_time': calc_time,
            'unused': unused,
            'solidus_reached': solidus,
            'results_data': results_data,
            'success': True
        }
        
    except Exception as e:
        return {
            'pressure': pressure if 'pressure' in locals() else 'unknown',
            'error': str(e),
            'success': False
        }

def process_single_composition_parallel(comp_data):
    """
    Worker function to process a single composition with parallel pressure steps.
    This function can be pickled because it's in a module.
    """
    (label, composition_dict, params, blk_cmp, max_pressure_workers, verbose) = comp_data
    
    try:
        import numpy as np
        from openpyxl import Workbook
        import time
        
        if verbose:
            print(f"Processing composition: {label}")
        t0 = time.time()
        
        # Prepare pressure steps
        pressure_steps = np.arange(params['P1'], params['P2'] - params['delta_P'], -params['delta_P'])
        if verbose:
            print(f"Will process {len(pressure_steps)} pressure steps: {pressure_steps[:5]}{'...' if len(pressure_steps) > 5 else ''}")
        
        # Prepare tasks for parallel pressure processing
        tasks = [
            (params['T1'], params['T2'], params['delta_T'], pressure, 
             params['const_fO2'], params['fO2Path'], params['fO2_offset'], 
             0.1, blk_cmp, composition_dict, label, verbose)
            for pressure in pressure_steps
        ]
        
        results = []
        
        # Run pressure steps using futureproof to avoid container deadlocks
        max_workers = min(max_pressure_workers, len(tasks))
        if verbose:
            print(f"Running {len(tasks)} pressure steps with futureproof ({max_workers} workers)...")
        
        # Use futureproof approach for pressure steps
        executor = FutureproofThreadPoolExecutor(max_workers=max_workers)
        with TaskManager(executor, error_policy=ErrorPolicyEnum.RAISE) as tm:
            # Submit all pressure step tasks
            task_to_pressure = {}
            for task in tasks:
                futureproof_task = tm.submit(run_single_pressure_step, task)
                task_to_pressure[futureproof_task] = task[3]  # Store pressure for error reporting
            
            # Process completed pressure step tasks
            completed_count = 0
            for futureproof_task in tm.as_completed():
                completed_count += 1
                pressure = task_to_pressure[futureproof_task]
                
                try:
                    if verbose:
                        print(f"  Processing pressure step {completed_count}/{len(tasks)}: P={pressure} MPa")
                    
                    # Works for both futureproof Task and stdlib Future fallback.
                    result = _task_result(futureproof_task)
                    
                    results.append(result)
                    
                    if result.get('success', False):
                        if verbose:
                            print(f"  ✓ P={result['pressure']} MPa: {result['num_steps']} temperatures calculated")
                    else:
                        if verbose:
                            print(f"  ✗ P={result['pressure']} MPa: Failed - {result.get('error', 'Unknown error')}")
                            
                except Exception as exc:
                    if verbose:
                        print(f"Pressure step failed for {label} at P={pressure} MPa: {exc}")
                    # Add a failed result to maintain consistency
                    results.append({
                        'pressure': pressure,
                        'error': str(exc),
                        'success': False
                    })
        
        # Sort results by pressure (highest to lowest)
        successful_results = sorted([r for r in results if r.get('success', False)], 
                                  key=lambda x: x['pressure'], reverse=True)
        
        if verbose:
            print(f"DEBUG: Starting Excel workbook creation for {label}")
        
        # Create and populate Excel workbook with collected data
        wb = Workbook()
        ws = wb.active
        ws.title = "init_cond"
        
        # Write initial conditions to the main sheet
        for i, (oxide, value) in enumerate(composition_dict.items(), 1):
            ws.cell(row=i, column=1, value=oxide)
            ws.cell(row=i, column=2, value=value)
        
        # Write parameter headers
        ws.cell(row=1, column=4, value="T1 (C)")
        ws.cell(row=2, column=4, value="T2 (C)")
        ws.cell(row=3, column=4, value="T step (C)")
        ws.cell(row=5, column=4, value="P1 (MPa)")
        ws.cell(row=6, column=4, value="P2 (MPa)")
        ws.cell(row=7, column=4, value="P step (MPa)")
        ws.cell(row=9, column=4, value="fO2 value")
        ws.cell(row=10, column=4, value="fO2 buffer")
        
        ws.cell(row=1, column=5, value=params['T1'])
        ws.cell(row=2, column=5, value=params['T2'])
        ws.cell(row=3, column=5, value=params['delta_T'])
        ws.cell(row=5, column=5, value=params['P1'])
        ws.cell(row=6, column=5, value=params['P2'])
        ws.cell(row=7, column=5, value=params['delta_P'])
        ws.cell(row=9, column=5, value=params['fO2_offset'])
        ws.cell(row=10, column=5, value=params['fO2Path'])
        
        ws.cell(row=1, column=7, value="Model")
        ws.cell(row=2, column=7, value="Calculation")
        ws.cell(row=1, column=8, value="rhyolite-MELTS")
        ws.cell(row=2, column=8, value="QF_P_Calc")
        
        all_solid_phases = set()  # Track phases that actually appear

        # Create system sheet with thermodynamic data
        if successful_results:
            system_sheet = wb.create_sheet("system")
            system_headers = ["Index", "T (C)", "P (MPa)", "deltaQFM", "mass (g)", "rho (g/cm3)", 
                            "G (kJ)", "H (kJ)", "S (J/K)", "Cp (J/K)", "V (cm3)", 
                            "alpha (1/K)", "beta (1/bar)", "d2V/dT2", "d2V/dP2", "d2V/dTdP", "Outcome"]
            
            for col, header in enumerate(system_headers, 1):
                system_sheet.cell(row=1, column=col, value=header)
            
            # Create liquid sheet
            liquid_sheet = wb.create_sheet("liquid")
            liquid_headers = system_headers + ["SiO2 (wt%)", "TiO2 (wt%)", "Al2O3 (wt%)", "Fe2O3 (wt%)", 
                                             "Cr2O3 (wt%)", "FeO (wt%)", "MnO (wt%)", "MgO (wt%)", 
                                             "NiO (wt%)", "CoO (wt%)", "CaO (wt%)", "Na2O (wt%)", 
                                             "K2O (wt%)", "P2O5 (wt%)", "H2O (wt%)", "CO2 (wt%)",
                                             "SO3 (wt%)", "Cl2O-1 (wt%)", "F2O -1 (wt%)"]
            
            for col, header in enumerate(liquid_headers, 1):
                liquid_sheet.cell(row=1, column=col, value=header)
            
            # Initialize solid_sheets dictionary for dynamic sheet creation
            solid_sheets = {}
            solid_sheet_rows = {}  # Track separate row indices for each solid phase sheet
            
            # Fill data from calculation results
            row_index = 2
            total_data_points = 0
            
            for pressure_result in successful_results:
                if 'results_data' in pressure_result:
                    for step_data in pressure_result['results_data']:
                        # Write system data
                        system_sheet.cell(row=row_index, column=1, value=row_index-1)  # Index
                        system_sheet.cell(row=row_index, column=2, value=step_data['temperature'])  # T (C)
                        system_sheet.cell(row=row_index, column=3, value=step_data['pressure'])  # P (MPa)
                        system_sheet.cell(row=row_index, column=4, value=0)  # deltaQFM placeholder
                        
                        # System thermodynamic properties
                        sys_props = step_data['system_properties']
                        system_sheet.cell(row=row_index, column=5, value=sys_props['Mass'])
                        system_sheet.cell(row=row_index, column=6, value=sys_props['Density'])
                        system_sheet.cell(row=row_index, column=7, value=sys_props['GibbsFreeEnergy']/1000)
                        system_sheet.cell(row=row_index, column=8, value=sys_props['Enthalpy']/1000)
                        system_sheet.cell(row=row_index, column=9, value=sys_props['Entropy'])
                        system_sheet.cell(row=row_index, column=10, value=sys_props['HeatCapacity'])
                        system_sheet.cell(row=row_index, column=11, value=sys_props['Volume'])
                        
                        # Additional properties
                        if sys_props['Volume'] != 0:
                            system_sheet.cell(row=row_index, column=12, value=sys_props['DvDt']/sys_props['Volume'])
                            system_sheet.cell(row=row_index, column=13, value=sys_props['DvDp']/sys_props['Volume']*(-1))
                        
                        system_sheet.cell(row=row_index, column=14, value=sys_props['D2vDt2'])
                        system_sheet.cell(row=row_index, column=15, value=sys_props['D2vDp2'])
                        system_sheet.cell(row=row_index, column=16, value=sys_props['D2vDtDp'])
                        system_sheet.cell(row=row_index, column=17, value="Success")
                        
                        # Write liquid data
                        liquid_sheet.cell(row=row_index, column=1, value=row_index-1)  # Index
                        liquid_sheet.cell(row=row_index, column=2, value=step_data['temperature'])  # T (C)
                        liquid_sheet.cell(row=row_index, column=3, value=step_data['pressure'])  # P (MPa)
                        liquid_sheet.cell(row=row_index, column=4, value=0)  # deltaQFM placeholder
                        
                        # Liquid thermodynamic properties
                        liq_props = step_data['liquid_properties']
                        liquid_sheet.cell(row=row_index, column=5, value=liq_props['Mass'])
                        liquid_sheet.cell(row=row_index, column=6, value=liq_props['Density'])
                        liquid_sheet.cell(row=row_index, column=7, value=liq_props['GibbsFreeEnergy']/1000)
                        liquid_sheet.cell(row=row_index, column=8, value=liq_props['Enthalpy']/1000)
                        liquid_sheet.cell(row=row_index, column=9, value=liq_props['Entropy'])
                        liquid_sheet.cell(row=row_index, column=10, value=liq_props['HeatCapacity'])
                        liquid_sheet.cell(row=row_index, column=11, value=liq_props['Volume'])
                        
                        if liq_props['Volume'] != 0:
                            liquid_sheet.cell(row=row_index, column=12, value=liq_props['DvDt']/liq_props['Volume'])
                            liquid_sheet.cell(row=row_index, column=13, value=liq_props['DvDp']/liq_props['Volume']*(-1))
                        
                        liquid_sheet.cell(row=row_index, column=14, value=liq_props['D2vDt2'])
                        liquid_sheet.cell(row=row_index, column=15, value=liq_props['D2vDp2'])
                        liquid_sheet.cell(row=row_index, column=16, value=liq_props['D2vDtDp'])
                        
                        # Liquid composition
                        liq_comp = step_data['liquid_composition']
                        oxides = ['SiO2', 'TiO2', 'Al2O3', 'Fe2O3', 'Cr2O3', 'FeO', 'MnO', 'MgO', 
                                'NiO', 'CoO', 'CaO', 'Na2O', 'K2O', 'P2O5', 'H2O', 'CO2', 'SO3', 
                                'Cl2O-1', 'F2O -1']
                        
                        for i, oxide in enumerate(oxides, 18):
                            liquid_sheet.cell(row=row_index, column=i, value=liq_comp.get(oxide, 0))
                        
                        # Write solid phase data - only for stable phases
                        solid_phases_dict = step_data.get('solid_phases', {})
                        
                        for phase_name, solid_data in solid_phases_dict.items():
                            all_solid_phases.add(phase_name)  # Track this phase as appearing
                            
                            # Create sheet for this phase if it doesn't exist yet
                            if phase_name not in solid_sheets:
                                sheet_name = phase_name.lower()
                                solid_sheet = wb.create_sheet(sheet_name)
                                solid_sheets[phase_name] = solid_sheet
                                solid_sheet_rows[phase_name] = 2  # Initialize row index for this sheet
                                
                                # Set up headers identical to liquid sheet
                                solid_headers = system_headers + ["SiO2 (wt%)", "TiO2 (wt%)", "Al2O3 (wt%)", "Fe2O3 (wt%)", 
                                                                 "Cr2O3 (wt%)", "FeO (wt%)", "MnO (wt%)", "MgO (wt%)", 
                                                                 "NiO (wt%)", "CoO (wt%)", "CaO (wt%)", "Na2O (wt%)", 
                                                                 "K2O (wt%)", "P2O5 (wt%)", "H2O (wt%)", "CO2 (wt%)",
                                                                 "SO3 (wt%)", "Cl2O-1 (wt%)", "F2O -1 (wt%)"]
                                
                                for col, header in enumerate(solid_headers, 1):
                                    solid_sheet.cell(row=1, column=col, value=header)
                            
                            solid_sheet = solid_sheets[phase_name]
                            solid_row = solid_sheet_rows[phase_name]  # Get the current row for this sheet
                            
                            # Write basic info
                            solid_sheet.cell(row=solid_row, column=1, value=row_index-1)  # Index (sequential for this sheet)
                            solid_sheet.cell(row=solid_row, column=2, value=step_data['temperature'])  # T (C)
                            solid_sheet.cell(row=solid_row, column=3, value=step_data['pressure'])  # P (MPa)
                            solid_sheet.cell(row=solid_row, column=4, value=0)  # deltaQFM placeholder
                            
                            # Write thermodynamic properties
                            solid_sheet.cell(row=solid_row, column=5, value=solid_data['Mass'])
                            solid_sheet.cell(row=solid_row, column=6, value=solid_data['Density'])
                            solid_sheet.cell(row=solid_row, column=7, value=solid_data['GibbsFreeEnergy']/1000)
                            solid_sheet.cell(row=solid_row, column=8, value=solid_data['Enthalpy']/1000)
                            solid_sheet.cell(row=solid_row, column=9, value=solid_data['Entropy'])
                            solid_sheet.cell(row=solid_row, column=10, value=solid_data['HeatCapacity'])
                            solid_sheet.cell(row=solid_row, column=11, value=solid_data['Volume'])
                            
                            if solid_data['Volume'] != 0:
                                solid_sheet.cell(row=solid_row, column=12, value=solid_data['DvDt']/solid_data['Volume'])
                                solid_sheet.cell(row=solid_row, column=13, value=solid_data['DvDp']/solid_data['Volume']*(-1))
                            else:
                                solid_sheet.cell(row=solid_row, column=12, value=0)
                                solid_sheet.cell(row=solid_row, column=13, value=0)
                            
                            solid_sheet.cell(row=solid_row, column=14, value=solid_data['D2vDt2'])
                            solid_sheet.cell(row=solid_row, column=15, value=solid_data['D2vDp2'])
                            solid_sheet.cell(row=solid_row, column=16, value=solid_data['D2vDtDp'])
                            solid_sheet.cell(row=solid_row, column=17, value="Success")
                            
                            # Composition data
                            solid_comp = solid_data['composition']
                            for i, oxide in enumerate(oxides, 18):
                                solid_sheet.cell(row=solid_row, column=i, value=solid_comp.get(oxide, 0))
                            
                            # Increment the row index for this specific sheet
                            solid_sheet_rows[phase_name] += 1
                        
                        row_index += 1
                        total_data_points += 1
        else:
            total_data_points = 0
        
        # Calculate totals
        total_calc_time = sum(r.get('total_calc_time', 0) for r in successful_results)
        
        # Save Excel file
        month_day = time.strftime("%m-%d")
        
        # Create directory if it doesn't exist
        import os
        if verbose:
            print(f"DEBUG: Creating directory for {label}")
        os.makedirs('parallel-results/fp-test', exist_ok=True)
        
        pressure_error = None
        P2 = (None, None, (np.nan, np.nan, np.nan))
        P3 = (None, None, (np.nan, np.nan, np.nan))
        if verbose:
            print(f"DEBUG: About to call pressure_calc for {label}")
        try:
            wb, P2, P3 = pressure_calc(wb=wb)
            if verbose:
                print(f"DEBUG: pressure_calc completed for {label}")
        except Exception as pressure_exc:
            pressure_error = str(pressure_exc)
            if verbose:
                print(f"DEBUG: pressure_calc skipped/failed for {label}: {pressure_error}")
        
        filename = f'parallel-results/fp-test/Parallel_MELTS_{label}_dP{params["delta_P"]}_{month_day}_{max_pressure_workers}cores.xlsx'
        if verbose:
            print(f"DEBUG: About to save Excel file for {label}: {filename}")
        wb.save(filename)
        if verbose:
            print(f"DEBUG: Excel file saved successfully for {label}")
        
        t1 = time.time()
        total_time = t1 - t0
        
        if verbose:
            print(f"Completed {label}: {len(successful_results)} pressure steps, {total_data_points} data points in {total_time:.2f}s")
            print(f"P_QF: {P2[0]}, P_Q2F: {P3[0]}")
            if all_solid_phases:
                print(f"  Found solid phases: {sorted(all_solid_phases)}")
            else:
                print(f"  No solid phases encountered")
        
        if verbose:
            print(f"DEBUG: About to return result for {label}")
        
        return {
            'label': label,
            'filename': filename,
            'num_pressure_steps': len(successful_results),
            'num_data_points': total_data_points,
            'P_QF': P2[0],
            'P_Q2F': P3[0],
            'total_time': total_time,
            'calc_time': total_calc_time,
            'pressure_error': pressure_error,
        }
        
    except Exception as e:
        print(f"Error processing composition {label}: {e}")
        return {
            'label': label,
            'error': str(e)
        }

def calculate_optimal_workers():
    """
    Calculate optimal worker counts based on system resources
    """
    import os
    import psutil
    
    # Get system info
    cpu_cores = os.cpu_count()
    memory_gb = psutil.virtual_memory().total / (1024**3)
    
    print(f"System: {cpu_cores} CPU cores, {memory_gb:.1f} GB RAM")
    
    # Conservative estimates for MELTS memory usage
    memory_per_worker_mb = 200  # MB per concurrent MELTS process
    available_memory_mb = memory_gb * 1024 * 0.8  # Use 80% of RAM
    
    # Calculate limits
    memory_limited_workers = int(available_memory_mb / memory_per_worker_mb)
    cpu_limited_workers = max(1, cpu_cores - 1)  # Leave one core for OS
    
    max_total_workers = min(memory_limited_workers, cpu_limited_workers)
    
    print(f"Limits: CPU={cpu_limited_workers}, Memory={memory_limited_workers}")
    print(f"Recommended max total concurrent workers: {max_total_workers}")
    
    # Suggest composition vs pressure worker splits
    suggestions = []
    for comp_workers in range(1, min(6, max_total_workers + 1)):
        pressure_workers = min(6, max_total_workers // comp_workers)
        if pressure_workers >= 1:
            total = comp_workers * pressure_workers
            suggestions.append((comp_workers, pressure_workers, total))
    
    print("\nRecommended configurations:")
    print("Composition_workers × Pressure_workers = Total")
    for comp, press, total in suggestions:
        print(f"    {comp:2d} × {press:2d} = {total:2d}")
    
    return max_total_workers, suggestions

def parallel_melts_main_loop(compositions_csv, max_composition_workers=1, max_pressure_workers=4, verbose=True):
    """
    Main function to run MELTS calculations with futureproof parallel processing.
    Uses futureproof TaskManager for robust error handling and container compatibility.
    
    Parameters:
    - max_composition_workers: Number of compositions to process simultaneously
    - max_pressure_workers: Number of pressure steps per composition to process simultaneously
    - verbose: If True, print progress and debug information
    """
    import pandas as pd
    import numpy as np
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from thermoengine import core, model
    import time
    
    if verbose:
        print("Starting module-based parallel MELTS calculations...")
    t0 = time.time()
    
    # Load compositions
    try:
        comps = pd.read_csv(compositions_csv, index_col=0)
    except pd.errors.ParserError as e:
        print(f"CSV parsing error: {e}")
        print("Attempting to read with error handling for inconsistent field counts...")
        # Try reading with error handling for inconsistent field counts
        comps = pd.read_csv(compositions_csv, index_col=0, on_bad_lines='skip')
    labels = list(comps)
    if verbose:
        print(f"Found {len(labels)} compositions to process")
    
    # Prepare composition data for parallel processing
    composition_tasks = []
    
    # Set up thermoengine objects for composition conversion
    modelDB = _build_model_database()
    Liquid = modelDB.get_phase('Liq')
    elm_sys_main = ['H','O','Na','Mg','Al','Si','P','K','Ca','Ti','Cr','Mn','Fe','Co','Ni']
    
    
    for label in labels:
        curCol = comps[label]
        composition = _extract_composition_dict(curCol)
        
        # Skip if missing required components
        if ('SiO2' not in composition) or ('Al2O3' not in composition):
            if verbose:
                print(f"Skipping {label}: missing required oxides")
            continue
        
        # Build parameters
        params = {
            'T1': _parse_required_float(curCol, 'T1'),
            'T2': _parse_required_float(curCol, 'T2'),
            'delta_T': _parse_required_float(curCol, 'ΔT'),
            'P1': _parse_required_float(curCol, 'P1'),
            'P2': _parse_required_float(curCol, 'P2'),
            'delta_P': _parse_required_float(curCol, 'ΔP'),
            'const_fO2': curCol['fO2 constraint'],
            'fO2Path': curCol['fO2 buffer'],
            'fO2_offset': _parse_required_float(curCol, 'fO2 offset', zero_to_epsilon=True),
        }
        
        # Only process QF_P_Calc for now
        if curCol['Calculation'] != 'QF_P_Calc':
            if verbose:
                print(f"Skipping {label}: not QF_P_Calc")
            continue 
        
        # Convert composition to molar format
        try:
            mol_oxides = core.chem.format_mol_oxide_comp(composition, convert_grams_to_moles=True)
            moles_end, oxide_res = Liquid.calc_endmember_comp(
                mol_oxide_comp=mol_oxides, method='intrinsic', output_residual=True)
            
        
            if not Liquid.test_endmember_comp(moles_end):
                if verbose:
                    print(f"Skipping {label}: infeasible composition")
                continue
            
            mol_elm = Liquid.convert_endmember_comp(moles_end, output='moles_elements')
            blk_cmp = np.array([mol_elm[core.chem.PERIODIC_ORDER.tolist().index(elm)] for elm in elm_sys_main])
            composition_tasks.append((label, composition, params, blk_cmp, max_pressure_workers, verbose))
            
        except Exception as e:
            if verbose:
                print(f"Error preparing {label}: {e}")
            continue
        
        
    
    if verbose:
        print(f"Prepared {len(composition_tasks)} compositions for parallel processing")
    
    # Process compositions using the module function
    results = []
    
    if max_composition_workers > 1:
        if verbose:
            print(f"Processing {max_composition_workers} compositions in parallel using futureproof...")
        
        # Use futureproof approach with better error handling
        executor = FutureproofThreadPoolExecutor(max_workers=max_composition_workers)
        with TaskManager(executor, error_policy=ErrorPolicyEnum.RAISE) as tm:
            # Submit all tasks and keep track of original task data
            task_to_original = {}
            for original_task in composition_tasks:
                futureproof_task = tm.submit(process_single_composition_parallel, original_task)
                task_to_original[futureproof_task] = original_task
            
            # Process completed tasks as they finish
            completed_count = 0
            for futureproof_task in tm.as_completed():
                completed_count += 1
                original_task = task_to_original[futureproof_task]
                
                try:
                    if verbose:
                        print(f"DEBUG: Getting result from futureproof task {completed_count}/{len(composition_tasks)}")
                    
                    result = _task_result(futureproof_task)
                    
                    if 'error' in result:
                        results.append(result)
                        if verbose:
                            print(f"✗ Failed: {result['label']} - {result['error']}")
                        continue
                    
                    results.append(result)
                    if verbose:
                        print(f"✓ Completed: {result['filename']} ({result['num_data_points']} data points)")
                        
                except Exception as exc:
                    if verbose:
                        print(f"Composition processing failed: {exc}")
                    results.append({'label': original_task[0], 'error': str(exc)})
    else:
        if verbose:
            print("Processing compositions serially with parallel pressure steps...")
        for i, task in enumerate(composition_tasks):
            try:
                if verbose:
                    print(f"DEBUG: Starting composition {i+1}/{len(composition_tasks)}: {task[0]}")
                result = process_single_composition_parallel(task)
                if verbose:
                    print(f"DEBUG: Got result from composition {i+1}: {task[0]}")
                results.append(result)
                if 'error' not in result:
                    if verbose:
                        print(f"✓ Completed: {result['filename']} ({result['num_data_points']} data points)")
                else:
                    if verbose:
                        print(f"✗ Failed: {result['label']} - {result['error']}")
            except Exception as exc:
                if verbose:
                    print(f"Composition processing failed: {exc}")
                results.append({'label': task[0], 'error': str(exc)})
    
    t1 = time.time()
    total_time = t1 - t0
    
    if verbose:
        print(f"\\nModule parallel processing complete!")
        print(f"Total time: {total_time:.2f}s")
        print(f"Successfully processed: {len([r for r in results if 'error' not in r])}/{len(composition_tasks)} compositions")
    
    return results
    

def test_pressure():
    CSV_Filename = "MarvinApliteComps.csv"
    modelDB = _build_model_database()
    Liquid = modelDB.get_phase('Liq')
    Feldspar = modelDB.get_phase('Fsp')
    Quartz = modelDB.get_phase('Qz')
    Spinel = modelDB.get_phase('SplS')
    Opx = modelDB.get_phase('Opx')
    RhomOx = _get_phase_compat(modelDB, 'Rhom', fallback_code='Mag')
    Water = _build_water_phase(modelDB)
    elm_sys = ['H','O','Na','Mg','Al','Si','P','K','Ca','Ti','Cr','Mn','Fe','Co','Ni']
    phs_sys = [Liquid, Feldspar, Water, Quartz, Spinel, Opx, RhomOx]
    def muH2O(t, p, state):
        return Water.gibbs_energy(t, p)
    comps = pd.read_csv(CSV_Filename,index_col=0)
    labels = list(comps)

    length = len(labels)

    # equilibrate code
    equil = equilibrate.Equilibrate(elm_sys, phs_sys)#, lagrange_l=[({'H':2.0,'O':1.0},muH2O)])
    n = 0
    while n < length:
        # read composition, check for acceptability, read initial conditions
        curCol = comps[labels[n]]
        composition = _extract_composition_dict(curCol)
        print(composition)

        params = {
            'T1': _parse_required_float(curCol, 'T1'),
            'T2': _parse_required_float(curCol, 'T2'),
            'delta_T': _parse_required_float(curCol, 'ΔT'),
            'P1': _parse_required_float(curCol, 'P1'),
            'P2': _parse_required_float(curCol, 'P2'),
            'delta_P': _parse_required_float(curCol, 'ΔP'),
            'const_fO2': curCol['fO2 constraint'],
            'fO2Path': curCol['fO2 buffer'],
            'fO2_offset': _parse_required_float(curCol, 'fO2 offset', zero_to_epsilon=True),
        }

        MELTS_Model = curCol['Model'][16:]
        print(MELTS_Model)
        model_versions = {
            '1.0.x': '1.0.2',
            '1.2.x': '1.2.0',
            '5.6.x': '5.6.1',
            '1.1.x': '1.1.0'
        }
        MELTS_Model = model_versions.get(MELTS_Model, MELTS_Model)
        print(MELTS_Model)
        params['MELTS_Model'] = MELTS_Model
        params['CalcType'] = curCol['Calculation']

        # calculate composition in moles (format required for equilibrate calculation)
        mol_oxides = core.chem.format_mol_oxide_comp(composition, convert_grams_to_moles=True)
        moles_end,oxide_res = Liquid.calc_endmember_comp(
            mol_oxide_comp=mol_oxides, method='intrinsic', output_residual=True)
        if not Liquid.test_endmember_comp(moles_end):
            print ("Calculated composition is infeasible!")
        mol_elm = Liquid.convert_endmember_comp(moles_end,output='moles_elements')
        # order oxides in bulk composition for calculations
        blk_cmp = []
        for elm in elm_sys:
            index = core.chem.PERIODIC_ORDER.tolist().index(elm)
            blk_cmp.append(mol_elm[index])
        blk_cmp = np.array(blk_cmp)
        
        # specificy liquid fraction of interest
        liq_fr = 0.1

        temp = find_wet_liquidus(equil, params['T1'], params['T2'], 125, 10, blk_cmp, params['fO2_offset'])
        print(temp)
        state = equil.execute(temp-1+273.15, 125*10, bulk_comp=blk_cmp, con_deltaNNO=params['fO2_offset'], debug=0)
        print(state.properties())
        n += 1
if __name__ == '__main__':
    test_pressure()
