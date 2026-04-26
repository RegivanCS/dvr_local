#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Aplica patches no tunnel_relay.py"""
import sys

with open('tunnel_relay.py', 'r', encoding='utf-8') as f:
    content = f.read()

patches = 0

# 1) login_dvr - local primeiro sempre
old = 'def login_dvr():'
new = '''def login_dvr(only_local=False):
    global DVR_URL
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    # Sempre tenta local primeiro
    try:
        r = s.post(f'{DVR_URL_LOCAL}/login', data={
            'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'
        }, allow_redirects=True, timeout=10)
        if '/login' not in r.url:
            DVR_URL = DVR_URL_LOCAL
            print(f'Login no DVR local OK ({DVR_URL_LOCAL})')
            return s
    except Exception as e:
        print(f'  DVR local: {e}')
    # Tenta remoto se nao for only_local
    if not only_local:
        try:
            r = s.post(f'{DVR_URL_REMOTE}/login', data={
                'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'
            }, allow_redirects=True, timeout=10)
            if '/login' not in r.url:
                DVR_URL = DVR_URL_REMOTE
                print(f'Login no DVR remoto OK ({DVR_URL_REMOTE})')
                return s
        except Exception as e:
            print(f'  DVR remoto: {e}')
    print('Nenhum DVR disponivel.')
    return None

def _login_remote():
    """Tenta login APENAS no DVR remoto."""
    s = requests.Session()
    s.headers.update(HTTP_HEADERS)
    try:
        r = s.post(f'{DVR_URL_REMOTE}/login', data={
            'user': DVR_USER, 'password': DVR_PASSWORD, 'next': '/'
        }, allow_redirects=True, timeout=10)
        if '/login' not in r.url:
            print(f'Login remoto OK ({DVR_URL_REMOTE})')
            return s
    except Exception as e:
        print(f'Remoto inacessivel: {e}')
    return None

'''
# Precisamos remover o login_dvr antigo ate o proximo def
old_login = content[content.find('def login_dvr():'):]
next_def = old_login.find('\ndef ', 3)
if next_def > 0:
    old_login_block = old_login[:next_def]
    content = content.replace(old_login_block, new)
    patches += 1
    print('Patched login_dvr')
else:
    print('Could not find end of login_dvr')

# 2) register_tunnel_camera - adicionar push remoto
old_reg = content.find('def register_tunnel_camera')
if old_reg >= 0:
    block = content[old_reg:]
    next_def = block.find('\ndef ', 3)
    if next_def > 0:
        old_block = block[:next_def]
        # Adicionar push remoto no final
        push_remote = '''
    # Tambem tenta cadastrar no remoto
        try:
        remote_s = _login_remote()
        if remote_s:
            r = remote_s.post(f'{DVR_URL_REMOTE}/api/camera/add', data=data, timeout=15)
            result = r.json()
            if result.get('success'):
                print(f'  DVR remoto atualizado! ID: {result.get(\"cam_id\")}')
    except Exception as e:
        print(f'  Falha no DVR remoto: {e}')
'''
        new_block = old_block.rstrip() + push_remote
        content = content.replace(old_block, new_block)
        patches += 1
        print('Patched register_tunnel_camera')
    else:
        print('Could not find end of register_tunnel_camera')

with open('tunnel_relay.py', 'w', encoding='utf-8') as f:
    f.write(content)
print(f'{patches} patches aplicados')

