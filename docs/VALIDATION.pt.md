# Validação

[English](VALIDATION.md) · **Português**

## O que os testes automáticos provam

Correm num espaço de nomes de rede descartável, com uma cópia privada de `/etc/ufw` (nunca a do
anfitrião):

- o comportamento real do `ufw`: perfis, o bloco gerido em `before.rules`, cópia e restauro,
  valores por omissão, limpeza na desinstalação (`tests/ufw`);
- o serviço num D-Bus privado, incluindo a política de grupos de um bus semelhante ao do
  sistema (`tests/integration`);
- tráfego real entre dois espaços de nomes: o que um perfil permite e bloqueia, e que uma
  reversão o repõe (`tests/integration/test_traffic.py`);
- as janelas GTK4/libadwaita num ecrã virtual (Xvfb), com teclas reais (`tests/ui`), incluindo o
  assistente de primeira execução com um serviço simulado;
- tentativas de injeção contra todos os argumentos que o serviço aceita (`tests/security`);
- o instalador e o desinstalador, instalados para uma pasta (`tests/install`); a viagem
  completa de instalação/desinstalação corre com `FWMANAGER_TEST_REAL_INSTALL=1` numa VM.

## O que só uma máquina real prova

Faça-o numa VM com sessão de ambiente de trabalho, **na consola dela e não por SSH**. Tire
primeiro um instantâneo.

| # | Verificação | Como | Esperado |
|---|-------------|------|----------|
| 1 | Instalar | `sudo add-apt-repository ppa:tomascody-web333/fwmanager && sudo apt install fwmanager` (ou `sudo scripts/dev-install.sh` a partir do código) | O fwmanager aparece no menu de aplicações; o serviço ainda não arrancou |
| 2 | Assistente inicial | abrir o `fwmanager` pelo menu | O assistente lista o que falta; **Corrigir** mostra um só pedido do polkit; depois o fwmanager reinicia-se e mostra a escolha de perfil |
| 3 | Polkit recusado | cancelar o pedido ou usar uma conta sem privilégios | Mensagem clara e os comandos a executar; nada alterado |
| 4 | Endurecimento do systemd | `systemctl status fwmanagerd`, `journalctl -u fwmanagerd` | Ativo, sem “permission denied” em `/etc/ufw`, `/run`, netlink |
| 5 | Mudar de perfil | aplicar `casa`, confirmar; aplicar `trabalho`, esperar 30 s | O perfil confirmado mantém-se; o não confirmado é desfeito |
| 6 | NetworkManager | ligar a um segundo Wi-Fi associado a outro perfil | O perfil acompanha a rede; a gateway continua acessível |
| 7 | DHCP | `sudo dhclient -r && sudo dhclient` com `publico` | Obtém-se um endereço |
| 8 | IPv6 | num anfitrião com IPv6: `ping -6` e depois **Testar** | A descoberta de vizinhos funciona; o teste não reporta falhas |
| 9 | Teste de política | **Testar** em todos os perfis | Seguro, ou avisos que correspondem à realidade |
| 10 | Aspeto no ambiente | tema claro/escuro, escala 200 %, janela estreita, Orca | Legível, nada cortado, o seletor de páginas passa para baixo |
| 11 | Reiniciar | reiniciar a máquina | O último perfil confirmado volta a aplicar-se, sem temporizador |
| 12 | Desinstalar pela janela | **Definições → Desinstalar o fwmanager…**, palavra-passe | Página final e a app fecha; o `ufw status` volta ao que era; o ufw só fica desligado se o fwmanager o tinha ligado; sem ficheiros nem entrada no menu |
| 13 | Setup e desinstalação pelo terminal | reinstalar, depois `fwmanager-cli setup` (com o seu utilizador) e mais tarde `fwmanager-cli uninstall` | Uma palavra-passe no setup e o perfil perguntado no terminal; abrir a janela a seguir não mostra o assistente; a desinstalação não deixa nada |
| 14 | Ícone | ver a grelha de aplicações, a dock e a janela na barra de tarefas (GNOME e, se possível, XFCE ou KDE) | O escudo em todo o lado; a janela aberta agrupa com o lançador |

Comunique qualquer diferença com o resultado de `journalctl -u fwmanagerd` e `ufw status verbose`.
