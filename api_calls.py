from autosaxs import api

"""
1. the first argument is the directory of the following structure:
directory/
  raw/
    *_calib.tif  #
    *_buffer.tif  # arbitrary number of buffer images
    *_sample.tif  # arbitrary number of sample images
  config.conf  # configuration file
  mask*  # mask file (.msk extension)
"""
api.fast_first_processing('debug/protein_v0_interactive')

"""
1. the first argument is the directory of the following structure:
directory/
  subtracted/
    *.dat  # arbitrary number of sample .dat files with 1d SAXS data
  config.conf  # configuration file
2. the second argument is the list of file names: 
file names should be from "{directory}/subtracted" subdirectory
"""
api.slow_second_processing(
    'debug/protein_v0_interactive',
    [
        'sub_ihs27_sample.dat',
        'sub_ihs28_sample.dat',
        'sub_ihs29_sample.dat'
    ]
)
