# -*- coding: utf-8 -*-
import sys

with open('tunnel_relay.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_start = '''def start_tunnel(cloudflared, cam, result_dict):
    """Inicia um tunnel para uma camera e captura a URL publica."""
    local_url = f'http://{cam["ip"]}:{cam["port"]}'
    print(f'  Iniciando tunnel para {local_url}...')
    proc = subprocess.Popen(
        [cloudflared, 'tunnel', '--url', local_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    result_dict['proc'] = proc
    url_pattern = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')
    for line in proc.stdout:
        m = url_pattern.search(line)
        if m:
            result_dict['url'] = m.group(0)
            print(f'  Tunnel ativo: {local_url} -> {result_dict["url"]}')
            break
    # Continua lendo stdout para manter o processo vivo
    for _ in proc.stdout:
        pass
    result_dict['url'] = None  # sinaliza que o tunnel caiu'''

new_start = '''def start_tunnel(cloudflared, cam, result_dict):
    """Inicia um tunnel para uma camera e captura a URL publica."""
    local_url = f'http://{cam["ip"]}:{cam["port"]}'
    print(f'  Iniciando tunnel para {local_url}...')
    proc = subprocess.Popen(
        [cloudflared, 'tunnel', '--url', local_url],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    result_dict['proc'] = proc
    url_pattern = re.compile(r'https://[a-z0-9\-]+\.trycloudflare\.com')
    for line in iter(proc.stdout.readline, ''):
        stripped = line.strip()
        if stripped:
            m = url_pattern.search(stripped)
            if m and 'api.trycloudflare.com' not in stripped:
                result_dict['url'] = m.group(0)
                print(f'  Tunnel ativo: {local_url} -> {result_dict["url"]}')
                break
    if not result_dict.get('url'):
        print(f'  Tunnel {local_url} nao gerou URL valida')
    result_dict['url'] = None  # sinaliza que o tunnel caiu'''

content = content.replace(old_start, new_start)

if new_start in content:
    with open('tunnel_relay.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("start_tunnel atualizado com sucesso")
else:
    print("FALHA: nao encontrou o bloco antigo")
    # Debug: mostrar o que tem perto
    idx = content.find('def start_tunnel')
    if idx >= 0:
        print(content[idx:idx+600])
