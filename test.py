import yaml
from processor import *
from interface import *
from viewer import *
import os
import sys
import logging
import warnings
import json

import numpy as np
import pandas as pd
import matplotlib as mpl
import matplotlib.pyplot as plt
import seaborn as sns

sys.path.append(os.path.expanduser('~/LLM/LLMAssistant'))
sys.path.append(os.path.expanduser('~/LLM/LLMAssistant/aiAssistantFramework'))

from aiAssistantFramework import lib as ai_lib
from aiAssistantFramework.lib import llm, telegram
import controller as ai_controller


def visual_model_test():
    model = 'GLM-4.5V'
    image_path = 'debug/pipeline0/sub_0002_ihs27_95.9.png'
    text = 'Describe the shape of the 1D SAXS data in this plot. Focus on the overall curve characteristics, peak positions, and any notable features.'

    messages = get_image_messages(image_path, text)
    answer, tokens = llm.send_request_to_llm(model=model, messages=messages)

    print(answer)


if __name__ == '__main__':
    visual_model_test()
