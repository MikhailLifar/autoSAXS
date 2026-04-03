"""Threading environment configuration for GUI application."""
import os
import atexit

# Threading environment variable names - comprehensive list
_THREADING_ENV_VARS = [
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS',
    'BLIS_NUM_THREADS',
    'TBB_NUM_THREADS',
    'NUMBA_NUM_THREADS',
]

# Save original values
_ORIGINAL_THREADING_ENV = {}
for var in _THREADING_ENV_VARS:
    _ORIGINAL_THREADING_ENV[var] = os.environ.get(var)


def setup_threading_env():
    """
    Set threading environment variables to 1 to prevent deadlocks in worker threads.
    This must be called BEFORE any NumPy/SciPy/pyFAI imports.
    """
    # Set to 1 thread to prevent deadlocks in worker threads
    for var in _THREADING_ENV_VARS:
        os.environ[var] = '1'


def restore_threading_env():
    """Restore original threading environment variables."""
    for var, original_value in _ORIGINAL_THREADING_ENV.items():
        if original_value is None:
            # Variable wasn't set originally, remove it
            os.environ.pop(var, None)
        else:
            # Restore original value
            os.environ[var] = original_value


# Initialize threading environment when module is imported
setup_threading_env()

# Register cleanup function to run on exit
atexit.register(restore_threading_env)

