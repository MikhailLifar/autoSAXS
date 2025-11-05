from collections.abc import Callable
import time
from typing import Any, Optional
import os
import copy
import glob

import threading
import sys

import numpy as np
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt


class PipelineInterrupt(Exception):
    """Exception raised when user wants to force early program termination."""
    pass


class Interface:

    @staticmethod
    def ask_for_parameter(param_name, type_caster, query=None, default='no default'):
        raise NotImplementedError
    
    @staticmethod
    def ask_for_multiple(parameters, types=None, group_name=None, defaults=None):
        raise NotImplementedError

    @staticmethod
    def ask_for_file(query=None, obligatory=True):
        raise NotImplementedError

    @staticmethod
    def wait_for_file(directory, obligatory=True, query=None, wait=2.0, wait_to_end_loading=2.0, filepattern='*'):
        raise NotImplementedError

    @staticmethod
    def ask_question(query, options=None, default_op=None):
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
            return value
        else:
            raise PipelineInterrupt(f"User terminated parameter input for '{param_name}'")
    
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
            accept_defaults = CLIInterface.ask_question(
                f'Do you accept the default parameters? (yes/no, default is yes) ')
            if accept_defaults.lower().startswith('y') or not accept_defaults:
                CLIInterface.send_message('Accepted default parameters')
                return {p: copy.deepcopy(defaults[p]) for p in parameters}
        
        assert types is not None
        new_parameters = dict()
        for p, t in zip(parameters, types):
            v = CLIInterface.ask_for_parameter(p, t, default=defaults.get(p, 'no default'))
            new_parameters[p] = v
        
        return new_parameters

    @staticmethod
    def ask_for_file(query=None, obligatory=True):
        assert query is not None
        query += ': '
        filepath = input(query)
        run_condition = not os.path.exists(filepath) and (obligatory or filepath)
        while run_condition:
            print(f'{filepath} does not exist')
            filepath = input(query)
            run_condition = not os.path.exists(filepath) and (obligatory or filepath)
            if not filepath and obligatory:
                break
        
        if filepath:
            return filepath
        elif obligatory:
            raise PipelineInterrupt("User terminated file selection")
        return ''
    
    @staticmethod
    def wait_for_file(directory, query=None, obligatory=True, wait=2.0, wait_to_end_loading=2.0, filepattern='*'):
        assert query is not None
        
        def _get_files():
            return glob.glob(os.path.join(directory, filepattern), recursive=True)

        old_files = _get_files()
        
        query += ': '
        print(query, flush=True)

        # Shared state between threads
        result = {'new_file': None, 'user_interrupted': False}
        stop_event = threading.Event()

        def monitor_files():
            while not stop_event.is_set():
                current_files = set(_get_files())
                old_files_set = set(old_files)
                if current_files != old_files_set:
                    diff = current_files - old_files_set
                    if len(diff) == 1:
                        new_file = diff.pop()
                        # Wait a bit more to ensure file is fully written
                        time.sleep(wait_to_end_loading)
                        result['new_file'] = new_file
                        stop_event.set()
                        return
                    elif len(diff) > 1:
                        # Multiple files appeared - this violates the assumption
                        result['new_file'] = None
                        stop_event.set()
                        return
                time.sleep(wait)

        def wait_for_input():
            """Wait for the user to press Enter."""
            try:
                input()
                if not stop_event.is_set():
                    result['user_interrupted'] = True
                    stop_event.set()
            except EOFError:
                # Handle cases where input() might not work (e.g., in some IDEs)
                pass

        # Start both threads
        file_thread = threading.Thread(target=monitor_files, daemon=True)
        input_thread = threading.Thread(target=wait_for_input, daemon=True)
        
        file_thread.start()
        input_thread.start()

        # Wait until one of the threads signals completion
        stop_event.wait()

        # Clean up threads
        # Note: daemon=True means they'll be killed when main thread exits

        if result['user_interrupted']:
            ret = ""
        elif result['new_file'] is not None:
            ret = result['new_file']
        else:
            # This shouldn't happen under normal circumstances due to the assertion
            # that only one file can be processed, but we handle it gracefully
            raise RuntimeError("Multiple files appeared simultaneously or unexpected error occurred")
        
        if obligatory and not ret:
            raise PipelineInterrupt('The user interrupted the pipeline execution')
        
        return ret


    @staticmethod
    def ask_question(query, options: Optional[dict] = None, default_op='no default'):
        if options is None:
            options = dict()
        query += ' '
        if options:
            query += '(' + '/'.join(f'{op_short}: {op}' for op_short, op in options.items()) \
            + f', default: {default_op}' * (default_op != 'no default') + ') '
        answ = input(query)
        if not answ and default_op != 'no default':
            CLIInterface.send_message(f'Set to default: {default_op}')
            answ = default_op
        if options:
            while answ not in options and answ != default_op:
                if answ or default_op == 'no default':
                    CLIInterface.send_message(f'Answer "{answ}" is not valid!')
                    answ = input(query)
                else:
                    CLIInterface.send_message(f'Set to default: {default_op}')
                    answ = default_op
        return answ

    @staticmethod
    def send_message(msg):
        print(msg)

    @staticmethod
    def interactive(parameters: dict, types: list, func: Callable[..., Any], continue_query: str = 'Adjust parameters?'):
        continue_query += ' (yes/no, default yes) '
        run_cycle = 'yes'
        func_ret = None
        
        while run_cycle.startswith('y'):
            for (p, v), t in zip(parameters.items(), types):
                v = CLIInterface.ask_for_parameter(p, t, default=v)
                parameters[p] = v
            
            func_ret = func(**parameters)

            run_cycle = CLIInterface.ask_question(continue_query).lower()
            if not run_cycle:
                run_cycle = 'yes'

        return parameters, func_ret
