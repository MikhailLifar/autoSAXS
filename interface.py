from collections.abc import Callable, Iterable
import time
from typing import Any, Optional
import os
import copy
import glob

import threading
import sys
import select

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
    def wait_for_file(
        directory, obligatory=False, query=None, 
        wait=2.0, wait_to_end_loading=2.0, 
        filepattern='*', skip_if_exists=False, except_prev_paths=False,
        allow_same_time=(1, float('inf'))):
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
    def wait_for_file(
        directory, query=None, obligatory=False, 
        wait=2.0, wait_to_end_loading=2.0, 
        filepattern='*', skip_if_exists=True, except_prev_paths=False,
        allow_same_time=(1, float('inf'))):
        assert query is not None

        def _get_all_files():
            return set(glob.glob(os.path.join(directory, '**/*'), recursive=True))

        def _get_pattern_files():
            return set(glob.glob(os.path.join(directory, filepattern), recursive=True))
        
        min_allowed, max_allowed = allow_same_time
        
        if skip_if_exists:
            pattern_files = _get_pattern_files()
            assert except_prev_paths is not True
            if isinstance(except_prev_paths, Iterable):
                pattern_files = pattern_files - set(except_prev_paths)
            pattern_files = list(pattern_files)
            if min_allowed <= len(pattern_files) <= max_allowed:
                CLIInterface.send_message(f"Skipped file uploading for pattern '{filepattern}', since found existing files")
                return pattern_files

        old_files = _get_all_files()

        query += ': '
        print(query, flush=True)

        # Shared state between threads
        result = {'new_files': None, 'user_interrupted': False}
        stop_event = threading.Event()
        
        def monitor_files():
            nonlocal old_files
            while not stop_event.is_set():
                current_files = _get_all_files()
                # print('Old: ', old_files)
                # print('Current: ', current_files)
                added_files = current_files - old_files
                # print('Added: ', added_files)
                if added_files:
                    # Prefer to process one file at a time
                    pattern_files = [p for p in _get_pattern_files() if p in added_files]
                    if min_allowed <= len(pattern_files) <= max_allowed:
                        time.sleep(wait_to_end_loading * (1.0 + np.log(len(pattern_files))))
                        result['new_files'] = pattern_files
                        stop_event.set()
                        pattern_files_str = '\n  '.join(pattern_files)
                        CLIInterface.send_message(f"Uploaded files:\n{pattern_files_str}")
                        return
                    elif len(pattern_files) == 0:
                        CLIInterface.send_message(f"No files match the required pattern '{filepattern}'. Please upload a correct file.")
                    else:
                        CLIInterface.send_message(f"The number of uploaded files matching '{filepattern}' should be >= {min_allowed} and <= {max_allowed}. Please make a correct upload.")
                old_files = current_files
                time.sleep(wait)

        def wait_for_input():
            """
            Wait for the user to press Enter in a way that does not leave
            a background thread blocking on input() after wait_for_file
            has already returned.

            We use select.select on sys.stdin with a timeout so that:
            - The thread periodically checks stop_event and exits promptly
              once a file has been detected.
            - Exactly one logical "Enter" press is consumed for the
              interruption, avoiding interference with later input() calls.
            """
            # Reuse the same polling interval as the file monitor.
            timeout = wait

            while not stop_event.is_set():
                try:
                    # Wait until there is something to read on stdin, or timeout.
                    rlist, _, _ = select.select([sys.stdin], [], [], timeout)
                except (OSError, ValueError):
                    # In unusual environments stdin might not be selectable;
                    # in that case, just exit and don't attempt interactive interrupt.
                    return

                if stop_event.is_set():
                    # File thread already finished; do not consume user input.
                    return

                if not rlist:
                    # Timeout expired; loop again to re-check stop_event.
                    continue

                # There is input available on stdin; read one line.
                line = sys.stdin.readline()

                if stop_event.is_set():
                    # A file appeared around the same time; ignore this line.
                    return

                # Treat any line (including just Enter) as user interruption.
                CLIInterface.send_message('User interrupted file selection')
                result['user_interrupted'] = True
                stop_event.set()
                return

        # Start both threads
        file_thread = threading.Thread(target=monitor_files, daemon=True)
        input_thread = threading.Thread(target=wait_for_input, daemon=True)

        file_thread.start()
        input_thread.start()

        # Wait until one of the threads signals completion
        stop_event.wait()

        # Give a small moment for threads to finish their current operations
        # This ensures result dict is fully updated before we check it
        time.sleep(0.1)

        # Clean up threads
        # Note: daemon=True means they'll be killed when main thread exits

        if result['user_interrupted']:
            new_files = []
        elif result['new_files'] is not None:
            new_files = result['new_files']
        else:
            # This shouldn't happen under normal circumstances due to the assertion
            # that only one file can be processed, but we handle it gracefully
            raise RuntimeError("Unexpected error occurred")

        if obligatory and not new_files:
            raise PipelineInterrupt('The user interrupted the pipeline execution')
        
        if except_prev_paths is True:
            return new_files
        elif except_prev_paths is False:
            return list(_get_pattern_files())
        elif isinstance(except_prev_paths, Iterable):
            return list(_get_pattern_files() - set(except_prev_paths))


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
