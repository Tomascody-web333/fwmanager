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
- **Os programas que correm como root nunca importam da pasta atual.** Os lançadores instalados
  arrancam o Python em modo isolado (`-I`), por isso um ficheiro como `json.py` deixado em `/tmp`
  não é executado quando um administrador escreve `sudo fwmanager-cli` nessa pasta.
- **O texto vindo de fora aparece tal como foi escrito.** Os nomes de Wi-Fi vêm de qualquer
  ponto de acesso ao alcance; a janela nunca os interpreta (nem a qualquer outro texto) como
  markup.
- **A lista de “tentativas bloqueadas recentes” vem do kernel.** É lida do diário do próprio
  kernel, onde os utilizadores não podem escrever; o `/var/log/ufw.log`, onde qualquer pessoa
  pode acrescentar uma linha parecida com `logger`, é só uma alternativa, e só se mostram linhas
  marcadas como vindas do kernel.
- **Os ficheiros de estado são privados.** `/var/lib/fwmanager` só pode ser lido pelo root (tem a
  lista das redes que usa).
- **O serviço só escreve o que o ufw precisa**: `/etc/ufw`, o ficheiro `/etc/default/ufw`
  (e não o resto de `/etc/default`), o seu bloqueio em `/run` e a sua própria pasta
  `/var/lib/fwmanager`.
- **Limites de tamanho.** Um perfil pode precisar no máximo de 40 regras do ufw, para que
  aplicá-lo demore segundos e não minutos em que o serviço não responderia a ninguém.
- **A desinstalação** (botão nas Definições ou `fwmanager-cli uninstall`) passa pelo mesmo
  auxiliar com uma segunda ação do polkit, `org.fwmanager.uninstall`, distinguida pelo primeiro
  argumento do auxiliar. Pede sempre a palavra-passe de administrador (`auth_admin`, nada fica
  guardado) e só executa o desinstalador instalado.
- **Nada é enviado para lado nenhum.** Sem acesso à rede, contas ou telemetria; os registos
  nunca contêm o conteúdo das regras.
- **Outras firewalls nunca são desativadas automaticamente.** O firewalld bloqueia o fwmanager
  (com uma explicação); Docker ou libvirt só geram um aviso.
- **A desinstalação** repõe os valores originais do ufw e só desliga o ufw se foi o fwmanager
  que o ligou.

Para comunicar um problema, abra uma issue no repositório, sem publicar endereços de rede reais.
