from collections.abc import Callable
from typing import Any
import os

import numpy as np
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt


class Interface:
    
    @staticmethod
    def ask_for_parameter(param_name, type_caster, query=None, default='no default'):
        raise NotImplementedError
    
    @staticmethod
    def ask_for_multiple(parameters, types=None, group_name=None, defaults=None):
        raise NotImplementedError

    @staticmethod
    def ask_for_file(query=None):
        raise NotImplementedError

    @staticmethod
    def ask_question(query, options=None):
        raise NotImplementedError

    @staticmethod
    def send_message(msg):
        raise NotImplementedError

    @staticmethod
    def interactive(parameters: dict, types: list, func: Callable[..., Any]):
        raise NotImplementedError


class CLIInterface(Interface):
    
    @staticmethod
    def ask_for_parameter(param_name, type_caster, query=None, default='no default'):
        if query is None:
            query = f'Set {param_name}'
        
        query_text = query
        if default != 'no default':
            query_text += f" (default: {default})"
        query_text += ": "

        user_input = 'starting value'
        value = 'no value'

        while user_input and value == 'no value':
            try:
                user_input = input(query_text)
                if not user_input and default != 'no default':
                    value = default
                elif not user_input:
                    continue
                else:
                    value = type_caster(user_input)
                print(f"-> {param_name} set to: {value}")
            except (ValueError, TypeError):
                error_msg = f"Invalid value for '{param_name}'. Expected type {type_caster.__name__}."
                print(error_msg)
        
        if value != 'no value':
            return value, 'ok'
        else:
            return None, 'force stop'
    
    @staticmethod
    def ask_for_multiple(parameters, types=None, group_name=None, defaults=None):
        if defaults is None:
            defaults = dict()

        if group_name is not None:
            CLIInterface.send_message(f'-- Setting {group_name} parameters --')
        else:
            CLIInterface.send_message(f'-- Setting parameters --')

        if defaults is not None and all(p in defaults for p in parameters):
            CLIInterface.send_message('Default parameters available:\n' + '\n'.join(f'{p}: {defaults[p]}' for p in parameters) + '\n')
            accept_defaults, exec_msg = CLIInterface.ask_question(
                f'Do you accept the default parameters? (yes/no, default is yes) ')
            if exec_msg == 'force_stop':
                return None, 'force_stop'
            if accept_defaults.lower().startswith('y') or not accept_defaults:
                CLIInterface.send_message('Accepted default parameters')
                return defaults, 'ok'
        
        assert types is not None
        new_parameters = dict()
        for p, t in zip(parameters, types):
            v, exec_msg = CLIInterface.ask_for_parameter(p, t, default=defaults.get(p, 'no default'))
            if exec_msg == 'force_stop':
                return None, 'force_stop'
            new_parameters[p] = v
        
        return new_parameters, 'ok'

    @staticmethod
    def ask_for_file(query=None):
        assert query is not None
        query += ': '
        filepath = input(query)
        while not os.path.exists(filepath):
            print(f'{filepath} does not exist')
            filepath = input(query)
            if not filepath:
                break
        
        if filepath:
            return filepath, 'ok'
        else:
            return None, 'force stop'

    @staticmethod
    def ask_question(query, options=None):
        return input(query), 'ok'

    @staticmethod
    def send_message(msg):
        print(msg)

    @staticmethod
    def interactive(parameters: dict, types: list, func: Callable[..., Any], continue_query: str = 'Adjust parameters?'):
        continue_query += ' (yes/no, default yes) '
        run_cycle = 'yes'
        exec_msg = 'ok'
        
        while run_cycle.startswith('y'):
            for (p, v), t in zip(parameters.items(), types):
                v, exec_msg = CLIInterface.ask_for_parameter(p, t, default=v)
                if exec_msg != 'ok':
                    return None, exec_msg
                parameters[p] = v
            
            func(**parameters)

            run_cycle = CLIInterface.ask_question(continue_query)[0].lower()
            if not run_cycle:
                run_cycle = 'yes'

        return parameters, exec_msg


def get_bright_fire_cmap():
    """
    Creates and registers a custom matplotlib colormap named 'bright_fire'.

    The colormap is a linear gradient from bright red, through bright orange, to bright yellow.
    - Red: Corresponds to the lowest value.
    - Yellow: Corresponds to the highest value.

    Returns:
        matplotlib.colors.Colormap: The created colormap object.
    """
    # Define bright shades of red, orange, and yellow using hex codes
    cmap_name = 'bright_fire'
    colors = ['#FF4136', '#FF851B', '#FFDC00']  # Bright Red -> Bright Orange -> Bright Yellow
    bright_fire_cmap = mcolors.LinearSegmentedColormap.from_list(cmap_name, colors)
    return bright_fire_cmap, cmap_name


