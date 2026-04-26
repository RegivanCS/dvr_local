#!/usr/bin/env python3
"""
Script para atualizar as configuracoes das cameras no DVR remoto.
Execute este script no servidor onde o DVR remoto esta rodando.
"""

import json
import os

def update_remote_cameras():
    """Atualiza as configuracoes das cameras com as URLs corretas dos tuneis."""

    config_file = 'cameras_config.json'

    if not os.path.exists(config_file):
        print(f'Arquivo {config_file} nao encontrado!')
        return False

    # Carregar configuracao atual
    with open(config_file, 'r') as f:
        config = json.load(f)

    print('Configuracao atual das cameras:')
    for cam_id, cam in config['cameras'].items():
        print(f'  {cam_id}: {cam["name"]} -> {cam["ip"]}')

    # URLs corretas dos tuneis (atualize estas URLs se necessario)
    correct_tunnels = {
        '1777239455850': 'weeks-somewhat-transcript-animated.trycloudflare.com',
        '1777239455851': 'structured-madonna-hearing-input.trycloudflare.com'
    }

    # Atualizar as cameras
    updated = False
    for cam_id, correct_ip in correct_tunnels.items():
        if cam_id in config['cameras']:
            old_ip = config['cameras'][cam_id]['ip']
            if old_ip != correct_ip:
                config['cameras'][cam_id]['ip'] = correct_ip
                config['cameras'][cam_id]['snapshot_url'] = f'https://{correct_ip}:443/snapshot.jpg'
                print(f'✓ Atualizada camera {cam_id}: {old_ip} -> {correct_ip}')
                updated = True
            else:
                print(f'- Camera {cam_id} ja esta correta')
        else:
            print(f'⚠ Camera {cam_id} nao encontrada na configuracao')

    if updated:
        # Salvar configuracao atualizada
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        print(f'\n✅ Configuracao atualizada! Reinicie o DVR remoto para aplicar as mudancas.')
        print('URLs dos tuneis atualizadas:')
        for cam_id, cam in config['cameras'].items():
            print(f'  {cam_id}: https://{cam["ip"]}/snapshot.jpg')
    else:
        print('\nℹ Nenhuma atualizacao necessaria.')

    return updated

if __name__ == '__main__':
    print('🔧 Atualizando configuracoes das cameras no DVR remoto...\n')
    update_remote_cameras()