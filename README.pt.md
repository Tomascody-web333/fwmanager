# fwmanager

[English](README.md) · **Português**

Uma interface gráfica para o **ufw**, pensada para quem está a começar. Não é uma firewall
própria: só torna o ufw mais fácil de usar. Escolha um perfil (`casa`, `trabalho`, `publico`),
deixe a aplicação mudar de perfil sozinha quando muda de Wi-Fi e conte com uma rede de
segurança que desfaz qualquer alteração que lhe corte a ligação.

100% local: sem contas, sem servidores, sem telemetria.

> Estado: completo para a instalação de desenvolvimento; o empacotamento (apt) fica para o fim.
> O que só uma máquina real consegue provar está em [docs/VALIDATION.pt.md](docs/VALIDATION.pt.md).

## Início rápido

```sh
sudo apt install ufw python3-gi gir1.2-gtk-4.0 gir1.2-adw-1   # Ubuntu 24.04 / Debian 13
sudo scripts/dev-install.sh                                   # instala e pára aqui
fwmanager                                                     # o resto configura-se na janela
```

A instalação só copia ficheiros. Na primeira vez que abre o **fwmanager**, um assistente curto
verifica o sistema (serviço a correr, a sua conta autorizada, ufw instalado, nenhum outro gestor
de firewall no caminho) e corrige o que faltar com **um único** pedido de palavra-passe de
administrador (polkit). Depois disso nunca mais pede, e escolhe como este computador se deve
comportar na rede atual. Nada precisa de terminal. Se a janela de palavra-passe não estiver
disponível, o assistente mostra os comandos exatos a executar.

A interface precisa do GTK 4 e do libadwaita 1.5 ou superior (Ubuntu 24.04, Fedora 40 ou
posterior); o `fwmanager-cli` funciona em sistemas mais antigos.

## Como funciona

```
fwmanager (interface GTK4, utilizador normal)  ─┐
fwmanager-cli (utilizador normal / root)       ─┼─ D-Bus ─►  fwmanagerd (root)  ─►  ufw  ─►  iptables/nftables
dispatcher do NetworkManager (root)            ─┘            só comandos fixos
```

- **Os perfis** são conjuntos de regras do ufw marcadas com o comentário `fwmanager:<perfil>`.
  Mudar de perfil só adiciona e remove essas regras. **As suas próprias regras do ufw nunca são
  tocadas.**
- **Alterações seguras**: cada alteração guarda uma cópia da configuração do ufw e inicia um
  temporizador de 30 s. Se não confirmar, a configuração anterior é restaurada byte a byte.
- **Perfis por rede**: o dispatcher do NetworkManager aplica o perfil associado à rede que
  acabou de ligar e verifica se a gateway continua acessível; se não, reverte. Uma rede Wi-Fi
  ou móvel sem perfil recebe `publico`; um cabo sem perfil mantém o atual, para que ligar um
  cabo nunca feche uma sessão SSH.
- **O serviço nunca aceita texto de regras.** Os clientes só podem chamar comandos fixos
  (aplicar um perfil pelo nome, confirmar, reverter, ler o estado); todos os argumentos são
  validados e só se executa o `ufw` com uma lista de argumentos (sem shell). Quem chama tem de
  pertencer ao grupo `fwmanager`.
- O ufw não consegue desligar o ping nem o mDNS pela linha de comandos, por isso esses
  interruptores por perfil são um pequeno bloco bem delimitado que o fwmanager mantém em
  `/etc/ufw/before.rules` (e `before6.rules`). É removido na desinstalação.

Requisitos: Python 3.11+, PyGObject, **ufw**, systemd, D-Bus, polkit (configuração inicial) e,
para a janela, GTK 4 + libadwaita 1.5+. O NetworkManager é opcional (só é preciso para os
perfis automáticos por rede).

## A interface

Execute o `fwmanager` como utilizador normal (recusa-se a correr como root). Na primeira
execução aparece o assistente de configuração (ver acima); pode ser saltado e não volta a
aparecer depois de concluído. Páginas: **Início** (estado, mudança rápida de perfil, um cartão
“a aguardar confirmação” com contagem decrescente), **Perfis** (editar o que cada perfil abre),
**Redes** (que perfil para que Wi-Fi), **Regras** (frases em linguagem simples, abaixo),
**Registo** (o que o serviço fez e as tentativas bloqueadas recentes) e **Definições**. Cada
perfil tem um botão **Testar** que corre o teste de política descrito abaixo antes de aplicar
seja o que for. Ctrl+1..6 saltam entre páginas e o seletor de páginas passa para baixo em
janelas estreitas.

Se o serviço não estiver a correr, a sua conta não estiver no grupo `fwmanager`, o ufw faltar
ou o firewalld estiver ativo, a janela explica-o no seu idioma e oferece um botão **Corrigir**
(ou o comando a executar, quando não é possível mostrar a janela de palavra-passe).

Pertencer ao grupo só vale para processos novos, por isso, depois da configuração única, o
fwmanager reinicia-se sozinho com o novo grupo (`sg fwmanager`). Se não for possível, pede-lhe
que termine a sessão e volte a entrar.

## Regras em linguagem simples

Em vez de portas e origens, a página **Regras** oferece frases fixas com espaços em branco:

- *Permitir [serviço] apenas em [perfis]*
- *Bloquear tudo o que entra, exceto [serviços], em [perfis]*
- *Aceitar ligações apenas da rede local / [redes] em [perfis]*
- *Esconder este computador (sem ping, sem mDNS) em [perfis]*
- *Fechar [serviço] em todos os perfis*

Não há interpretação de texto livre: cada espaço é uma lista (serviços do catálogo, nomes de
perfis, um âmbito) ou um endereço de rede validado. Antes de guardar vê a frase final e uma
pré-visualização do que muda em cada perfil. As regras assentam sobre os perfis de origem e as
suas edições, aplicam-se por ordem (uma regra posterior vence uma anterior) e são compiladas
em perfis normais, pelo que as verificações de segurança, a confirmação de 30 s e o teste de
política funcionam sobre elas sem alterações. **Guardar uma regra nunca toca na firewall**:
só entra em vigor quando aplica o perfil, que é também o momento certo para carregar em
**Testar**. As regras ficam em `/var/lib/fwmanager/rules.json`.

## Idiomas

Português (Portugal) e inglês. O idioma é escolhido por esta ordem: a sua escolha em
**Definições** (`~/.config/fwmanager/settings.toml`) > `/etc/fwmanager/config.toml` > o
idioma do sistema (`pt*` dá português, o resto inglês). Mudá-lo nas Definições reconstrói a
janela de imediato; não pede palavra-passe e nunca toca no serviço. O próprio serviço regista
sempre em inglês; a interface traduz os códigos de estado e de erro. Os scripts de instalação
também falam o idioma do sistema.

As traduções estão em `po/*.po`; os ficheiros `.mo` são compilados pelo `dev-install.sh` (não
são precisas ferramentas gettext). Depois de alterar texto visível, execute
`python3 scripts/update-po.py`, traduza as entradas novas e `python3 scripts/update-po.py --check`
(também corrido pelos testes) tem de passar.

## Instalação de desenvolvimento (só VM/contentor)

```sh
sudo apt install ufw                       # o fwmanager usa o ufw; instale-o primeiro
sudo scripts/dev-install.sh                # --user NOME e --lang pt|en são atalhos opcionais
sudo scripts/dev-uninstall.sh
```

O `dev-install.sh` copia os ficheiros, a política polkit e o auxiliar de configuração
(`/usr/local/libexec/fwmanager/fwmanager-setup`, o único programa que pede direitos de
administrador) e põe o root no grupo `fwmanager` para o hook do NetworkManager. Criar o grupo,
adicionar a sua conta e arrancar o serviço acontece na janela, ou logo de seguida se passar
`--user NOME`. `--no-systemd` salta o passo do systemd.

O `dev-uninstall.sh` remove os ficheiros, as regras do fwmanager no ufw e o seu bloco em
`before.rules`, e volta a desligar o ufw **apenas se foi o fwmanager que o ligou**, para a
máquina nunca ficar a filtrar sem ninguém a geri-la. Um ufw que ativou por si fica ligado.

> **Aviso:** no primeiro arranque, sem estado guardado, o serviço aplica `publico` (nada pode
> entrar). Se instalar por SSH, o instalador deixa por isso o serviço parado (contornável com
> `--allow-ssh-lockout`); arranque-o na própria máquina ou confirme primeiro um perfil que
> permita SSH (`fwmanager-cli apply casa` e depois `confirm`).

No arranque do sistema, o serviço volta a aplicar o último perfil confirmado, sem temporizador.
Se o `firewalld` estiver ativo, não aplica nada e a interface explica porquê.

## Testar uma política antes de confiar nela

```sh
fwmanager-cli test-policy casa --static   # só analisa, não altera nada
fwmanager-cli test-policy casa            # analisa + teste real (aplicado uns segundos, depois revertido)
```

Código de saída: `0` seguro, `2` seguro com avisos, `3` inseguro (partiria algo), `1` não
foi possível correr.

- **Estático** (serviço, só leitura): mostra que regras seriam adicionadas e removidas, valida
  cada uma com `ufw --dry-run` e verifica que a configuração real do ufw ainda tem o que uma
  máquina normal precisa (respostas, loopback, DHCP/DHCPv6, descoberta de vizinhos IPv6 e PMTU).
  Avisa também se a sua sessão SSH atual não conseguiria voltar a ligar-se com o perfil, ou se
  o IPv6 não está filtrado.
- **Real** (cliente): mede a gateway (IPv4/IPv6), o DNS e o loopback antes e depois de aplicar
  o perfil e reverte sempre. Tudo o que funcionava antes e falha depois é reportado como
  partido. Se o teste for morto a meio, o serviço reverte sozinho após o tempo limite.

## Desenvolvimento

```sh
python3 -m venv --system-site-packages .venv   # os pacotes do sistema fornecem o PyGObject
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest tests/unit tests/security      # sem privilégios
sudo .venv/bin/pytest tests                     # tudo: ufw real + D-Bus numa netns privada
xvfb-run -a pytest tests/ui                     # janelas GTK num ecrã virtual (ou deixe os
                                                # testes iniciarem o seu próprio Xvfb)
```

Os testes de integração nunca tocam na firewall do anfitrião: correm o ufw dentro de um espaço
de nomes de rede descartável (`unshare -n`) com uma cópia privada de `/etc/ufw`
(`ufw --rootdir`). O teste real de instalação/desinstalação altera a máquina, por isso é
opcional: `sudo FWMANAGER_TEST_REAL_INSTALL=1 .venv/bin/pytest tests/install`.

## Documentação

- [docs/VALIDATION.pt.md](docs/VALIDATION.pt.md): o que os testes automáticos cobrem e a lista
  de verificação do que só uma máquina real prova (systemd, NetworkManager, IPv6, DHCP,
  ambiente de trabalho, polkit).
- [docs/SECURITY.pt.md](docs/SECURITY.pt.md): o modelo de segurança numa página.
- Todos os documentos existem em inglês e em português (`*.pt.md`).

## Limites conhecidos

- O ufw não consegue aplicar um perfil de forma atómica: há uma fração de segundo entre remover
  as regras antigas e adicionar as novas, sempre no sentido seguro (fechar antes de abrir).
  Se uma alteração falhar a meio, é desfeita automaticamente.
- O comportamento IPv6 é coberto por verificações da configuração do ufw, mas um teste IPv6
  real precisa de uma máquina com IPv6 ativo.
- Ainda não foram exercitados numa máquina real: o endurecimento do systemd, o hook do
  NetworkManager, o DHCP, o pedido do polkit e o aspeto no ambiente de trabalho (ver
  [docs/VALIDATION.pt.md](docs/VALIDATION.pt.md)).
- Uma rede é reconhecida pela sua ligação do NetworkManager (nome do Wi-Fi e id da ligação). Um
  ponto de acesso falso com o mesmo nome e a mesma palavra-passe é tratado como o verdadeiro, por
  isso mantenha serviços abertos só em redes protegidas por uma palavra-passe de confiança
  (WPA2/WPA3).
- A unidade systemd é menos restritiva do que o mínimo (`CAP_NET_RAW`, `AF_INET`, escrita em
  `/etc/ufw`) porque o ufw e o iptables o exigem.
