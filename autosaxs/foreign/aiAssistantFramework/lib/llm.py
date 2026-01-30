"""
Minimal vendored copy of aiAssistantFramework.lib.llm for autosaxs.
Only send_request_to_llm and helpers. API keys from environment variables when set.
"""
import os
import json
import logging
import re
import random
import string
from copy import deepcopy

import numpy as np
import requests
from openai import OpenAI
import openai
import httpx

logger = logging.getLogger(name=__name__)

# Optional: GigaChat (only used if model is GigaChat-Pro or GigaChat)
try:
    from gigachat import GigaChat
    from gigachat.models import Chat, Messages, MessagesRole
    _GIGACHAT_AVAILABLE = True
except ImportError:
    _GIGACHAT_AVAILABLE = False

available_models = [
    'Qwen-plus', 'Qwen3-max', 'GLM-4.6', 'GLM-4.5V', 'DeepSeek-V3.1', 'DeepSeek-V3.1-Think',
    'Llama-3.3-70B-Instruct-Turbo', 'Llama-4-Maverick-17B-128E-Instruct-FP8',
    'Llama-4-Scout-17B-16E-Instruct', 'DeepSeek-R1-Distill-Llama-70B',
    'gemma-3-27b-it', 'gemma-3-12b-it', 'gemma-3-4b-it',
]
prices = {
    'Llama-3.3-70B-Instruct': 0.23, 'Llama-3.3-70B-Instruct-Turbo': 0.12,
    'Llama-4-Maverick-17B-128E-Instruct-FP8': 0.17, 'Llama-4-Scout-17B-16E-Instruct': 0.08,
    'DeepSeek-V3.1': 0.56, 'DeepSeek-V3.1-Think': 0.56, 'DeepSeek-R1-Distill-Llama-70B': 0.23,
    'Qwen3-Next-80B-A3B-Instruct': 0.14, 'Qwen3-Coder-480B-A35B-Instruct': 0.4,
    'Qwen3-235B-A22B-Instruct-2507': 0.09, 'gemma-3-27b-it': 0.1, 'gemma-3-12b-it': 0.05,
    'gemma-3-4b-it': 0.02, 'GigaChat-Pro': 15, 'GigaChat': 2, 'YandexGPT': 6,
    'GLM-4.6': 0.6, 'GLM-4.5V': 0.6, 'Qwen3-max': 2.4, 'Qwen-plus': 1.2,
}

# API keys: from environment (AUTOSAXS_LLM_*) with fallbacks for backward compatibility
def _get_api():
    return {
        'DEEPINFRA_KEY': os.environ.get('AUTOSAXS_LLM_DEEPINFRA_KEY', os.environ.get('DEEPINFRA_KEY', '')),
        'GIGACHAT_KEY': os.environ.get('AUTOSAXS_LLM_GIGACHAT_KEY', os.environ.get('GIGACHAT_KEY', '')),
        'YA_PROJ_ID': os.environ.get('AUTOSAXS_LLM_YA_PROJ_ID', ''),
        'YA_API_ID': os.environ.get('AUTOSAXS_LLM_YA_API_ID', ''),
        'YA_API_TOKEN': os.environ.get('AUTOSAXS_LLM_YA_API_TOKEN', ''),
        'DEEPSEEK': os.environ.get('AUTOSAXS_LLM_DEEPSEEK', os.environ.get('DEEPSEEK_API_KEY', '')),
        'GLM': os.environ.get('AUTOSAXS_LLM_GLM', ''),
        'QWEN': os.environ.get('AUTOSAXS_LLM_QWEN', os.environ.get('DASHSCOPE_API_KEY', '')),
    }


def _content_to_string(content):
    """Flatten content to string for backends that expect string (GigaChat, Yandex)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get('type') == 'text' and 'text' in part:
                    parts.append(part['text'])
                elif part.get('type') == 'image_url' or part.get('image_url'):
                    parts.append('[image]')
        return '\n'.join(parts)
    return str(content)


def _messages_for_string_backend(messages):
    """Return copy of messages with content as string (for GigaChat, Yandex)."""
    out = []
    for m in messages:
        out.append({'role': m['role'], 'content': _content_to_string(m['content'])})
    return out


def convert_messages_to_giga_chat(messages):
    """Expects messages with string content."""
    if not _GIGACHAT_AVAILABLE:
        raise RuntimeError('gigachat package is not installed')
    giga_messages = []
    for message in messages:
        role = message['role']
        if role == 'system':
            r = MessagesRole.SYSTEM
        elif role == 'user':
            r = MessagesRole.USER
        elif role == 'assistant':
            r = MessagesRole.ASSISTANT
        else:
            r = MessagesRole.USER
        content = _content_to_string(message['content'])
        giga_messages.append(Messages(role=r, content=content))
    return giga_messages


def convert_messages_to_yandex_gpt(messages):
    """Expects messages with string content."""
    yandex_messages = []
    for message in messages:
        yandex_messages.append({
            'role': message['role'],
            'text': _content_to_string(message['content']),
        })
    return yandex_messages


def calc_confidence(chat_completion):
    if chat_completion is None:
        return None
    logprobs_data = getattr(chat_completion.choices[0], 'logprobs', None)
    if logprobs_data is None:
        return None
    content = getattr(logprobs_data, 'content', None)
    if content is None:
        return None
    logprobs = [getattr(token, 'logprob', 0) for token in content]
    m = chat_completion.usage.completion_tokens
    if m == 0:
        return None
    C_SMC = np.sum(logprobs)
    alpha = 0.8
    coef_NMT = ((5 + m) ** alpha) / (6 ** alpha)
    C_NMT = np.exp(C_SMC / coef_NMT)
    return C_NMT


def send_request_to_llm(model, messages, temperature=1.0, max_tokens=4096, disable_cache=True, return_confidence=False):
    assert model in available_models, f"Unknown model {model}. List: {available_models}"
    logger.info('Send request to the model %s', model)
    API = _get_api()

    def no_result():
        return (None, 0, None) if return_confidence else (None, 0)

    messages = deepcopy(messages)
    # Normalize for disable_cache (count length) and for backends that need string content
    messages_str = _messages_for_string_backend(messages)

    if disable_cache:
        count = np.sum([len(m['content']) for m in messages_str])
        if count > 4 * 1024:
            s = messages_str[0]['content']
            n = 10
            if re.match(r'\d' * n + r'\s', s[:n + 1]):
                s = s[n + 1:]
            trash = ''.join(random.choice(string.digits) for _ in range(n))
            messages[0]['content'] = trash + ' ' + (messages_str[0]['content'] if isinstance(messages[0]['content'], str) else _content_to_string(messages[0]['content']))
            messages_str[0]['content'] = trash + ' ' + s

    chat_completion = None

    if model in ["GigaChat-Pro", "GigaChat"]:
        if not _GIGACHAT_AVAILABLE:
            logger.error('GigaChat requested but gigachat package is not installed')
            return no_result()
        credentials = API.get('GIGACHAT_KEY') or ''
        if not credentials:
            logger.error('GigaChat API key not set (AUTOSAXS_LLM_GIGACHAT_KEY or GIGACHAT_KEY)')
            return no_result()
        giga_messages = convert_messages_to_giga_chat(messages_str)
        chat = Chat(messages=giga_messages, temperature=temperature, max_tokens=max_tokens)
        try:
            with GigaChat(credentials=credentials, verify_ssl_certs=False, model=model, timeout=60) as giga:
                chat_completion = giga.chat(chat)
        except httpx.HTTPError as e:
            logger.error('Error in LLM request to GigaChat: %s', e)
            return no_result()
        except Exception as e:
            logger.error('Unknown error in LLM request to GigaChat: %s', e)
            return no_result()
        try:
            total_tokens = chat_completion.usage.total_tokens
            chat_completion_text = chat_completion.choices[0].message.content
        except Exception as e:
            logger.error('GigaChat response missing total_tokens/content: %s', e)
            return no_result()

    elif model == "YandexGPT":
        token = API.get('YA_API_TOKEN') or ''
        proj = API.get('YA_PROJ_ID') or ''
        if not token or not proj:
            logger.error('YandexGPT API keys not set (AUTOSAXS_LLM_YA_API_TOKEN, AUTOSAXS_LLM_YA_PROJ_ID)')
            return no_result()
        prompt = {
            "modelUri": f"gpt://{proj}/yandexgpt/latest",
            "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": max_tokens},
            "messages": convert_messages_to_yandex_gpt(messages_str),
        }
        url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
        headers = {"Content-Type": "application/json", "Authorization": f"Api-Key {token}"}
        try:
            resp = requests.post(url, headers=headers, json=prompt)
            chat_completion = resp.json()
        except requests.RequestException as e:
            logger.error('Error in LLM request to YandexGPT: %s', e)
            return no_result()
        except Exception as e:
            logger.error('Unknown error in LLM request to YandexGPT: %s', e)
            return no_result()
        if chat_completion.get('error', ''):
            logger.error('Error in LLM request to YandexGPT: %s', chat_completion['error'])
            return no_result()
        try:
            total_tokens = int(chat_completion['result']['usage']['totalTokens'])
            chat_completion_text = chat_completion['result']['alternatives'][0]['message']['text']
        except Exception as e:
            logger.error('YandexGPT response missing totalTokens/text: %s', e)
            return no_result()

    elif model in ["DeepSeek-V3.1", "DeepSeek-V3.1-Think", 'GLM-4.6', 'GLM-4.5V', 'Qwen3-max', 'Qwen-plus']:
        if model.startswith('DeepSeek'):
            base_url = "https://api.deepseek.com"
            api_key = API.get('DEEPSEEK') or ''
            m = "deepseek-chat" if model == "DeepSeek-V3.1" else "deepseek-reasoner"
        elif model.startswith('GLM'):
            base_url = "https://api.z.ai/api/paas/v4/"
            api_key = API.get('GLM') or ''
            m = model.lower()
        elif model.startswith('Qwen'):
            base_url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
            api_key = API.get('QWEN') or ''
            m = model.lower()
        else:
            api_key = ''
            m = model.lower()
        if not api_key:
            logger.error('API key not set for model %s (check AUTOSAXS_LLM_* / DEEPSEEK / DASHSCOPE)', model)
            return no_result()
        openai_ = OpenAI(api_key=api_key, base_url=base_url)
        # OpenAI-compatible API accepts list content (vision)
        try:
            chat_completion = openai_.chat.completions.create(
                model=m, messages=messages, temperature=temperature, max_tokens=max_tokens,
                logprobs=return_confidence,
            )
        except openai.OpenAIError as e:
            logger.error('Error in LLM request to %s: %s', model, e)
            return no_result()
        except Exception as e:
            logger.error('Unknown error in LLM request to %s: %s', model, e)
            return no_result()
        try:
            total_tokens = chat_completion.usage.total_tokens
            chat_completion_text = chat_completion.choices[0].message.content
        except Exception as e:
            logger.error('Response missing total_tokens/content: %s', e)
            return no_result()

    else:
        # DeepInfra and similar
        base_url = "https://api.deepinfra.com/v1/openai"
        if model.startswith('Llama'):
            model_ = f"meta-llama/{model}"
        elif model.startswith("DeepSeek"):
            model_ = f"deepseek-ai/{model}"
        elif model.startswith("Qwen"):
            model_ = f"Qwen/{model}"
        elif model.startswith('gemma'):
            model_ = f'google/{model}'
        else:
            model_ = model
        api_key = API.get('DEEPINFRA_KEY') or ''
        if not api_key:
            logger.error('DeepInfra API key not set (AUTOSAXS_LLM_DEEPINFRA_KEY or DEEPINFRA_KEY)')
            return no_result()
        openai_ = OpenAI(api_key=api_key, base_url=base_url)
        # DeepInfra: pass string content
        try:
            chat_completion = openai_.chat.completions.create(
                model=model_, messages=messages_str, temperature=temperature, max_tokens=max_tokens,
            )
        except openai.OpenAIError as e:
            logger.error('Error in LLM request to %s: %s', model, e)
            return no_result()
        except Exception as e:
            logger.error('Unknown error in LLM request to %s: %s', model, e)
            return no_result()
        try:
            total_tokens = chat_completion.usage.total_tokens
            chat_completion_text = chat_completion.choices[0].message.content
        except Exception as e:
            logger.error('Response missing total_tokens/content: %s', e)
            return no_result()

    if return_confidence:
        confidence = calc_confidence(chat_completion)
        return chat_completion_text, total_tokens, confidence
    return chat_completion_text, total_tokens
