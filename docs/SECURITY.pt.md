# Modelo de segurança

[English](SECURITY.md) · **Português**

- **A janela nunca corre como root.** O `fwmanager` recusa-se a arrancar como root e fala com o
  serviço por D-Bus.
- **O serviço (`fwmanagerd`, root) só aceita comandos fixos**: aplicar um perfil pelo nome,
  confirmar, reverter, ler o estado e os editores validados de perfis, regras e redes. Não
  existe nenhum método que receba texto do ufw ou de regras. Todos os argumentos são
  validados; o único programa executado é o `ufw` (o serviço nunca executa `systemctl`),
  sempre com uma lista de argumentos e sem shell.
- **Quem pode chamar**: só membros do grupo `fwmanager`. O serviço verifica o uid e os grupos de
  quem chama em cada pedido (`GetConnectionUnixUser`), além da política do D-Bus.
- **As regras em linguagem simples** escolhem-se entre frases fixas; os espaços são listas ou
  endereços validados. São compiladas em perfis normais, pelo que recebem as mesmas verificações.
- **Alterações seguras**: antes de cada alteração tira-se uma cópia exata de `/etc/ufw` e
  `/etc/default/ufw`; se não for confirmada em 30 s (ou se a aplicação falhar a meio), é
  restaurada.
- **A configuração inicial** é o único passo que precisa de direitos de administrador. Passa
  pelo polkit (`pkexec`) para um só auxiliar, `/usr/local/libexec/fwmanager/fwmanager-setup`. O
  auxiliar não aceita argumentos livres: adiciona ao grupo `fwmanager` a conta que executou o
  `pkexec` (`PKEXEC_UID`, nunca um valor da linha de comandos) e arranca o serviço. A
  autorização é `auth_admin_keep`: uma palavra-passe, sem credencial guardada.
- **Nada é enviado para lado nenhum.** Sem acesso à rede, contas ou telemetria; os registos
  nunca contêm o conteúdo das regras.
- **Outras firewalls nunca são desativadas automaticamente.** O firewalld bloqueia o fwmanager
  (com uma explicação); Docker ou libvirt só geram um aviso.
- **A desinstalação** repõe os valores originais do ufw e só desliga o ufw se foi o fwmanager
  que o ligou.

Para comunicar um problema, abra uma issue no repositório, sem publicar endereços de rede reais.
