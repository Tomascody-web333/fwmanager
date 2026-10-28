## Install / Instalar

Download `fwmanager.@VERSION@.deb` and `SHA256SUMS` into the same folder, then:
Descarregue `fwmanager.@VERSION@.deb` e `SHA256SUMS` para a mesma pasta e depois:

```sh
sha256sum -c SHA256SUMS
sudo apt install ./fwmanager.@VERSION@.deb
fwmanager
```

The window needs Ubuntu 24.04 or newer (GTK 4, libadwaita 1.5); `fwmanager-cli` also works on
older systems. Uninstall from Settings, with `fwmanager-cli uninstall` or `sudo apt purge fwmanager`.

A janela precisa do Ubuntu 24.04 ou mais recente (GTK 4, libadwaita 1.5); o `fwmanager-cli`
funciona também em sistemas mais antigos. Desinstale nas Definições, com
`fwmanager-cli uninstall` ou com `sudo apt purge fwmanager`.
